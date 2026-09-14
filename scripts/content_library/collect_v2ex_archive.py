#!/usr/bin/env python3
"""把 V2EX 公开热榜归档(cxyfreedom/v2ex-hot-hub)收进阅读库(articles)。

数据来源: https://github.com/cxyfreedom/v2ex-hot-hub —— 一个每日更新的第三方镜像,
每日 raw/<日期>.json 存当天 V2EX 热门主题, 含标题/正文/回复数/节点/作者。
优点: 免 token、带历史、带全文。正好补 OpenBiliClaw 当前空缺的 V2EX 源
(项目里只有 V2EXSourceConfig 配置桩, 无真实适配器实现)。

用法:
  python3 scripts/content_library/collect_v2ex_archive.py                 # 全量灌库(增量去重) + 桥接推荐池
  python3 scripts/content_library/collect_v2ex_archive.py --limit-days 7  # 只收最近 7 天
  python3 scripts/content_library/collect_v2ex_archive.py --dry-run       # 只数数不写库
  python3 scripts/content_library/collect_v2ex_archive.py --repo /path    # 指定本地仓库
  python3 scripts/content_library/collect_v2ex_archive.py --no-pool       # 只灌阅读库, 不碰推荐池
  python3 scripts/content_library/collect_v2ex_archive.py --recent 14     # 只最近14天进推荐池(默认30)
  python3 scripts/content_library/collect_v2ex_archive.py --loop --interval 24  # 常驻循环(pm2 用)

行为:
  - git clone/pull 归档到 data/v2ex_hot_hub (首次 clone, 之后 pull 增量)
  - 遍历 raw/*.json, 解析每条 topic
  - 按 url 去重 upsert 进 articles (阅读库):
      * 不存在 -> INSERT (published_at 用 topic.created epoch 转换, 让历史按时间排序)
      * 已存在且 content_text 为空 -> 补填正文/摘要/标签 (CASE WHEN 保护已有值)
      * 已存在且 content_text 非空 -> 跳过 (绝不覆盖用户/已有数据)
  - 同时(默认 --pool)按 topic id 去重 upsert 进 content_cache (推荐池):
      * 这是推荐池 V2EX 断流的真正补法——v2ex_producer --mode api 走 v2ex.com API,
        现网络下 403 死掉; 归档走 GitHub 镜像, 带全文, 且能回填正文到已存在的
        标题流空行 (RSSHub / api 模式写入的 v2ex 行)。
      * 仅最近 --recent 天(默认 30)的归档进推荐池, 避免把多年历史一次性
        灌进实时推荐; 阅读库 articles 仍收全量。
      * 幂等: 重复运行安全; 已有行只补空字段, 绝不覆盖。
      * 不存在 -> INSERT (published_at 用 topic.created epoch 转换, 让历史按时间排序)
      * 已存在且 content_text 为空 -> 补填正文/摘要/标签 (CASE WHEN 保护已有值)
      * 已存在且 content_text 非空 -> 跳过 (绝不覆盖用户/已有数据)
  - source_type='v2ex', tag 写入 ["V2EX", 节点名]
幂等: 重复运行安全; 不删数据、不改已有正文。

依赖: git (clone/pull 归档)
"""
from __future__ import annotations

import argparse
import datetime as dt

# 中国本地时间(UTC+8)。articles 表所有时间字段统一存北京时间字符串。
CN_TZ = dt.timezone(dt.timedelta(hours=8))
import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import sqlite3  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(BASE, "data", "openbiliclaw.db")
REPO_URL = "https://github.com/cxyfreedom/v2ex-hot-hub.git"
DEFAULT_REPO_DIR = os.path.join(BASE, "data", "v2ex_hot_hub")

PLATFORM = "v2ex"
SOURCE_NAME = "V2EX"
MIN_BODY = 1  # V2EX 热帖基本都有正文, 低于此长度不收为正文(仍收标题)


def _epoch_to_iso(epoch) -> str | None:
    try:
        e = int(epoch)
        if e <= 0:
            return None
        return dt.datetime.fromtimestamp(e, CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _ensure_repo(repo_dir: str) -> bool:
    """clone 或 pull 归档仓库, 返回是否成功拿到 raw 目录。"""
    if os.path.isdir(os.path.join(repo_dir, "raw")):
        print(f"[git] pull 归档 {repo_dir}")
        try:
            subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=repo_dir, capture_output=True, text=True, timeout=120, check=False,
            )
        except Exception as e:  # noqa: BLE001
            print(f"  ! git pull 失败(继续用本地现有数据): {e}")
        return True

    print(f"[git] clone 归档 -> {repo_dir}")
    parent = os.path.dirname(repo_dir)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", REPO_URL, repo_dir],
            capture_output=True, text=True, timeout=180, check=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"  ! clone 失败: {e.stderr or e}")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"  ! clone 失败: {e}")
        return False


def _iter_topics(repo_dir: str, limit_days: int | None):
    """生成 (topic_dict, file_date_str) 迭代器, 按日期倒序。"""
    raw_dir = os.path.join(repo_dir, "raw")
    if not os.path.isdir(raw_dir):
        return
    files = sorted(
        (f for f in os.listdir(raw_dir) if f.endswith(".json")),
        reverse=True,
    )
    if limit_days and limit_days > 0:
        files = files[: limit_days]
    for fname in files:
        fpath = os.path.join(raw_dir, fname)
        date_str = fname[:-len(".json")]
        try:
            with open(fpath, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:  # noqa: BLE001
            continue
        topics = data if isinstance(data, list) else (data.get("data") or data.get("items") or [])
        for t in topics:
            if isinstance(t, dict) and t.get("id"):
                yield t, date_str


def _topic_to_article(topic: dict, date_str: str) -> dict | None:
    url = (topic.get("url") or "").strip()
    title = (topic.get("title") or "").strip()
    if not url or not title:
        return None

    member = topic.get("member") or {}
    node = topic.get("node") or {}
    author = (member.get("username") if isinstance(member, dict) else "") or ""
    node_name = (node.get("name") if isinstance(node, dict) else "") or ""
    node_title = (node.get("title") if isinstance(node, dict) else "") or node_name

    content = (topic.get("content") or "").strip()
    body = content if len(content) >= MIN_BODY else ""
    summary_src = content or (topic.get("content_rendered") or "")
    summary = summary_src[:200].replace("\n", " ").strip()
    if node_title:
        summary = f"[{node_title}] {summary}" if summary else f"[{node_title}]"

    published_iso = _epoch_to_iso(topic.get("created")) or (date_str + " 12:00:00")
    replies = topic.get("replies") or 0
    tags = json.dumps([SOURCE_NAME, node_name or "v2ex"], ensure_ascii=False)

    return {
        "source_type": PLATFORM,
        "source_name": node_title or SOURCE_NAME,
        "title": title,
        "url": url,
        "author": author,
        "summary": summary,
        "content_text": body,
        "published_at": published_iso,
        "tags": tags,
        "replies": replies,
        "bvid": str(topic.get("id") or "").strip(),
    }


def _upsert_pool(cur: sqlite3.Cursor, art: dict, now_iso: str) -> str:
    """Upsert one topic into content_cache (recommendation pool).

    Dedup by topic id (bvid). On conflict (an existing v2ex row from the
    old feed producer or RSSHub) only fill empty fields, never overwrite
    real data — this enriches the title-only rows with 正文.
    Returns 'inserted' | 'filled' | 'skipped'.
    """
    bvid = art["bvid"]
    if not bvid:
        return "skipped"
    row = cur.execute(
        "SELECT body_text FROM content_cache WHERE bvid=?", (bvid,)
    ).fetchone()
    if row is None:
        cur.execute(
            """INSERT INTO content_cache (
                bvid, title, up_name, author_name, content_url,
                source_platform, source, content_type, pool_status,
                discovered_at, body_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                bvid, art["title"], art["author"], art["author"], art["url"],
                "v2ex", "v2ex-archive", "thread", "fresh",
                now_iso, art["content_text"],
            ),
        )
        return "inserted"
    existing_body = (row[0] or "") if row[0] is not None else ""
    if not existing_body.strip() and art["content_text"]:
        cur.execute(
            """UPDATE content_cache
               SET body_text = CASE WHEN body_text IS NULL OR TRIM(body_text)='' THEN ? ELSE body_text END,
                   title = CASE WHEN title IS NULL OR TRIM(title)='' THEN ? ELSE title END,
                   up_name = CASE WHEN up_name IS NULL OR TRIM(up_name)='' THEN ? ELSE up_name END,
                   author_name = CASE WHEN author_name IS NULL OR TRIM(author_name)='' THEN ? ELSE author_name END,
                   content_url = CASE WHEN content_url IS NULL OR TRIM(content_url)='' THEN ? ELSE content_url END,
                   discovered_at = ?
               WHERE bvid = ?""",
            (art["content_text"], art["title"], art["author"], art["author"],
             art["url"], now_iso, bvid),
        )
        return "filled"
    return "skipped"


def collect(repo_dir: str, *, limit_days: int | None, dry_run: bool,
            pool: bool, pool_window: int) -> dict:
    total = inserted = filled = skipped = ignored = 0
    pool_inserted = pool_filled = pool_skipped = 0
    conn = None if dry_run else sqlite3.connect(DB_PATH)
    # v0.4.0+: articles 表迁移到 content.db，ATTACH 以便跨库查询
    try:
        from pathlib import Path as _Path
        _content_db = _Path(__file__).parent.parent.parent / "data" / "content.db"
        if _content_db.exists():
            conn.execute("ATTACH DATABASE ? AS content", (str(_content_db),))
    except Exception:
        pass


    cur = None if dry_run else conn.cursor()
    now_iso = dt.datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    cutoff = (dt.date.today() - dt.timedelta(days=pool_window)).isoformat()

    for topic, date_str in _iter_topics(repo_dir, limit_days):
        art = _topic_to_article(topic, date_str)
        if art is None:
            ignored += 1
            continue
        total += 1

        if dry_run:
            inserted += 1
            if pool and date_str >= cutoff:
                pool_inserted += 1
            continue

        row = cur.execute("SELECT id, content_text FROM articles WHERE url=?", (art["url"],)).fetchone()
        if row is None:
            cur.execute(
                """INSERT INTO articles
                   (source_type, source_name, title, url, author, summary, content_text,
                    published_at, tags, status, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (art["source_type"], art["source_name"], art["title"], art["url"],
                 art["author"], art["summary"], art["content_text"], art["published_at"],
                 art["tags"], "unread", now_iso, now_iso),
            )
            inserted += 1
        else:
            existing_body = (row[1] or "") if row[1] is not None else ""
            # 仅当已有正文为空时补填, 保护已有真实内容(不删不改)
            if not existing_body.strip() and art["content_text"]:
                cur.execute(
                    """UPDATE articles
                       SET content_text = CASE WHEN content_text IS NULL OR TRIM(content_text)='' THEN ? ELSE content_text END,
                           summary = CASE WHEN summary IS NULL OR TRIM(summary)='' THEN ? ELSE summary END,
                           tags = CASE WHEN tags IS NULL OR TRIM(tags)='' THEN ? ELSE tags END,
                           updated_at = ?
                       WHERE id = ?""",
                    (art["content_text"], art["summary"], art["tags"], now_iso, row[0]),
                )
                filled += 1
            else:
                skipped += 1

        # 推荐池桥接: 仅最近 pool_window 天的归档进实时推荐
        if pool and date_str >= cutoff:
            res = _upsert_pool(cur, art, now_iso)
            if res == "inserted":
                pool_inserted += 1
            elif res == "filled":
                pool_filled += 1
            else:
                pool_skipped += 1

    if not dry_run:
        conn.commit()
        conn.close()

    return {
        "total": total,
        "inserted": inserted,
        "filled": filled,
        "skipped": skipped,
        "ignored": ignored,
        "pool_inserted": pool_inserted,
        "pool_filled": pool_filled,
        "pool_skipped": pool_skipped,
        "pool": pool,
        "pool_window": pool_window,
        "dry_run": dry_run,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="把 V2EX 热榜归档收进阅读库(并可选桥接推荐池)")
    ap.add_argument("--repo", default=DEFAULT_REPO_DIR, help="本地归档仓库路径")
    ap.add_argument("--limit-days", type=int, default=None, help="只收最近 N 天(默认全量)")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    ap.add_argument("--pool", dest="pool", action="store_true",
                    help="同时把归档桥接进推荐池 content_cache(默认开)")
    ap.add_argument("--no-pool", dest="pool", action="store_false",
                    help="只灌阅读库, 不碰推荐池")
    ap.set_defaults(pool=True)
    ap.add_argument("--recent", type=int, default=30,
                    help="仅最近 N 天的归档进推荐池(默认 30, 避免多年历史灌进实时推荐)")
    ap.add_argument("--loop", action="store_true",
                    help="持续循环(每隔 --interval 小时跑一轮), 用于 pm2 常驻")
    ap.add_argument("--interval", type=int, default=24,
                    help="--loop 模式下的间隔小时数(默认 24)")
    args = ap.parse_args()

    if not _ensure_repo(args.repo):
        print("无法获取 V2EX 归档, 中止。")
        return 1

    if args.loop:
        interval = max(1, args.interval)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
        )
        logger = logging.getLogger("collect_v2ex_archive")
        logger.info("loop 模式启动 (interval=%dh, pool=%s, recent=%d)",
                    interval, args.pool, args.recent)
        while True:
            print(f"[解析] 遍历 {args.repo}/raw ...")
            stats = collect(
                args.repo, limit_days=args.limit_days, dry_run=False,
                pool=args.pool, pool_window=args.recent,
            )
            logger.info(
                "阅读库 新增 %d/补空 %d/跳过 %d | 推荐池 新增 %d/回填 %d/跳过 %d",
                stats["inserted"], stats["filled"], stats["skipped"],
                stats["pool_inserted"], stats["pool_filled"], stats["pool_skipped"],
            )
            time.sleep(interval * 3600)
        return 0

    print(f"[解析] 遍历 {args.repo}/raw ...")
    stats = collect(
        args.repo, limit_days=args.limit_days, dry_run=args.dry_run,
        pool=args.pool, pool_window=args.recent,
    )

    mode = "DRY-RUN" if stats["dry_run"] else "写入"
    print(
        f"[{mode}] 候选 {stats['total']} | 阅读库新增 {stats['inserted']} | "
        f"阅读库补空正文 {stats['filled']} | 阅读库跳过(已有) {stats['skipped']} | "
        f"忽略(无url/title) {stats['ignored']}"
    )
    if stats["pool"]:
        print(
            f"[{mode}] 推荐池 新增 {stats['pool_inserted']} | "
            f"回填正文 {stats['pool_filled']} | 跳过(已有) {stats['pool_skipped']} "
            f"(窗口={stats['pool_window']}天)"
        )
    else:
        print("[info] 推荐池桥接已关闭(--no-pool)")
    if stats["dry_run"]:
        print("(dry-run 模式未写库)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

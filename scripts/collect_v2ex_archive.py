#!/usr/bin/env python3
"""把 V2EX 公开热榜归档(cxyfreedom/v2ex-hot-hub)收进阅读库(articles)。

数据来源: https://github.com/cxyfreedom/v2ex-hot-hub —— 一个每日更新的第三方镜像,
每日 raw/<日期>.json 存当天 V2EX 热门主题, 含标题/正文/回复数/节点/作者。
优点: 免 token、带历史、带全文。正好补 OpenBiliClaw 当前空缺的 V2EX 源
(项目里只有 V2EXSourceConfig 配置桩, 无真实适配器实现)。

用法:
  python3 scripts/collect_v2ex_archive.py                 # 全量灌库(增量去重)
  python3 scripts/collect_v2ex_archive.py --limit-days 7  # 只收最近 7 天
  python3 scripts/collect_v2ex_archive.py --dry-run       # 只数数不写库
  python3 scripts/collect_v2ex_archive.py --repo /path    # 指定本地仓库

行为:
  - git clone/pull 归档到 data/v2ex_hot_hub (首次 clone, 之后 pull 增量)
  - 遍历 raw/*.json, 解析每条 topic
  - 按 url 去重 upsert 进 articles:
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
import json
import os
import shutil
import sqlite3
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
        return dt.datetime.fromtimestamp(e, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
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
            with open(fpath, "r", encoding="utf-8") as fh:
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
    }


def collect(repo_dir: str, *, limit_days: int | None, dry_run: bool) -> dict:
    total = inserted = filled = skipped = ignored = 0
    conn = None if dry_run else sqlite3.connect(DB_PATH)
    cur = None if dry_run else conn.cursor()
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    for topic, date_str in _iter_topics(repo_dir, limit_days):
        art = _topic_to_article(topic, date_str)
        if art is None:
            ignored += 1
            continue
        total += 1

        if dry_run:
            inserted += 1
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

    if not dry_run:
        conn.commit()
        conn.close()

    return {
        "total": total,
        "inserted": inserted,
        "filled": filled,
        "skipped": skipped,
        "ignored": ignored,
        "dry_run": dry_run,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="把 V2EX 热榜归档收进阅读库")
    ap.add_argument("--repo", default=DEFAULT_REPO_DIR, help="本地归档仓库路径")
    ap.add_argument("--limit-days", type=int, default=None, help="只收最近 N 天(默认全量)")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = ap.parse_args()

    if not _ensure_repo(args.repo):
        print("无法获取 V2EX 归档, 中止。")
        return 1

    print(f"[解析] 遍历 {args.repo}/raw ...")
    stats = collect(args.repo, limit_days=args.limit_days, dry_run=args.dry_run)

    mode = "DRY-RUN" if stats["dry_run"] else "写入"
    print(
        f"[{mode}] 候选 {stats['total']} | 新增 {stats['inserted']} | "
        f"补空正文 {stats['filled']} | 跳过(已有) {stats['skipped']} | 忽略(无url/title) {stats['ignored']}"
    )
    if stats["dry_run"]:
        print("(dry-run 模式未写库)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

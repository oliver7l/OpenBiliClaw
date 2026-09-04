#!/usr/bin/env python3
"""批量导入 mindback_data 备份数据到 OpenBiliClaw 数据库。

支持的数据源:
  --bilibili   bilibili-recommend/  (B站推荐历史, ~2355条)
  --xhs        xhs_data/             (小红书推荐流, ~43000条)
  --v2ex-hot   v2ex-hot-hub/data/   (V2EX每日热门, ~5555条, 含正文→同时进articles)
  --zhihu      zhihu/recommend-cache/ (知乎推荐缓存, ~1329文件)
  --all        导入以上全部

用法:
  python scripts/import_mindback_data.py --bilibili --dry-run
  python scripts/import_mindback_data.py --all --limit 100
  python scripts/import_mindback_data.py --v2ex-hot  # 实际导入
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ── 路径配置 ──────────────────────────────────────────────
MINDBACK_BASE = "/Volumes/未命名/未命名文件夹/2026年04月27日-备份项目/2026年05月09日-SQLiteDB/mindback_data"
DB_PATH = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/openbiliclaw.db"

BATCH_SIZE = 500  # 批量插入大小


# ══════════════════════════════════════════════════════════
#  bilibili-recommend
# ══════════════════════════════════════════════════════════

def import_bilibili(dry_run: bool = False, limit: int = 0) -> dict[str, Any]:
    """导入 bilibili-recommend/ 下的推荐历史 JSON。"""
    pattern = os.path.join(MINDBACK_BASE, "bilibili-recommend", "*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        return {"ok": False, "reason": "no_files", "source": "bilibili"}

    logger.info("bilibili: 发现 %d 个 JSON 文件", len(files))

    seen_bvids: set[str] = set()
    rows: list[dict[str, Any]] = []

    for fpath in files:
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.warning("bilibili: 跳过损坏文件 %s: %s", os.path.basename(fpath), exc)
            continue

        items = data.get("data", {}).get("item", [])
        if not isinstance(items, list):
            continue

        for item in items:
            bvid = item.get("bvid")
            if not bvid or bvid in seen_bvids:
                continue
            seen_bvids.add(bvid)

            owner = item.get("owner", {}) or {}
            stat = item.get("stat", {}) or {}
            rcmd = item.get("rcmd_reason", "")
            if isinstance(rcmd, dict):
                rcmd = rcmd.get("content", "") or ""

            rows.append({
                "bvid": bvid,
                "title": (item.get("title") or "").strip(),
                "up_name": owner.get("name", "") or "",
                "up_mid": owner.get("mid", 0) or 0,
                "duration": item.get("duration", 0) or 0,
                "cover_url": item.get("pic", "") or "",
                "content_url": item.get("uri", "") or f"https://www.bilibili.com/video/{bvid}",
                "view_count": stat.get("view", 0) or 0,
                "like_count": stat.get("like", 0) or 0,
                "favorite_count": stat.get("favorite", 0) or 0,
                "collect_count": stat.get("favorite", 0) or 0,
                "comment_count": stat.get("reply", 0) or 0,
                "share_count": stat.get("share", 0) or 0,
                "danmaku_count": stat.get("danmaku", 0) or 0,
                "relevance_reason": str(rcmd)[:200] if rcmd else "",
                "source": "bili-recommend-history",
                "source_platform": "bilibili",
                "content_type": "video",
                "author_name": owner.get("name", "") or "",
            })

            if limit and len(rows) >= limit:
                break
        if limit and len(rows) >= limit:
            break

    logger.info("bilibili: 解析到 %d 条唯一视频", len(rows))
    if dry_run:
        return {"ok": True, "dry_run": True, "source": "bilibili", "count": len(rows),
                "sample": rows[:3]}

    inserted = _batch_insert_content_cache(rows)
    return {"ok": True, "source": "bilibili", "parsed": len(rows), "inserted": inserted}


# ══════════════════════════════════════════════════════════
#  xhs_data
# ══════════════════════════════════════════════════════════

def import_xhs(dry_run: bool = False, limit: int = 0) -> dict[str, Any]:
    """导入 xhs_data/ 下的小红书推荐流 JSON。"""
    pattern = os.path.join(MINDBACK_BASE, "xhs_data", "*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        return {"ok": False, "reason": "no_files", "source": "xhs"}

    logger.info("xhs: 发现 %d 个 JSON 文件", len(files))

    seen_ids: set[str] = set()
    rows: list[dict[str, Any]] = []

    for fpath in files:
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.warning("xhs: 跳过损坏文件 %s: %s", os.path.basename(fpath), exc)
            continue

        if not data.get("success"):
            continue
        items = data.get("data", {}).get("items", [])
        if not isinstance(items, list):
            continue

        for item in items:
            note_id = item.get("id")
            if not note_id or note_id in seen_ids:
                continue
            seen_ids.add(note_id)

            pk = f"xhs_{note_id}"
            desc = (item.get("desc") or "").strip()
            title = (item.get("title") or "").strip() or desc[:80] or "(无标题)"
            note_type = item.get("type", "normal") or "normal"
            images = item.get("images") or []

            rows.append({
                "bvid": pk,
                "content_id": note_id,
                "title": title,
                "up_name": item.get("userName", "") or "",
                "author_name": item.get("userName", "") or "",
                "duration": item.get("videoDuration", 0) or 0,
                "cover_url": item.get("coverUrl", "") or "",
                "content_url": item.get("url", "") or f"https://www.xiaohongshu.com/explore/{note_id}",
                "view_count": 0,
                "like_count": item.get("likedCount", 0) or 0,
                "favorite_count": item.get("collectedCount", 0) or 0,
                "collect_count": item.get("collectedCount", 0) or 0,
                "comment_count": item.get("commentCount", 0) or 0,
                "share_count": item.get("shareCount", 0) or 0,
                "body_text": desc,
                "description": desc[:500] if desc else "",
                "tags": json.dumps([f"image_{i}" for i in range(len(images))], ensure_ascii=False) if images else "[]",
                "source": "xhs-recommend-history",
                "source_platform": "xiaohongshu",
                "content_type": "video" if note_type == "video" else "note",
                "topic_group": "",
            })

            if limit and len(rows) >= limit:
                break
        if limit and len(rows) >= limit:
            break

    logger.info("xhs: 解析到 %d 条唯一笔记", len(rows))
    if dry_run:
        return {"ok": True, "dry_run": True, "source": "xhs", "count": len(rows),
                "sample": rows[:3]}

    inserted = _batch_insert_content_cache(rows)
    return {"ok": True, "source": "xhs", "parsed": len(rows), "inserted": inserted}


# ══════════════════════════════════════════════════════════
#  v2ex-hot-hub
# ══════════════════════════════════════════════════════════

def import_v2ex_hot(dry_run: bool = False, limit: int = 0) -> dict[str, Any]:
    """导入 v2ex-hot-hub/data/ 下的每日热门 JSON（含正文→同时进 articles）。"""
    pattern = os.path.join(MINDBACK_BASE, "v2ex-hot-hub", "v2ex-hot-hub", "data", "*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        return {"ok": False, "reason": "no_files", "source": "v2ex-hot"}

    logger.info("v2ex-hot: 发现 %d 个 JSON 文件", len(files))

    seen_ids: set[str] = set()
    cache_rows: list[dict[str, Any]] = []
    article_rows: list[dict[str, Any]] = []

    for fpath in files:
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.warning("v2ex-hot: 跳过损坏文件 %s: %s", os.path.basename(fpath), exc)
            continue

        if not isinstance(data, list):
            continue

        for item in data:
            tid = item.get("id")
            if not tid or tid in seen_ids:
                continue
            seen_ids.add(tid)

            pk = f"v2ex_{tid}"
            title = (item.get("title") or "").strip()
            if not title:
                continue
            content = (item.get("content") or item.get("content_rendered") or "").strip()
            node = item.get("node", {}) or {}
            member = item.get("member", {}) or {}
            created_ts = item.get("created", 0) or 0
            created_str = ""
            if created_ts:
                try:
                    created_str = datetime.fromtimestamp(int(created_ts)).strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, OSError):
                    pass

            url = item.get("url", "") or f"https://www.v2ex.com/t/{tid}"

            cache_rows.append({
                "bvid": pk,
                "content_id": str(tid),
                "title": title,
                "up_name": member.get("username", "") or "",
                "author_name": member.get("username", "") or "",
                "content_url": url,
                "comment_count": item.get("replies", 0) or 0,
                "reply_count": item.get("replies", 0) or 0,
                "body_text": content[:3000] if content else "",
                "description": content[:200] if content else "",
                "topic_group": node.get("name", "") or "",
                "source": "v2ex-hot-hub",
                "source_platform": "v2ex",
                "content_type": "thread",
                "discovered_at": created_str or None,
            })

            if content:
                article_rows.append({
                    "source_type": "v2ex",
                    "source_name": "V2EX热门",
                    "title": title,
                    "url": url,
                    "author": member.get("username", "") or "",
                    "content_text": content[:10000],
                    "published_at": created_str,
                    "tags": json.dumps([node["name"]] if node.get("name") else [], ensure_ascii=False),
                })

            if limit and len(cache_rows) >= limit:
                break
        if limit and len(cache_rows) >= limit:
            break

    logger.info("v2ex-hot: 解析到 %d 条帖子, %d 条含正文", len(cache_rows), len(article_rows))
    if dry_run:
        return {"ok": True, "dry_run": True, "source": "v2ex-hot",
                "count": len(cache_rows), "with_body": len(article_rows),
                "sample": cache_rows[:3]}

    inserted = _batch_insert_content_cache(cache_rows)
    art_inserted = _batch_insert_articles(article_rows)
    return {"ok": True, "source": "v2ex-hot", "parsed": len(cache_rows),
            "inserted": inserted, "articles_inserted": art_inserted}


# ══════════════════════════════════════════════════════════
#  zhihu/recommend-cache
# ══════════════════════════════════════════════════════════

def import_zhihu(dry_run: bool = False, limit: int = 0) -> dict[str, Any]:
    """导入 zhihu/recommend-cache/ 下的知乎推荐缓存 JSON。"""
    pattern = os.path.join(MINDBACK_BASE, "zhihu", "recommend-cache", "*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        return {"ok": False, "reason": "no_files", "source": "zhihu"}

    logger.info("zhihu: 发现 %d 个 JSON 文件", len(files))

    seen_ids: set[str] = set()
    rows: list[dict[str, Any]] = []

    for fpath in files:
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.warning("zhihu: 跳过损坏文件 %s: %s", os.path.basename(fpath), exc)
            continue

        # 知乎推荐缓存结构: {recommendType, timestamp, data: {data: [...], paging, fresh_text}}
        inner = data.get("data", {})
        items = inner.get("data", []) if isinstance(inner, dict) else []
        if not isinstance(items, list):
            items = []

        for item in items:
            if not isinstance(item, dict):
                continue
            # 知乎内容可能是 answer / article / zvideo
            target = item.get("target") or item
            if not isinstance(target, dict):
                continue

            zid = target.get("id") or item.get("id")
            if not zid or zid in seen_ids:
                continue
            seen_ids.add(str(zid))

            # 提取标题
            title = (target.get("title") or target.get("question", {}).get("title", "")
                     or item.get("card_id", "") or "").strip()
            if not title:
                excerpt = (target.get("excerpt") or "").strip()
                title = excerpt[:80] if excerpt else "(无标题)"
            if len(title) > 200:
                title = title[:200]

            # 提取作者（author 可能是 dict 或 str）
            author_info = target.get("author") or {}
            if isinstance(author_info, dict):
                author_name = author_info.get("name", "") or ""
            elif isinstance(author_info, str):
                author_name = author_info
            else:
                author_name = ""

            # 提取正文/摘要
            excerpt = (target.get("excerpt") or target.get("excerpt_title", "") or "").strip()
            content = target.get("content") or excerpt
            if isinstance(content, str):
                content = content.strip()
            else:
                content = ""

            # 互动数据
            voteup = target.get("voteup_count", 0) or 0
            comment = target.get("comment_count", 0) or 0

            # URL
            obj_type = target.get("type", "answer") or "answer"
            if obj_type == "article":
                url = f"https://zhuanlan.zhihu.com/p/{zid}"
            elif obj_type == "zvideo":
                url = f"https://www.zhihu.com/zvideo/{zid}"
            else:
                qid = target.get("question", {}).get("id", "")
                url = f"https://www.zhihu.com/question/{qid}/answer/{zid}" if qid else f"https://www.zhihu.com/answer/{zid}"

            pk = f"zhihu_{zid}"
            rows.append({
                "bvid": pk,
                "content_id": str(zid),
                "title": title,
                "up_name": author_name,
                "author_name": author_name,
                "content_url": url,
                "like_count": voteup,
                "comment_count": comment,
                "body_text": (excerpt or content)[:3000],
                "description": excerpt[:200] if excerpt else "",
                "source": "zhihu-recommend-history",
                "source_platform": "zhihu",
                "content_type": "article" if obj_type == "article" else "thread",
                "topic_group": obj_type,
            })

            if limit and len(rows) >= limit:
                break
        if limit and len(rows) >= limit:
            break

    logger.info("zhihu: 解析到 %d 条唯一内容", len(rows))
    if dry_run:
        return {"ok": True, "dry_run": True, "source": "zhihu", "count": len(rows),
                "sample": rows[:3]}

    inserted = _batch_insert_content_cache(rows)
    return {"ok": True, "source": "zhihu", "parsed": len(rows), "inserted": inserted}


# ══════════════════════════════════════════════════════════
#  批量插入工具函数
# ══════════════════════════════════════════════════════════

_CONTENT_CACHE_COLUMNS = [
    "bvid", "title", "up_name", "up_mid", "duration", "tags",
    "description", "cover_url", "view_count", "like_count",
    "favorite_count", "collect_count", "comment_count", "share_count",
    "danmaku_count", "reply_count", "relevance_reason",
    "pool_status", "source", "body_text", "content_type",
    "content_id", "content_url", "source_platform", "author_name",
    "topic_group",
]


def _batch_insert_content_cache(rows: list[dict[str, Any]]) -> int:
    """批量插入 content_cache，返回实际插入行数。"""
    if not rows:
        return 0

    conn = sqlite3.connect(DB_PATH)
    inserted = 0
    try:
        placeholders = ", ".join(["?"] * len(_CONTENT_CACHE_COLUMNS))
        columns_str = ", ".join(_CONTENT_CACHE_COLUMNS)
        sql = f"INSERT OR IGNORE INTO content_cache ({columns_str}) VALUES ({placeholders})"

        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            values = []
            for row in batch:
                values.append(tuple(row.get(col) for col in _CONTENT_CACHE_COLUMNS))
            cur = conn.executemany(sql, values)
            inserted += cur.rowcount
            conn.commit()
            logger.info("  content_cache 批量插入 %d/%d (累计 %d)",
                        min(i + BATCH_SIZE, len(rows)), len(rows), inserted)
    finally:
        conn.close()
    return inserted


_ARTICLES_COLUMNS = [
    "source_type", "source_name", "title", "url", "author",
    "content_text", "published_at", "tags",
]


def _batch_insert_articles(rows: list[dict[str, Any]]) -> int:
    """批量插入 articles，返回实际插入行数。"""
    if not rows:
        return 0

    conn = sqlite3.connect(DB_PATH)
    inserted = 0
    try:
        placeholders = ", ".join(["?"] * len(_ARTICLES_COLUMNS))
        columns_str = ", ".join(_ARTICLES_COLUMNS)
        sql = f"INSERT OR IGNORE INTO articles ({columns_str}) VALUES ({placeholders})"

        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            values = []
            for row in batch:
                values.append(tuple(row.get(col) for col in _ARTICLES_COLUMNS))
            cur = conn.executemany(sql, values)
            inserted += cur.rowcount
            conn.commit()
    finally:
        conn.close()
    return inserted


# ══════════════════════════════════════════════════════════
#  主入口
# ══════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="批量导入 mindback_data 备份数据到 OpenBiliClaw")
    parser.add_argument("--bilibili", action="store_true", help="导入 B站推荐历史")
    parser.add_argument("--xhs", action="store_true", help="导入小红书推荐流")
    parser.add_argument("--v2ex-hot", action="store_true", help="导入 V2EX 每日热门（含正文）")
    parser.add_argument("--zhihu", action="store_true", help="导入知乎推荐缓存")
    parser.add_argument("--all", action="store_true", help="导入以上全部")
    parser.add_argument("--dry-run", action="store_true", help="只解析不写入数据库")
    parser.add_argument("--limit", type=int, default=0, help="每个数据源最大导入条数（0=全部）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    sources: list[tuple[str, Any]] = []
    if args.all or args.bilibili:
        sources.append(("bilibili", import_bilibili))
    if args.all or args.xhs:
        sources.append(("xhs", import_xhs))
    if args.all or args.v2ex_hot:
        sources.append(("v2ex-hot", import_v2ex_hot))
    if args.all or args.zhihu:
        sources.append(("zhihu", import_zhihu))

    if not sources:
        parser.print_help()
        sys.exit(1)

    logger.info("=== 开始导入 mindback_data (dry_run=%s, limit=%d) ===",
                args.dry_run, args.limit)
    t0 = time.time()

    total_parsed = 0
    total_inserted = 0
    results: list[dict[str, Any]] = []

    for name, func in sources:
        logger.info("── 导入数据源: %s ──", name)
        try:
            result = func(dry_run=args.dry_run, limit=args.limit)
            results.append(result)
            if result.get("ok"):
                parsed = result.get("count", result.get("parsed", 0))
                inserted = result.get("inserted", 0)
                total_parsed += parsed
                total_inserted += inserted
                if args.dry_run:
                    logger.info("%s: 解析 %d 条 (dry-run, 未写入)", name, parsed)
                    if result.get("sample"):
                        for s in result["sample"][:2]:
                            logger.info("  样例: [%s] %s", s.get("source", ""), s.get("title", "")[:60])
                else:
                    logger.info("%s: 解析 %d 条, 插入 %d 条", name, parsed, inserted)
            else:
                logger.warning("%s: 失败 - %s", name, result.get("reason", "unknown"))
        except Exception as exc:
            logger.error("%s: 异常 - %s", name, exc, exc_info=True)
            results.append({"ok": False, "source": name, "error": str(exc)})

    elapsed = time.time() - t0
    logger.info("=== 导入完成: 解析 %d 条, 插入 %d 条, 耗时 %.1f 秒 ===",
                total_parsed, total_inserted, elapsed)

    # 打印汇总
    print("\n" + "=" * 60)
    print("导入结果汇总")
    print("=" * 60)
    for r in results:
        name = r.get("source", "?")
        if r.get("ok"):
            if r.get("dry_run"):
                print(f"  {name:15s}  解析 {r.get('count', 0):>6d} 条  (dry-run)")
            else:
                ins = r.get("inserted", 0)
                parsed = r.get("parsed", 0)
                art = r.get("articles_inserted", 0)
                extra = f", articles +{art}" if art else ""
                print(f"  {name:15s}  解析 {parsed:>6d} 条, 插入 {ins:>6d} 条{extra}")
        else:
            print(f"  {name:15s}  失败: {r.get('reason', r.get('error', 'unknown'))}")
    print("=" * 60)


if __name__ == "__main__":
    main()

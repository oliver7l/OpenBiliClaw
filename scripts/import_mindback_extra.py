"""Import extra mindback_data sources into OpenBiliClaw.

Handles four data sources not covered by import_mindback_data.py:
  * xiaoyuzhou (小宇宙播客) — 629 episodes with HTML show notes → articles
  * youtube-feed (YouTube订阅流) — feed list + video details → content_cache + articles
  * xhs-hot (小红书热门榜) — career/travel categories → content_cache
  * chat-analysis (聊天记录AI分析) — DeepSeek structured analysis of 15 chats → articles

Usage:
  python3 scripts/import_mindback_extra.py --xiaoyuzhou --once
  python3 scripts/import_mindback_extra.py --youtube --dry-run
  python3 scripts/import_mindback_extra.py --xhs-hot --limit 100
  python3 scripts/import_mindback_extra.py --chat-analysis
  python3 scripts/import_mindback_extra.py --all
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime
from typing import Any
from html import unescape

logger = logging.getLogger(__name__)

DB_PATH = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/openbiliclaw.db"

MINDBACK_BASE = "/Volumes/未命名/未命名文件夹/2026年04月27日-备份项目/2026年05月09日-SQLiteDB/mindback_data"
SCHEDULER_DIR = os.path.join(MINDBACK_BASE, "001-scheduler-data")
DEEPSEEK_DIR = os.path.join(MINDBACK_BASE, "deepseek-analysis")

_DRY_RUN = False

# ---------------------------------------------------------------------------
# HTML to text helper
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def html_to_text(html: str) -> str:
    """Strip HTML tags and normalize whitespace."""
    if not html:
        return ""
    text = _TAG_RE.sub("", html)
    text = unescape(text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def insert_article(
    conn: sqlite3.Connection,
    source_type: str,
    source_name: str,
    title: str,
    url: str,
    content_text: str,
    author: str = "",
    summary: str = "",
    published_at: str = "",
    tags: str = "[]",
) -> bool:
    """Insert one article, skipping duplicates by url. Returns True if inserted."""
    if not title or not url:
        return False
    try:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO articles
               (source_type, source_name, title, url, author, summary, content_text, published_at, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (source_type, source_name, title[:500], url, author[:200],
             summary[:1000], content_text, published_at, tags),
        )
        return cursor.rowcount > 0
    except sqlite3.IntegrityError:
        return False


def insert_content_cache(
    conn: sqlite3.Connection,
    bvid: str,
    title: str,
    source: str,
    source_platform: str,
    content_type: str,
    content_url: str = "",
    author_name: str = "",
    body_text: str = "",
    like_count: int = 0,
    view_count: int = 0,
    duration: str = "",
    tags: str = "[]",
    cover_url: str = "",
) -> bool:
    """Insert one content_cache row, skipping duplicates by bvid."""
    if not bvid or not title:
        return False
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO content_cache
               (bvid, title, source, source_platform, content_type, content_url,
                author_name, body_text, like_count, view_count, duration, tags,
                cover_url, pool_status, discovered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'fresh', ?)""",
            (bvid, title[:500], source, source_platform, content_type, content_url,
             author_name[:200], body_text, like_count, view_count, duration, tags,
             cover_url, now),
        )
        return cursor.rowcount > 0
    except sqlite3.IntegrityError:
        return False
    except sqlite3.OperationalError:
        # Fallback for missing columns
        try:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO content_cache
                   (bvid, title, source, source_platform, content_type, content_url,
                    author_name, body_text, pool_status, discovered_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'fresh', ?)""",
                (bvid, title[:500], source, source_platform, content_type, content_url,
                 author_name[:200], body_text, now),
            )
            return cursor.rowcount > 0
        except sqlite3.IntegrityError:
            return False


# ---------------------------------------------------------------------------
# 1. 小宇宙播客 (xiaoyuzhou)
# ---------------------------------------------------------------------------


def import_xiaoyuzhou(dry_run: bool = False, limit: int = 0) -> dict[str, Any]:
    """Import xiaoyuzhou podcast episodes into articles."""
    json_path = os.path.join(SCHEDULER_DIR, "xiaoyuzhou-fetch", "data", "xiaoyuzhou-articles.json")
    if not os.path.exists(json_path):
        return {"ok": False, "reason": f"file not found: {json_path}", "inserted": 0}

    with open(json_path, encoding="utf-8") as f:
        episodes = json.load(f)

    if not isinstance(episodes, list):
        return {"ok": False, "reason": "not a list", "inserted": 0}

    total = len(episodes)
    if limit > 0:
        episodes = episodes[:limit]

    inserted = 0
    skipped = 0
    conn = get_conn() if not dry_run else None
    try:
        for ep in episodes:
            title = str(ep.get("title", "")).strip()
            url = str(ep.get("link", "")).strip()
            if not title or not url:
                skipped += 1
                continue

            desc_html = str(ep.get("description", ""))
            content_text = html_to_text(desc_html)
            author = str(ep.get("author", "")).strip()
            pub_date = str(ep.get("pubDate", "")).strip()
            source = str(ep.get("source", "")).strip()

            tags_json = json.dumps([source] if source else [], ensure_ascii=False)

            if dry_run:
                inserted += 1
                continue

            if insert_article(
                conn,
                source_type="xiaoyuzhou",
                source_name="小宇宙播客",
                title=title,
                url=url,
                content_text=content_text,
                author=author,
                summary=content_text[:300],
                published_at=pub_date,
                tags=tags_json,
            ):
                inserted += 1
            else:
                skipped += 1

        if not dry_run:
            conn.commit()
    finally:
        if conn:
            conn.close()

    return {"ok": True, "total": total, "inserted": inserted, "skipped": skipped}


# ---------------------------------------------------------------------------
# 2. YouTube订阅流 (youtube-feed)
# ---------------------------------------------------------------------------


def _parse_youtube_details(details_dir: str) -> dict[str, dict[str, Any]]:
    """Parse youtube detail files (list of {field,value}) into vid→dict."""
    details: dict[str, dict[str, Any]] = {}
    for fp in sorted(glob.glob(os.path.join(details_dir, "*.json"))):
        try:
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                continue
            item: dict[str, Any] = {}
            for kv in data:
                if isinstance(kv, dict) and "field" in kv:
                    item[kv["field"]] = kv.get("value", "")
            vid = item.get("video_id") or item.get("id")
            if vid and vid not in details:
                details[vid] = item
        except (json.JSONDecodeError, OSError):
            continue
    return details


def import_youtube(dry_run: bool = False, limit: int = 0) -> dict[str, Any]:
    """Import YouTube subscription feed into content_cache + articles."""
    feed_dir = os.path.join(SCHEDULER_DIR, "youtube-fetcher", "feed")
    details_dir = os.path.join(feed_dir, "details")

    if not os.path.isdir(feed_dir):
        return {"ok": False, "reason": f"dir not found: {feed_dir}", "inserted": 0}

    # Collect all videos from feed files (dedup by video_id)
    all_videos: dict[str, dict[str, Any]] = {}
    for fp in sorted(glob.glob(os.path.join(feed_dir, "*.json"))):
        try:
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                continue
            for item in data:
                vid = item.get("video_id") or item.get("id")
                if vid and vid not in all_videos:
                    all_videos[vid] = item
        except (json.JSONDecodeError, OSError):
            continue

    # Merge with details (details may have richer description)
    if os.path.isdir(details_dir):
        details = _parse_youtube_details(details_dir)
        for vid, detail in details.items():
            if vid in all_videos:
                # Prefer detail's description if richer
                if detail.get("description") and len(str(detail.get("description", ""))) > len(str(all_videos[vid].get("description", ""))):
                    all_videos[vid]["description"] = detail["description"]
            else:
                all_videos[vid] = detail

    videos = list(all_videos.values())
    total = len(videos)
    if limit > 0:
        videos = videos[:limit]

    inserted_cache = 0
    inserted_articles = 0
    skipped = 0
    conn = get_conn() if not dry_run else None
    try:
        for v in videos:
            vid = str(v.get("video_id") or v.get("id") or "").strip()
            title = str(v.get("title", "")).strip()
            if not vid or not title:
                skipped += 1
                continue

            url = str(v.get("url", "") or f"https://www.youtube.com/watch?v={vid}").strip()
            channel = str(v.get("channel", "")).strip()
            description = str(v.get("description", "")).strip()
            views = v.get("views", 0)
            likes = v.get("likes", 0)
            duration = str(v.get("duration", "")).strip()
            published = str(v.get("published", "")).strip()
            category = str(v.get("category", "")).strip()
            thumbnail = str(v.get("thumbnail", "")).strip()

            tags_json = json.dumps([category] if category else [], ensure_ascii=False)

            if dry_run:
                inserted_cache += 1
                continue

            # content_cache
            if insert_content_cache(
                conn,
                bvid=vid,
                title=title,
                source="youtube-feed",
                source_platform="youtube",
                content_type="video",
                content_url=url,
                author_name=channel,
                body_text=description[:5000],
                like_count=int(likes) if str(likes).isdigit() else 0,
                view_count=int(views) if str(views).isdigit() else 0,
                duration=duration,
                tags=tags_json,
                cover_url=thumbnail,
            ):
                inserted_cache += 1

            # articles (only if description has meaningful content)
            if description and len(description) > 50:
                if insert_article(
                    conn,
                    source_type="youtube",
                    source_name="YouTube订阅流",
                    title=title,
                    url=url,
                    content_text=description,
                    author=channel,
                    summary=description[:300],
                    published_at=published,
                    tags=tags_json,
                ):
                    inserted_articles += 1

        if not dry_run:
            conn.commit()
    finally:
        if conn:
            conn.close()

    return {
        "ok": True,
        "total": total,
        "inserted_cache": inserted_cache,
        "inserted_articles": inserted_articles,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# 3. 小红书热门榜 (xhs-hot)
# ---------------------------------------------------------------------------


def import_xhs_hot(dry_run: bool = False, limit: int = 0) -> dict[str, Any]:
    """Import xiaohongshu hot list (career/travel/etc) into content_cache."""
    xhs_dir = os.path.join(SCHEDULER_DIR, "xhs-hot")
    if not os.path.isdir(xhs_dir):
        return {"ok": False, "reason": f"dir not found: {xhs_dir}", "inserted": 0}

    # Collect all notes from all files (dedup by id)
    all_notes: dict[str, dict[str, Any]] = {}
    for fp in sorted(glob.glob(os.path.join(xhs_dir, "*.json"))):
        try:
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
            items = data.get("data", {}).get("items", [])
            if not isinstance(items, list):
                continue
            # Extract category from filename: xhs-hot-career-...
            cat_match = re.search(r"xhs-hot-(\w+)-", os.path.basename(fp))
            category = cat_match.group(1) if cat_match else "hot"
            for item in items:
                nid = item.get("id")
                if nid and nid not in all_notes:
                    item["_category"] = category
                    all_notes[nid] = item
        except (json.JSONDecodeError, OSError):
            continue

    notes = list(all_notes.values())
    total = len(notes)
    if limit > 0:
        notes = notes[:limit]

    inserted = 0
    skipped = 0
    conn = get_conn() if not dry_run else None
    try:
        for note in notes:
            nid = str(note.get("id", "")).strip()
            nc = note.get("note_card", {})
            if not isinstance(nc, dict):
                nc = {}
            title = str(nc.get("display_title", "")).strip()
            if not nid or not title:
                skipped += 1
                continue

            author = str(nc.get("user", {}).get("nickname", "")).strip() if isinstance(nc.get("user"), dict) else ""
            likes_str = str(nc.get("interact_info", {}).get("liked_count", "0")) if isinstance(nc.get("interact_info"), dict) else "0"
            likes = int(likes_str) if likes_str.isdigit() else 0
            cover = ""
            cover_info = nc.get("cover", {})
            if isinstance(cover_info, dict):
                cover = str(cover_info.get("url", "")).strip()
                if not cover and isinstance(cover_info.get("info_list"), list):
                    for info in cover_info["info_list"]:
                        if isinstance(info, dict) and info.get("url"):
                            cover = info["url"]
                            break

            category = note.get("_category", "hot")
            url = f"https://www.xiaohongshu.com/explore/{nid}"
            tags_json = json.dumps([category], ensure_ascii=False)

            if dry_run:
                inserted += 1
                continue

            if insert_content_cache(
                conn,
                bvid=nid,
                title=title,
                source=f"xhs-hot-{category}",
                source_platform="xiaohongshu",
                content_type="note",
                content_url=url,
                author_name=author,
                like_count=likes,
                tags=tags_json,
                cover_url=cover,
            ):
                inserted += 1
            else:
                skipped += 1

        if not dry_run:
            conn.commit()
    finally:
        if conn:
            conn.close()

    return {"ok": True, "total": total, "inserted": inserted, "skipped": skipped}


# ---------------------------------------------------------------------------
# 4. 聊天记录AI分析 (chat-analysis / deepseek-analysis)
# ---------------------------------------------------------------------------


def import_chat_analysis(dry_run: bool = False, limit: int = 0) -> dict[str, Any]:
    """Import DeepSeek chat analysis into articles.

    Each directory under deepseek-analysis/ is one chat target (person or group).
    Each file analysis_{start}_{end}.txt is one structured analysis segment.
    """
    if not os.path.isdir(DEEPSEEK_DIR):
        return {"ok": False, "reason": f"dir not found: {DEEPSEEK_DIR}", "inserted": 0}

    all_files: list[tuple[str, str, str]] = []  # (target_name, filepath, segment_label)
    for target_dir in sorted(os.listdir(DEEPSEEK_DIR)):
        full_dir = os.path.join(DEEPSEEK_DIR, target_dir)
        if not os.path.isdir(full_dir):
            continue
        # Clean target name: remove "_分析结果" suffix
        target_name = target_dir.replace("_分析结果", "").strip()
        for fp in sorted(glob.glob(os.path.join(full_dir, "*.txt"))):
            basename = os.path.basename(fp)
            # Extract segment from filename: analysis_0_3000.txt → "第0-3000行"
            seg_match = re.search(r"analysis_(\d+)_(\d+)", basename)
            if seg_match:
                segment = f"第{seg_match.group(1)}-{seg_match.group(2)}行"
            else:
                segment = basename.replace(".txt", "")
            all_files.append((target_name, fp, segment))

    total = len(all_files)
    if limit > 0:
        all_files = all_files[:limit]

    inserted = 0
    skipped = 0
    targets_seen: set[str] = set()
    conn = get_conn() if not dry_run else None
    try:
        for target_name, fp, segment in all_files:
            targets_seen.add(target_name)
            try:
                with open(fp, encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                skipped += 1
                continue

            if not content.strip():
                skipped += 1
                continue

            title = f"【{target_name}】{segment}聊天记录分析"
            url = f"chat-analysis://{target_name}/{segment}"
            # Extract first meaningful line as summary
            summary = ""
            for line in content.split("\n"):
                line = line.strip()
                if line and not line.startswith("=") and not line.startswith("分析文件") and not line.startswith("分析范围") and not line.startswith("分析重点"):
                    summary = line[:200]
                    break

            tags_json = json.dumps([target_name, "聊天记录分析"], ensure_ascii=False)

            if dry_run:
                inserted += 1
                continue

            if insert_article(
                conn,
                source_type="chat-analysis",
                source_name="聊天记录AI分析",
                title=title,
                url=url,
                content_text=content,
                author=target_name,
                summary=summary,
                tags=tags_json,
            ):
                inserted += 1
            else:
                skipped += 1

        if not dry_run:
            conn.commit()
    finally:
        if conn:
            conn.close()

    return {
        "ok": True,
        "total": total,
        "inserted": inserted,
        "skipped": skipped,
        "targets": len(targets_seen),
        "target_names": sorted(targets_seen),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Import extra mindback_data sources into OpenBiliClaw")
    parser.add_argument("--xiaoyuzhou", action="store_true", help="导入小宇宙播客（629条→articles）")
    parser.add_argument("--youtube", action="store_true", help="导入YouTube订阅流（→content_cache+articles）")
    parser.add_argument("--xhs-hot", action="store_true", help="导入小红书热门榜（→content_cache）")
    parser.add_argument("--chat-analysis", action="store_true", help="导入聊天记录AI分析（→articles）")
    parser.add_argument("--all", action="store_true", help="导入以上全部")
    parser.add_argument("--dry-run", action="store_true", help="只解析不写入数据库")
    parser.add_argument("--limit", type=int, default=0, help="每个数据源最大导入条数（0=全部）")
    args = parser.parse_args()

    global _DRY_RUN
    _DRY_RUN = args.dry_run

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    sources = []
    if args.all or args.xiaoyuzhou:
        sources.append(("小宇宙播客", import_xiaoyuzhou))
    if args.all or args.youtube:
        sources.append(("YouTube订阅流", import_youtube))
    if args.all or args.xhs_hot:
        sources.append(("小红书热门榜", import_xhs_hot))
    if args.all or args.chat_analysis:
        sources.append(("聊天记录AI分析", import_chat_analysis))

    if not sources:
        parser.print_help()
        return

    print(f"{'='*60}")
    print(f"导入模式: {'DRY-RUN(不写入)' if args.dry_run else '正式写入'}")
    print(f"数据源: {', '.join(s[0] for s in sources)}")
    print(f"{'='*60}\n")

    for name, func in sources:
        print(f"\n--- {name} ---")
        t0 = time.time()
        result = func(dry_run=args.dry_run, limit=args.limit)
        elapsed = time.time() - t0
        if result.get("ok"):
            print(f"  完成: {json.dumps(result, ensure_ascii=False)} ({elapsed:.1f}s)")
        else:
            print(f"  失败: {result.get('reason', 'unknown')}")

    print(f"\n{'='*60}")
    print("全部完成")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()

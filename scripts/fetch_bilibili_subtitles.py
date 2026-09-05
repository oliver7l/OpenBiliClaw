#!/usr/bin/env python3
"""批量抓取专题中 B 站视频的字幕，更新到 articles 表。

只抓字幕，不下载视频文件。无字幕的视频自动跳过。
已有 content_text 的文章默认跳过（避免覆盖）。

【频率控制】默认每次只抓 1 篇（--limit 1），非常低频。
建议配合定时任务每天运行一次，实现"一天一篇"的抓取节奏。

用法:
    python scripts/fetch_bilibili_subtitles.py                     # 默认只抓1篇
    python scripts/fetch_bilibili_subtitles.py --slug advertising  # 只抓指定专题
    python scripts/fetch_bilibili_subtitles.py --limit 10          # 一次抓10篇
    python scripts/fetch_bilibili_subtitles.py --limit 0           # 抓全部（不推荐）
    python scripts/fetch_bilibili_subtitles.py --overwrite         # 覆盖已有 content_text
    python scripts/fetch_bilibili_subtitles.py --dry-run           # 只统计不实际抓取
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))
sys.path.insert(0, str(_PROJECT_ROOT))

from openbiliclaw.bilibili.subtitle import BilibiliSubtitleFetcher  # noqa: E402
from openbiliclaw.storage.database import Database  # noqa: E402


def load_bilibili_videos(conn, topic_slug: str | None = None) -> list[dict]:
    """从专题中加载所有 B 站视频。

    返回 [{bvid, title, url, article_id, has_content}, ...]
    """
    if topic_slug:
        rows = conn.execute(
            """SELECT ti.content_key, ti.title, ti.url, a.id as article_id,
                      LENGTH(COALESCE(a.content_text, '')) as content_len
               FROM topic_items ti
               JOIN topics t ON t.id = ti.topic_id
               LEFT JOIN articles a ON a.url = ti.url
               WHERE t.slug = ? AND ti.source_platform LIKE '%bili%'
               ORDER BY ti.collected_at DESC""",
            (topic_slug,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT ti.content_key, ti.title, ti.url, a.id as article_id,
                      LENGTH(COALESCE(a.content_text, '')) as content_len
               FROM topic_items ti
               JOIN topics t ON t.id = ti.topic_id
               LEFT JOIN articles a ON a.url = ti.url
               WHERE ti.source_platform LIKE '%bili%'
               ORDER BY ti.collected_at DESC""",
        ).fetchall()

    videos = []
    seen_bvids = set()
    for row in rows:
        bvid = row[0] or ""
        # 从 URL 中提取 BV 号
        if not bvid.startswith("BV"):
            match = re.search(r"BV[a-zA-Z0-9]+", row[2] or "")
            if match:
                bvid = match.group(0)
        if not bvid or bvid in seen_bvids:
            continue
        seen_bvids.add(bvid)
        videos.append(
            {
                "bvid": bvid,
                "title": row[1] or "",
                "url": row[2] or "",
                "article_id": row[3],
                "has_content": (row[4] or 0) > 50,  # 超过50字符算有内容
            }
        )
    return videos


def read_cookie() -> str:
    """从配置文件读取 B 站 cookie。"""
    config_path = _PROJECT_ROOT / "config.toml"
    if not config_path.exists():
        return ""
    content = config_path.read_text(encoding="utf-8")
    match = re.search(r'cookie\s*=\s*"([^"]+)"', content)
    return match.group(1) if match else ""


async def fetch_and_update(
    videos: list[dict],
    conn,
    *,
    cookie: str,
    limit: int = 0,
    overwrite: bool = False,
    dry_run: bool = False,
    delay: float = 1.0,
) -> dict:
    """批量抓取字幕并更新到 articles 表。

    返回统计信息。
    """
    stats = {
        "total": len(videos),
        "skipped_has_content": 0,
        "skipped_no_subtitle": 0,
        "fetched": 0,
        "updated": 0,
        "failed": 0,
        "total_chars": 0,
    }

    # 过滤掉已有内容的（除非 overwrite）
    to_fetch = []
    for v in videos:
        if v["has_content"] and not overwrite:
            stats["skipped_has_content"] += 1
            continue
        to_fetch.append(v)

    if limit > 0:
        to_fetch = to_fetch[:limit]

    print(f"待抓取: {len(to_fetch)} 个视频 "
          f"(跳过已有内容: {stats['skipped_has_content']})")

    if dry_run:
        print("【dry-run】不实际抓取")
        for v in to_fetch[:10]:
            print(f"  - {v['bvid']}: {v['title'][:50]}")
        return stats

    async with BilibiliSubtitleFetcher(cookie=cookie) as fetcher:
        for i, v in enumerate(to_fetch, 1):
            bvid = v["bvid"]
            title = v["title"][:50]
            try:
                text = await fetcher.fetch_subtitle_text(bvid)
                if not text:
                    stats["skipped_no_subtitle"] += 1
                    print(f"  [{i}/{len(to_fetch)}] {bvid} 无字幕，跳过: {title}")
                    continue

                stats["fetched"] += 1
                stats["total_chars"] += len(text)

                # 更新到 articles 表
                if v["article_id"]:
                    conn.execute(
                        "UPDATE articles SET content_text = ?, updated_at = datetime('now') WHERE id = ?",
                        (text, v["article_id"]),
                    )
                    conn.commit()
                    stats["updated"] += 1
                    print(f"  [{i}/{len(to_fetch)}] {bvid} ✅ {len(text)}字: {title}")
                else:
                    # articles 表中没有这条，跳过更新（只统计）
                    print(f"  [{i}/{len(to_fetch)}] {bvid} ⚠️ articles表中无记录，仅抓取: {title}")

            except Exception as e:
                stats["failed"] += 1
                print(f"  [{i}/{len(to_fetch)}] {bvid} ❌ 失败: {e}: {title}")

            # 限速，避免被风控
            if i < len(to_fetch):
                await asyncio.sleep(delay)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="批量抓取 B 站视频字幕")
    parser.add_argument("--slug", help="只抓取指定专题")
    parser.add_argument("--limit", type=int, default=1, help="最多抓取多少个视频（默认1，非常低频）")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已有 content_text")
    parser.add_argument("--dry-run", action="store_true", help="只统计不实际抓取")
    parser.add_argument("--delay", type=float, default=2.0, help="请求间隔（秒），默认2秒避免风控")
    args = parser.parse_args()

    db = Database(db_path=str(_PROJECT_ROOT / "data" / "openbiliclaw.db"))
    db.initialize()
    conn = db.conn

    cookie = read_cookie()
    if not cookie:
        print("⚠️ 配置中未找到 B 站 cookie，将以未登录状态抓取（可能获取不到 AI 字幕）")

    # 加载视频列表
    videos = load_bilibili_videos(conn, args.slug)
    print(f"找到 {len(videos)} 个 B 站视频" + (f"（专题: {args.slug}）" if args.slug else ""))

    # 抓取并更新
    stats = asyncio.run(
        fetch_and_update(
            videos,
            conn,
            cookie=cookie,
            limit=args.limit,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
            delay=args.delay,
        )
    )

    # 输出统计
    print("\n" + "=" * 60)
    print("📊 抓取统计:")
    print(f"  总视频数: {stats['total']}")
    print(f"  跳过（已有内容）: {stats['skipped_has_content']}")
    print(f"  跳过（无字幕）: {stats['skipped_no_subtitle']}")
    print(f"  成功抓取: {stats['fetched']}")
    print(f"  更新到 articles: {stats['updated']}")
    print(f"  失败: {stats['failed']}")
    print(f"  总字幕字数: {stats['total_chars']:,}")


if __name__ == "__main__":
    main()

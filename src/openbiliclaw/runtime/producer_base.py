"""Discovery producer 公共骨架 — K10 去重（refactor-plan 阶段 5）。

各平台 producer 的逐字重复部分收敛到这里，平台差异通过参数注入：
- ``insert_rows_into_cache``：favorites 簇 6 个 producer 逐字相同的
  content_cache 入库函数（OR IGNORE 去重 + IntegrityError 跳过）
- ``run_once_for_platform``：keyword 簇 3 个 producer 的单轮拉取循环
  （fetch → parse → connect_inbox → insert → commit）

后续逐步收敛：``run_forever`` / ``_main`` 各平台仍保留差异（环境变量、
CLI 参数、轮询间隔），待稳定后再评估进一步抽取。
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from openbiliclaw.runtime._db import connect_inbox


def insert_rows_into_cache(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """Insert new rows into content_cache, skipping duplicates by bvid."""
    inserted = 0
    for row in rows:
        try:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO content_cache (
                    bvid, title, up_name, author_name, content_url,
                    source_platform, source, content_type, pool_status,
                    body_text, like_count, comment_count, favorite_count,
                    share_count, discovered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["bvid"],
                    row["title"],
                    row["up_name"],
                    row["author_name"],
                    row["content_url"],
                    row["source_platform"],
                    row["source"],
                    row["content_type"],
                    row["pool_status"],
                    row["body_text"],
                    row["like_count"],
                    row["comment_count"],
                    row["favorite_count"],
                    row["share_count"],
                    row["discovered_at"],
                ),
            )
            if cursor.rowcount > 0:
                inserted += 1
        except sqlite3.IntegrityError:
            continue
    return inserted


def run_once_for_platform(
    platform: str,
    *,
    fetch_feed: Callable[[], list[Any]],
    parse_items: Callable[[list[Any]], list[dict[str, Any]]],
    insert_rows: Callable[[sqlite3.Connection, list[dict[str, Any]]], int] = insert_rows_into_cache,
) -> dict[str, Any]:
    """One full fetch cycle for a keyword producer. Returns a summary dict."""
    items = fetch_feed()
    if not items:
        return {"ok": False, "reason": "empty_feed", "items_fetched": 0, "inserted": 0}

    rows = parse_items(items)
    if not rows:
        return {"ok": False, "reason": "no_valid_items", "items_fetched": len(items), "inserted": 0}

    conn = connect_inbox(platform)
    try:
        inserted = insert_rows(conn, rows)
        conn.commit()
        return {
            "ok": True,
            "items_fetched": len(items),
            "valid_items": len(rows),
            "inserted": inserted,
            "skipped_duplicates": len(rows) - inserted,
        }
    finally:
        conn.close()

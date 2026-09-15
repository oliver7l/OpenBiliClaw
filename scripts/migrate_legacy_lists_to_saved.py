"""一次性迁移：legacy favorites / watch_later（content.db）→ saved_memberships 正本。

背景（2026-09-15 用户拍板「新表 saved_memberships 为正本」）：
- legacy 两表在 content.db，各自只有 bvid+note；新表在主库（saved_items 存内容
  元数据快照，saved_memberships 存名单关系）。
- 此前「remove 清 legacy、upsert 不写 legacy」的双写不对称已随本次切换消除。

幂等性：按 item_key（make_item_key('bilibili', bvid)）冲突即跳过，可重复执行。
元数据：从 pool.content_cache 取 title/up_name/cover_url/content_url/source_platform，
取不到时留空（source_platform 默认 bilibili）。

用法：
    .venv/bin/python scripts/migrate_legacy_lists_to_saved.py            # 预览
    .venv/bin/python scripts/migrate_legacy_lists_to_saved.py --apply    # 写库
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from openbiliclaw.saved_sync.identity import make_item_key  # noqa: E402

LEGACY_SOURCES = (
    # (legacy 表名, saved list_kind)
    ("favorites", "favorite"),
    ("watch_later", "watch_later"),
)


def _rows(conn: sqlite3.Connection, table: str) -> list[dict[str, object]]:
    rows = conn.execute(
        f"""
        SELECT w.bvid, w.note, w.added_at,
               COALESCE(c.title, '')           AS title,
               COALESCE(c.up_name, '')         AS up_name,
               COALESCE(c.cover_url, '')       AS cover_url,
               COALESCE(c.content_url, '')     AS content_url,
               COALESCE(c.source_platform, '') AS source_platform
          FROM {table} AS w
          LEFT JOIN pool.content_cache AS c ON c.bvid = w.bvid
        """
    ).fetchall()
    return [dict(zip(("bvid", "note", "added_at", "title", "up_name", "cover_url", "content_url", "source_platform"), r)) for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="真正写库（默认只预览）")
    parser.add_argument("--db", default=str(PROJECT_ROOT / "data" / "openbiliclaw.db"))
    args = parser.parse_args()

    content = sqlite3.connect(f"file:{PROJECT_ROOT / 'data' / 'content.db'}?mode=ro", uri=True)
    content.row_factory = sqlite3.Row
    content.execute(f"ATTACH DATABASE '{PROJECT_ROOT / 'data' / 'pool.db'}' AS pool")
    main_conn = sqlite3.connect(args.db, timeout=30)
    main_conn.row_factory = sqlite3.Row

    migrated = skipped = 0
    for table, list_kind in LEGACY_SOURCES:
        for row in _rows(content, table):
            bvid = str(row["bvid"]).strip()
            if not bvid:
                continue
            item_key = make_item_key("bilibili", bvid)
            exists = main_conn.execute(
                "SELECT 1 FROM saved_memberships WHERE list_kind = ? AND item_key = ?",
                (list_kind, item_key),
            ).fetchone()
            if exists:
                skipped += 1
                continue
            print(
                f"[{list_kind}] {bvid} → {item_key}"
                f"  title={str(row['title'])[:24]!r} note={str(row['note'])[:20]!r}"
            )
            if not args.apply:
                migrated += 1
                continue
            main_conn.execute(
                """
                INSERT INTO saved_items (
                    item_key, source_platform, content_id, content_url, content_type,
                    title, author_name, cover_url
                ) VALUES (?, ?, ?, ?, 'video', ?, ?, ?)
                ON CONFLICT(item_key) DO UPDATE SET
                    title = CASE WHEN excluded.title != '' THEN excluded.title ELSE saved_items.title END,
                    author_name = CASE WHEN excluded.author_name != '' THEN excluded.author_name ELSE saved_items.author_name END,
                    cover_url = CASE WHEN excluded.cover_url != '' THEN excluded.cover_url ELSE saved_items.cover_url END,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    item_key,
                    str(row["source_platform"]) or "bilibili",
                    bvid,
                    str(row["content_url"]),
                    str(row["title"]),
                    str(row["up_name"]),
                    str(row["cover_url"]),
                ),
            )
            main_conn.execute(
                """
                INSERT INTO saved_memberships (list_kind, item_key, note, added_at)
                VALUES (?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
                ON CONFLICT(list_kind, item_key) DO NOTHING
                """,
                (list_kind, item_key, str(row["note"]), row["added_at"]),
            )
            main_conn.execute(
                """
                INSERT INTO native_save_states (
                    list_kind, item_key, requested_action, resolved_action, resolved_target,
                    status, task_id, execution_id, last_error_code, last_error_message
                ) VALUES (?, ?, ?, '', '', 'pending', '', '', '', '')
                ON CONFLICT(list_kind, item_key) DO NOTHING
                """,
                (list_kind, item_key, list_kind),
            )
            migrated += 1

    if args.apply:
        main_conn.commit()
    main_conn.close()
    content.close()
    mode = "已写库" if args.apply else "预览（--apply 写库）"
    print(f"\n迁移 {migrated} 条，跳过（已存在）{skipped} 条 —— {mode}")


if __name__ == "__main__":
    main()

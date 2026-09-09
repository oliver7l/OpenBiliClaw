#!/usr/bin/env python3
"""Migrate knowledge-domain tables from the main db (and knowledge_audit.db
for entity_relations) into the dedicated knowledge.db sub-database.

P8 of the db sharding plan. The 11 knowledge tables physically move to
knowledge.db; after this, code accesses them with the ``knowledge.`` prefix
via an ATTACH connection. Idempotent: creates knowledge.db, copies structure
(indexes) + data, and verifies row counts. Run before restarting services.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

MAIN = DATA / "openbiliclaw.db"
AUDIT = DATA / "knowledge_audit.db"
KNOWLEDGE = DATA / "knowledge.db"

# 表名 -> 迁移数据来源（entity_relations 活跃数据在 knowledge_audit.db）
TABLES = {
    "content_insights_reports": "main",
    "entities": "main",
    "entity_relations": "audit",  # main 为 0 行，audit 4712 行为活跃数据
    "insight_reports": "main",
    "knowledge_backlinks": "main",
    "knowledge_cards": "main",
    "knowledge_concepts": "main",
    "knowledge_graph": "main",
    "learning_paths": "main",
    "topic_items": "main",
    "topics": "main",
}


def _table_ddl(con: sqlite3.Connection, table: str) -> str | None:
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row[0] if row else None


def _indexes_ddl(con: sqlite3.Connection, table: str) -> list[str]:
    rows = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
        "AND sql IS NOT NULL",
        (table,),
    ).fetchall()
    return [r[0] for r in rows]


def main() -> int:
    if not MAIN.exists():
        print("✗ 主库不存在:", MAIN)
        return 1

    print(f"目标子库: {KNOWLEDGE}")
    src = sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True)
    audit = sqlite3.connect(f"file:{AUDIT}?mode=ro", uri=True) if AUDIT.exists() else None
    dst = sqlite3.connect(str(KNOWLEDGE))
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA busy_timeout=5000")
    dst.execute("PRAGMA synchronous=NORMAL")

    try:
        for table, origin in TABLES.items():
            s = audit if origin == "audit" else src
            if not s:
                print(f"  ✗ {origin} 库不存在，跳过 {table}")
                continue
            ddl = _table_ddl(s, table)
            if not ddl:
                print(f"  ✗ 来源库无 {table}，跳过")
                continue
            existing = dst.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not existing:
                dst.execute(ddl)
            # 索引（幂等）
            for idx in _indexes_ddl(s, table):
                try:
                    dst.execute(idx)
                except sqlite3.OperationalError as e:
                    print(f"    （索引已存在或跳过）{idx[:48]}... {e}")
            cols = [r[1] for r in s.execute(f'PRAGMA table_info("{table}")')]
            col_sql = ", ".join(f'"{c}"' for c in cols)
            ph = ", ".join("?" for _ in cols)
            rows = s.execute(f'SELECT {col_sql} FROM "{table}"').fetchall()
            if rows:
                dst.executemany(
                    f'INSERT OR IGNORE INTO "{table}" ({col_sql}) VALUES ({ph})',
                    [tuple(r) for r in rows],
                )
            dst.commit()
            cnt = dst.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            print(f"  ✓ {table}: {len(rows)} -> {cnt}")
        dst.commit()

        # 一致性校验
        print("\n=== 一致性校验 ===")
        ok = True
        for table, origin in TABLES.items():
            s = audit if origin == "audit" else src
            if not s:
                continue
            try:
                a = s.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            except sqlite3.OperationalError:
                continue
            b = dst.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            status = "OK" if a == b else "MISMATCH"
            if a != b:
                ok = False
            print(f"  {table:28s} 源={a:<8d} 子库={b:<8d} {status}")
        print("\n✅ 迁移完成" if ok else "\n⚠️ 存在不一致，请检查！")
        return 0 if ok else 2
    finally:
        src.close()
        if audit:
            audit.close()
        dst.close()


if __name__ == "__main__":
    sys.exit(main())
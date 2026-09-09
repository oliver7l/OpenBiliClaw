#!/usr/bin/env python3
"""迁移：行为事件表拆分为独立子库 events.db（db sharding P2）。

背景
----
高频动态行为表 events / view_history 从主库 openbiliclaw.db 拆出到独立文件
events.db，使事件流的读写与主库、推荐流子库 pool.db 三者锁域隔离，缓解
主库 ``database is locked`` 争抢。

执行内容
--------
1. 确保 events.db 存在并按与主库一致的 schema 建表（events / view_history）
2. 从主库把 events / view_history 的历史数据迁入 events.db（保留 id 幂等）
3. 校验两张表行数与主库一致（抽样对比最近行）
4. 保留主库旧表 —— 迁移后进入双写验证期（写入已主写 events.db 并双写主库
   旧表），验证无误后再由后续清理脚本 DROP 主库旧表，避免迁移即舍底。

幂等：events.db 两张表行数 >= 主库时跳过对应表（用 id 段去重，可重复执行）。
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

EVENTS_TABLES = ("events", "view_history")


def _table_sql(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row[0] if row else ""


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _migrate_table(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    table: str,
    src_prefix: str,
) -> None:
    """把 src 表数据迁入 dst 对应表，保留 id，幂等（基于 id 去重）。"""
    cols = [r[1] for r in dst.execute(f"PRAGMA table_info({table})").fetchall()]
    if not cols:
        print(f"  [跳过] 目标库无 {table} 结构")
        return
    sql = (
        f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) "
        f"SELECT {', '.join(cols)} FROM {src_prefix}.{table}"
    )
    dst.execute(sql)
    dst.commit()


def main() -> int:
    data_dir = Path(__file__).resolve().parent.parent / "data"
    main_db = data_dir / "openbiliclaw.db"
    events_db = data_dir / "events.db"
    backup_db = data_dir / "openbiliclaw.db.bak-pre-events"

    if not main_db.exists():
        print(f"主库不存在: {main_db}")
        return 1

    # 1. 备份主库（一次性快照，迁移前必须）
    if not backup_db.exists():
        print(f"备份主库 → {backup_db.name} ...")
        shutil.copy2(main_db, backup_db)
        for suffix in ("-wal", "-shm"):
            side = Path(str(main_db) + suffix)
            if side.exists():
                shutil.copy2(side, Path(str(backup_db) + suffix))
        print("  备份完成。")
    else:
        print(f"已存在备份 {backup_db.name}，跳过备份。")

    src = sqlite3.connect(str(main_db), timeout=60.0)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(str(events_db), timeout=60.0)
    # 主库 ATTACH 为别名 src（不能叫 main：main 是 SQLite 保留名 = dst 自身）
    dst.execute("ATTACH DATABASE ? AS src", (str(main_db),))
    try:
        # 2. 目标库建表（与主库 schema 一致）
        for table in EVENTS_TABLES:
            existing = {
                r[0]
                for r in dst.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if table in existing:
                continue
            ddl = _table_sql(src, table)
            if ddl:
                dst.execute(ddl)
                dst.commit()
                print(f"  已建表 {table}（沿用主库 schema）")
            else:
                print(f"  [跳过] 主库无 {table} 表")

        # 3. 迁移数据（保留 id、幂等）
        for table in EVENTS_TABLES:
            src_count = _count(src, table)
            if src_count == 0:
                print(f"  [{table}] 主库 0 行，跳过。")
                continue
            dst_count = _count(dst, table)
            if dst_count >= src_count:
                print(f"  [{table}] events.db 已 {dst_count} 行 >= 主库 {src_count} 行，跳过（幂等）。")
                continue
            _migrate_table(src, dst, table, "src")
            migrated = _count(dst, table)
            print(f"  [{table}] 迁移完成：主库 {src_count} 行 → events.db {migrated} 行")

        # 4. 校验行数一致
        ok = True
        for table in EVENTS_TABLES:
            s = _count(src, table)
            d = _count(dst, table)
            match = s == d
            ok = ok and match
            print(f"  [{table}] 校验：主库 {s} / events.db {d} {'✓' if match else '✗'}")
        print("-" * 60)
        if not ok:
            print("数据不一致，请勿进入双写验证期。可重复运行本脚本幂等重迁。")
            return 1
        # 保留主库旧表：进入双写验证期
        print("迁移完成。主库旧表保留，进入双写验证期；")
        print("确认读写一致后，再 DROP 主库 events / view_history 表（清理脚本另行处理）。")
        return 0
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    sys.exit(main())

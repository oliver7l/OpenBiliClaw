#!/usr/bin/env python3
"""迁移：推荐流表拆分为独立子库 pool.db（总库 + 子库）。

背景
----
推荐流（content_cache / recommendations / user_feedback / xhs_observed_urls）
从总库 openbiliclaw.db 拆出到独立文件 pool.db，使推荐流的读写与主库
（日记/阅读库/事件等）彻底隔离锁域，采集器与补货写库不再拖慢推荐流读、
主库其他模块写也不再影响推荐流。

执行内容
--------
1. 备份主库到 ``openbiliclaw.db.bak-pre-pool``（sqlite3 backup API，一致性快照）
2. 创建 ``pool.db``：复制 4 张表的结构 + 数据 + 索引
3. 校验 pool.db 各表行数与主库迁移前一致
4. 从主库 DROP 这 4 张表（后续无前缀 SQL 经 ATTACH 自动落到 pool schema）

幂等：pool.db 已存在且 4 表齐全时跳过（不重复迁移）。
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

POOL_TABLES = (
    "content_cache",
    "recommendations",
    "user_feedback",
    "xhs_observed_urls",
)


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    data_dir = root / "data"
    main_db = data_dir / "openbiliclaw.db"
    pool_db = data_dir / "pool.db"
    backup_db = data_dir / "openbiliclaw.db.bak-pre-pool"

    if not main_db.exists():
        print(f"主库不存在: {main_db}")
        return 1

    main_conn = sqlite3.connect(str(main_db), timeout=30.0)
    main_conn.row_factory = sqlite3.Row
    try:
        existing_pool_tables = {
            r["name"]
            for r in main_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing = [t for t in POOL_TABLES if t not in existing_pool_tables]
        if missing:
            print(f"主库缺少推荐流表（{missing}），跳过迁移。")
            return 1

        if pool_db.exists():
            pool_conn = sqlite3.connect(str(pool_db), timeout=30.0)
            try:
                pool_tables = {
                    r[0]
                    for r in pool_conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            finally:
                pool_conn.close()
            if all(t in pool_tables for t in POOL_TABLES):
                print("pool.db 已存在且 4 表齐全，跳过迁移（幂等）。")
                return 0

        # 1. 备份主库
        if not backup_db.exists():
            print(f"备份主库 → {backup_db.name} ...")
            shutil.copy2(main_db, backup_db)
            wal = Path(str(main_db) + "-wal")
            shm = Path(str(main_db) + "-shm")
            if wal.exists():
                shutil.copy2(wal, Path(str(backup_db) + "-wal"))
            if shm.exists():
                shutil.copy2(shm, Path(str(backup_db) + "-shm"))
        else:
            print("备份已存在，跳过备份。")

        # 2. 创建 pool.db 并复制 4 表（结构 + 数据 + 索引）
        pool_conn = sqlite3.connect(str(pool_db), timeout=30.0)
        try:
            # 把主库 ATTACH 为 srcmain，供跨库复制数据
            pool_conn.execute("ATTACH DATABASE ? AS srcmain", (str(main_db),))
            # 提取主库 DDL
            for table in POOL_TABLES:
                row = main_conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                ddl = row["sql"] if row else ""
                if not ddl:
                    print(f"警告：主库缺少 {table} 的 DDL，跳过。")
                    continue
                # 表名改写为 pool 库内同名（DDL 无前缀，直接建到 pool.db main schema）
                pool_conn.execute(ddl)
                cols = [
                    r["name"]
                    for r in main_conn.execute(f"PRAGMA table_info({table})")
                ]
                col_list = ", ".join(f'"{c}"' for c in cols)
                pool_conn.execute(
                    f'INSERT INTO {table} ({col_list}) '
                    f'SELECT {col_list} FROM srcmain."{table}"'
                )
                # 复制索引（非主键/唯一约束自动索引）
                for idx in main_conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
                    "AND sql IS NOT NULL",
                    (table,),
                ):
                    if idx["sql"]:
                        pool_conn.execute(idx["sql"])
            pool_conn.commit()

            # 3. 校验行数
            for table in POOL_TABLES:
                main_count = main_conn.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
                pool_count = pool_conn.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
                print(f"  {table}: 主库 {main_count} → 子库 {pool_count}")
                if main_count != pool_count:
                    print(f"校验失败：{table} 行数不一致，终止迁移（主库未动）。")
                    return 1
        finally:
            pool_conn.close()

        # 4. 主库 DROP 4 表（含其索引）
        print("从主库 DROP 推荐流表 ...")
        for table in POOL_TABLES:
            main_conn.execute(f'DROP TABLE IF EXISTS "{table}"')
        main_conn.commit()
        print("迁移完成。")
        return 0
    finally:
        main_conn.close()


if __name__ == "__main__":
    sys.exit(main())

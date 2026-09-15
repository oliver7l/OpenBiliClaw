"""P7: 把主库 15 张 health_* 表迁移到独立子库 data/health.db。

流程：备份主库 → 提取各表 DDL 到 health.db 建表 → INSERT SELECT 迁移数据
→ 校验行数与抽样一致 → 备份后 DROP 主库旧表。

health 模块自带与主库一致的 DDL（见 health/store.py _SCHEMA_SQL），
本脚本从主库 sqlite_master 提炼权威 DDL 建到 health.db，保证结构 1:1。
"""
from __future__ import annotations

import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
#: 2026-09-16 奥卡姆瘦身：删掉了 4 个零数据的实体表（allergies / vitals /
#: immunizations / insights）——需要时再加回来，表清单必须与
#: ``health/store.py`` 的 `_SCHEMA_SQL` 保持一致。
HEALTH_TABLES = [
    "health_patients", "health_encounters", "health_conditions", "health_medications",
    "health_lab_results", "health_lab_components", "health_procedures",
    "health_doctors", "health_documents",
    "health_appointments", "health_medication_logs",
]


def main_db() -> Path:
    return DATA / "openbiliclaw.db"


def health_db() -> Path:
    return DATA / "health.db"


def connect(path: Path, *, ro: bool = False):
    uri = f"file:{path}?mode=ro" if ro else str(path)
    con = sqlite3.connect(uri, timeout=60)
    con.row_factory = sqlite3.Row
    return con


def backup(db_path: Path, tag: str) -> Path:
    backups = DATA / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backups / f"{db_path.stem}_pre_{tag}_{stamp}.db"
    src = connect(db_path, ro=True)
    dst = sqlite3.connect(dest)
    src.backup(dst)
    dst.commit()
    src.close(); dst.close()
    print(f"[backup] → {dest}")
    return dest


def table_ddl(con: sqlite3.Connection, table: str) -> str | None:
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row[0] if row and row[0] else None


def migrate_table(main: sqlite3.Connection, sub: sqlite3.Connection, table: str) -> int:
    ddl = table_ddl(main, table)
    if not ddl:
        print(f"  ✗ 主库无 {table}，跳过")
        return 0
    sub.execute(ddl)
    cols = [r[1] for r in main.execute(f'PRAGMA table_info("{table}")')]
    col_sql = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join("?" for _ in cols)
    rows = main.execute(f'SELECT {col_sql} FROM "{table}"').fetchall()
    if rows:
        sub.executemany(
            f'INSERT INTO "{table}" ({col_sql}) VALUES ({placeholders})',
            [tuple(r) for r in rows],
        )
    sub.commit()
    return len(rows)


def copy_indexes(main: sqlite3.Connection, sub: sqlite3.Connection, table: str) -> None:
    for idx in main.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL"
        " AND name NOT LIKE 'sqlite_autoindex%'",
        (table,),
    ):
        try:
            sub.execute(idx["sql"])
        except sqlite3.OperationalError:
            pass
    sub.commit()


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "migrate"
    if not main_db().exists():
        print(f"[fatal] 主库不存在 {main_db()}")
        sys.exit(1)

    if action in ("migrate", "all"):
        backup(main_db(), "p7_health")
        main = connect(main_db())
        sub = connect(health_db())
        sub.execute("PRAGMA journal_mode=WAL")
        print("[migrate] 建表 + 迁移数据:")
        for t in HEALTH_TABLES:
            n = migrate_table(main, sub, t)
            copy_indexes(main, sub, t)
            print(f"  {t}: {n} 行")
        # 校验
        ok = True
        for t in HEALTH_TABLES:
            m = main.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            s = sub.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            if m != s:
                print(f"  ✗ {t}: main={m} sub={s}")
                ok = False
            else:
                print(f"  ✓ {t}: {s} 行一致")
        main.close(); sub.close()
        if not ok:
            print("\n[abort] 校验不一致")
            sys.exit(2)
        print("\n[migrate] 完成，待代码切到 health.db 后执行 drop 动作")

    if action in ("drop", "all"):
        backup(main_db(), "p7_health_drop")
        main = connect(main_db())
        for t in HEALTH_TABLES:
            ex = main.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)
            ).fetchone()
            if ex:
                main.execute(f'DROP TABLE "{t}"')
        main.commit()
        left = main.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        main.close()
        print(f"[drop] 已删除主库 health 旧表，主库剩余表 {left}")


if __name__ == "__main__":
    main()
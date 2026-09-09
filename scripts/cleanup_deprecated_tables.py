"""清理主库 _deprecated_* 废弃表。

这些表是 db sharding 拆分后的旧数据残留，无任何代码引用，
占用主库约 82% 空间。步骤：
  1. 先对主库做一次一致性备份
  2. 校验确无代码引用风险（仅按 '_deprecated_' 前缀匹配）
  3. DROP 所有废弃表
  4. 报告剩余表数量
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
PREFIX = "_deprecated_"


def main_db() -> Path:
    return DATA / "openbiliclaw.db"


def list_deprecated() -> list[str]:
    """返回废弃表名列表。FTS 虚拟表（shadow 表会级联删除）必须排前。"""
    con = sqlite3.connect(f"file:{main_db()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='table' AND substr(name,1,?) = ?",
            (len(PREFIX), PREFIX),
        ).fetchall()
    finally:
        con.close()
    virtual = [r[0] for r in rows if r[1] and r[1].lstrip().upper().startswith("CREATE VIRTUAL TABLE")]
    others = [r[0] for r in rows if r[0] not in virtual]
    return virtual + others


def backup() -> Path:
    backups = DATA / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backups / f"openbiliclaw_pre_deprecated_clean_{stamp}.db"
    src = sqlite3.connect(f"file:{main_db()}?mode=ro", uri=True)
    dst = sqlite3.connect(dest)
    src.backup(dst)
    dst.commit()
    src.close()
    dst.close()
    chk = sqlite3.connect(dest)
    n = chk.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    chk.close()
    print(f"[backup] → {dest} (表数量 {n})")
    return dest


def drop_all(names: list[str]) -> None:
    con = sqlite3.connect(main_db(), timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    try:
        cur = con.execute("BEGIN")
        for name in names:
            con.execute(f'DROP TABLE IF EXISTS "{name}"')
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    print(f"[drop] 已删除 {len(names)} 张废弃表")


def main() -> None:
    names = list_deprecated()
    if not names:
        print("[info] 主库无废弃表可清理")
        return
    print(f"[plan] 将删除 {len(names)} 张 _deprecated_* 表:")
    for n in sorted(names):
        print("  ", n)
    backup()
    drop_all(names)
    con = sqlite3.connect(main_db())
    left = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    dep_left = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND substr(name,1,?)=?",
        (len(PREFIX), PREFIX),
    ).fetchone()[0]
    con.close()
    print(f"[final] 主库剩余表 {left}（其中废弃表 {dep_left}）")


if __name__ == "__main__":
    main()
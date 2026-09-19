#!/usr/bin/env python3
"""按 moves 表逆向还原所有已搬迁文件到原始位置。

用法:
  python restore.py --dry-run   # 预览
  python restore.py             # 执行还原
"""
import os
import sys
import sqlite3

LIB = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/19_统一相册库"
DB_PATH = f"{LIB}/library.db"


def main():
    dry = "--dry-run" in sys.argv
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    rows = cur.execute("SELECT file_key, from_path, to_path FROM moves").fetchall()
    print(f"待还原 {len(rows)} 个文件" + ("（dry-run 预览）" if dry else ""))
    n_ok = n_missing = n_conflict = 0
    for fk, src, dst in rows:
        if not os.path.exists(dst):
            n_missing += 1
            continue
        if os.path.exists(src):
            n_conflict += 1
            continue
        if not dry:
            os.makedirs(os.path.dirname(src), exist_ok=True)
            os.rename(dst, src)
            cur.execute("DELETE FROM moves WHERE file_key=?", (fk,))
            cur.execute("UPDATE files SET path=? WHERE file_key=?", (src, fk))
        n_ok += 1
    con.commit()
    con.close()
    print(f"还原 {n_ok}, 目标缺失 {n_missing}, 原位已有文件(冲突) {n_conflict}"
          + ("（dry-run 未实际执行）" if dry else ""))


if __name__ == "__main__":
    main()

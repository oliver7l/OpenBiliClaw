#!/usr/bin/env python3
"""按 plan 表执行搬迁（Stage B）。

- 同卷 mv（rename），秒级、无数据拷贝
- 只搬 action='keep' 的文件；action='dup'（重复副本）默认不动，加 --move-dups 才搬
- 目标目录自动创建；目标已存在同名文件则跳过并记录
- 每次执行写入 moves 表（可被 restore.py 逆向还原）

用法:
  python execute_reorg.py --dry-run   # 只预览，不执行
  python execute_reorg.py             # 执行搬迁
  python execute_reorg.py --move-dups # 连重复副本一起搬（默认不搬）
"""
import os
import sys
import sqlite3
import datetime

LIB = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/19_统一相册库"
DB_PATH = f"{LIB}/library.db"


def main():
    move_dups = "--move-dups" in sys.argv
    dry = "--dry-run" in sys.argv
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS moves(
      file_key TEXT PRIMARY KEY, from_path TEXT, to_path TEXT,
      moved_at TEXT DEFAULT (datetime('now')))""")

    where = "action='keep'" if not move_dups else "action IN ('keep','dup')"
    rows = cur.execute(f"SELECT file_key, from_path, to_path FROM plan WHERE {where}").fetchall()
    print(f"待搬迁 {len(rows)} 个文件" + ("（dry-run 预览）" if dry else ""))

    n_ok = n_skip_gone = n_skip_exist = 0
    moves = []
    for fk, src, dst in rows:
        if not os.path.exists(src):
            n_skip_gone += 1
            continue
        if os.path.exists(dst):
            n_skip_exist += 1
            continue
        if not dry:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.rename(src, dst)
        moves.append((fk, src, dst))
        n_ok += 1
        if n_ok % 2000 == 0:
            print(f"  {n_ok}/{len(rows)}")
    if not dry:
        cur.executemany("INSERT OR REPLACE INTO moves VALUES(?,?,?,datetime('now'))", moves)
        # 同步 files.path / rel 指向新位置
        cur.executemany("UPDATE files SET path=? WHERE file_key=?",
                        [(dst, fk) for fk, src, dst in moves])
        cur.execute("UPDATE meta SET value=value WHERE key='version'")
    con.commit()
    con.close()
    print(f"完成: 搬迁 {n_ok}, 源缺失跳过 {n_skip_gone}, 目标已存在跳过 {n_skip_exist}"
          + ("（dry-run 未实际执行）" if dry else ""))
    if not dry and n_ok:
        print("还原: python tools/restore.py  可整体逆向回原始位置")


if __name__ == "__main__":
    main()

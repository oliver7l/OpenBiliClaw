#!/usr/bin/env python3
"""搬迁前硬链接快照（零数据拷贝，仅占 inode）。

原理：对每个待搬迁文件 os.link(原路径, 快照路径)。之后即便把原文件 rename 走，
快照目录里仍持有同一 inode 的链接，数据不会丢，可随时手工取回。

用法:
  python snapshot_hardlink.py            # 对 plan(action='keep') 的文件建快照
  python snapshot_hardlink.py --all      # 含重复副本
"""
import os
import sys
import sqlite3

LIB = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/19_统一相册库"
DB_PATH = f"{LIB}/library.db"
SNAP = f"{LIB}/_搬迁前快照"
ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"


def main():
    scope = "action IN ('keep','dup')" if "--all" in sys.argv else "action='keep'"
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        f"SELECT file_key, from_path FROM plan WHERE {scope}").fetchall()
    if not os.path.exists(SNAP):
        os.makedirs(SNAP)
    n_ok = n_skip = n_fail = 0
    links = []
    for fk, src in rows:
        if not os.path.exists(src):
            n_skip += 1
            continue
        dst = os.path.join(SNAP, os.path.relpath(src, ROOT))
        if os.path.exists(dst):
            n_skip += 1
            continue
        d = os.path.dirname(dst)
        if not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
        try:
            os.link(src, dst)
        except OSError as e:
            n_fail += 1
            if n_fail < 3:
                print("  link 失败:", src, e)
            continue
        links.append((fk, dst))
        n_ok += 1
        if n_ok % 5000 == 0:
            print(f"  {n_ok}/{len(rows)}")
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS snapshot(
      file_key TEXT PRIMARY KEY, snap_path TEXT,
      created_at TEXT DEFAULT (datetime('now')))""")
    cur.executemany("INSERT OR REPLACE INTO snapshot VALUES(?,?,datetime('now'))", links)
    con.commit()
    con.close()
    print(f"快照完成: 成功 {n_ok}, 跳过 {n_skip}, 失败 {n_fail} -> {SNAP}")


if __name__ == "__main__":
    main()

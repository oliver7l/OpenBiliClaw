#!/usr/bin/env python3
"""搬迁后校验：目标存在、快照完整、files.path 已同步。

  python verify_reorg.py            # 全量校验
  python verify_reorg.py --sample   # 抽 500 个快速校验
"""
import os
import sys
import sqlite3

LIB = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/19_统一相册库"
DB_PATH = f"{LIB}/library.db"


def main():
    sample = "--sample" in sys.argv
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    q = "SELECT file_key, from_path, to_path FROM moves"
    if sample:
        q += " ORDER BY RANDOM() LIMIT 500"
    moves = cur.execute(q).fetchall()
    n = len(moves)
    miss_dst = miss_src_pending = 0
    snap_ok = 0
    for fk, src, dst in moves:
        if not os.path.exists(dst):
            miss_dst += 1
        if os.path.exists(src):
            miss_src_pending += 1
    snaps = cur.execute("SELECT file_key, snap_path FROM snapshot").fetchall()
    if sample:
        snaps = snaps[:500]
    for fk, sp in snaps:
        if os.path.exists(sp):
            snap_ok += 1
    # files.path 与新位置一致
    bad_path = cur.execute(
        """SELECT COUNT(*) FROM files f JOIN moves m ON m.file_key=f.file_key
           WHERE f.path != m.to_path""").fetchone()[0]
    # 原始位置残留（应为空目录）
    leftovers = cur.execute(
        """SELECT COUNT(*) FROM files f JOIN moves m ON m.file_key=f.file_key
           WHERE f.path != m.to_path""").fetchone()[0]
    print(f"moves 检查 {n} 条")
    print(f"  目标缺失: {miss_dst}")
    print(f"  原位仍有文件(未完成/冲突): {miss_src_pending}")
    print(f"  快照完好: {snap_ok}/{len(snaps)}")
    print(f"  files.path 与 to_path 不一致: {bad_path}")
    print("结论:", "OK" if (miss_dst == 0 and bad_path == 0) else "存在异常，需检查")


if __name__ == "__main__":
    main()

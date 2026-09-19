#!/usr/bin/env python3
"""重复副本全文件哈希校验。

现有 content_key = md5(size + 头部64K)，速度优先但存在极低概率碰撞。
本脚本对「副本 + 其对应主副本」读全文件算 md5，写入 files.md5，
并统计每个内容组内是否真的一致 —— 只有全部一致才允许后续清理。

只读 + 写 md5 列，绝不删除任何文件。

用法:
  python tools/verify_dups.py            # 校验重复副本组（约 1 万文件）
  python tools/verify_dups.py --all      # 全库 25909 个都算（慢，约 128GB IO）
"""
import os
import sys
import time
import sqlite3
import hashlib
import argparse
from collections import defaultdict

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
DB = f"{ROOT}/19_统一相册库/library.db"


def md5file(p, chunk=1 << 20):
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--batch", type=int, default=200)
    args = ap.parse_args()

    db = sqlite3.connect(DB)
    cols = [r[1] for r in db.execute("PRAGMA table_info(files)")]
    if "md5" not in cols:
        db.execute("ALTER TABLE files ADD COLUMN md5 TEXT")
        db.execute("CREATE INDEX IF NOT EXISTS idx_files_md5 ON files(md5)")
        db.commit()

    if args.all:
        rows = db.execute("SELECT file_key, path FROM files").fetchall()
    else:
        # 只算「有副本的内容」涉及的全部文件
        cks = {r[0] for r in db.execute(
            "SELECT DISTINCT content_key FROM files WHERE path LIKE '%/_重复/%'")}
        rows = []
        for ck in cks:
            rows += db.execute(
                "SELECT file_key, path FROM files WHERE content_key=?", (ck,)).fetchall()
    todo = [(fk, p) for fk, p in rows if os.path.exists(p)]
    print(f"待校验 {len(todo)} 个文件", file=sys.stderr)

    t0 = time.time()
    n_err = 0
    for i, (fk, p) in enumerate(todo):
        try:
            db.execute("UPDATE files SET md5=? WHERE file_key=?", (md5file(p), fk))
        except Exception as e:
            n_err += 1
            print(f"  ⚠️ {p[-60:]}: {e}", file=sys.stderr)
        if (i + 1) % args.batch == 0:
            db.commit()
            el = time.time() - t0
            print(f"[{i+1}/{len(todo)}] {el:.0f}s 错={n_err} "
                  f"剩余约 {el/(i+1)*(len(todo)-i-1)/60:.1f}min", file=sys.stderr)
    db.commit()

    # 统计：每个 content_key 组内全 md5 是否唯一
    bad = defaultdict(set)
    for ck, m in db.execute(
            """SELECT content_key, md5 FROM files
               WHERE content_key IN (SELECT DISTINCT content_key FROM files
                                     WHERE path LIKE '%/_重复/%')"""):
        if m:
            bad[ck].add(m)
    multi = {k: v for k, v in bad.items() if len(v) > 1}
    print(f"\n有副本的内容组: {len(bad)}")
    print(f"组内全文件 md5 不一致（碰撞/伪重复）: {len(multi)}")
    for k in list(multi)[:5]:
        print("   ", k, "→", len(multi[k]), "个不同 md5")
        for r in db.execute("SELECT path FROM files WHERE content_key=? LIMIT 4", (k,)):
            print("       ", r[0][-95:])
    print(f"\n用时 {time.time()-t0:.0f}s，错 {n_err}")


if __name__ == "__main__":
    main()

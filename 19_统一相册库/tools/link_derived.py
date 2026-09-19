#!/usr/bin/env python3
"""找出「同一张照片的不同版本」（缩放副本/压缩版/裁剪版）。

content_key 只认字节级相同，认不出 QQ 压缩后的副本、resize 后的版本。
本脚本用已入库的 CLIP 向量做近邻：相似度极高的不同内容 = 同一张图的派生版本。

产出表 derived(ck_a, ck_b, sim, size_a, size_b, reason)：
  - reason=resized：尺寸不同，语义几乎一致（缩放/压缩副本）
  - reason=near_dup：尺寸也相同但内容哈希不同（极近似，如同一张图的不同压缩）

用法:
  python tools/link_derived.py                # 默认阈值 0.97
  python tools/link_derived.py --thresh 0.98 --report
"""
import os
import sqlite3
import argparse
import numpy as np

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
DB = f"{ROOT}/19_统一相册库/library.db"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thresh", type=float, default=0.97)
    ap.add_argument("--chunk", type=int, default=1000)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--include-screenshots", action="store_true",
                    help="不排除 07 截屏目录（会产生近万对假阳性）")
    args = ap.parse_args()

    db = sqlite3.connect(DB)
    rows = db.execute("SELECT content_key, vec FROM clips").fetchall()
    keys = [r[0] for r in rows]
    X = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
    X = X / np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)
    sizes = dict(db.execute(
        "SELECT content_key, MIN(size) FROM files WHERE is_primary=1 GROUP BY content_key"))
    # 07「截屏」目录里的白底文字截图，CLIP 特征天然趋同（近万对假阳性），默认排除
    excl = set()
    if not args.include_screenshots:
        excl = {r[0] for r in db.execute(
            "SELECT DISTINCT content_key FROM files WHERE lib='07' AND rel LIKE '截屏/%'")}
        print(f"排除 07 截屏类内容 {len(excl)} 个", flush=True)
    keep = [i for i, k in enumerate(keys) if k not in excl]
    keys = [keys[i] for i in keep]
    X = X[keep]
    n = len(keys)
    print(f"向量 {n} 条，阈值 {args.thresh}", flush=True)

    db.execute("""CREATE TABLE IF NOT EXISTS derived (
        ck_a TEXT, ck_b TEXT, sim REAL, size_a INTEGER, size_b INTEGER,
        reason TEXT, PRIMARY KEY (ck_a, ck_b))""")
    db.execute("DELETE FROM derived")

    pairs = []
    for i in range(0, n, args.chunk):
        S = X[i:i + args.chunk] @ X.T
        idx = np.argwhere(S >= args.thresh)
        for a, b in idx:
            ia, ib = i + int(a), int(b)
            if ia >= ib:
                continue
            ka, kb = keys[ia], keys[ib]
            sa, sb = sizes.get(ka, 0), sizes.get(kb, 0)
            if sa == sb:
                continue          # 同尺寸近似图不算派生
            pairs.append((ka, kb, float(S[a, b]), sa, sb,
                          "resized" if min(sa, sb) / max(sa, sb) < 0.95 else "near_dup"))
        print(f"  [{min(i+args.chunk, n)}/{n}] 累计 {len(pairs)} 对", flush=True)

    db.executemany("INSERT OR REPLACE INTO derived VALUES(?,?,?,?,?,?)", pairs)
    db.commit()
    print(f"\n派生关系 {len(pairs)} 对")
    if args.report:
        print("\n按来源组合:")
        for r in db.execute("""
            SELECT a.lib || ' → ' || b.lib, COUNT(*), ROUND(AVG(d.sim),3)
            FROM derived d
            JOIN files a ON a.content_key=d.ck_a AND a.is_primary=1
            JOIN files b ON b.content_key=d.ck_b AND b.is_primary=1
            GROUP BY 1 ORDER BY 2 DESC LIMIT 12"""):
            print(f"   {r[0]}: {r[1]} 对, 平均相似度 {r[2]}")
        print("\n样例（尺寸差异最大的 8 组）:")
        for r in db.execute("""
            SELECT d.ck_a, d.ck_b, d.sim, d.size_a, d.size_b, a.path, b.path
            FROM derived d
            JOIN files a ON a.content_key=d.ck_a AND a.is_primary=1
            JOIN files b ON b.content_key=d.ck_b AND b.is_primary=1
            ORDER BY CAST(d.size_b AS REAL)/d.size_a ASC LIMIT 8"""):
            print(f"   sim={r[2]:.3f}  {r[3]/1e6:.1f}MB vs {r[4]/1e6:.1f}MB")
            print(f"      A {r[5][-80:]}")
            print(f"      B {r[6][-80:]}")


if __name__ == "__main__":
    main()

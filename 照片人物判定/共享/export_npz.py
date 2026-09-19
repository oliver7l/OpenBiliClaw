#!/usr/bin/env python3
"""把 library.db 的 faces 表导出成审核/实验工具用的 npz。

为什么要这一步：生产链路（train_child / train_per_person / apply_per_person）直接读
db，而我的审核工具（bench_ens / selfaudit）读 npz——重扫后必须重新导出一次，
否则审核看到的是旧检测器的坐标与嵌入。

导出键与 `bench_ens.load()` 对齐：ck / lib / det / box / mbf / r50。

用法:
  python export_npz.py                       # → _faces_backup/faces_<det>_<date>.npz
  python export_npz.py --out /tmp/x.npz
  python export_npz.py --min-det 0.6 --min-side 10
"""
import argparse
import os
import sqlite3
import sys

import numpy as np

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
DB = f"{ROOT}/19_统一相册库/library.db"
OUTDIR = f"{ROOT}/19_统一相册库/_faces_backup"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--min-det", type=float, default=0.0)
    ap.add_argument("--min-side", type=int, default=0)
    ap.add_argument("--tag", default="scrfd")
    a = ap.parse_args()

    con = sqlite3.connect(DB)
    rows = con.execute("select lib, content_key, det_score, x, y, w, h, "
                       "emb_mbf, emb_r50 from faces").fetchall()
    con.close()

    lib, ck, det, box, mbf, r50 = [], [], [], [], [], []
    n_bad = 0
    for lb, c, ds, x, y, w, h, mb, r5 in rows:
        if mb is None or r5 is None:
            n_bad += 1
            continue
        if ds is not None and ds < a.min_det:
            continue
        if w is not None and h is not None and min(w, h) < a.min_side:
            continue
        lib.append(str(lb)); ck.append(str(c))
        det.append(float(ds) if ds is not None else 0.0)
        box.append((float(x), float(y), float(w), float(h)))
        mbf.append(np.frombuffer(mb, dtype=np.float32))
        r50.append(np.frombuffer(r5, dtype=np.float32))

    if not ck:
        print("faces 表为空或全被过滤 —— 重扫还没结束？", file=sys.stderr)
        sys.exit(1)

    # ⚠️ 原判据 `len(dims) != 2` 是错的：mbf 和 r50 同为 512 维时 dims 只有 {512}（len=1），
    # 于是**每次都误报"嵌入维度不齐"**，反而掩盖真问题。正确判据是 mbf 内部、r50 内部各自一致。
    dm = {len(v) for v in mbf}
    dr = {len(v) for v in r50}
    if len(dm) != 1 or len(dr) != 1:
        print(f"⚠️ 嵌入维度不齐（mbf={sorted(dm)} r50={sorted(dr)}）"
              f"—— 视为混了两种模型，请检查重扫是否中途换过参数", file=sys.stderr)
    os.makedirs(OUTDIR, exist_ok=True)
    out = a.out or f"{OUTDIR}/faces_{a.tag}_20260919.npz"
    np.savez_compressed(
        out,
        ck=np.array(ck, dtype=object), lib=np.array(lib, dtype=object),
        det=np.array(det, dtype=np.float32), box=np.array(box, dtype=np.float32),
        mbf=np.vstack(mbf).astype(np.float32), r50=np.vstack(r50).astype(np.float32))
    import collections
    print(f"导出 {len(ck)} 张脸（跳过缺嵌入 {n_bad}）→ {out} "
          f"({os.path.getsize(out)/1e6:.1f} MB)", file=sys.stderr)
    print("  来源分布: " + str(dict(collections.Counter(lib).most_common())), file=sys.stderr)


if __name__ == "__main__":
    main()

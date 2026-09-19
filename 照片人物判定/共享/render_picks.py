#!/usr/bin/env python3
"""按编号重渲染：读 `cluster_probe.py` / `audit.py` 产出的 JSON，把指定条目重新拼图。

用途（合并了三个高频需求）：
  * 放大复核：整张探针图 8 列太挤，`--cols 4` 重渲染看得清
  * 只看某几簇：`--cluster 18,0` 或 `--nos 0,1,2,17`
  * 落真值前的确认：`--mark` 会在编号旁附带 ck 前缀，便于对回 JSON 写 verdicts

用法:
  python render_picks.py --json _audit/簇探针.json --cluster 18 --cols 4 -o _audit/复核_C18.jpg
  python render_picks.py --json _audit/簇探针.json --nos 0,1,2,3 --cols 4
"""
import os
import sys
import json
import argparse

os.nice(10)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit import crop_face, make_sheet, imread_any

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
OUTDIR = f"{ROOT}/照片人物判定/_audit"


def from_labels(npz, clusters, n, seed, cols_hint=None):
    """直接从 cluster_probe 的 labels.npz 取任意簇的成员（不限于当次渲染过的 top-N）。
    随机抽 n 张（含最像中心的前几张）。"""
    import sqlite3
    import numpy as np
    z = np.load(npz, allow_pickle=True)
    lab, ck, rid = z["lab"], z["ck"], z["rid"]
    con = sqlite3.connect(f"{ROOT}/19_统一相册库/library.db")
    con.row_factory = sqlite3.Row
    box, emb = {}, {}
    for r in con.execute(f"SELECT rowid,x,y,w,h,emb_{z['emb']} AS e FROM faces"):
        box[int(r["rowid"])] = (r["x"], r["y"], r["w"], r["h"])
        emb[int(r["rowid"])] = r["e"]
    paths = {}
    for r in con.execute("SELECT content_key, path FROM files WHERE is_primary=1"):
        paths.setdefault(r["content_key"], r["path"])
    con.close()
    X = {}
    for i, rr in enumerate(rid):
        e = emb.get(int(rr))
        if e is not None:
            v = np.frombuffer(e, dtype=np.float32)
            nv = np.linalg.norm(v)
            X[int(rr)] = v / nv if nv > 1e-9 else v
    import random
    rng = random.Random(seed)
    out = []
    for cl in clusters:
        m = [int(r) for r, l in zip(rid, lab) if l == cl]
        if not m:
            print(f"簇{cl}：无成员"); continue
        V = np.stack([X[r] for r in m if r in X]) if m else None
        C = V.mean(axis=0); C /= max(np.linalg.norm(C), 1e-9)
        sims = V @ C
        order = np.argsort(-sims)
        take = list(order[:max(1, n // 3)]) + rng.sample(range(len(m)), min(n, len(m)))
        take = list(dict.fromkeys(int(t) for t in take))[:n]
        m2 = [r for r in m if r in X]
        for t in take:
            r = m2[t]
            p = paths.get(dict(zip(rid.tolist(), ck.tolist())).get(r, ""))
            bx = box.get(r)
            if not p or bx is None:
                continue
            out.append(dict(no=len(out), cluster=int(cl),
                            ck=str(dict(zip(rid.tolist(), ck.tolist()))[r]),
                            box=[float(v) for v in bx], sim=float(sims[t]), path=p))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    ap.add_argument("--labels", default=None,
                    help="直接读 labels.npz 取任意簇（配合 --cluster，免重跑聚类）")
    ap.add_argument("--n", type=int, default=6, help="--labels 模式下每簇取几张")
    ap.add_argument("--cluster", default=None, help="按簇筛，逗号分隔")
    ap.add_argument("--nos", default=None, help="按编号筛，逗号分隔（优先级高于 --cluster）")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--size", type=int, default=280)
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--tag", action="store_true", help="编号后附 ck 前缀（写 verdicts 用）")
    a = ap.parse_args()

    if a.labels:
        if not a.cluster:
            print("--labels 模式必须给 --cluster"); return
        items_all = from_labels(a.labels, [int(v) for v in a.cluster.split(",")],
                                a.n, 3)
    else:
        D = json.load(open(a.json, encoding="utf-8"))
        items_all = D.get("items") or [it for c in D.get("clusters", []) for it in c["items"]]
        if a.nos:
            keep = {int(v) for v in a.nos.split(",")}
        elif a.cluster:
            keep = {int(v) for v in a.cluster.split(",")}
            items_all = [it for it in items_all if it.get("cluster") in keep]
            keep = None
        else:
            keep = None
        if keep is not None:
            items_all = [it for it in items_all if it["no"] in keep]

    items = []
    for it in items_all:
        im = crop_face(it["path"], it["box"], size=a.size)
        if im is None:
            continue
        cap = f"#{it['no']}"
        if it.get("cluster") is not None:
            cap += f" C{it['cluster']}"
        if a.tag:
            cap += f" {it['ck'][:6]}"
        items.append((im, cap))
    if not items:
        print("没有可渲染条目"); return
    base = os.path.basename(a.json) if a.json else "labels"
    out = a.out or f"{OUTDIR}/复核_{base.replace('.json','')}.jpg"
    make_sheet(items, a.cols, out, title=f"{base}  共{len(items)}张")
    print(f"{len(items)} 张 → {out}")


if __name__ == "__main__":
    main()

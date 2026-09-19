#!/usr/bin/env python3
"""把「人肉验证过的簇」导出成身份种子（跨重扫稳定键：content_key + 归一化中心）。

与 verdicts.jsonl 的区别（**为什么必须分开存**）：
  verdicts.jsonl = 我逐张看图判的真值，是"金标"，样本少（百量级）；
  cluster_seeds.jsonl = 无监督聚类 + 我看代表图后命名的**半自动**种子，
  量大（千量级），纯度靠 τ 截断 + 代表图抽检保证，不是逐张判的。
  混在一起会让"金标"这个口径失效，所以分文件、分 src。

簇命名存在 `_audit/簇命名.json`，格式 {"人": [簇号...]}。
簇号必须来自同一次 `cluster_probe.py` 运行（labels.npz 与命名表同批次）。
**重扫会换 rowid**，所以仓库里只存 (ck, cx, cy) 这种跨重扫稳定的键。

用法:
  python cluster_seeds.py --stat                 # 只统计
  python cluster_seeds.py --emit                 # 写 _audit/cluster_seeds.jsonl
  python cluster_seeds.py --emit --tau 0.60      # 更严：只取簇核心
"""
import os
import sys
import json
import argparse
import sqlite3
import collections

os.nice(10)
import numpy as np

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB_DB = f"{ROOT}/19_统一相册库/library.db"
OUTDIR = f"{ROOT}/照片人物判定/_audit"
LABELS = f"{OUTDIR}/簇探针_k60.labels.npz"
NAMING = f"{OUTDIR}/簇命名.json"
OUT = f"{OUTDIR}/cluster_seeds.jsonl"


def load_emb(rid, col):
    con = sqlite3.connect(LIB_DB)
    emb = {}
    for r, e in con.execute(f"SELECT rowid, emb_{col} FROM faces"):
        emb[int(r)] = e
    con.close()
    X = np.zeros((len(rid), 512), dtype=np.float32)
    miss = 0
    for i, r in enumerate(rid):
        e = emb.get(int(r))
        if e is None:
            miss += 1; continue
        X[i] = np.frombuffer(e, dtype=np.float32)
    n = np.linalg.norm(X, axis=1, keepdims=True); n[n < 1e-9] = 1
    return X / n, miss


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tau", type=float, default=0.55, help="与簇中心的余弦下限")
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--stat", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(NAMING):
        print(f"缺 {NAMING}（格式 {{\"人\":[簇号...]}}）"); return
    naming = {k: v for k, v in json.load(open(NAMING, encoding="utf-8")).items()
              if not k.startswith("_")}   # `_note`/`_excluded` 等是元数据，不是人
    z = np.load(LABELS, allow_pickle=True)
    lab, ck, rid = z["lab"], z["ck"], z["rid"]
    X, miss = load_emb(rid, str(z["emb"]))
    if miss:
        print(f"⚠️ {miss} 张脸的嵌入已不在库中（重扫期间库在变）——"
              f"**重扫结束后必须重跑 cluster_probe.py 再导出**，否则种子会缺")

    recs = []
    print(f"{'人':<8}{'簇':>6}{'簇内':>7}{'取τ':>7}{'占比':>7}{'中位sim':>9}")
    for person, cls in naming.items():
        if not isinstance(cls, (list, tuple)):
            cls = [cls]
        for cl in cls:
            m = np.where(lab == cl)[0]
            if not len(m):
                print(f"{person:<8}{cl:>6}  （无成员）"); continue
            C = X[m].mean(axis=0); C /= max(np.linalg.norm(C), 1e-9)
            sims = X[m] @ C
            keep = m[sims >= a.tau]
            print(f"{person:<8}{cl:>6}{len(m):>7}{len(keep):>7}"
                  f"{len(keep)/max(len(m),1):>7.0%}{np.median(sims):>9.3f}")
            for i in keep:
                recs.append(dict(person=person, ck=str(ck[i]), rid=int(rid[i]),
                                 sim=round(float(X[i] @ C), 4),
                                 cluster=int(cl), src="cluster"))

    # 同一人同一张照片留一张（多脸照片会把同 ck 记多次）
    seen, uniq = {}, []
    for r in sorted(recs, key=lambda r: -r["sim"]):
        k = (r["person"], r["ck"])
        if k in seen:
            continue
        seen[k] = 1
        uniq.append(r)
    print(f"\n合计 {len(uniq)} 条（同人同照片去重前 {len(recs)}）")
    print("按人：", dict(collections.Counter(r["person"] for r in uniq)))
    if not (a.emit and not a.stat):
        return

    # 补 (cx, cy)：用该脸的 box 与所在图片尺寸算归一化中心（跨重扫可吸附）
    # 图片尺寸优先读 _dims.json 缓存（audit.py 维护，格式 ck→[ih,iw]）；
    # 逐张读原图在重扫抢 I/O 时会把整个脚本拖到超时（实测被 SIGTERM）。
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from audit import imread_any
    dims_cache = {}
    dc = f"{OUTDIR}/_dims.json"
    if os.path.exists(dc):
        try:
            dims_cache = json.load(open(dc))
        except Exception:
            dims_cache = {}
    con = sqlite3.connect(LIB_DB); con.row_factory = sqlite3.Row
    box = {int(r["rowid"]): (r["x"], r["y"], r["w"], r["h"])
           for r in con.execute("SELECT rowid,x,y,w,h FROM faces")}
    paths = {}
    for r in con.execute("SELECT content_key, path FROM files WHERE is_primary=1"):
        paths.setdefault(r["content_key"], r["path"])
    con.close()

    wh, out, n_read = {}, [], 0
    for r in uniq:
        d = dims_cache.get(r["ck"])
        if d is None:
            p = paths.get(r["ck"])
            if not p:
                continue
            if r["ck"] not in wh:
                im = imread_any(p)
                wh[r["ck"]] = (im.shape[1], im.shape[0])[::-1] if im is not None else None
            d = wh[r["ck"]]
            n_read += 1
        bx = box.get(r["rid"])
        if not d or bx is None:
            continue
        ih, iw = int(d[0]), int(d[1])
        r["cx"] = round((bx[0] + bx[2] / 2) / iw, 4)
        r["cy"] = round((bx[1] + bx[3] / 2) / ih, 4)
        r["iw"], r["ih"] = iw, ih
        out.append(r)
    if n_read:
        print(f"（{n_read} 张图无尺寸缓存，现读）")
    with open(OUT, "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"→ {OUT}  （{len(out)} 条，含归一化中心）")


if __name__ == "__main__":
    main()

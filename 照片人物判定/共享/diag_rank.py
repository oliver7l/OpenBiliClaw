#!/usr/bin/env python3
"""照片级分数排名分布 —— 回答「往下扫到第几名，分数还剩多少」。

为什么需要：
  现役阈值来自「人眼金标 5% 分位」（= 只信已确认命中的最低那批），**天然是正样本口径**，
  天生保守。要抬召回必须知道：阈值往下放宽到第 100/200/400/800/1600 名时，
  分数掉到多少、归档口径下的确证/冲突各多少。本脚本只出**分布**，不做判定；
  判定靠 `render_tagged.py --mode global --skip N` 出图，我逐张看图定精度。

口径（与 apply 完全一致）：
  · 一张照片的分数 = 该照片内该身份**最高分脸**的分数（照片级聚合）
  · 只统计照片一次（同一 content_key 的多张脸只取 max）
  · 归档归属 = 18 目录名（canon 化，乐仔小时候→乐仔）

输出每个身份：
  · 全库照片数 / 现役阈值 / 阈值以上照片数（= 现役发货规模）
  · 目标名次点上的分数（名次→分数，看衰减梯度）
  · 每档（前50/200/500/1000/2000/4000/8000）的 归档确证/冲突/非归档 计数

用法:
  python diag_rank.py                       # 全部身份
  python diag_rank.py --person 乐仔 --marks 100,200,400,800
"""
import argparse
import collections
import json
import os
import pickle
import sqlite3
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB_DB = f"{ROOT}/19_统一相册库/library.db"
NPZ = f"{ROOT}/19_统一相册库/_faces_backup/faces_scrfd_20260919.npz"
ENS_PKL = f"{ROOT}/照片人物判定/per_person_ens.pkl"
GOLD_THR = f"{ROOT}/照片人物判定/_audit/金标阈值.json"
ALIAS = {"乐仔小时候": "乐仔"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--person", default=None, help="默认全部")
    ap.add_argument("--src", default=NPZ)
    ap.add_argument("--marks", default="50,200,500,1000,2000,4000,8000,16000")
    ap.add_argument("--bands", default="200,500,1000,2000,4000,8000,20000",
                    help="档案口径计数用的名次边界")
    ap.add_argument("--json", default=None, help="把结果落盘成 json")
    a = ap.parse_args()

    import ens_models as EM

    d = np.load(a.src, allow_pickle=True)
    ck = d["ck"].astype(str)
    det = d["det"].astype(np.float32)
    box = d["box"].astype(np.float32)
    keep = (det >= 0.60) & (np.minimum(box[:, 2], box[:, 3]) >= 10)
    ck, det, box = ck[keep], det[keep], box[keep]
    mbf = EM.l2n(d["mbf"].astype(np.float32))[keep]
    r50 = EM.l2n(d["r50"].astype(np.float32))[keep]
    feats = {"mbf": mbf, "r50": r50, "fused": EM.l2n(np.hstack([mbf, r50]))}

    con = sqlite3.connect(LIB_DB)
    ck18 = collections.defaultdict(set)
    for rel, c in con.execute("select rel, content_key from files where lib='18'"):
        ck18[c].add(ALIAS.get(rel.split("/")[0], rel.split("/")[0]))
    con.close()

    thr = json.load(open(GOLD_THR, encoding="utf-8")) if os.path.exists(GOLD_THR) else {}
    bundle = pickle.load(open(ENS_PKL, "rb"))
    people = [a.person] if a.person else [p for p in EM.IDENT if p in bundle]
    marks = [int(x) for x in a.marks.split(",") if x.strip()]
    bands = [int(x) for x in a.bands.split(",") if x.strip()]

    # 每个身份一个条目；零锚点身份照样算（看它到底能捞到什么）
    prep = EM.ens_prep(ck, feats, ck18, EM.IDENT)
    nface = collections.Counter(ck)
    out = {}
    for p in people:
        if p not in bundle:
            print(f"⚠️ {p} 不在 pkl 中")
            continue
        z, _ = EM.ens_score_one(bundle[p], prep, ck, p, feats, det, box, EM.IDENT)
        # 照片级聚合：每张照片取最高分脸
        best = {}
        for i, c in enumerate(ck):
            if c not in best or z[i] > best[c][0]:
                best[c] = (float(z[i]), i)
        rows = sorted(best.items(), key=lambda kv: -kv[1][0])
        pp = ALIAS.get(p, p)
        t = float(thr.get(p, {}).get("thr", float("nan")))
        nabove = sum(1 for _, (s, _) in rows if s >= t) if not np.isnan(t) else -1

        print(f"\n=== {p} ===")
        print(f"  全库有脸照片={len(rows)}  现役阈值={t:.3f}  阈值以上照片={nabove}")
        print(f"  名次→照片级分数: " + "  ".join(
            f"#{m}={rows[m - 1][1][0]:.3f}" for m in marks if m <= len(rows)))
        prev = 0
        line = []
        for b in bands:
            seg = rows[prev:b]
            pos = sum(1 for c, _ in seg if ck18.get(c) == {pp})
            neg = sum(1 for c, _ in seg if ck18.get(c) is not None and pp not in ck18[c])
            unk = sum(1 for c, _ in seg if ck18.get(c) is None)
            conf = neg / max(1, pos + neg)
            line.append(f"    #{prev + 1}-{min(b, len(rows))}: 确证{pos:>4} 冲突{neg:>4} "
                        f"非归档{unk:>5} 冲突率{conf:.1%}")
            prev = b
            if b >= len(rows):
                break
        print("\n".join(line))
        out[p] = dict(n_photos=len(rows), threshold=t, n_above=nabove,
                      ranks=[(m, rows[m - 1][1][0]) for m in marks if m <= len(rows)])

    if a.json:
        json.dump(out, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\n→ {a.json}")


if __name__ == "__main__":
    main()

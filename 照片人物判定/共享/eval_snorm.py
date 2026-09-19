#!/usr/bin/env python3
"""S-norm 快评：直接读 _audit/_snorm_scores.npz（diag_snorm 打分矩阵缓存），
在 708 条人眼金标上评估 raw / faceS(t-norm) / libZ(z-norm) 三种尺度的判别力
与「单人×单库」正样本分位对齐度。秒级迭代，不用重打分。"""
import collections
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
SN = np.load(f"{ROOT}/照片人物判定/_audit/_snorm_scores.npz", allow_pickle=True)
VERDICTS = f"{ROOT}/照片人物判定/_audit/verdicts.jsonl"
ALIAS = {"乐仔小时候": "乐仔"}

photo_cks = SN["photo_cks"].astype(str)
libs = SN["libs"].astype(str)
persons = sorted({k.split("_", 1)[1] for k in SN.files if k.startswith("raw_")})


def auc(pos, neg):
    pos, neg = np.asarray(pos, dtype=np.float64), np.asarray(neg, dtype=np.float64)
    if not len(pos) or not len(neg):
        return float("nan")
    allv = np.concatenate([pos, neg])
    order = np.argsort(allv, kind="mergesort")
    ranks = np.empty(len(allv))
    v = allv[order]
    i = 0
    while i < len(v):
        j = i
        while j < len(v) and v[j] == v[i]:
            j += 1
        ranks[order[i:j]] = (i + 1 + j) / 2.0
        i = j
    return (ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


gold = collections.defaultdict(dict)
for line in open(VERDICTS, encoding="utf-8"):
    d = json.loads(line)
    gold[ALIAS.get(d["person"], d["person"])][d["key"]] = d["verdict"]

gck = collections.defaultdict(dict)
for p, kv in gold.items():
    for k, v in kv.items():
        gck[p].setdefault(k.split("|")[0], []).append(v)

j_of = {c: j for j, c in enumerate(photo_cks)}

print(f"照片 {len(photo_cks)}，身份 {persons}")
print(f"{'身份':<6}{'正/负':>7}   {'raw':>6} {'faceS':>6} {'libZ':>6}   正样本中位 raw→libZ（按库）")
summary = {}
for p in persons:
    raw = SN[f"raw_{p}"]; tn = SN[f"tnorm_{p}"]; zn = SN[f"znorm_{p}"]
    pos = {s: ([], []) for s in ("raw", "tn", "zn")}
    neg = {s: [] for s in ("raw", "tn", "zn")}
    per_lib = collections.defaultdict(lambda: ([], []))
    for c, vs in gck[p].items():
        v = [x for x in vs if x != 2]
        if not v or c not in j_of:
            continue
        v = max(v)
        j = j_of[c]
        L = libs[j]
        if v == 1:
            pos["raw"][0].append(raw[j]); pos["tn"][0].append(tn[j]); pos["zn"][0].append(zn[j])
            per_lib[L][0].append(raw[j]); per_lib[L][1].append(zn[j])
        else:
            neg["raw"].append(raw[j]); neg["tn"].append(tn[j]); neg["zn"].append(zn[j])
    a = (auc(pos["raw"][0], neg["raw"]), auc(pos["tn"][0], neg["tn"]),
         auc(pos["zn"][0], neg["zn"]))
    parts = " | ".join(f"{L}:{np.median(x) if x else float('nan'):+.2f}→{np.median(y) if y else float('nan'):+.2f}"
                       for L, (x, y) in sorted(per_lib.items()))
    print(f"{p:<6}{len(pos['raw'][0]):>4}/{len(neg['raw']):<3} {a[0]:>6.3f} {a[1]:>6.3f} {a[2]:>6.3f}   {parts}")
    summary[p] = dict(raw=a[0], tnorm=a[1], znorm=a[2],
                      n_pos=len(pos["raw"][0]), n_neg=len(neg["raw"]))

json.dump(summary, open(f"{ROOT}/照片人物判定/_audit/_snorm_eval.json", "w"), ensure_ascii=False, indent=1)

#!/usr/bin/env python3
"""跨域分数归一化（S-norm/Z-norm）诊断 —— 第 7 轮核心实验。

背景（第 6 轮发现）：
  同一身份在不同来源库的分数分布整体错开（乐仔 08 库 98.7% z>0 / 09 库 1.7%），
  单一全局阈值必然顾此失彼。InsightFace 官方指南对这种「底库分布变化」给的
  标准解法就是 z-norm / t-norm 分数归一化。

本脚本做两件事：
  1. 用**其他 4 人模型在同一批脸上的分数**当 impostor cohort，
     对每个人的分数做两种归一化：
       · face-S  : s − mean(其他人同脸分数 top3)     （逐脸归一，t-norm 风格）
       · lib-Z   : (s − μ_imp(L)) / σ_imp(L)          （按库域归一，z-norm 风格，
                                                      μ/σ 来自该库内其他人照片级最高分）
  2. 用 _audit/verdicts.jsonl 人眼金标（verdict 1/0，2=存疑丢弃）评估：
       归一化前后 AUC、以及「单人×单库」内正样本分位是否对齐
       —— 对齐 = 一个归一化阈值可跨库通用。

注意：分数=logit 原尺度；评估只在人眼金标上做，绝不看归档归属当真值。
"""
import collections
import json
import os
import pickle
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
NPZ = f"{ROOT}/19_统一相册库/_faces_backup/faces_scrfd_20260919.npz"
ENS_PKL = f"{ROOT}/照片人物判定/per_person_ens.pkl"
VERDICTS = f"{ROOT}/照片人物判定/_audit/verdicts.jsonl"

ALIAS = {"乐仔小时候": "乐仔"}

from audit import imread_any  # noqa: E402  （保证 HEIC 读取路径已被 import 校验）
import ens_models as EM       # noqa: E402


def load_all():
    con_keys = None
    d = np.load(NPZ, allow_pickle=True)
    ck = d["ck"].astype(str)
    lib = d["lib"].astype(str)
    det = d["det"].astype(np.float32)
    box = d["box"].astype(np.float32)
    keep = (det >= 0.60) & (np.minimum(box[:, 2], box[:, 3]) >= 10)
    ck, lib, det, box = ck[keep], lib[keep], det[keep], box[keep]
    mbf = EM.l2n(d["mbf"].astype(np.float32))[keep]
    r50 = EM.l2n(d["r50"].astype(np.float32))[keep]
    feats = {"mbf": mbf, "r50": r50, "fused": EM.l2n(np.hstack([mbf, r50]))}

    # 18 归档 → 幼童门控要用的照片级归属（与 apply/render 同口径）
    import sqlite3
    con = sqlite3.connect(f"{ROOT}/19_统一相册库/library.db")
    ck18 = collections.defaultdict(set)
    for rel, c in con.execute("select rel, content_key from files where lib='18'"):
        ck18[c].add(ALIAS.get(rel.split("/")[0], rel.split("/")[0]))
    con.close()
    return ck, lib, det, box, feats, ck18


def main():
    ck, lib, det, box, feats, ck18 = load_all()
    bundle = pickle.load(open(ENS_PKL, "rb"))
    persons = [p for p in bundle if ALIAS.get(p, p) != p or True]
    persons = sorted({ALIAS.get(p, p) for p in bundle} - {"妈妈"})   # 妈妈零锚点，不进 cohort 排序结论
    print(f"faces={len(ck)}  persons(cohort)={persons}", flush=True)

    prep = EM.ens_prep(ck, feats, ck18, EM.IDENT)
    Z = {}
    for p in persons:
        src = ALIAS.get(p, p)
        if src not in bundle:
            continue
        z, _ = EM.ens_score_one(bundle[src], prep, ck, src,
                                feats, det, box, EM.IDENT)
        Z[p] = np.asarray(z, dtype=np.float64)
        print(f"  scored {p}: mean={Z[p].mean():.2f}", flush=True)

    # ---- 照片级最高分（与 apply 的照片级口径一致）----
    best = {}   # person -> {ck: (score, face_idx)}
    for p in persons:
        b = {}
        for i, c in enumerate(ck):
            if c not in b or Z[p][i] > b[c][0]:
                b[c] = (Z[p][i], i)
        best[p] = b

    # ---- face-S（逐脸 t-norm 风格）：cohort = 其他人**同脸**分数 ----
    # 照片级对齐：对每张照片，用其他人在**同照片最高分脸**上的分数做 cohort。
    photo_cks = sorted(best[persons[0]].keys())
    ck2lib = {}
    for i, c in enumerate(ck):
        if c not in ck2lib:
            ck2lib[c] = lib[i]

    S_face = {p: np.array([best[p][c][0] for c in photo_cks]) for p in persons}
    S_libs = np.array([ck2lib[c] for c in photo_cks])

    # face-S：s − mean(top3 其他人在同一照片上的分)
    S_tnorm = {}
    for p in persons:
        others = [q for q in persons if q != p]
        M = np.vstack([S_face[q] for q in others])            # (4, N)
        top3 = np.sort(M, axis=0)[-3:, :]                     # 每列 top3
        S_tnorm[p] = S_face[p] - top3.mean(axis=0)

    # lib-Z：(s − μ_imp(L)) / σ_imp(L)，μ/σ 用该库内其他人的照片级分
    S_znorm = {}
    imp_stats = {}
    for p in persons:
        others = [q for q in persons if q != p]
        M = np.vstack([S_face[q] for q in others])
        imp_mu = M.mean(axis=0)                               # 每张照片的 impostor 均值
        per_lib = {}
        for L in sorted(set(S_libs)):
            m = (S_libs == L)
            per_lib[L] = (float(imp_mu[m].mean()), float(imp_mu[m].std() + 1e-9))
        imp_stats[p] = per_lib
        S_znorm[p] = np.array([
            (S_face[p][j] - per_lib[S_libs[j]][0]) / per_lib[S_libs[j]][1]
            for j in range(len(photo_cks))
        ])

    # ---- 人眼金标评估 ----
    gold = collections.defaultdict(dict)   # person -> key -> verdict
    for line in open(VERDICTS, encoding="utf-8"):
        d = json.loads(line)
        gold[ALIAS.get(d["person"], d["person"])][d["key"]] = d["verdict"]

    pos_idx = {p: {c: j for j, c in enumerate(photo_cks)} for p in persons}
    # key 的中心匹配：金标 key=ck|cx|cy；照片级条目没有单脸中心。
    # 改用照片级匹配：金标 ck（key 前段）→ 该照片。同一照片同人多脸金标时取 OR。
    gck = collections.defaultdict(dict)
    for p, kv in gold.items():
        for k, v in kv.items():
            c = k.split("|")[0]
            gck[p].setdefault(c, []).append(v)

    def auc(pos, neg):
        if not pos or not neg:
            return float("nan")
        pos, neg = np.asarray(pos), np.asarray(neg)
        order = np.argsort(np.concatenate([pos, neg]))
        ranks = np.empty(len(order))
        vals = np.concatenate([pos, neg])[order]
        i = 0
        while i < len(vals):
            j = i
            while j < len(vals) and vals[j] == vals[i]:
                j += 1
            ranks[order[i:j]] = (i + j + 1) / 2.0
            i = j
        r_pos = ranks[:len(pos)].sum()
        return (r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))

    print("\n=== 人眼金标评估（verdict 1 vs 0；2=存疑丢弃）===")
    print(f"{'身份':<6}{'金标':>6}  {'raw AUC':>8} {'faceS':>8} {'libZ':>8}"
          f"   raw正分位(07/08/09) → libZ正分位(07/08/09)")
    for p in persons:
        pos_r, neg_r, pos_t, pos_z, neg_all = [], [], [], [], []
        per_lib_pos = collections.defaultdict(lambda: ([], []))
        for c, vs in gck[p].items():
            v = [x for x in vs if x != 2]
            if not v:
                continue
            v = max(v)                      # 照片级 OR：任一人眼判正即正
            j = pos_idx[p].get(c)
            if j is None:
                continue
            (pos_r if v == 1 else neg_r).append(S_face[p][j])
            if v == 1:
                pos_t.append(S_tnorm[p][j])
                pos_z.append(S_znorm[p][j])
                per_lib_pos[S_libs[j]][0].append(S_face[p][j])
                per_lib_pos[S_libs[j]][1].append(S_znorm[p][j])
            else:
                neg_all.append(j)
        parts = []
        for L in sorted(per_lib_pos):
            a, b = per_lib_pos[L]
            if not a:
                continue
            parts.append(f"{L}:{np.median(a):+.2f}→{np.median(b):+.2f}")
        print(f"{p:<6}{len(pos_r):>6}  {auc(pos_r, neg_r):>8.3f} "
              f"{auc(pos_t, [S_tnorm[p][j] for j in neg_all]):>8.3f} "
              f"{auc(pos_z, [S_znorm[p][j] for j in neg_all]):>8.3f}"
              f"   {' | '.join(parts)}")

    # ---- impostor 分布按库（域偏移的直接证据）----
    print("\n=== 各库 impostor 均值分布（域偏移幅度）===")
    for p in persons:
        row = "  ".join(f"{L}:{mu:+.2f}/σ{sd:.2f}" for L, (mu, sd) in imp_stats[p].items())
        print(f"{p:<6} {row}")

    np.savez_compressed(f"{HERE}/../_audit/_snorm_scores.npz",
                        photo_cks=np.array(photo_cks), libs=S_libs,
                        **{f"raw_{p}": S_face[p] for p in persons},
                        **{f"tnorm_{p}": S_tnorm[p] for p in persons},
                        **{f"znorm_{p}": S_znorm[p] for p in persons})
    print(f"\n→ 分数矩阵已存 _audit/_snorm_scores.npz（{len(photo_cks)} 照片 × "
          f"{len(persons)} 人 × 3 尺度）")


if __name__ == "__main__":
    main()

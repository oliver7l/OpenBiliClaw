#!/usr/bin/env python3
"""按**来源库**拆开的召回诊断 —— 找「召回缺口到底在哪一库、哪一个人身上」。

为什么要单独看库：
  · 08 = 夸克「乐仔照片」相册 ⇒ **天然的近乎全正样本组**（这孩子自己的相册）。
    如果 08 里只有一小部分被判成乐仔，那问题根本不在阈值，而在该库的域差异/画质。
  · 07 = 家庭手机备份     ⇒ 家人为主，但含风景/截图。
  · 09 = QQ 群相册        ⇒ 同班孩子扎堆，是小脸/低画质/多人合影的重灾区。
  · 18 = 人物分组         ⇒ 与本人"相关"的照片（**不是**身份真值）。

输出每个人 × 每个库：
  n       = 该库 primary 照片中「有脸」的张数
  z>0     = 模型 logit 为正的张数（p>0.5 的自报口径）
  ≥阈值   = 现役阈值以上的张数（拿不到阈值则显示 -）
  中位分  = 该库分数中位（跨身份**不可比**，只用于同身份跨库对比找域差异）

用法: python diag_lib_recall.py [--person 乐仔]
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
    ap.add_argument("--person", default=None)
    a = ap.parse_args()

    import ens_models as EM

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
    feats["ada"] = EM.ada_join(ck, box)   # AdaFace 第三通道（第 9 轮）

    con = sqlite3.connect(LIB_DB)
    ck18 = collections.defaultdict(set)
    for rel, c in con.execute("select rel, content_key from files where lib='18'"):
        ck18[c].add(ALIAS.get(rel.split("/")[0], rel.split("/")[0]))
    # 只认 primary 副本，避免 08 的重复文件把分母灌水
    prim = {}
    for c, l in con.execute("select content_key, lib from files where is_primary=1"):
        prim.setdefault(c, set()).add(l)
    con.close()

    thr = json.load(open(GOLD_THR, encoding="utf-8")) if os.path.exists(GOLD_THR) else {}
    bundle = pickle.load(open(ENS_PKL, "rb"))
    people = [a.person] if a.person else [p for p in EM.IDENT if p in bundle]

    prep = EM.ens_prep(ck, feats, ck18, EM.IDENT)
    LIBS = ["07", "08", "09", "18"]
    for p in people:
        z, _ = EM.ens_score_one(bundle[p], prep, ck, p, feats, det, box, EM.IDENT)
        best = {}
        for i, c in enumerate(ck):
            if c not in best or z[i] > best[c]:
                best[c] = float(z[i])
        t = thr.get(p, {}).get("thr")
        print(f"\n=== {p}（现役阈值 {t if t else '—'}）===")
        print(f"  {'库':<4}{'有脸照片':>8}{'z>0':>8}{'≥阈值':>8}{'中位分':>9}   最强库内名次得分")
        for l in LIBS:
            sub = [s for c, s in best.items() if l in (prim.get(c) or set())]
            if not sub:
                continue
            sub = np.array(sub)
            npos = int((sub > 0).sum())
            nge = int((sub >= t).sum()) if t else -1
            print(f"  {l:<4}{len(sub):>8}{npos:>8}{nge:>8}{np.median(sub):>9.2f}"
                  f"   max={sub.max():.2f}")
        allv = np.array(list(best.values()))
        print(f"  合计 有脸照片={len(allv)}  z>0={int((allv > 0).sum())}"
              f"  ≥阈值={int((allv >= t).sum()) if t else '-'}")


if __name__ == "__main__":
    main()

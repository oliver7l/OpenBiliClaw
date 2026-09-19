#!/usr/bin/env python3
"""诊断「归档标定为什么无解」。

背景：apply_per_person.recalibrate_archive 的口径是——沿分数降序扫照片，
找到第一个同时满足「归档确证 ≥3 张」且「归档冲突率 ≤ 3%」的位置当阈值；
若扫到 `6×归档规模` 还没找到，就判无解、回退全库 99.95 分位。
2026-09-19 重扫后 艳艳/我 双双无解 —— 本脚本回答"卡在哪一步"。

输出每个身份：
  · u18        = 归档里「只归本人」的照片数（标定的样本基）
  · 分数分位   = 该身份全库分数的 min/中位/99/99.9/99.95 分位（看尺度是否合理）
  · 归档照片在分数榜上的位置分布（前 1%/10%/50% 各有多少张归档照）
  · 首次满足条件的位置（或"扫到 cap 仍不满足"及其当时的 pos/neg）

用法: python diag_calib.py [--person 艳艳] [--src .../faces_*.npz]
"""
import argparse
import collections
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
ALIAS = {"乐仔小时候": "乐仔"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--person", default=None, help="默认全部 6 人")
    ap.add_argument("--src", default=NPZ)
    ap.add_argument("--tgt", type=float, default=0.03)
    ap.add_argument("--cap-mult", type=float, default=6.0)
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
    feats["ada"] = EM.ada_join(ck, box)   # AdaFace 第三通道（第 9 轮）

    con = sqlite3.connect(LIB_DB)
    ck18 = collections.defaultdict(set)
    for rel, c in con.execute("select rel, content_key from files where lib='18'"):
        ck18[c].add(ALIAS.get(rel.split("/")[0], rel.split("/")[0]))
    con.close()

    bundle = pickle.load(open(ENS_PKL, "rb"))
    people = [a.person] if a.person else [p for p in EM.IDENT if p in bundle]
    prep = {}
    for p in people:
        if p not in bundle:
            print(f"⚠️ {p} 不在 pkl 中"); continue
        if "all" not in prep:
            prep["all"] = EM.ens_prep(ck, feats, ck18, EM.IDENT)
        z, _ = EM.ens_score_one(bundle[p], prep["all"], ck, p, feats, det, box, EM.IDENT)

        pp = ALIAS.get(p, p)
        u18 = sum(1 for w in ck18.values() if w == {pp})
        n18 = sum(1 for w in ck18.values() if pp in w)
        q = np.quantile(z, [0.5, 0.99, 0.999, 0.9995])
        print(f"\n=== {p} ===")
        print(f"  u18(只归本人)={u18}  含本人归档={n18}  全库脸={len(z)}")
        print(f"  分数 min={z.min():.3f} 中位={q[0]:.3f} 99%={q[1]:.3f} "
              f"99.9%={q[2]:.3f} 99.95%={q[3]:.3f} max={z.max():.3f}")

        nface = collections.Counter(ck)

        def scan(neg_mode):
            """neg_mode='any'：归档不含本人 = 冲突。
            'single'：**只有单脸照**才算冲突 —— 合影归在别人名下不构成证据，
            因为 18 是照片级单人归档，一张"爸爸+我+乐仔"的合影只能归一个人，
            其余人全被记成冲突（实测让 艳艳/我 的冲突率虚高到 46%/49% 导致标定无解）。"""
            order = np.argsort(-z)
            seen, pos, neg, unk = set(), 0, 0, 0
            first = None
            cap = int(a.cap_mult * max(u18, 20))
            marks = {}
            for i in order:
                c = ck[i]
                if c in seen:
                    continue
                seen.add(c)
                w = ck18.get(c)
                if w is None:
                    unk += 1
                elif w == {pp}:
                    pos += 1
                elif pp not in w:
                    if neg_mode == "any" or nface.get(c, 0) == 1:
                        neg += 1
                    else:
                        unk += 1
                if len(seen) in (50, 200, 500, 1000, 2000, 5000):
                    marks[len(seen)] = (pos, neg, unk)
                if first is None and pos >= 3 and neg / max(1, pos + neg) <= a.tgt:
                    first = (float(z[i]), len(seen), pos, neg)
                if len(seen) > cap:
                    break
            return first, marks, cap, len(seen), pos, neg, unk

        for nm in ("any", "single"):
            first, marks, cap, nsee, pos, neg, unk = scan(nm)
            print("  --- " + ("口径A: 归档不含本人即冲突（现行）" if nm == "any"
                             else "口径B: 仅单脸照算冲突（合影豁免）") + " ---")
            for k in sorted(marks):
                print(f"    前{k:>5}: 确证{marks[k][0]:>4} 冲突{marks[k][1]:>4} 非归档{marks[k][2]:>5}")
            if first:
                print(f"    ✅ 首次达标：阈值 {first[0]:.3f}"
                      f"（第 {first[1]} 张照片，确证 {first[2]} 冲突 {first[3]}）")
            else:
                print(f"    ⛔ 扫到 cap={cap}（{nsee} 张）仍不满足：确证 {pos} 冲突 {neg} "
                      f"非归档 {unk} —— {'确证不足 3' if pos < 3 else '冲突率过高'}")


if __name__ == "__main__":
    main()

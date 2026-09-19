#!/usr/bin/env python3
"""对某身份的某个名次段做**独立于模型的证据核对**。

为什么必须要有这个：
  "我读图觉得 24 张都像同一个人" 是**一个人、一个时刻的主观判断**，不足以支撑
  「把阈值下压 12 倍」这种量级的决定。必须再找一个与模型**无关**的证据源来交叉。

本脚本给三条独立证据（都不来自 per_person_ens）：
  1. **来源库分布**：这批照片来自哪个 lib。
     · 07/08 = 家庭手机备份/乐仔相册 ⇒ 家人照片，孩子几乎不可能是"别人"。
     · 09    = QQ 班级群相册    ⇒ **同班孩子多，相似小女孩/小男孩扎堆**
       ⇒ 09 占比高时必须警惕「模型把同班同学当成本人」，光看图区分不了。
  2. **18 归档归属**：照片级人工/半人工归档把这张照片归给谁（确证/冲突/非归档）。
     这是独立于模型的第二信号（但注意：18 是照片级单人归档，配角身份冲突率天然虚高）。
  3. **同框伙伴**：同照片内其它身份的最高分（如 07 家庭照里 乐仔 与 七月 常常同框）。
     家庭照中出现"另一个家人"能显著抬高可信度。

用法:
  python diag_band_evidence.py --person 七月 --from 117 --to 278 --stride 7
  python diag_band_evidence.py --person 乐仔 --from 336 --to 774 --stride 19
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
    ap.add_argument("--person", required=True)
    ap.add_argument("--from", dest="r0", type=int, required=True, help="起始名次（1 起）")
    ap.add_argument("--to", dest="r1", type=int, required=True)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--top-partner", type=int, default=3, help="每人看几个同框伙伴")
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

    con = sqlite3.connect(LIB_DB)
    ck18 = collections.defaultdict(set)
    for rel, c in con.execute("select rel, content_key from files where lib='18'"):
        ck18[c].add(ALIAS.get(rel.split("/")[0], rel.split("/")[0]))
    cklib = {}
    for c, l in con.execute("select content_key, lib from files where is_primary=1"):
        cklib.setdefault(c, set()).add(l)
    con.close()

    bundle = pickle.load(open(ENS_PKL, "rb"))
    prep = EM.ens_prep(ck, feats, ck18, EM.IDENT)

    # 先算主角，再算其余身份（用于同框伙伴）
    zs = {}
    for p in [a.person] + [q for q in EM.IDENT if q != a.person and q in bundle]:
        if p not in bundle:
            continue
        zs[p], _ = EM.ens_score_one(bundle[p], prep, ck, p, feats, det, box, EM.IDENT)

    z = zs[a.person]
    best = {}
    for i, c in enumerate(ck):
        if c not in best or z[i] > best[c][0]:
            best[c] = (float(z[i]), i)
    rows = sorted(best.items(), key=lambda kv: -kv[1][0])
    sel = rows[a.r0 - 1:a.r1][::max(1, a.stride)]

    pp = ALIAS.get(a.person, a.person)
    print(f"=== {a.person} 名次 {a.r0}-{a.r1}（步长 {a.stride}，实取 {len(sel)} 张）===")

    # 1) 来源库
    lc = collections.Counter()
    for c, _ in sel:
        for l in (cklib.get(c) or {"?"}):
            lc[l] += 1
    print("  来源库: " + "  ".join(f"{k}={v}({v / max(1, len(sel)):.0%})"
                                  for k, v in lc.most_common()))

    # 2) 归档归属
    pos = sum(1 for c, _ in sel if ck18.get(c) == {pp})
    neg = sum(1 for c, _ in sel if ck18.get(c) is not None and pp not in ck18[c])
    unk = sum(1 for c, _ in sel if ck18.get(c) is None)
    print(f"  归档归属: 确证 {pos}  冲突 {neg}  非归档 {unk}"
          f"（冲突率 {neg / max(1, pos + neg):.1%}）")

    # 3) 同框伙伴：同照片里其它身份的最高分
    partners = collections.Counter()
    for c, (s, i) in sel:
        idxs = [j for j in range(len(ck)) if ck[j] == c]
        for q in zs:
            if q == a.person:
                continue
            if max(zs[q][j] for j in idxs) > 0:   # logit > 0 = 该身份自报"更像正"
                partners[q] += 1
    if partners:
        print("  同框伙伴(logit>0 的照片数): " + "  ".join(
            f"{k}={v}({v / max(1, len(sel)):.0%})" for k, v in partners.most_common(a.top_partner)))
    else:
        print("  同框伙伴: 无（本段照片里没有其它身份拿到正分）")

    # 分来源库的归档确证情况 —— 09 与 07 的"确证率"差别是关键判据
    print("  分库确证率:")
    for l in sorted(lc):
        sub = [c for c, _ in sel if l in (cklib.get(c) or set())]
        p2 = sum(1 for c in sub if ck18.get(c) == {pp})
        n2 = sum(1 for c in sub if ck18.get(c) is not None and pp not in ck18[c])
        print(f"    lib={l}: {len(sub)} 张，归档确证 {p2} / 冲突 {n2}"
              f"（确证率 {p2 / max(1, len(sub)):.0%}）")


if __name__ == "__main__":
    main()

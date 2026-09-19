#!/usr/bin/env python3
"""诊断：18 归档的「单脸照片」到底能不能信？

背景：训练正样本 = 「18 归档里整张照片只有一张脸」的照片（认为这张脸必是本人）。
但对照锚点图发现 妈妈/我 的单脸照片疑是**乐仔**——最可能的原因是
**拍娃的父母**：父母在镜头后或侧脸没被检出，于是"单脸照片"里那唯一一张脸是孩子。

本脚本做留一法验证，完全不循环：
  对身份 P 的每张单脸归档脸 i，比较
    sim(i, P 的子中心[排除 i])  vs  sim(i, 乐仔(或指定对照)的子中心)
  若后者更高，说明这张"P 的单脸照片"更像别人 ⇒ 归档被污染。

用法: python diag_archive_purity.py [--src .../faces_*.npz]
"""
import argparse
import collections
import os
import sqlite3

os.nice(10)
import numpy as np

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB_DB = f"{ROOT}/19_统一相册库/library.db"
NPZ = f"{ROOT}/19_统一相册库/_faces_backup/faces_yunet_20260919.npz"
ALIAS = {"乐仔小时候": "乐仔", "妈妈小时候": "妈妈", "艳艳小时候": "艳艳"}
IDENT = ["乐仔", "艳艳", "妈妈", "我", "七月", "爸爸"]


def l2n(X, axis=1):
    X = np.asarray(X, dtype=np.float32)
    n = np.linalg.norm(X, axis=axis, keepdims=True)
    n[n < 1e-9] = 1.0
    return X / n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=NPZ)
    ap.add_argument("--ctrl", default="乐仔", help="对照身份（怀疑被误归到别人名下）")
    ap.add_argument("--k", type=int, default=5)
    a = ap.parse_args()

    d = np.load(a.src, allow_pickle=True)
    ck = d["ck"].astype(str)
    det = d["det"].astype(np.float32)
    box = d["box"].astype(np.float32)
    keep = (det >= 0.60) & (np.minimum(box[:, 2], box[:, 3]) >= 10)
    ck = ck[keep]
    X = l2n(np.hstack([l2n(d["mbf"].astype(np.float32)),
                       l2n(d["r50"].astype(np.float32))]))[keep]
    print(f"脸 {len(ck)}")

    con = sqlite3.connect(LIB_DB)
    arch = collections.defaultdict(set)
    for rel, c in con.execute("select rel, content_key from files where lib='18'"):
        arch[ALIAS.get(rel.split("/")[0], rel.split("/")[0])].add(c)
    con.close()

    nface = collections.Counter(ck)
    byck = collections.defaultdict(list)
    for i, c in enumerate(ck):
        byck[c].append(i)

    def singles(p):
        out = []
        for c in arch.get(p, ()):  # noqa: E501
            if nface.get(c) == 1 and c in byck:
                out.append(byck[c][0])
        return np.array(sorted(set(out)), dtype=int)

    ctrl = singles(a.ctrl)
    ctrl_c = l2n(X[ctrl].mean(axis=0, keepdims=True))[0] if len(ctrl) else None
    print(f"对照 {a.ctrl}: {len(ctrl)} 张单脸，中心已建\n")
    print(f"{'身份':<6}{'单脸数':>6}{'更像对照':>9}{'更像自己':>9}{'污染率':>8}   对照相似中位/自己相似中位")
    for p in IDENT:
        if p == a.ctrl:
            continue
        idx = singles(p)
        if len(idx) < 3:
            print(f"{p:<6}{len(idx):>6}     样本太少，跳过")
            continue
        sp = 0
        m_ctrl, m_own = [], []
        for i in idx:
            others = [j for j in idx if j != i]
            if not others:
                continue
            own_c = l2n(X[others].mean(axis=0, keepdims=True))[0]
            s_own = float(X[i] @ own_c)
            s_ctl = float(X[i] @ ctrl_c) if ctrl_c is not None else -1.0
            m_own.append(s_own); m_ctrl.append(s_ctl)
            if s_ctl > s_own:
                sp += 1
        n = len(m_own)
        print(f"{p:<6}{len(idx):>6}{sp:>9}{n-sp:>9}{sp/max(1,n):>7.1%}   "
              f"{np.median(m_ctrl):.3f} / {np.median(m_own):.3f}")


if __name__ == "__main__":
    main()

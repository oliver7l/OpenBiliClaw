#!/usr/bin/env python3
"""家庭成员多类判定模型（自训练版）——替代此前所有二分类/kNN/聚类判定。

设计
----
之前的教训（都已实测踩坑）：
  · 07 cluster 真值严重不纯：'妈妈' 簇 1587 张脸里混着乐仔等其他人，
    子簇分析/单人脸/尺寸分层都只能到 0.5~0.7 纯度，拿它当 gallery，
    09 里陌生幼童能打到 0.84 分。
  · 18_人物分组 又是旧模型判定的结果，与聚类互为因果地互相污染。
  · 逐人二分类（one-vs-all）不利用「家人互斥」结构，被噪声 gallery 拖垮
    （妈妈 CV recall@FPR1% 仅 0.20）。

本脚本的方案：
  1) 种子   = 单人脸照片 ∩ (07cluster ∪ 18目录)。照片里只有一张脸时，
              照片级标签才可信地等于人脸级标签 —— 这是全库唯一干净的
              人脸级真值来源。
  2) 模型   = 7 类 softmax 逻辑回归（mbf+r50 融合嵌入），利用家人互斥结构。
  3) 自训练 = 每轮对全库 3.7 万张脸预测，把 p>阈值 的高置信样本回填为
              伪标签种子重训（清除初始种子噪声 + 覆盖更多姿态/年龄）。
  4) 验证   = 初始种子分层留出 20% 不参与自训练，专测；报告混淆矩阵。

用法
----
    python build_family_classifier.py                # 训练+评估+写盘
    python build_family_classifier.py --rounds 2     # 少两轮自训练
    python build_family_classifier.py --eval-only    # 不写模型文件
"""
import argparse
import json
import sqlite3
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import KFold

ROOT = Path(__file__).resolve().parents[2]
LIB_DB = ROOT / "19_统一相册库" / "library.db"
P07_DB = ROOT / "07_相册" / "相册&视频备份" / "_photo_index" / "photo_index.db"
OUT = ROOT / "照片人物判定" / "family_clf_model.json"

FAMILY = ["乐仔", "乐仔小时候", "艳艳", "我", "妈妈", "七月", "爸爸"]
MERGE_GROUPS = {"乐仔小时候": "乐仔"}   # 评估时归并报告，但训练保留独立类

MIN_DET = 0.60
MIN_SIDE = 12
SEED = 42


# ---------------- 基础 ----------------
def l2n(X, axis=1):
    X = np.asarray(X, dtype=np.float32)
    n = np.linalg.norm(X, axis=axis, keepdims=True)
    n[n < 1e-9] = 1.0
    return X / n


def ok_quality(r):
    det, w, h = r[3], r[4], r[5]
    if det is None or det < MIN_DET:
        return False
    if w is not None and h is not None and min(w, h) < MIN_SIDE:
        return False
    return True


def load_faces():
    db = sqlite3.connect(str(LIB_DB))
    rows = db.execute(
        "SELECT face_id, content_key, lib, det_score, w, h, cluster, emb_mbf, emb_r50 "
        "FROM faces WHERE emb_mbf IS NOT NULL AND emb_r50 IS NOT NULL").fetchall()
    db.close()
    return rows


def build_matrices(faces):
    mbf = l2n(np.vstack([np.frombuffer(r[7], dtype=np.float32) for r in faces]))
    r50 = l2n(np.vstack([np.frombuffer(r[8], dtype=np.float32) for r in faces]))
    return {"mbf": mbf, "r50": r50, "fused": l2n(np.hstack([mbf, r50]))}


def cluster_map():
    db = sqlite3.connect(str(P07_DB))
    m = {int(c): n for c, n in db.execute(
        "SELECT cluster, name FROM persons WHERE name IS NOT NULL")}
    db.close()
    return m


def initial_seeds(faces, db, cmap):
    """单人脸照片 ∩ (07cluster ∪ 18目录)"""
    inv = {v: k for k, v in cmap.items()}
    qcnt = Counter(r[1] for r in faces if ok_quality(r))
    seeds = {}
    for p in FAMILY:
        cl = inv.get(p)
        ck18 = {r[0] for r in db.execute(
            "SELECT DISTINCT content_key FROM files WHERE lib='18' AND rel LIKE ?",
            (p + "/%",))}
        idx = [i for i, r in enumerate(faces) if ok_quality(r)
               and qcnt[r[1]] == 1
               and ((cl is not None and r[6] == cl and r[2] == "07")
                    or r[1] in ck18)]
        seeds[p] = np.array(sorted(idx), dtype=int)
    return seeds


def purity(X):
    p = X.mean(0)
    p /= (np.linalg.norm(p) + 1e-9)
    return float(np.median(X @ p))


def confusion(clf, X, y, names):
    pr = clf.predict(X)
    k = len(names)
    cm = np.zeros((k, k), int)
    for t, p in zip(y, pr):
        cm[t, p] += 1
    return cm


def print_cm(cm, names):
    print("      " + " ".join(f"{n[:4]:>6}" for n in names))
    for i, n in enumerate(names):
        print(f"{n:<6}" + " ".join(f"{cm[i,j]:>6}" for j in range(len(names))))


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3, help="自训练轮数")
    ap.add_argument("--pseudo-th", type=float, default=0.90,
                    help="伪标签概率门槛")
    ap.add_argument("--pseudo-cap", type=int, default=1500,
                    help="每类伪标签上限")
    ap.add_argument("--C", type=float, default=1.0)
    ap.add_argument("--eval-only", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    print("加载全库人脸 …", flush=True)
    faces = load_faces()
    M = build_matrices(faces)
    cmap = cluster_map()
    Xall = M["fused"]
    print(f"  {len(faces)} 张脸", flush=True)

    db = sqlite3.connect(str(LIB_DB))
    seeds = initial_seeds(faces, db, cmap)
    db.close()
    print("初始种子（单人脸照片）：")
    for p in FAMILY:
        print(f"  {p:<8}{len(seeds[p])}")

    # 分层留出 20% 做验证（不参与自训练）
    rng = np.random.default_rng(SEED)
    tr_list, Xval_list, y_list = [], [], []
    for ci, p in enumerate(FAMILY):
        s = seeds[p]
        perm = rng.permutation(len(s))
        nv = max(10, int(len(s) * 0.2))
        tr_list.append(s[perm[nv:]])
        Xval_list.append(s[perm[:nv]])
        y_list += [ci] * (len(s) - nv)
    val_idx = np.concatenate(Xval_list)
    tr_idx = np.concatenate(tr_list)
    y_tr = np.array(y_list)
    yval = np.concatenate([[i] * len(v) for i, v in enumerate(Xval_list)])

    Xtr0, ytr0 = Xall[tr_idx], y_tr
    Xval = Xall[val_idx]

    # ---- 初始模型 ----
    clf = LogisticRegression(C=args.C, max_iter=3000).fit(Xtr0, ytr0)
    cm = confusion(clf, Xval, yval, FAMILY)
    acc0 = cm.trace() / cm.sum()
    print(f"\n初始模型 留出验证准确率: {acc0:.3f}")
    print_cm(cm, FAMILY)

    # ---- 自训练 ----
    pseudo = []   # (idx, class) 累计
    hist = [{"round": 0, "acc": round(acc0, 4), "n_seed": len(tr_idx),
             "n_pseudo": 0}]
    for rnd in range(1, args.rounds + 1):
        prob = clf.predict_proba(Xall)
        new_idx, new_y = [], []
        used = set(tr_idx.tolist()) | set(val_idx.tolist()) | {i for i, _ in pseudo}
        for ci, p in enumerate(FAMILY):
            order = np.argsort(-prob[:, ci])
            cand = [i for i in order
                    if prob[i, ci] >= args.pseudo_th and i not in used]
            cand = cand[:args.pseudo_cap]
            new_idx += cand
            new_y += [ci] * len(cand)
        if not new_idx:
            print(f"轮{rnd}: 无新伪标签，提前收敛")
            break
        pseudo += list(zip(new_idx, new_y))
        all_i = np.array([i for i, _ in pseudo] + tr_idx.tolist())
        all_y = np.array([c for _, c in pseudo] + y_tr.tolist())
        clf = LogisticRegression(C=args.C, max_iter=3000).fit(Xall[all_i], all_y)
        cm = confusion(clf, Xval, yval, FAMILY)
        acc = cm.trace() / cm.sum()
        print(f"轮{rnd}: +伪标签 {len(new_idx)} (累计 {len(pseudo)}) "
              f"→ 留出验证 {acc:.3f}")
        print_cm(cm, FAMILY)
        hist.append({"round": rnd, "acc": round(acc, 4),
                     "n_seed": len(all_i), "n_pseudo": len(pseudo)})
        if acc <= hist[-2]["acc"]:
            print("  留出验证未提升，停止自训练")
            break

    # ---- 归并「乐仔小时候→乐仔」后的准确率 ----
    merge_map = [0 if FAMILY[i] in MERGE_GROUPS else i for i in range(len(FAMILY))]
    uniq = sorted(set(merge_map))
    remap = {c: i for i, c in enumerate(uniq)}
    pr = clf.predict(Xval)
    ym = np.array([remap[merge_map[int(t)]] for t in yval])
    pm = np.array([remap[merge_map[int(t)]] for t in pr])
    acc_merged = float((ym == pm).mean())
    merged_names = [FAMILY[c] for c in uniq]
    print(f"\n归并乐仔小时候→乐仔后 留出验证: {acc_merged:.3f} "
          f"({', '.join(merged_names)})")

    # ---- 全库预测分布（定阈值参考）----
    prob_all = clf.predict_proba(Xall)
    maxp = prob_all.max(axis=1)
    for t in (0.5, 0.7, 0.9, 0.97, 0.99):
        n = int((maxp >= t).sum())
        print(f"  全库 max-prob>={t}: {n} 张脸 "
              f"({n/len(maxp):.1%})")

    if args.eval_only:
        return

    # ---- 写盘 ----
    all_i = np.array([i for i, _ in pseudo] + tr_idx.tolist())
    all_y = np.array([c for _, c in pseudo] + y_tr.tolist())
    final_clf = LogisticRegression(C=args.C, max_iter=3000).fit(Xall[all_i], all_y)
    model = {
        "type": "multiclass_logistic_regression",
        "version": 1,
        "classes": FAMILY,
        "space": "fused",           # l2n(concat(l2n(mbf), l2n(r50))) 1024 维
        "dim": int(Xall.shape[1]),
        "coef": [[round(float(x), 8) for x in row]
                 for row in final_clf.coef_],
        "intercept": [round(float(x), 8) for x in final_clf.intercept_],
        "pseudo_th": args.pseudo_th,
        "n_seed_initial": int(len(tr_idx)),
        "n_pseudo": int(len(pseudo)),
        "eval": {
            "holdout_acc": round(float(acc), 4),
            "holdout_acc_merged": round(acc_merged, 4),
            "confusion": cm.tolist(),
            "history": hist,
            "maxprob_coverage": {str(t): int((maxp >= t).sum())
                                 for t in (0.5, 0.7, 0.9, 0.97, 0.99)},
        },
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "source": "单人脸照片种子 + 7类softmax + 自训练",
    }
    OUT.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    print(f"\n模型已写入 {OUT.relative_to(ROOT)} "
          f"({OUT.stat().st_size/1024:.0f} KB)，用时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

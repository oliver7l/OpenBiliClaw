#!/usr/bin/env python3
"""判定模型横评：LR(现役) vs LDA / RBF-SVM / RF / GBDT / MLP(torch) / KNN / 中心-NN。

评估协议要点（否则数字全是虚高，分不出差异）：
  1. 正样本只用「18 归档的单人脸照片」——照片级真值可靠，不做自举扩充
     （自举会把两端样本挑出来，CV AUC 必然 0.99+，那是泄漏不是能力）
  2. 负样本用「类中心余弦 top-K 难负 + 09 陌生人随机」——难负用与模型无关的
     方式挑选，保证对每个模型都公平
  3. 分层 5-fold，收集 OOF 概率后统一算 AUC / TPR@FPR=1% / TPR@FPR=0.1%
  4. 最终指标看「严格区 TPR@FPR=0.1%」——这才是实际部署用的工作点

用法:
  python bench_clf.py             # 全量跑
  python bench_clf.py --person 妈妈
"""
import os
import sys
import json
import time
import sqlite3
import argparse
import collections

os.nice(10)
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
from sklearn.svm import SVC
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.neighbors import KNeighborsClassifier
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import roc_auc_score

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB_DB = f"{ROOT}/19_统一相册库/library.db"
OUT = f"{ROOT}/照片人物判定/_bench_clf.json"

FAMILY = ["乐仔", "艳艳", "妈妈", "我", "七月", "爸爸", "乐仔小时候"]
MUTEX = {"乐仔": "乐仔小时候", "乐仔小时候": "乐仔"}
N_HARD = 1500          # 类中心余弦 top-K 难负
N_RAND09 = 3000        # 09 陌生人随机
N_HOME = 1500          # 07/08 家庭库随机
MIN_SIDE = 10
N_FOLD = 5


def l2n(X):
    n = np.linalg.norm(X, axis=1, keepdims=True)
    return X / np.maximum(n, 1e-9)


def load():
    con = sqlite3.connect(LIB_DB)
    rows = con.execute(
        "select rowid, content_key, det_score, w, h, emb_mbf, emb_r50 from faces").fetchall()
    ck_person = {}
    for rel, ck in con.execute("select rel, content_key from files where lib='18'"):
        ck_person.setdefault(ck, rel.split("/")[0])
    ck_lib = {}
    for ck, lib in con.execute("select content_key, lib from files where is_primary=1"):
        ck_lib.setdefault(ck, lib)
    con.close()

    rid, cks, Xm, Xr = [], [], [], []
    for r, ck, det, w, h, mb, r5 in rows:
        if mb is None or r5 is None:
            continue
        if det is not None and det < 0.60:
            continue
        if w and h and min(w, h) < MIN_SIDE:
            continue
        rid.append(r); cks.append(ck)
        Xm.append(np.frombuffer(mb, dtype=np.float32))
        Xr.append(np.frombuffer(r5, dtype=np.float32))
    Xm, Xr = l2n(np.vstack(Xm)), l2n(np.vstack(Xr))
    feats = {"mbf": Xm, "r50": Xr, "fused": l2n(np.hstack([Xm, Xr]))}
    return (np.array(rid), np.array(cks, dtype=object), feats, ck_person, ck_lib)


def build_set(person, rid, cks, feats, ck_person, ck_lib):
    """返回 (正样本 idx, 负样本 idx)。正样本=该人归档的单人脸照片。"""
    # 每张 18 归档照片里的脸数
    nface = collections.Counter(cks)
    mine = {ck for ck, p in ck_person.items() if p == person}
    pos = [i for i, ck in enumerate(cks)
           if ck in mine and nface[ck] == 1 and ck_lib.get(ck) != "09"]
    other = {ck for ck, p in ck_person.items()
             if p != person and p != MUTEX.get(person)}
    X = feats["fused"]
    center = l2n(X[pos].mean(axis=0, keepdims=True))[0]
    sim = X @ center

    pool = np.array([i for i, ck in enumerate(cks) if ck not in mine], dtype=int)
    order = np.argsort(-sim[pool])
    hard = pool[order[:N_HARD]]
    rng = np.random.default_rng(7)
    idx09 = np.array([i for i in pool if ck_lib.get(cks[i]) == "09"], dtype=int)
    idxhome = np.array([i for i in pool if ck_lib.get(cks[i]) in ("07", "08")], dtype=int)
    other_idx = np.array([i for i in pool if cks[i] in other], dtype=int)
    parts = [hard, other_idx]
    if len(idx09):
        parts.append(rng.choice(idx09, size=min(N_RAND09, len(idx09)), replace=False))
    if len(idxhome):
        parts.append(rng.choice(idxhome, size=min(N_HOME, len(idxhome)), replace=False))
    neg = np.unique(np.concatenate(parts))
    return np.array(pos, dtype=int), neg


# ---------------------------------------------------------------- 模型工厂

class TorchMLP:
    """torch 多层感知机：BN + Dropout，比 sklearn 的 MLP 更能抗过拟合。
    输出概率，接口与 sklearn 一致。"""

    def __init__(self, dim, hidden=(256, 64), epochs=60, lr=3e-3, seed=0):
        self.dim, self.hidden, self.epochs, self.lr, self.seed = \
            dim, hidden, epochs, lr, seed

    def fit(self, X, y):
        import torch
        import torch.nn as nn
        torch.manual_seed(self.seed)
        rng = np.random.default_rng(self.seed)
        npos = int(y.sum()); nneg = len(y) - npos
        w = np.where(y == 1, len(y) / max(2 * npos, 1), len(y) / max(2 * nneg, 1))
        layers, d = [], self.dim
        for h in self.hidden:
            layers += [nn.Linear(d, h), nn.BatchNorm1d(h), nn.ReLU(), nn.Dropout(0.3)]
            d = h
        layers.append(nn.Linear(d, 1))
        net = nn.Sequential(*layers)
        opt = torch.optim.Adam(net.parameters(), lr=self.lr, weight_decay=1e-4)
        Xt = torch.tensor(np.asarray(X, dtype=np.float32))
        yt = torch.tensor(np.asarray(y, dtype=np.float32)).unsqueeze(1)
        wt = torch.tensor(w, dtype=np.float32).unsqueeze(1)
        n = len(Xt)
        net.train()
        for ep in range(self.epochs):
            perm = torch.tensor(rng.permutation(n))
            for s in range(0, n, 512):
                b = perm[s:s + 512]
                opt.zero_grad()
                z = net(Xt[b])
                loss = nn.functional.binary_cross_entropy_with_logits(z, yt[b], weight=wt[b])
                loss.backward()
                opt.step()
        net.eval()
        self.net = net
        return self

    def predict_proba(self, X):
        import torch
        with torch.no_grad():
            z = self.net(torch.tensor(np.asarray(X, dtype=np.float32))).squeeze(1)
            p = torch.sigmoid(z).numpy()
        return np.column_stack([1 - p, p])


class CentroidNN:
    """类中心余弦 + Platt 校准（现役备选，作为非线性-free 的下界参照）。"""

    def __init__(self):
        pass

    def fit(self, X, y):
        self.c = l2n(X[y == 1].mean(axis=0, keepdims=True))[0]
        s = X @ self.c
        lr = LogisticRegression(C=1.0, max_iter=2000)
        lr.fit(s.reshape(-1, 1), y)
        self.a = float(lr.coef_[0][0]); self.b = float(lr.intercept_[0])
        return self

    def predict_proba(self, X):
        from scipy.special import expit
        p = expit((X @ self.c) * self.a + self.b)
        return np.column_stack([1 - p, p])


def make_models(dim):
    m = {}
    m["LR(现役)"] = lambda: LogisticRegression(C=1.0, max_iter=5000)
    m["LR(C=0.03)"] = lambda: LogisticRegression(C=0.03, max_iter=5000)
    m["LDA(shrink)"] = lambda: LinearDiscriminantAnalysis(solver="lsqr", shrinkage="auto")
    m["中心NN+Platt"] = lambda: CentroidNN()
    m["KNN(k=5)"] = lambda: KNeighborsClassifier(n_neighbors=5, metric="cosine")
    m["RF(400)"] = lambda: RandomForestClassifier(n_estimators=400, min_samples_leaf=2,
                                                  n_jobs=-1, random_state=0)
    m["GBDT(200)"] = lambda: GradientBoostingClassifier(n_estimators=200, max_depth=3,
                                                        random_state=0)
    m["SVM-RBF(子采样)"] = lambda: SVC(C=4.0, kernel="rbf", gamma="scale", random_state=0)
    m["MLP(256,64)"] = lambda: TorchMLP(dim, (256, 64))
    m["MLP(512,128,32)"] = lambda: TorchMLP(dim, (512, 128, 32), epochs=80)
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        m["HistGB(300)"] = lambda: HistGradientBoostingClassifier(
            max_iter=300, max_depth=3, random_state=0)
    except ImportError:
        pass
    return m


def subsample_for_svm(Xi, yi, n_neg=3000, seed=0):
    """SVC fit 是 O(n²)，probability 再乘 6 —— 全量 1.2 万样本跑不完。
    保留全部正样本，负样本均匀下采样到 n_neg。"""
    rng = np.random.default_rng(seed)
    pos = np.where(yi == 1)[0]
    neg = np.where(yi == 0)[0]
    if len(neg) > n_neg:
        neg = rng.choice(neg, size=n_neg, replace=False)
    idx = np.concatenate([pos, neg])
    return Xi[idx], yi[idx]


# ---------------------------------------------------------------- 评估

def tpr_at_fpr(y, s, fpr_target):
    """在给定 FPR 预算下的 TPR。"""
    order = np.argsort(-s)
    y = np.asarray(y)[order]
    n_neg = int((y == 0).sum())
    if n_neg == 0:
        return 0.0
    budget = max(1, int(np.floor(fpr_target * n_neg)))
    fp, tp = 0, 0
    for v in y:
        if v == 1:
            tp += 1
        else:
            fp += 1
            if fp > budget:
                break
    n_pos = int((y == 1).sum())
    return tp / n_pos if n_pos else 0.0


def oof_proba(make, X, y, folds=N_FOLD, seed=0, svm_sub=False):
    skf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    s = np.zeros(len(y), dtype=np.float64)
    for tr, te in skf.split(X, y):
        if len(np.unique(y[tr])) < 2:
            continue
        Xtr, ytr = X[tr], y[tr]
        if svm_sub:
            Xtr, ytr = subsample_for_svm(Xtr, ytr)
        m = make()
        try:
            m.fit(Xtr, ytr)
            # AUC/TPR 只需要连续分数：decision_function 比 probability 快 6 倍
            #（SVC probability=True 内部还要做 5 折 Platt 校准，1.2 万样本上卡死）
            if hasattr(m, "decision_function") and not hasattr(m, "net"):
                s[te] = m.decision_function(X[te])
            else:
                s[te] = m.predict_proba(X[te])[:, 1]
        except Exception as e:
            print(f"      ! 折内失败 {type(e).__name__}: {str(e)[:60]}")
            return None
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--person", default=None)
    ap.add_argument("--feature", default="fused")
    args = ap.parse_args()

    t0 = time.time()
    rid, cks, feats, ck_person, ck_lib = load()
    print(f"加载 {len(rid)} 张脸，特征 {args.feature}，用时 {time.time() - t0:.0f}s\n")

    persons = [args.person] if args.person else FAMILY
    X = feats[args.feature]
    results = {}

    print(f"{'人':<10}{'模型':<16}{'AUC':>8}{'TPR@1%':>9}{'TPR@0.1%':>10}{'正/负':>12}")
    print("-" * 66)

    for p in persons:
        pos, neg = build_set(p, rid, cks, feats, ck_person, ck_lib)
        if len(pos) < 12:
            print(f"{p:<10}正样本仅 {len(pos)}，跳过")
            continue
        idx = np.concatenate([pos, neg])
        Xi, yi = X[idx], np.concatenate([np.ones(len(pos)), np.zeros(len(neg))])
        models = make_models(Xi.shape[1])
        rows = []
        for tag, make in models.items():
            t_m = time.time()
            s = oof_proba(make, Xi, yi, svm_sub=tag.startswith("SVM"))
            if s is None:
                continue
            try:
                auc = roc_auc_score(yi, s)
            except Exception:
                auc = float("nan")
            t1 = tpr_at_fpr(yi, s, 0.01)
            t01 = tpr_at_fpr(yi, s, 0.001)
            rows.append((tag, auc, t1, t01))
            print(f"{p:<10}{tag:<16}{auc:>8.4f}{t1:>9.3f}{t01:>10.3f}"
                  f"{f'{len(pos)}/{len(neg)}':>12}")
        results[p] = dict(n_pos=int(len(pos)), n_neg=int(len(neg)),
                          rows=[dict(model=t, auc=a, tpr1=b, tpr01=c) for t, a, b, c in rows])
        print("-" * 66)

    json.dump(results, open(OUT, "w"), ensure_ascii=False, indent=1)

    # 汇总：各模型在所有人身上的平均
    print("\n=== 汇总（各人平均）===")
    agg = collections.defaultdict(list)
    for p, d in results.items():
        for r in d["rows"]:
            agg[r["model"]].append(r)
    print(f"{'模型':<16}{'AUC':>9}{'TPR@1%':>9}{'TPR@0.1%':>10}{'人数':>6}")
    out = []
    for tag, rs in agg.items():
        a = np.mean([r["auc"] for r in rs])
        b = np.mean([r["tpr1"] for r in rs])
        c = np.mean([r["tpr01"] for r in rs])
        out.append((c, b, a, tag, len(rs)))
    for c, b, a, tag, n in sorted(out, reverse=True):
        print(f"{tag:<16}{a:>9.4f}{b:>9.3f}{c:>10.3f}{n:>6}")
    print(f"\n（按 TPR@FPR=0.1% 降序 —— 这是实际部署的工作点）")
    print(f"结果已存 {OUT}   总用时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()

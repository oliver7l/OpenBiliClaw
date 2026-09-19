#!/usr/bin/env python3
"""集成实验台：多嵌入 × 多子模型 × 元特征 → 集成，评测「谁真的更准」。

与 bench_clf.py 的区别：
  * 数据从 _faces_backup/*.npz 读（不依赖正在重扫的 faces 表），可随时离线重跑
  * 基模型跨**多种特征**（mbf / r50 / fused / fused+PCA）——同一模型不同特征视作不同子模型
  * 额外算元特征：检测分、脸尺寸、幼童概率、照片上下文（同框人脸数/同框最高分）
  * 集成方式：rank 平均 / z 平均 / stacking(OOF 喂元学习器)
  * 除 OOF 指标外，额外给「全库部署指标」：precision / recall@归档（最贴近实际体感）

评估协议（沿用 bench_clf，防虚高）：
  正样本 = 18 归档单人脸照片（照片级真值，不自举）
  负样本 = 类中心余弦 top-K 难负 + 09 随机 + 07/08 随机（挑法与模型无关，对所有模型公平）
  分层 5-fold 收集 OOF，主指标 TPR@FPR=0.1%（实际部署工作点）

用法:
  python bench_ens.py                 # 全量 7 人
  python bench_ens.py --person 乐仔
  python bench_ens.py --src /path/to.npz
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
from sklearn.ensemble import (RandomForestClassifier, GradientBoostingClassifier,
                              HistGradientBoostingClassifier)
from sklearn.neighbors import KNeighborsClassifier
from sklearn.random_projection import GaussianRandomProjection
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import roc_auc_score

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB_DB = f"{ROOT}/19_统一相册库/library.db"
# 嵌入来源：重扫后必须指向新 npz，否则审核工具看到的是旧检测器的坐标与嵌入。
# 用环境变量覆盖（不硬改常量，旧备份仍可随时回滚比对）。
NPZ = os.environ.get("OBC_FACES_NPZ") or \
    f"{ROOT}/19_统一相册库/_faces_backup/faces_yunet_20260919.npz"
CHILD = f"{ROOT}/照片人物判定/child_model.json"
OUT = f"{ROOT}/照片人物判定/_bench_ens.json"

FAMILY = ["乐仔", "艳艳", "妈妈", "我", "七月", "爸爸", "乐仔小时候"]
MUTEX = {"乐仔": "乐仔小时候", "乐仔小时候": "乐仔"}
N_HARD, N_RAND09, N_HOME = 1500, 3000, 1500
MIN_SIDE, N_FOLD = 10, 5


def l2n(X):
    return X / np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)


# ------------------------------------------------------------------ 数据
def load(src=NPZ):
    d = np.load(src, allow_pickle=True)
    ck = d["ck"].astype(object)
    lib = d["lib"].astype(object)
    det = d["det"].astype(np.float32)
    box = d["box"].astype(np.float32)
    Xm, Xr = l2n(d["mbf"].astype(np.float32)), l2n(d["r50"].astype(np.float32))

    keep = (det >= 0.60) & (np.minimum(box[:, 2], box[:, 3]) >= MIN_SIDE)
    ck, lib, det, box, Xm, Xr = ck[keep], lib[keep], det[keep], box[keep], Xm[keep], Xr[keep]

    con = sqlite3.connect(LIB_DB)
    ck_person = {}
    for rel, c in con.execute("select rel, content_key from files where lib='18'"):
        ck_person.setdefault(c, rel.split("/")[0])
    ck_lib = {}
    for c, l in con.execute("select content_key, lib from files where is_primary=1"):
        ck_lib.setdefault(c, l)
    con.close()

    feats = {"mbf": Xm, "r50": Xr, "fused": l2n(np.hstack([Xm, Xr]))}
    # 随机投影代替 PCA：零拟合成本、不吃内存，且对各折无泄漏
    #（PCA 在 3.5w×1024 上做 full SVD 会把内存打满，实测直接被 OOM kill）
    rp = GaussianRandomProjection(n_components=256, random_state=0)
    feats["rp256"] = l2n(rp.fit_transform(feats["fused"]).astype(np.float32))

    # 元特征（不依赖 person，全库一次算好）
    child = json.load(open(CHILD))
    # 幼童模型自报 feature：train_child 在 {mbf,r50,fused} 里挑 CV AUC 最高的（打平取 mbf）。
    # 硬编 feats["fused"] 会在模型选 mbf 时维度不匹配（512 vs 1024）—— 已踩过。
    _cf = str(child.get("feature") or "fused")
    p_child = 1.0 / (1.0 + np.exp(-np.clip(
        feats[_cf] @ np.array(child["coef"], dtype=np.float32) + child["intercept"], -30, 30)))
    side = np.maximum(box[:, 2], box[:, 3])
    meta_static = np.column_stack([det, np.log1p(side), p_child]).astype(np.float32)
    return dict(ck=ck, lib=lib, det=det, box=box, feats=feats,
                meta_static=meta_static, ck_person=ck_person, ck_lib=ck_lib)


def build_set(person, D, feat_key="fused", hard_only=False, neg09=False):
    """hard_only/neg09：把负样本池限制成「最难的那批」。
    neg09=True 时负样本只取 09 幼儿园库（同龄同性别的小孩）——
    这才是乐仔判定真正的对手；掺进成人负样本会让所有指标虚高。"""
    ck, X = D["ck"], D["feats"][feat_key]
    nface = collections.Counter(ck)
    mine = {c for c, p in D["ck_person"].items() if p == person}
    pos = np.array([i for i, c in enumerate(ck)
                    if c in mine and nface[c] == 1 and D["ck_lib"].get(c) != "09"], dtype=int)
    other = {c for c, p in D["ck_person"].items()
             if p != person and p != MUTEX.get(person)}
    center = l2n(X[pos].mean(axis=0, keepdims=True))[0]
    sim = X @ center
    pool = np.array([i for i, c in enumerate(ck) if c not in mine], dtype=int)
    hard = pool[np.argsort(-sim[pool])[:N_HARD]]
    rng = np.random.default_rng(7)
    idx09 = np.array([i for i in pool if D["ck_lib"].get(ck[i]) == "09"], dtype=int)
    idxhome = np.array([i for i in pool if D["ck_lib"].get(ck[i]) in ("07", "08")], dtype=int)
    oidx = np.array([i for i in pool if ck[i] in other], dtype=int)
    if neg09:
        idx09 = np.array([i for i in pool if D["ck_lib"].get(ck[i]) == "09"], dtype=int)
        if len(idx09) == 0:
            return pos, np.array([], dtype=int)
        return pos, np.unique(np.concatenate([
            np.array([i for i in idx09 if ck[i] not in mine], dtype=int)]))[:12000]
    parts = [hard, oidx]
    if len(idx09):
        parts.append(rng.choice(idx09, size=min(N_RAND09, len(idx09)), replace=False))
    if len(idxhome):
        parts.append(rng.choice(idxhome, size=min(N_HOME, len(idxhome)), replace=False))
    return pos, np.unique(np.concatenate(parts))


def ctx_features(person, D, idx, feat_key="fused"):
    """照片上下文特征：同框人数、同框其他脸与该类中心的最高相似。
    部署时同样可算（一张照片一起打分），不是泄漏。"""
    ck, X = D["ck"], D["feats"][feat_key]
    mine = {c for c, p in D["ck_person"].items() if p == person}
    pos0 = np.array([i for i, c in enumerate(ck)
                     if c in mine and collections.Counter(ck)[c] == 1], dtype=int)
    center = l2n(X[pos0].mean(axis=0, keepdims=True))[0]
    sim = X @ center
    byck = collections.defaultdict(list)
    for i, c in enumerate(ck):
        byck[c].append(i)
    n = len(idx)
    nf = np.zeros(n, dtype=np.float32)
    mx = np.zeros(n, dtype=np.float32)
    for k, i in enumerate(idx):
        g = byck[ck[i]]
        nf[k] = len(g)
        others = [j for j in g if j != i]
        mx[k] = max([sim[j] for j in others], default=0.0)
    return np.column_stack([np.log1p(nf), mx]).astype(np.float32)


# ------------------------------------------------------------------ 模型
class TorchMLP:
    def __init__(self, dim, hidden=(256, 64), epochs=60, lr=3e-3, seed=0):
        self.dim, self.hidden, self.epochs, self.lr, self.seed = dim, hidden, epochs, lr, seed

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
        import torch as _t
        Xt = _t.tensor(np.asarray(X, dtype=np.float32), dtype=_t.float32)
        yt = _t.tensor(np.asarray(y, dtype=np.float32), dtype=_t.float32).unsqueeze(1)
        wt = _t.tensor(w, dtype=_t.float32).unsqueeze(1)
        net.train()
        for _ in range(self.epochs):
            perm = torch.tensor(rng.permutation(len(Xt)), dtype=torch.long)
            for s in range(0, len(Xt), 512):
                b = perm[s:s + 512]
                opt.zero_grad()
                z = net(Xt[b])
                nn.functional.binary_cross_entropy_with_logits(
                    z, yt[b], weight=wt[b]).backward()
                opt.step()
        net.eval()
        self.net = net
        return self

    def predict_proba(self, X):
        import torch
        with torch.no_grad():
            p = torch.sigmoid(self.net(
                torch.tensor(np.asarray(X, dtype=np.float32),
                             dtype=torch.float32)).squeeze(1)).numpy()
        return np.column_stack([1 - p, p])


class MultiCenter:
    """多中心最近邻：一个人的脸在嵌入空间里往往不是一个球（年龄/发型/光照多模态），
    单取一个均值中心会把跨年龄段的正样本推远。把正样本 k-means 成 k 个子中心，
    取「与最近子中心的余弦」作分数，再 Platt 校准。"""

    def __init__(self, k=5, seed=0):
        self.k, self.seed = k, seed

    def fit(self, X, y):
        from sklearn.cluster import KMeans
        P = X[y == 1]
        k = max(1, min(self.k, len(P) // 10))
        if k == 1:
            C = l2n(P.mean(axis=0, keepdims=True))
        else:
            km = KMeans(n_clusters=k, n_init=5, random_state=self.seed).fit(P)
            C = l2n(km.cluster_centers_)
        self.C = C
        s = (X @ C.T).max(axis=1)
        lr = LogisticRegression(C=1.0, max_iter=2000).fit(s.reshape(-1, 1), y)
        self.a, self.b = float(lr.coef_[0][0]), float(lr.intercept_[0])
        return self

    def predict_proba(self, X):
        from scipy.special import expit
        p = expit((X @ self.C.T).max(axis=1) * self.a + self.b)
        return np.column_stack([1 - p, p])


class CentroidNN:
    def fit(self, X, y):
        self.c = l2n(X[y == 1].mean(axis=0, keepdims=True))[0]
        s = X @ self.c
        lr = LogisticRegression(C=1.0, max_iter=2000).fit(s.reshape(-1, 1), y)
        self.a, self.b = float(lr.coef_[0][0]), float(lr.intercept_[0])
        return self

    def predict_proba(self, X):
        from scipy.special import expit
        p = expit((X @ self.c) * self.a + self.b)
        return np.column_stack([1 - p, p])


def score(m, X):
    if hasattr(m, "net"):
        import torch
        with torch.no_grad():
            return torch.sigmoid(m.net(
                torch.tensor(np.asarray(X, dtype=np.float32),
                             dtype=torch.float32)).squeeze(1)).numpy()
    if hasattr(m, "decision_function"):
        return m.decision_function(X)
    return m.predict_proba(X)[:, 1]


# 子模型清单：(名字, 特征key, 工厂)
def base_specs():
    S = []
    S.append(("LR/mbf", "mbf", lambda d: LogisticRegression(C=1.0, max_iter=5000)))
    S.append(("LR/r50", "r50", lambda d: LogisticRegression(C=1.0, max_iter=5000)))
    S.append(("LR/fused", "fused", lambda d: LogisticRegression(C=1.0, max_iter=5000)))
    S.append(("中心NN/fused", "fused", lambda d: CentroidNN()))
    S.append(("多中心k5/fused", "fused", lambda d: MultiCenter(5)))
    S.append(("多中心k5/mbf", "mbf", lambda d: MultiCenter(5)))
    S.append(("多中心k5/r50", "r50", lambda d: MultiCenter(5)))
    S.append(("LDA/fused", "fused", lambda d: LinearDiscriminantAnalysis(solver="lsqr", shrinkage="auto")))
    S.append(("KNN/fused", "fused", lambda d: KNeighborsClassifier(n_neighbors=5, metric="cosine")))
    S.append(("HistGB/rp256", "rp256",
              lambda d: HistGradientBoostingClassifier(max_iter=150, max_depth=3, random_state=0)))
    S.append(("MLP/fused", "fused", lambda d: TorchMLP(d, (256, 64))))
    return S


def platt_fit(s, y):
    """分数 → logit 的线性校准（a*s+b）。

    为什么必须校准：集成的输入若用「按批次现算的 z-score」，训练集（90% 难负）
    和全库（绝大多数是易负）的均值/方差完全不同，同一份权重套到全库上阈值就漂了
    ——实测会全库只判正 36 张。Platt 的 a、b 在 OOF 上学定后固定不变，
    训练集与全库共用一套变换，尺度才可比。"""
    lr = LogisticRegression(C=1.0, max_iter=2000).fit(np.asarray(s).reshape(-1, 1), y)
    return float(lr.coef_[0][0]), float(lr.intercept_[0])


def platt_apply(s, ab):
    return np.asarray(s) * ab[0] + ab[1]


def oof_scores(specs, D, idx, y, feats_extra=None):
    """返回 ((K, n) 的 OOF 分数矩阵, 每行的 Platt 系数)。"""
    n = len(y)
    S = np.zeros((len(specs), n), dtype=np.float64)
    skf = StratifiedKFold(N_FOLD, shuffle=True, random_state=0)
    folds = list(skf.split(np.zeros(n), y))
    for k, (name, fkey, mk) in enumerate(specs):
        X = D["feats"][fkey][idx]
        if feats_extra is not None:
            X = np.hstack([X, feats_extra])
        t = time.time()
        ok = True
        for tr, te in folds:
            if len(np.unique(y[tr])) < 2:
                ok = False
                break
            try:
                m = mk(X.shape[1]); m.fit(X[tr], y[tr])
                S[k, te] = score(m, X[te])
            except Exception as e:
                print(f"    ! {name} 折内失败 {type(e).__name__}: {str(e)[:50]}")
                ok = False
                break
        if not ok:
            S[k, :] = np.nan
        print(f"    {name:<20} {time.time() - t:5.1f}s" + ("" if ok else "  (失败)"))
    ab = [platt_fit(S[k], y) if not np.isnan(S[k, 0]) else (1.0, 0.0)
          for k in range(len(specs))]
    return S, ab


def tpr_at_fpr(y, s, fpr_target):
    order = np.argsort(-s)
    y = np.asarray(y)[order]
    n_neg = int((y == 0).sum()); n_pos = int((y == 1).sum())
    if n_neg == 0 or n_pos == 0:
        return 0.0
    budget = max(1, int(np.floor(fpr_target * n_neg)))
    fp = tp = 0
    for v in y:
        if v == 1:
            tp += 1
        else:
            fp += 1
            if fp > budget:
                break
    return tp / n_pos


def ev(y, s):
    try:
        auc = roc_auc_score(y, s)
    except Exception:
        auc = float("nan")
    return dict(auc=float(auc), tpr1=float(tpr_at_fpr(y, s, 0.01)),
                tpr01=float(tpr_at_fpr(y, s, 0.001)))


def zscore(S):
    mu = np.nanmean(S, axis=1, keepdims=True)
    sd = np.nanstd(S, axis=1, keepdims=True) + 1e-9
    return (S - mu) / sd


def rank01(S):
    n = S.shape[1]
    R = np.zeros_like(S)
    for k in range(S.shape[0]):
        order = np.argsort(np.argsort(np.nan_to_num(S[k], nan=-1e9)))
        R[k] = order / max(n - 1, 1)
    return R


def stack_oof(S, y, meta, C=1.0):
    """stacking：基模型 OOF 分数（+元特征）→ 元学习器，再做一层 CV 取无偏 OOF。
    输入的 S 必须是**已 Platt 校准**的分数，不能是现算的 z-score（会尺度漂移）。"""
    X = np.nan_to_num(np.column_stack([S.T, meta]), nan=0.0)
    s = np.zeros(len(y))
    skf = StratifiedKFold(N_FOLD, shuffle=True, random_state=1)
    for tr, te in skf.split(X, y):
        m = LogisticRegression(C=C, max_iter=5000)
        m.fit(X[tr], y[tr])
        s[te] = m.decision_function(X[te])
    return s


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--person", default=None)
    ap.add_argument("--src", default=NPZ)
    ap.add_argument("--meta", choices=["none", "ctx", "all"], default="all",
                    help="none=纯嵌入; ctx=只加同框先验; all=再加 det/size/p_child")
    ap.add_argument("--no-meta", action="store_true", help="等价 --meta none")
    ap.add_argument("--fast", action="store_true", help="跳过 MLP/HistGB（慢，结果不变由集成决定）")
    ap.add_argument("--neg09", action="store_true",
                    help="负样本只用 09 幼儿园库（同龄小孩）——真实对手，指标会比混成人时低很多")
    a = ap.parse_args()

    t0 = time.time()
    D = load(a.src)
    print(f"加载 {len(D['ck'])} 张脸（{time.time() - t0:.0f}s）\n")
    persons = [a.person] if a.person else FAMILY
    specs = base_specs()
    if a.fast:
        specs = [s for s in specs if not s[0].startswith(("MLP", "HistGB"))]
    results = {}

    for person in persons:
        print(f"===== {person} =====")
        pos, neg = build_set(person, D, neg09=a.neg09)
        if len(pos) < 12:
            print(f"  正样本 {len(pos)}，跳过\n")
            continue
        idx = np.concatenate([pos, neg])
        y = np.concatenate([np.ones(len(pos)), np.zeros(len(neg))])
        print(f"  正 {len(pos)} 负 {len(neg)}")
        mode = "none" if a.no_meta else a.meta
        ctx = ctx_features(person, D, idx)
        meta = {"none": None,
                "ctx": ctx,
                "all": np.hstack([D["meta_static"][idx], ctx])}[mode]
        print(f"  元特征档位: {mode}")

        S, ab = oof_scores(specs, D, idx, y)
        good = ~np.isnan(S[:, 0])
        Sg = S[good]
        Sc = np.vstack([platt_apply(Sg[k], ab[k]) for k in np.where(good)[0]])
        names = [specs[k][0] for k in np.where(good)[0]]

        rows = []
        for k, nm in enumerate(names):
            r = ev(y, Sg[k]); r["model"] = nm; rows.append(r)
        # 集成
        M = meta if meta is not None else np.zeros((len(y), 0))
        ens = {}
        ens["集成·z平均"] = np.nanmean(zscore(Sc), axis=0)
        ens["集成·rank平均"] = np.nanmean(rank01(Sc), axis=0)
        ens["集成·stack(LR)"] = stack_oof(Sc, y, M)
        ens["集成·stack+rk"] = stack_oof(
            np.vstack([Sc, np.nanmean(rank01(Sc), axis=0)]), y, M)
        for nm, s in ens.items():
            r = ev(y, s); r["model"] = nm; rows.append(r)

        print(f"  {'模型':<20}{'AUC':>8}{'TPR@1%':>9}{'TPR@0.1%':>10}")
        for r in sorted(rows, key=lambda r: -r["tpr01"]):
            print(f"  {r['model']:<20}{r['auc']:>8.4f}{r['tpr1']:>9.3f}{r['tpr01']:>10.3f}")
        results[person] = dict(n_pos=int(len(pos)), n_neg=int(len(neg)), rows=rows)
        print()

    sfx = ("" if mode == "all" else f"_{mode}") + ("_neg09" if a.neg09 else "")
    out_path = OUT.replace(".json", f"{sfx}.json")
    json.dump(results, open(out_path, "w"), ensure_ascii=False, indent=1)

    print("=== 汇总（7 人平均，按 TPR@FPR=0.1% 降序）===")
    agg = collections.defaultdict(list)
    for p, d in results.items():
        for r in d["rows"]:
            agg[r["model"]].append(r)
    out = []
    for nm, rs in agg.items():
        out.append((np.mean([r["tpr01"] for r in rs]), np.mean([r["tpr1"] for r in rs]),
                    np.mean([r["auc"] for r in rs]), nm, len(rs)))
    print(f"{'模型':<20}{'AUC':>9}{'TPR@1%':>9}{'TPR@0.1%':>10}{'人数':>5}")
    for c, b, aa, nm, n in sorted(out, reverse=True):
        print(f"{nm:<20}{aa:>9.4f}{b:>9.3f}{c:>10.3f}{n:>5}")
    print(f"\n结果 → {out_path}   总用时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()

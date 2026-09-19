#!/usr/bin/env python3
"""集成判定的**生产**模型库。

与 `bench_ens.py` 的关系：bench_ens 是离线实验台（一次跑完就丢），本模块是
生产件——被 train_ens_prod.py 训练、被 apply_per_person.py 加载，因此
**两个脚本必须从同一个模块 import**，否则 pickle 出来的类名对不上会反序列化失败。

设计要点（都来自 2026-09-19 的实测结论）：
  * 单 LR 在 TPR@FPR=0.1% 只有 0.10 上下，集成能到 0.32 —— 但集成的输入
    必须**先 Platt 校准**（固定系数），不能用"按批次现算的 z-score"：
    训练集（难负占比高）与全库（绝大多数是易负）统计量不同，会尺度漂移。
  * 元特征里 **p_child 不进模型**（幼童概率会学成"幼童=本人"，对同龄同学零信息），
    它只作为 apply 阶段的跨类否决门。
  * 多中心（k-means 子中心）是为「同一人跨年龄/发型多模态」准备的：单均值中心
    会把跨年龄段正样本推远，实体证据见 乐仔小时候（分数上限只有乐仔的 1/50）。
"""
import numpy as np

# 身份清单是**跨脚本契约**：训练与部署必须用同一份、同一顺序，
# 否则「像不像别的家人」这组特征的列会错位（错位不报错，只是悄悄变差）。
IDENT = ["乐仔", "艳艳", "妈妈", "我", "七月", "爸爸"]

# 非跨身份元特征的列名与顺序（后面接 sim_<身份> 各列）。同样跨脚本契约。
META_KEY_BASE = ["ctx_n", "ctx_max", "det", "logside"]


def l2n(X, axis=1):
    X = np.asarray(X, dtype=np.float32)
    n = np.linalg.norm(X, axis=axis, keepdims=True)
    n[n < 1e-9] = 1.0
    return X / n


class CentroidNN:
    """余弦到类中心 → 单变量 Platt。便宜、稳，是集成里的"基准分"。"""

    def fit(self, X, y):
        from sklearn.linear_model import LogisticRegression
        self.c = l2n(X[y == 1].mean(axis=0, keepdims=True))[0]
        s = X @ self.c
        lr = LogisticRegression(C=1.0, max_iter=2000).fit(s.reshape(-1, 1), y)
        self.a, self.b = float(lr.coef_[0][0]), float(lr.intercept_[0])
        return self

    def predict_proba(self, X):
        from scipy.special import expit
        p = expit((X @ self.c) * self.a + self.b)
        return np.column_stack([1 - p, p])


class MultiCenter:
    """多中心最近邻：正样本 k-means 成 k 个子中心，取与最近子中心的余弦。"""

    def __init__(self, k=5, seed=0):
        self.k, self.seed = k, seed

    def fit(self, X, y):
        from sklearn.cluster import KMeans
        from sklearn.linear_model import LogisticRegression
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


class TorchMLP:
    """小 MLP：唯一能学非线性交互的子模型（嵌入维 1024~2048，两层足够）。"""

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
        Xt = torch.tensor(np.asarray(X, dtype=np.float32), dtype=torch.float32)
        yt = torch.tensor(np.asarray(y, dtype=np.float32), dtype=torch.float32).unsqueeze(1)
        wt = torch.tensor(w, dtype=torch.float32).unsqueeze(1)
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


def score(m, X):
    """统一打分：DecisionFunction 类（LR）走 decision_function，其余走 proba[:,1]。

    为什么不用 predict_proba 统一：LR 的 proba 在极端不平衡下会被压缩到 [0,1] 的
    窄带里，排序不变但 Platt 拟合的数值条件变差；decision_function 保留幅度。
    """
    if hasattr(m, "net"):
        import torch
        with torch.no_grad():
            return torch.sigmoid(m.net(
                torch.tensor(np.asarray(X, dtype=np.float32),
                             dtype=torch.float32)).squeeze(1)).numpy()
    if hasattr(m, "decision_function"):
        return m.decision_function(X)
    return m.predict_proba(X)[:, 1]


def base_specs():
    """(名字, 特征key, 工厂)。名字会进 pickle，改名等于作废已训练模型。"""
    from sklearn.linear_model import LogisticRegression
    from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
    from sklearn.neighbors import KNeighborsClassifier
    S = [
        ("LR/mbf", "mbf", lambda d: LogisticRegression(C=1.0, max_iter=5000)),
        ("LR/r50", "r50", lambda d: LogisticRegression(C=1.0, max_iter=5000)),
        ("LR/fused", "fused", lambda d: LogisticRegression(C=1.0, max_iter=5000)),
        ("中心NN/fused", "fused", lambda d: CentroidNN()),
        ("中心NN/mbf", "mbf", lambda d: CentroidNN()),
        ("多中心k5/fused", "fused", lambda d: MultiCenter(5)),
        ("多中心k5/mbf", "mbf", lambda d: MultiCenter(5)),
        ("多中心k5/r50", "r50", lambda d: MultiCenter(5)),
        ("LDA/fused", "fused",
         lambda d: LinearDiscriminantAnalysis(solver="lsqr", shrinkage="auto")),
        ("KNN/fused", "fused",
         lambda d: KNeighborsClassifier(n_neighbors=5, metric="cosine")),
        ("MLP/fused", "fused", lambda d: TorchMLP(d, (256, 64))),
    ]
    return S


def ctx_features(ck, X, pos_idx, idx, feat=None):
    """照片上下文特征：同框人脸数、同框其他脸与「本人类中心」的最高相似。

    为什么有用：家人照片常常整张只有本人（或本人+家属），而幼儿园活动照里
    同框一堆同学。这条特征在训练与部署都能算（一张照片的人脸一起打分即可），
    不是泄漏。
    **风险**：幼儿园同框里同学会被"带上去"，所以只作辅助特征、不给大权重。

    pos_idx: 估计类中心用的行（单脸归档脸）；idx: 需要算特征的行。
    """
    if len(pos_idx) == 0 or len(idx) == 0:
        return np.zeros((len(idx), 2), dtype=np.float32)
    Xp = X if feat is None else X
    center = l2n(Xp[pos_idx].mean(axis=0, keepdims=True))[0]
    sim = Xp @ center
    import collections as _c
    byck = _c.defaultdict(list)
    for i, c in enumerate(ck):
        byck[c].append(i)
    nf = np.zeros(len(idx), dtype=np.float32)
    mx = np.zeros(len(idx), dtype=np.float32)
    for k, i in enumerate(idx):
        g = byck[ck[i]]
        nf[k] = len(g)
        others = [j for j in g if j != i]
        mx[k] = max([float(sim[j]) for j in others], default=0.0)
    return np.column_stack([np.log1p(nf), mx]).astype(np.float32)


def xperson_centers(ck, X, ck18, idents=None, k=5, seed=0):
    """每个身份的子中心（用其归档单脸拟合），供「像不像别的家人」特征复用。

    只拟合一次、所有身份共用：为 A 算特征要用到 B 的中心，为 B 算又要用 A 的，
    如果各自现拟合就是 6×5 次无用功。
    """
    from sklearn.cluster import KMeans
    import collections as _c
    idents = list(idents or IDENT)
    nface = _c.Counter(ck)
    out = {}
    for q in idents:
        mine = {c for c, w in ck18.items() if w == {q}}
        idx = np.array([i for i, c in enumerate(ck)
                        if c in mine and nface[c] == 1], dtype=int)
        if len(idx) < 5:      # 单脸归档太少（妈妈/我/爸爸），退化为整库归档脸
            idx = np.array([i for i, c in enumerate(ck) if c in mine], dtype=int)
        if len(idx) == 0:
            continue
        P = X[idx]
        # k 的上限用 len//8 而不是 //10：妈妈/我/爸爸 的归档单脸只有 19~48 张，
        # //10 会把它们压成 1~2 个中心，跨身份特征就失去分辨力
        kk = max(1, min(k, len(P) // 8))
        if kk == 1:
            C = l2n(P.mean(axis=0, keepdims=True))
        else:
            C = l2n(KMeans(n_clusters=kk, n_init=5, random_state=seed)
                    .fit(P).cluster_centers_)
        out[q] = C
    return out


def xperson_features(centers, X, person, idx, idents=None, sims=None):
    """「这张脸最像哪个家人」——对每个他人取「与其次中心的最大余弦」。

    为什么这是关键特征：残差错误集中在 乐仔↔七月（全库最相似对，余弦 0.626）。
    单看「有多像乐仔」永远分不清这两个孩子，但「更像乐仔还是更像七月」是直接可比
    的证据。列顺序按 idents 固定（跨脚本契约）。

    sims: 可选的预计算结果 {身份: 全库相似度数组}（部署时 6 次矩阵乘就能覆盖所有人，
    不然每个身份都要重算一遍，35k 张脸 × 6 身份会白跑 5 倍）。
    """
    idents = list(idents or IDENT)
    cols = [q for q in idents if q != person and q in centers]
    if not cols:
        return np.zeros((len(idx), 0), dtype=np.float32)
    if sims is None:
        sims = {q: (X @ centers[q].T).max(axis=1) for q in centers}
    return np.column_stack([np.asarray(sims[q])[idx] for q in cols]).astype(np.float32)


def ens_prep(cks, feats, ck18, idents=None):
    """集成打分的**公共预处理**（与身份无关，算一次给所有人复用）。

    为什么单独抽出来：部署（apply_per_person）与审核（selfaudit）必须走同一条
    打分路径——否则我肉眼审的是"实验版模型"，上线的却是另一个，审核结论作废。
    """
    import collections as _c
    idents = list(idents or IDENT)
    nface = _c.Counter(cks)
    who = np.empty(len(cks), dtype=object)
    for i, c in enumerate(cks):
        who[i] = ck18.get(c)
    centers = xperson_centers(cks, feats["fused"], ck18, idents)
    sims = {q: (feats["fused"] @ C.T).max(axis=1) for q, C in centers.items()}
    return dict(nface=nface, who=who, centers=centers, sims=sims, n=len(cks))


def ens_score_one(b, prep, cks, person, feats, det, box, idents=None):
    """一个人的集成分数（logit 尺度）。b 是 per_person_ens.pkl 里该人的包。

    返回 (分数数组, 用于估类中心的归档单脸索引)。
    """
    idents = list(idents or IDENT)
    n = prep["n"]
    allidx = np.arange(n)
    S = np.vstack([score(m, feats[fk]) for _, fk, m in b["models"]])
    Sc = np.vstack([platt_apply(S[k], b["platt"][k]) for k in range(len(b["platt"]))])
    nface, who = prep["nface"], prep["who"]
    pos_idx = np.array([i for i, c in enumerate(cks)
                        if nface[c] == 1 and who[i] == {person}], dtype=int)
    ctx = ctx_features(cks, feats["fused"], pos_idx, allidx)
    xp = xperson_features(prep["centers"], feats["fused"], person, allidx, idents,
                          prep["sims"])
    boxarr = np.asarray(box, dtype=np.float32)
    meta = np.column_stack([ctx, det, np.log1p(np.maximum(boxarr[:, 2], boxarr[:, 3])),
                            xp]).astype(np.float32)
    z = (np.column_stack([Sc.T, meta])
         @ np.asarray(b["stacker"]["coef"], dtype=np.float64)
         + b["stacker"]["intercept"])
    return z, pos_idx


def purify_loo(pos_idx, X, centers_other, margin=0.02, rounds=2, min_keep=6, log=None):
    """正样本留一法提纯：把「更像别的身份」的脸从正样本里剔掉。

    为什么必需：18 归档是「与某个名字**相关**」的目录，里面混着截图、名人、他人
    （实测某成员的单脸归档里有一张**马云的视频截图**，模型还很认真地把它排到第 2）。
    一个人只有 19~48 个正样本时，1~2 张脏样本就足以把中心拉偏。

    做法：对每个正样本 i，用它**排除自己**之后的类中心算相似度 s_own，
    再与其他身份的子中心比较；若 `s_other + margin > s_own` 则剔除，迭代两轮。
    留一用「总和 − 自己」的增量算法，避免 O(n²) 重复求均值。

    centers_other: 其他身份的子中心列表（由各自归档单脸拟合，与 i 无关，不循环）。
    """
    keep = [int(i) for i in pos_idx]
    if len(keep) < min_keep or not centers_other:
        return keep, 0
    Call = np.vstack(centers_other)          # 其他身份的子中心拼一块，只为少做矩阵乘
    n_drop_total = 0
    for _ in range(rounds):
        if len(keep) < min_keep:
            break
        P = X[keep]
        S = P.sum(axis=0)
        n = len(keep)
        # 留一中心：(总和 − 自己) / (n−1)，一次算完 n 个（避免 O(n²) 求均值）
        own = (S[None, :] - P) / max(n - 1, 1)
        nn = np.linalg.norm(own, axis=1, keepdims=True)
        nn[nn < 1e-9] = 1.0
        own /= nn
        s_own = np.einsum("ij,ij->i", P, own)
        s_other = (P @ Call.T).max(axis=1)
        drop = [keep[k] for k in np.where(s_other + margin > s_own)[0]]
        if not drop:
            break
        keep = [i for i in keep if i not in set(drop)]
        n_drop_total += len(drop)
        if log:
            log(f"    提纯：剔除 {len(drop)} 张（更像别的身份），剩 {len(keep)}")
    return keep, n_drop_total


def platt_fit(s, y):
    """分数 → logit 的线性校准 (a*s+b)，系数在 OOF 上学定后固定。"""
    from sklearn.linear_model import LogisticRegression
    lr = LogisticRegression(C=1.0, max_iter=2000).fit(np.asarray(s).reshape(-1, 1), y)
    return float(lr.coef_[0][0]), float(lr.intercept_[0])


def platt_apply(s, ab):
    return np.asarray(s) * ab[0] + ab[1]


def child_prob(feats, cm):
    """幼童判别器打分 —— **必须按模型自报的 feature 取特征**。

    ⚠️ 真实踩坑（2026-09-19，链路在第 3 步整段挂掉）：
    `train_child.py` 在 {mbf, r50, fused} 里挑 CV AUC 最高的那个，**打平时取排在前面的**
    （mbf）。而调用方若硬编 `feats["fused"]`（= l2n(mbf‖r50)，1024 维），就会和 coef（512 维）
    维度不匹配 —— numpy 只会报 `size 512 is different from 1024`，从字面完全看不出
    "模型选的是另一个特征"。三处调用（train_ens_prod / bench_ens / diag_purify）曾同时写错，
    因此这里统一收口，并在不匹配时给出可读报错。

    feats: {"mbf": (n,512), "r50": (n,512), "fused": (n,1024), ...}
    返回 p_child（sigmoid 概率）。
    """
    f = str(cm.get("feature") or "fused")
    X = feats.get(f)
    if X is None:
        raise KeyError(f"child_model 需要特征 {f!r}，但 feats 只有 {sorted(feats)}")
    X = np.asarray(X, dtype=np.float32)
    coef = np.asarray(cm["coef"], dtype=np.float32)
    if X.shape[1] != coef.shape[0]:
        raise ValueError(
            f"幼童模型特征维度不匹配：feats[{f!r}] 为 {X.shape[1]} 维、coef 为 {coef.shape[0]} 维"
            f"（child_model.feature={f!r}；注意 fused=mbf‖r50 是另一个特征）")
    z = X @ coef + float(cm["intercept"])
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))

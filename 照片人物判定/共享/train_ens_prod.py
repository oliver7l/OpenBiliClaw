#!/usr/bin/env python3
"""训练「每人一个集成判定器」——生产件。

与 train_per_person.py（单 LR / 中心 NN）的差别：
  * 每人 11 个子模型（3 个嵌入空间 × LR/中心NN/多中心，+ LDA + KNN + MLP）
  * 子模型分数先 **Platt 校准**（固定系数），再喂 stacker（LR 元学习器）
  * 元特征只用「同框上下文 + 检测分 + 脸尺寸」，**不含 p_child**
    （p_child 会学成"幼童=本人"，对同龄同学零信息；它只留给 apply 阶段做跨类否决）
  * 正样本额外吸收**我肉眼判过的脸**（_audit/verdicts.jsonl）——
    这是唯一能补进 09 幼儿园库的监督信号（归档只覆盖照片级）
  * 「乐仔小时候」与「乐仔」**合并为一个身份**（同一个人，此前两个模型打架：
    924 张被标成"小时候"、只 205 张标成"乐仔"）

产出的 pickle 必须与 apply_per_person.py **共用 ens_models.py**，否则反序列化会失败。

用法:
  python train_ens_prod.py                       # 全量（读 db）
  python train_ens_prod.py --src old.npz          # 离线（读 npz，重扫期间可跑）
  python train_ens_prod.py --ident 乐仔
  python train_ens_prod.py --fast                 # 跳 MLP（快，用于冒烟测试）
"""
import argparse
import collections
import json
import os
import pickle
import sqlite3
import sys
import time

os.nice(10)
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold

import ens_models as EM

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
DB = f"{ROOT}/19_统一相册库/library.db"
OUT_PKL = f"{ROOT}/照片人物判定/per_person_ens.pkl"
OUT_JSON = f"{ROOT}/照片人物判定/per_person_ens.json"
VERDICTS = f"{ROOT}/照片人物判定/_audit/verdicts.jsonl"
SEEDS = f"{ROOT}/照片人物判定/_audit/cluster_seeds.jsonl"
DIMS_CACHE = f"{ROOT}/照片人物判定/_audit/_dims.json"

IDENT = EM.IDENT
# 目录名 → 身份（18 归档是照片级标注，目录名只是"这张照片关联到谁"）
ALIAS = {"乐仔小时候": "乐仔", "妈妈小时候": "妈妈", "艳艳小时候": "艳艳"}
KIDS = {"乐仔", "七月"}

N_09, N_07_08 = 3000, 1500
MIN_DET, MIN_SIDE = 0.60, 10
N_FOLD = 5
TOL_CENTER = 0.06          # 归一化中心匹配容差（verdicts 吸附到脸）
META_KEYS = EM.META_KEY_BASE


# ------------------------------------------------------------------ 数据源
def load_from_db():
    con = sqlite3.connect(DB)
    rows = con.execute("select lib, content_key, det_score, x, y, w, h, "
                       "emb_mbf, emb_r50 from faces").fetchall()
    con.close()
    lib = [str(r[0]) for r in rows]
    ck = [str(r[1]) for r in rows]
    det = np.array([float(r[2] or 0.0) for r in rows], dtype=np.float32)
    box = np.array([[float(r[3]), float(r[4]), float(r[5]), float(r[6])]
                    for r in rows], dtype=np.float32)
    mbf = np.vstack([np.frombuffer(r[7], dtype=np.float32) for r in rows])
    r50 = np.vstack([np.frombuffer(r[8], dtype=np.float32) for r in rows])
    return ck, lib, det, box, mbf, r50


def load_from_npz(path):
    d = np.load(path, allow_pickle=True)
    return (list(d["ck"].astype(str)), list(d["lib"].astype(str)),
            d["det"].astype(np.float32), d["box"].astype(np.float32),
            d["mbf"].astype(np.float32), d["r50"].astype(np.float32))


def load(src):
    if src.endswith(".npz"):
        ck, lib, det, box, mbf, r50 = load_from_npz(src)
    else:
        ck, lib, det, box, mbf, r50 = load_from_db()
    keep = (det >= MIN_DET) & (np.minimum(box[:, 2], box[:, 3]) >= MIN_SIDE)
    ck = [c for c, k in zip(ck, keep) if k]
    lib = [l for l, k in zip(lib, keep) if k]
    det, box = det[keep], box[keep]
    mbf, r50 = EM.l2n(mbf[keep]), EM.l2n(r50[keep])
    feats = {"mbf": mbf, "r50": r50, "fused": EM.l2n(np.hstack([mbf, r50]))}
    return dict(ck=np.array(ck, dtype=object), lib=np.array(lib, dtype=object),
                det=det, box=box, feats=feats)


def load_archive():
    """18 归档：ck → 身份集合（已 canon 化）；以及 ck → 来源 lib。"""
    con = sqlite3.connect(DB)
    ck18 = collections.defaultdict(set)
    for rel, c in con.execute("select rel, content_key from files where lib='18'"):
        ck18[c].add(ALIAS.get(rel.split("/")[0], rel.split("/")[0]))
    ck_lib = {}
    for c, l in con.execute("select content_key, lib from files where is_primary=1"):
        ck_lib.setdefault(c, l)
    con.close()
    return dict(ck18), ck_lib


def _snap_rows(D, rows, log=None):
    """把 [{ck,cx,cy,...}] 按 (ck, 归一化中心) 吸附回当前脸表 → [(face_index, row)]。
    键跨重扫稳定（重扫会换 rowid / box），容差 TOL_CENTER。"""
    dims = {}
    if os.path.exists(DIMS_CACHE):
        try:
            dims = json.load(open(DIMS_CACHE))
        except Exception:
            dims = {}
    byck = collections.defaultdict(list)
    for i, c in enumerate(D["ck"]):
        byck[c].append(i)
    out, n_miss = [], 0
    for r in rows:
        c = str(r.get("ck"))
        cand = byck.get(c)
        if not cand:
            n_miss += 1
            continue
        cx, cy = r.get("cx"), r.get("cy")
        if cx is None or cy is None or c not in dims or not dims[c]:
            if len(cand) == 1:
                out.append((cand[0], r))
            else:
                n_miss += 1
            continue
        ih, iw = dims[c][0], dims[c][1]
        best, bd = None, 1e9
        for i in cand:
            bx = D["box"][i]
            d = ((bx[0] + bx[2] / 2) / iw - cx) ** 2 + ((bx[1] + bx[3] / 2) / ih - cy) ** 2
            if d < bd:
                best, bd = i, d
        if best is not None and bd ** 0.5 <= TOL_CENTER:
            out.append((best, r))
        else:
            n_miss += 1
    return out, n_miss


def load_seeds(D, log=None):
    """簇种子（cluster_seeds.jsonl，半自动、量大）→ [(face_index, person)]。
    与 verdicts.jsonl（金标、量小）**分文件**，口径不混。"""
    if not os.path.exists(SEEDS):
        return [], 0
    rows = [json.loads(l) for l in open(SEEDS, encoding="utf-8") if l.strip()]
    pairs, miss = _snap_rows(D, rows)
    if log:
        log(f"簇种子 {len(rows)} 条 → 吸附 {len(pairs)} 条（失败 {miss}）")
    return [(i, r["person"]) for i, r in pairs], miss


def load_verdicts(D):
    """我肉眼判过的脸 → [(face_index, person, verdict)]。键是 (ck, 归一化中心)，
    跨重扫稳定；用容差吸附回当前脸表。

    ⚠️ 标签约定（selfaudit.py 定的）：**1=是本人 / 0=不是本人 / 2=存疑**。
    存疑必须**丢掉**，不能当负样本——拿"连我都拿不准的脸"去监督模型，
    等于教模型一个错答案；早期误把 2 当负样本，等于把 64 张存疑脸喂成负例。
    """
    if not os.path.exists(VERDICTS):
        return [], 0
    rows = []
    for line in open(VERDICTS, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        v = int(r.get("verdict", 0))
        if v not in (0, 1):       # 2=存疑 → 丢弃
            continue
        rows.append(r)
    pairs, miss = _snap_rows(D, rows)
    return [(i, r["person"], int(r["verdict"])) for i, r in pairs], miss


# ------------------------------------------------------------------ 训练集
def load_child():
    p = f"{ROOT}/照片人物判定/child_model.json"
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding="utf-8"))


def child_prob(D, cm):
    """委托给 ens_models.child_prob —— 它按 cm["feature"] 取特征并校验维度。

    这里曾是硬编 `D["feats"]["fused"]`：train_child 选了 mbf（512 维）时直接
    ValueError 崩掉（numpy 报 "size 512 is different from 1024"，看不出是选错特征）。
    """
    return EM.child_prob(D["feats"], cm)


def build_set(person, D, ck18, ck_lib, verdicts, pch=None, child_hi=None,
              centers=None, log=None, purify=False, seeds=None):
    ck, lib = D["ck"], D["lib"]
    nface = collections.Counter(ck)
    mine = {c for c, w in ck18.items() if w == {person}}
    pos0 = [i for i, c in enumerate(ck) if c in mine and nface[c] == 1]   # 归档单脸
    # ⚠️ 归档目录名 ≠ 身份，而且「单脸照片」也**不保证是本人**：
    #    拍娃的父母常常是镜头后面那个人（或侧脸没被检出），于是"整张只有一张脸"
    #    的照片里，那唯一一张脸是**孩子**。实测 妈妈 目录里的单脸照片几乎全是乐仔
    #    （妈妈的手机相册就是拍娃）——这正是 妈妈 模型一直学不出来的根因。
    #    修法：成人身份用幼童判别器把「明确幼童」的脸从正样本里剔掉。
    n_drop = 0
    if pch is not None and child_hi is not None and person not in KIDS and len(pos0):
        keep = [i for i in pos0 if not (np.isfinite(pch[i]) and pch[i] >= child_hi)]
        n_drop = len(pos0) - len(keep)
        pos0 = keep
    # ⚠️ 留一法提纯**默认关闭**。它看起来很美（乐仔 TPR@0.1% 0.335→0.516，
    # 妈妈 0.104→0.263），但把"剔除集"渲染出来肉眼复核后发现：
    # **被剔的绝大多数是真本人的难样本**（婴儿期、侧脸、被遮挡）——指标涨是因为
    # 题目变简单了，不是模型变强。这是第 2 次踩"假提升"（第 1 次是 p_child 泄漏）。
    # 因此改为显式 `--purify` 才启用，且启用后**必须**用 `diag_purify.py` 复核剔除集。
    n_drop2 = 0
    if purify and centers:
        c_other = [centers[q] for q in EM.IDENT if q != person and q in centers]
        pos0, n_drop2 = EM.purify_loo(pos0, D["feats"]["fused"], c_other, log=log)
        if log:
            log("    ⚠️ 已开启留一法提纯 —— 请用 diag_purify.py 复核剔除集再定稿")

    # 簇种子（半自动、量大）：本人簇 = 强正样本；**别人的簇 = 脸级负样本**。
    # 后者是本轮最有价值的新信号：此前负样本只能来自"照片级归档"的间接推断，
    # 现在可以直接说"这张脸是爸爸的，所以不是乐仔的"。
    seed_pos, seed_neg = [], []
    if seeds:
        seed_pos = [i for i, p in seeds if p == person]
        seed_neg = [i for i, p in seeds if p != person]
    # 我肉眼判过、且确定是本人的脸（主要是 09 幼儿园库，归档覆盖不到）
    pos = list(pos0) + seed_pos + [i for i, p, v in verdicts if p == person and v == 1]
    pos = np.array(sorted(set(int(i) for i in pos)), dtype=int)

    pos_ck = {ck[i] for i in pos}
    others = {c for c, w in ck18.items() if w != {person} and person not in w}
    neg = [i for i, c in enumerate(ck)
           if c in others and nface[c] == 1 and c not in pos_ck]
    # 簇种子负样本 + 我肉眼判过、确定不是本人的脸（最难的那批负样本）
    neg += [i for i in seed_neg if int(i) not in set(pos.tolist())]
    neg += [i for i, p, v in verdicts if p == person and v == 0]

    rng = np.random.default_rng(7)
    idx09 = np.array([i for i, l in enumerate(lib)
                      if l == "09" and ck[i] not in pos_ck], dtype=int)
    if len(idx09):
        neg += list(rng.choice(idx09, size=min(N_09, len(idx09)), replace=False))
    if person not in KIDS:
        idx_home = np.array([i for i, l in enumerate(lib)
                             if l in ("07", "08") and ck[i] not in pos_ck], dtype=int)
        if len(idx_home):
            neg += list(rng.choice(idx_home, size=min(N_07_08, len(idx_home)),
                                   replace=False))
    neg = np.array(sorted(set(int(i) for i in neg)), dtype=int)
    return (pos, neg, np.array(sorted(set(pos0)), dtype=int),
            n_drop, n_drop2)


def ctx_for(D, pos_idx, idx):
    return EM.ctx_features(D["ck"], D["feats"]["fused"], pos_idx, idx)


def tpr_at_fpr(y, s, fpr):
    order = np.argsort(-s)
    y = np.asarray(y)[order]
    n_neg, n_pos = int((y == 0).sum()), int((y == 1).sum())
    if n_neg == 0 or n_pos == 0:
        return 0.0
    budget = max(1, int(np.floor(fpr * n_neg)))
    fp = tp = 0
    for v in y:
        if v == 1:
            tp += 1
        else:
            fp += 1
            if fp > budget:
                break
    return tp / n_pos


# ------------------------------------------------------------------ 主流程
def train_person(person, D, ck18, ck_lib, verdicts, specs, centers, pch, chi, log,
                 purify=False, seeds=None):
    pos, neg, pos0, n_drop, n_drop2 = build_set(
        person, D, ck18, ck_lib, verdicts, pch, chi, centers, log, purify, seeds)
    if len(pos) < 8:
        log(f"  正样本只有 {len(pos)} 张，跳过"
            f"（归档单脸剔除幼童 {n_drop} + 提纯 {n_drop2} 后不足）")
        return None
    idx = np.concatenate([pos, neg])
    y = np.concatenate([np.ones(len(pos)), np.zeros(len(neg))])
    # ctx 的类中心只用「归档单脸」估计——必须与 apply 阶段完全同定义，
    # 否则同框似然在训练/部署之间尺度不一致（重演 z-score 漂移那个坑）
    ctx = ctx_for(D, pos0, idx)
    xp = EM.xperson_features(centers, D["feats"]["fused"], person, idx, EM.IDENT)
    xp_keys = [f"sim_{q}" for q in EM.IDENT if q != person and q in centers]
    meta = np.column_stack([ctx, D["det"][idx], np.log1p(
        np.maximum(D["box"][idx][:, 2], D["box"][idx][:, 3])), xp]).astype(np.float32)
    n_sd = sum(1 for i, p in (seeds or []) if p == person)
    log(f"  正 {len(pos)}（归档单脸剔幼童{n_drop}/提纯{n_drop2}；"
        f"簇种子 +{n_sd}；肉眼镜检 +{sum(1 for i,p,v in verdicts if p==person and v==1)}）"
        f" 负 {len(neg)}（簇种子 +{sum(1 for i,p in (seeds or []) if p!=person)}；"
        f"肉眼镜检 +{sum(1 for i,p,v in verdicts if p==person and v==0)}）")

    # --- OOF：每个子模型出交叉验证分数，用于 Platt 与 stacker 拟合 ---
    K = len(specs)
    S = np.zeros((K, len(y)), dtype=np.float64)
    folds = list(StratifiedKFold(N_FOLD, shuffle=True, random_state=0).split(
        np.zeros(len(y)), y))
    for k, (name, fkey, mk) in enumerate(specs):
        X = D["feats"][fkey][idx]
        for tr, te in folds:
            m = mk(X.shape[1]); m.fit(X[tr], y[tr])
            S[k, te] = EM.score(m, X[te])
    ab = [EM.platt_fit(S[k], y) for k in range(K)]
    Sc = np.vstack([EM.platt_apply(S[k], ab[k]) for k in range(K)])

    rows = []
    for k, (name, _, _) in enumerate(specs):
        rows.append(dict(model=name, auc=float(roc_auc_score(y, S[k])),
                         tpr01=float(tpr_at_fpr(y, S[k], 0.001))))
    ens_oof = stack_cv(Sc, y, meta)
    rows.append(dict(model="集成·stack", auc=float(roc_auc_score(y, ens_oof)),
                     tpr01=float(tpr_at_fpr(y, ens_oof, 0.001))))
    rows.append(dict(model="集成·子模型均值",
                     auc=float(roc_auc_score(y, np.nanmean(Sc, axis=0))),
                     tpr01=float(tpr_at_fpr(y, np.nanmean(Sc, axis=0), 0.001))))

    # --- 全量重训：生产用的子模型 + stacker ---
    full = []
    for name, fkey, mk in specs:
        X = D["feats"][fkey][idx]
        m = mk(X.shape[1]); m.fit(X, y)
        full.append((name, fkey, m))
    st = LogisticRegression(C=1.0, max_iter=5000).fit(
        np.column_stack([Sc.T, meta]), y)
    return dict(models=[(n, f, m) for n, f, m in full],
                platt=[list(a) for a in ab],
                stacker=dict(coef=st.coef_[0].tolist(), intercept=float(st.intercept_[0])),
                meta_keys=META_KEYS + xp_keys, n_pos=int(len(pos)), n_neg=int(len(neg)),
                rows=rows, trained_at=time.strftime("%Y-%m-%d %H:%M"))


def stack_cv(Sc, y, meta):
    """stacker 的无偏 OOF（用另一套折，避免与基模型折同源）。"""
    X = np.nan_to_num(np.column_stack([Sc.T, meta]), nan=0.0)
    s = np.zeros(len(y))
    for tr, te in StratifiedKFold(N_FOLD, shuffle=True, random_state=1).split(X, y):
        m = LogisticRegression(C=1.0, max_iter=5000).fit(X[tr], y[tr])
        s[te] = m.decision_function(X[te])
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="db", help="db 或 *.npz")
    ap.add_argument("--ident", default=None)
    ap.add_argument("--fast", action="store_true", help="跳 MLP（冒烟测试用）")
    ap.add_argument("--purify", action="store_true",
                    help="开启留一法正样本提纯。⚠️ 默认关闭：它会把真本人的难样本也剔掉、"
                         "制造「指标变好」的假象（已由图审证伪）；"
                         "开启后必须跑 diag_purify.py 复核剔除集")
    ap.add_argument("--seeds", default="auto",
                    help="簇种子（_audit/cluster_seeds.jsonl）：auto=有就用，"
                         "off=不用。它是「半自动、量大」的监督，与 verdicts 金标分开记账")
    ap.add_argument("--out", default=None,
                    help="输出 pickle 路径（默认写生产件 per_person_ens.pkl）。"
                         "冒烟测试务必带本参数，否则会把生产模型覆盖成测试档")
    a = ap.parse_args()
    out_pkl = a.out or OUT_PKL
    out_json = (out_pkl[:-4] + ".json") if a.out else OUT_JSON

    def log(*x):
        print(*x, flush=True)

    t0 = time.time()
    log(f"数据源 {a.src}")
    D = load(a.src)
    log(f"全库脸 {len(D['ck'])}（{time.time()-t0:.0f}s）")
    ck18, ck_lib = load_archive()
    log(f"18 归档覆盖 {len(ck18)} 张唯一照片")
    verdicts, n_miss = load_verdicts(D)
    log(f"肉眼镜检真值 {len(verdicts)} 条（吸附失败 {n_miss}）")
    seeds = []
    if a.seeds != "off":
        seeds, n_miss2 = load_seeds(D, log)
        log(f"簇种子 {len(seeds)} 条（吸附失败 {n_miss2}）"
            f"{'；⚠️ 文件不存在，只有归档+金标在监督' if not seeds else ''}")
    cm = load_child()
    pch, chi = None, None
    if cm is None:
        log("⚠️ 没有 child_model.json —— 成人正样本无法剔除「拍娃照里的孩子」，"
            "妈妈/我 这类身份的正样本会很脏（先跑 train_child.py）")
    else:
        pch = child_prob(D, cm)
        chi = float(cm["thresholds"]["child"])
        log(f"幼童判别器已载入（幼童门限 {chi:.3f}），"
            f"预计全库幼童脸 {int((pch >= chi).sum())} 张")

    specs = EM.base_specs()
    if a.fast:
        specs = [s for s in specs if not s[0].startswith("MLP")]
    # 「像不像别的家人」的子中心：全身份拟合一次，所有身份共用（顺序=IDENT，跨脚本契约）
    centers = EM.xperson_centers(D["ck"], D["feats"]["fused"], ck18, EM.IDENT)
    log(f"跨身份子中心：{ {q: (len(C)) for q, C in centers.items()} }")
    idents = [v for v in a.ident.split(",") if v] if a.ident else IDENT
    bundle, metrics = {}, {}
    for p in idents:
        log(f"===== {p} =====")
        r = train_person(p, D, ck18, ck_lib, verdicts, specs, centers, pch, chi, log,
                         a.purify, seeds)
        if r is None:
            continue
        bundle[p] = r
        metrics[p] = dict(n_pos=r["n_pos"], n_neg=r["n_neg"], rows=r["rows"],
                          trained_at=r["trained_at"])
        log(f"  {'模型':<18}{'AUC':>8}{'TPR@0.1%':>10}")
        for row in sorted(r["rows"], key=lambda v: -v["tpr01"]):
            log(f"  {row['model']:<18}{row['auc']:>8.4f}{row['tpr01']:>10.3f}")

    if not bundle:
        log("没有任何身份训练成功")
        sys.exit(1)
    # 增量合并：只覆盖本次训过的身份，其余保留
    old = {}
    if os.path.exists(out_pkl):
        try:
            old = pickle.load(open(out_pkl, "rb"))
        except Exception:
            old = {}
    old.update(bundle)
    pickle.dump(old, open(out_pkl, "wb"), protocol=4)
    mj = {}
    if os.path.exists(out_json):
        try:
            mj = json.load(open(out_json, encoding="utf-8"))
        except Exception:
            mj = {}
    mj.update(metrics)
    json.dump(mj, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"\n→ {out_pkl}（{os.path.getsize(out_pkl)/1e6:.1f} MB，含 {len(old)} 个身份）")
    log(f"→ {out_json}")
    log(f"总用时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

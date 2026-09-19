#!/usr/bin/env python3
"""为每个家庭成员训练「独立二分类判定模型」（一人一模型，非聚类、非多类 softmax）。

设计要点
--------
0) 行的唯一键是 faces.rowid —— **不能用 face_id**：faces 表里 face_id 在 4~7096 段
   被两批数据重复使用（37449 行只有 30895 个唯一 face_id），按 face_id 建字典会丢行、
   取错嵌入。18 归档照片大量是重复副本（primary 标记在 07/08 原件上），所以
   ck→人 的映射必须取 lib='18' 的**全部**文件，不能只取 is_primary=1。

1) 种子只来自 18_人物分组/<人>/ —— 照片级人工归档过的照片，当前唯一可信真值源。
   **完全不读 faces.cluster / faces.person**：07 的簇真值已被污染（'妈妈' 簇 1587 张
   vs 历史人工 186 张），拿簇当种子会一路错下去。

2) 自举扩种：单脸照片的脸 → 提纯 → 算类中心 → 到合影里挑「最像本人」的那张脸
   当正样本，同照片其余脸当难负（合影内部互斥，一定是别人）。共两轮。

3) 通用负样本：从非该人的 2.8 万张脸里随机抽 6000 张（09 班级相册占大头，
   大量陌生幼童/家长），专治「陌生人被判成家人」这个老毛病。

4) 难负挖掘：全库打分后把高分负样本回炉；分数 > HARD_CAP 的视为漏标本人，不下压。

5) 一人一模型 = 开集判定：一张脸可以谁都不命中（陌生人），也可以命中多人（取分高者）。

6) 特征变体（mbf / r50 / 融合）与判别器（LR / 类中心+Platt）都用 CV AUC 自动择优。

用法
----
    python train_per_person.py            # 训练全部
    python train_per_person.py --person 妈妈
"""
import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import KFold

ROOT = Path(__file__).resolve().parents[2]
LIB_DB = ROOT / "19_统一相册库" / "library.db"
OUT_DIR = ROOT / "照片人物判定"
MODEL_JSON = OUT_DIR / "per_person_models.json"

# 只做家人（刘峰/彭超等同事暂不建模）
FAMILY = ["乐仔", "艳艳", "妈妈", "我", "七月", "爸爸", "乐仔小时候"]
MUTEX = {"乐仔": "乐仔小时候", "乐仔小时候": "乐仔"}   # 同一人不同年龄段，互不当负样本
# 种子提纯：同一张脸若在别人的模型上得分也高，说明这张种子本身就有误判 → 剔除
PRUNE_ROUNDS = 3      # 轮1: 09抽样 → 轮2: 09全量+保护 → 轮3: 定稿
SIM_MARGIN = 0.04      # 类中心余弦 margin（低于此值说明这张种子更像别人）
MIN_KEEP = 12

MIN_DET = 0.60
MIN_SIDE_SEED = 12      # 种子要求清晰一点
MIN_SIDE_ALL = 10       # 预测时放宽
# 误判率基准集：只用「他人归档脸 + 09 班级库」。
# 不能用 07/08 家庭库估 FPR —— 那里本人照片极多，会被当成误判，把阈值抬到 0.98。
N_NEG_09 = 5000        # 第 1 轮训练用抽样；第 2 轮起用 09 全量（见 KIDS/protect09）
N_NEG_HOME = 4000      # 07/08 随机负样本，让模型认识家庭库里的亲戚/路人
KIDS = {"乐仔", "乐仔小时候", "七月"}   # 他们在 09 班级库里有真照片，需保护
N_UNIV_NEG = 6000
HARD_CAP = 0.90
TOP_HARD = 300
N_ROUNDS = 2
BOOTSTRAP_ROUNDS = 2
C_GRID = [0.03, 0.1, 0.3, 1.0, 3.0]
TARGETS = {"strict": 0.002, "normal": 0.005, "loose": 0.01}
SEED = 42
rng = np.random.default_rng(SEED)


# ---------------- 工具 ----------------
def l2n(X, axis=1):
    X = np.asarray(X, dtype=np.float32)
    n = np.linalg.norm(X, axis=axis, keepdims=True)
    n[n < 1e-9] = 1.0
    return X / n


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def topk_mean(S, k):
    k = max(1, min(k, S.shape[1]))
    idx = np.argpartition(-S, k - 1, axis=1)[:, :k]
    return np.take_along_axis(S, idx, axis=1).mean(axis=1)


def purify(Xs, rounds=4):
    """类内互近邻中位数剔离群（非聚类）。"""
    keep = np.ones(len(Xs), dtype=bool)
    for _ in range(rounds):
        idx = np.where(keep)[0]
        if len(idx) < 6:
            break
        typ = topk_mean(Xs[idx] @ Xs[idx].T, min(10, len(idx)))
        med = np.median(typ); mad = np.median(np.abs(typ - med)) + 1e-6
        drop = idx[typ < med - 2.0 * mad]
        if len(drop) == 0 or len(idx) - len(drop) < 6:
            break
        keep[drop] = False
    return np.where(keep)[0]


def load():
    """返回 rowid 数组、ck 数组、face 级特征三变体、ck→人(18)、ck→脸数、ck→主库。"""
    con = sqlite3.connect(LIB_DB)
    rows = con.execute(
        "select rowid, content_key, det_score, w, h, emb_mbf, emb_r50 from faces").fetchall()
    ck_person = {}
    for rel, ck in con.execute("select rel, content_key from files where lib='18'"):
        ck_person.setdefault(ck, rel.split("/")[0])
    nface = dict(con.execute("select content_key, count(*) from faces group by content_key"))
    ck_lib = {}
    for ck, lib in con.execute("select content_key, lib from files where is_primary=1"):
        ck_lib.setdefault(ck, lib)
    con.close()

    rid, cks, ok, Xm, Xr = [], [], [], [], []
    for r, ck, det, w, h, mb, r5 in rows:
        if mb is None or r5 is None:
            continue
        if det is not None and det < MIN_DET:
            continue
        if w is not None and h is not None and min(w, h) < MIN_SIDE_ALL:
            continue
        a = np.frombuffer(mb, dtype=np.float32)
        b = np.frombuffer(r5, dtype=np.float32)
        rid.append(r); cks.append(ck); Xm.append(a); Xr.append(b)
        ok.append(min(w, h) if (w and h) else 99)
    Xm, Xr = l2n(np.vstack(Xm)), l2n(np.vstack(Xr))
    feats = {"mbf": Xm, "r50": Xr,
             "fused": l2n(np.hstack([Xm, Xr]))}
    return (np.array(rid), np.array(cks, dtype=object), np.array(ok), feats,
            ck_person, nface, ck_lib)


def pick_threshold(pos_p, neg_p, target_fpr):
    grid = np.arange(0.02, 0.9901, 0.005)
    best = None
    for th in grid:
        fpr = float((neg_p >= th).mean())
        if fpr <= target_fpr:
            rec = float((pos_p >= th).mean())
            if best is None or rec > best[1]:
                best = (float(th), rec, fpr)
    if best is None:
        th = float(np.quantile(neg_p, 1 - target_fpr))
        best = (th, float((pos_p >= th).mean()), float((neg_p >= th).mean()))
    return best


# ---------------- 判别器 ----------------
class LRModel:
    def __init__(self, C):
        self.C = C

    def fit(self, Xp, Xn):
        self.clf = LogisticRegression(C=self.C, max_iter=5000, class_weight="balanced")
        self.clf.fit(np.vstack([Xp, Xn]),
                     np.concatenate([np.ones(len(Xp)), np.zeros(len(Xn))]))

    def prob(self, X):
        return sigmoid(self.clf.decision_function(X))

    def export(self):
        return {"kind": "lr", "coef": self.clf.coef_[0].astype(float).tolist(),
                "intercept": float(self.clf.intercept_[0])}


class CentModel:
    def fit(self, Xp, Xn):
        self.w = l2n(Xp.mean(axis=0, keepdims=True))[0]
        s = np.concatenate([Xp @ self.w, Xn @ self.w]).reshape(-1, 1)
        y = np.concatenate([np.ones(len(Xp)), np.zeros(len(Xn))])
        self.platt = LogisticRegression(C=1.0, max_iter=1000).fit(s, y)

    def prob(self, X):
        return self.platt.predict_proba((X @ self.w).reshape(-1, 1))[:, 1]

    def export(self):
        return {"kind": "cent", "center": self.w.astype(float).tolist(),
                "platt_a": float(self.platt.coef_[0][0]),
                "platt_b": float(self.platt.intercept_[0])}


def oof_proba(make, Xp, Xn):
    oof_p = np.zeros(len(Xp)); oof_n = np.zeros(len(Xn))
    ns = max(2, min(5, len(Xp)))
    for tr, te in KFold(n_splits=ns, shuffle=True, random_state=SEED).split(Xp):
        m = make(); m.fit(Xp[tr], Xn)
        oof_p[te] = m.prob(Xp[te])
        oof_n += m.prob(Xn) / ns
    return oof_p, oof_n


def auc_of(op, on):
    return roc_auc_score(np.concatenate([np.ones(len(op)), np.zeros(len(on))]),
                         np.concatenate([op, on]))


def model_probs(m, feats):
    """用已训练好的模型对全量人脸打分（用于种子互斥提纯）。"""
    X = feats[m["feature"]]
    if m["kind"] == "lr":
        return sigmoid(X @ np.asarray(m["coef"], dtype=np.float32) + m["intercept"])
    c = np.asarray(m["center"], dtype=np.float32)
    return sigmoid((X @ c) * m["platt_a"] + m["platt_b"])


# ---------------- 主流程 ----------------
def build_seeds(person, rid, cks, side, X, ck_person, nface, log, allow=None):
    """返回 (正样本 rowid 数组, 合影难负 rowid 数组, 说明字符串)

    allow: 若为集合，则正样本必须是其中的成员（用于「跨模型互斥提纯」后的重训）。
    """
    ck_of = dict(zip(rid, cks))
    mine_ck = [ck for ck, p in ck_person.items() if p == person]
    rows_of_ck = {}
    for r, ck in zip(rid, cks):
        rows_of_ck.setdefault(ck, []).append(r)

    def good(rows):
        # rid 已按 rowid 升序，用二分定位 side（人脸短边尺寸）
        return [r for r in rows if side[int(np.searchsorted(rid, r))] >= MIN_SIDE_SEED]

    pos_of = {r: i for i, r in enumerate(rid)}
    single, multi = [], {}
    for ck in mine_ck:
        rows = rows_of_ck.get(ck, [])
        if not rows:
            continue
        rows = good(rows)
        if len(rows) == 0:
            continue
        if nface.get(ck, 0) == 1:
            single.extend(rows)
        else:
            multi[ck] = rows
    if len(single) < 8:
        log(f"  {person}: 单脸种子不足（{len(single)}），跳过")
        return None, None, ""
    idx = np.array([pos_of[r] for r in single])
    idx = idx[purify(X[idx])]
    pos = list(rid[idx])
    hard = []
    info = f"单脸 {len(single)}→{len(pos)}"
    for _ in range(BOOTSTRAP_ROUNDS):
        center = l2n(X[[pos_of[r] for r in pos]].mean(axis=0, keepdims=True))[0]
        seed_sim = X[[pos_of[r] for r in pos]] @ center
        floor = max(float(np.percentile(seed_sim, 20)) - 0.02, 0.40)
        reps, hard = [], []
        for ck, rows in multi.items():
            ii = [pos_of[r] for r in rows]
            sims = X[ii] @ center
            j = int(np.argmax(sims))
            if sims[j] >= floor:
                reps.append(rows[j])
            hard.extend(rows[t] for t in range(len(rows)) if t != j)
        if not reps:
            break
        ri = np.array([pos_of[r] for r in reps])
        reps = [rid[i] for i in ri[purify(X[ri])]]
        new_pos = sorted(set(pos) | set(reps))
        if len(new_pos) == len(pos):
            break
        pos = new_pos
        info += f" +合影{len(reps)}→{len(pos)}"
    pos = np.array(sorted(set(int(r) for r in pos)))
    if allow is not None:
        pos = np.array([r for r in pos if int(r) in allow])
    return pos, np.array(hard), info


def train_one(person, rid, cks, side, feats, ck_person, nface, ck_lib, all_seeds, log,
              allow=None, protect09=None):
    mine_ck = {ck for ck, p in ck_person.items() if p == person}
    # 互斥伙伴（乐仔↔乐仔小时候）的归档脸不算负样本，否则阈值被抬到 0.99、召回只剩 34%
    other_ck = {ck for ck, p in ck_person.items()
                if p != person and p != MUTEX.get(person)}
    pos_of = {r: i for i, r in enumerate(rid)}
    other_idx = np.array([i for i in range(len(rid)) if cks[i] in other_ck])
    idx09 = np.array([i for i in range(len(rid)) if ck_lib.get(cks[i]) == "09"])
    idxhome = np.array([i for i in range(len(rid))
                        if ck_lib.get(cks[i]) in ("07", "08") and cks[i] not in mine_ck])
    # 误判率基准集（不含 07/08，那里本人太多）
    eval_idx = np.unique(np.concatenate([
        other_idx, rng.choice(idx09, size=min(N_NEG_09, len(idx09)), replace=False)]))
    best_feat = None
    # 用类中心模型快速挑特征
    for fname, X in feats.items():
        pos, hard, info = build_seeds(person, rid, cks, side, X, ck_person, nface,
                                      lambda *a: None, allow=allow)
        if pos is None or len(pos) < 6:
            continue
        neg = rng.choice(eval_idx, size=min(2000, len(eval_idx)), replace=False)
        op, on = oof_proba(CentModel, X[[pos_of[r] for r in pos]], X[neg])
        a = auc_of(op, on)
        if best_feat is None or a > best_feat[0]:
            best_feat = (a, fname, pos, hard, info)
    if best_feat is None:
        log(f"  {person}: 无可用种子，跳过")
        return None
    auc0, fname, pos, hard, info = best_feat
    X = feats[fname]
    pi = np.array([pos_of[r] for r in pos])
    Xp = X[pi]
    log(f"  {person}: [{fname}] {info}；合影难负 {len(hard)}")

    # 负样本：通用池 + 合影难负 + 其他人种子
    pool_mask = np.array([cks[i] not in mine_ck for i in range(len(rid))])
    pool_idx = np.where(pool_mask)[0]
    hard_ids = list(hard)
    for other, s in all_seeds.items():
        if other == person or MUTEX.get(person) == other:
            continue
        hard_ids.extend(s)
    hard_idx = np.array(sorted({pos_of[r] for r in hard_ids if r in pos_of}))
    # 09 负样本：第 1 轮抽样（此时还没有保护集）；后续轮用全量并剔除保护脸
    if protect09 is None:
        take09 = rng.choice(idx09, size=min(5000, len(idx09)), replace=False)
    else:
        take09 = np.array([i for i in idx09 if i not in protect09])
    takehome = rng.choice(idxhome, size=min(N_NEG_HOME, len(idxhome)), replace=False)
    neg_idx = np.unique(np.concatenate([take09, takehome, hard_idx]))

    cands = [("lr", lambda C=C: LRModel(C)) for C in C_GRID] + [("cent", CentModel)]
    best = None
    for tag, make in cands:
        op, on = oof_proba(make, Xp, X[neg_idx])
        a = auc_of(op, on)
        if best is None or a > best[0]:
            best = (a, tag, make, op, on)
    auc, tag, make, op, on = best

    # 难负挖掘只在干净基准集里做（07/08 里本人太多，会把本人当难负压死）
    for _ in range(N_ROUNDS):
        m = make(); m.fit(Xp, X[neg_idx])
        s_all = m.prob(X[eval_idx])
        order = np.argsort(-s_all)[:TOP_HARD]
        cand = eval_idx[order][s_all[order] < HARD_CAP]
        new = np.setdiff1d(cand, neg_idx)
        if len(new) == 0:
            break
        neg_idx = np.unique(np.concatenate([neg_idx, new]))

    m = make(); m.fit(Xp, X[neg_idx])
    s_pos, s_neg = m.prob(Xp), m.prob(X[neg_idx])
    s_pool = m.prob(X[pool_idx])
    s_eval = m.prob(X[eval_idx])      # 阈值基准：他人归档脸 + 09 班级库
    ths, metrics = {}, {}
    for name, tgt in TARGETS.items():
        th, rec, fpr = pick_threshold(op, s_eval, tgt)
        ths[name] = round(th, 4)
        metrics[name] = {"recall_oof": round(rec, 4),
                         "fpr_base": round(float((s_eval >= th).mean()), 5),
                         "recall_seed": round(float((s_pos >= th).mean()), 4),
                         "n_base": int(len(eval_idx)),
                         "est_false_pos": int(round(float((s_eval >= th).mean()) * len(eval_idx))),
                         # 全库口径含本人未归档照片，仅供参考
                         "fpr_all": round(float((s_pool >= th).mean()), 5)}
    out = m.export()
    out.update({"person": person, "model": tag, "feature": fname,
                "cv_auc": round(float(auc), 4), "n_seed": int(len(pos)),
                "n_hard_group": int(len(hard)), "n_neg": int(len(neg_idx)),
                "n_pool": int(len(pool_idx)), "thresholds": ths, "metrics": metrics,
                "seed_rowids": [int(r) for r in pos],
                "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--person", default=None)
    args = ap.parse_args()

    def log(*a):
        print(*a, flush=True)

    t0 = datetime.now()
    log("加载人脸 ...")
    rid, cks, side, feats, ck_person, nface, ck_lib = load()
    log(f"  人脸 {len(rid)} 张；18 归档 ck {len(ck_person)}")

    log("提纯各人种子（互相做难负）...")
    all_seeds = {}
    for p in FAMILY:
        best = None
        for fname, X in feats.items():
            pos, _, _ = build_seeds(p, rid, cks, side, X, ck_person, nface, lambda *a: None)
            if pos is None:
                continue
            if best is None or len(pos) > len(best):
                best = pos
        all_seeds[p] = [int(r) for r in (best if best is not None else [])]
        log(f"  {p}: {len(all_seeds[p])}")

    persons = FAMILY if args.person is None else [args.person]
    pos_of = {int(r): i for i, r in enumerate(rid)}
    allow_map = {p: None for p in persons}
    protect09 = {p: None for p in persons}
    idx09_all = np.array([i for i in range(len(rid)) if ck_lib.get(cks[i]) == "09"])
    out = {}
    for it in range(PRUNE_ROUNDS):
        log(f"\n--- 第 {it + 1}/{PRUNE_ROUNDS} 轮 ---")
        out = {}
        for p in persons:
            log(f"训练 {p} ...")
            m = train_one(p, rid, cks, side, feats, ck_person, nface, ck_lib, all_seeds, log,
                          allow=allow_map[p], protect09=protect09[p])
            if not m:
                continue
            out[p] = m
            mm = m["metrics"]["normal"]
            log(f"  ✓ {p}: [{m['model']}/{m['feature']}] AUC={m['cv_auc']} 正={m['n_seed']} "
                f"负={m['n_neg']} thr={m['thresholds']['normal']} "
                f"召回={mm['recall_oof']} 基准误判≈{mm['est_false_pos']}")
        if it == PRUNE_ROUNDS - 1:
            break

        # ---- 09 保护集：kids 在班级库里有真照片，第 2 轮起这些脸不入负样本 ----
        if it == 0:
            log("生成 09 保护集（kids 的真阳性不入负样本）...")
            for p in persons:
                if p in KIDS and p in out:
                    s09 = model_probs(out[p], feats)[idx09_all]
                    prot = idx09_all[s09 > 0.90]
                    protect09[p] = set(int(i) for i in prot)
                    log(f"  {p}: 09 保护 {len(prot)} 张脸（全部 {len(idx09_all)}）")

        # ---- 种子提纯：同一张脸若离别人的类中心更近，说明这张种子本身就有误判 ----
        # 用「类中心余弦 margin」而不是模型分数：模型分数对训练集内的种子必然虚高，
        # 用它做提纯等于什么都没剔（实测剔除 0）。类中心只由种子算，泄漏小。
        log("提纯种子（类间余弦 margin）...")
        Xf = feats["fused"]
        centers = {}
        for q in out:
            iq = np.array([pos_of[int(r)] for r in out[q]["seed_rowids"]])
            centers[q] = l2n(Xf[iq].mean(axis=0, keepdims=True))[0]
        for p in persons:
            if p not in out:
                continue
            seeds = np.array(out[p]["seed_rowids"], dtype=int)
            idx = np.array([pos_of[int(r)] for r in seeds])
            own = Xf[idx] @ centers[p]
            others = [q for q in out if q != p and MUTEX.get(p) != q]
            omax = (np.max(np.vstack([Xf[idx] @ centers[q] for q in others]), axis=0)
                    if others else np.zeros(len(idx)))
            margin = own - omax
            keep = seeds[margin >= SIM_MARGIN]
            if len(keep) < MIN_KEEP:
                keep = seeds[np.argsort(-margin)[:MIN_KEEP]]
            allow_map[p] = {int(r) for r in keep}
            all_seeds[p] = [int(r) for r in keep]
            log(f"  {p}: {len(seeds)} → {len(keep)}（剔除 {len(seeds) - len(keep)}）")

    MODEL_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"\n模型已写入 {MODEL_JSON}  用时 {datetime.now() - t0}")


if __name__ == "__main__":
    main()

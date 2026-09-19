#!/usr/bin/env python3
"""为每个家庭成员训练「判别式分类模型」（替代聚类/kNN 判定）。

为什么不用聚类/kNN
------------------
实测发现 07 的 cluster 真值严重不纯：'妈妈' 簇被塞了 1446 张脸（历史真值只有
186 张），子簇分析显示里面至少混了 3 个人的脸 —— 拿这种簇当 gallery，
top1 相似度天然虚高，于是才会出现「09 里戴眼镜的陌生幼童被判成妈妈 0.84 分」
这种事故。所以本脚本不再信任簇，改为：

  1) 候选池     = 07 cluster 脸 ∪ 18人物目录照片的脸
  2) 子簇切分   = KMeans(k=3)，用「18 目录照片占比」锚定到底哪个子簇是本人
                  （18 目录是照片级人工确认过的单人照片，可作锚）
  3) 迭代提纯   = 互近邻中位数剔离群，去掉子簇里残留的异类
  4) 判别训练   = L2 逻辑回归，学「本人 vs 全库其他所有人」的线性边界
  5) 难负挖掘   = 把当前模型打分最高的负样本回炉重训，专治「像但不是」的混淆
  6) 评估       = 正样本 5 折 CV 测 recall，全量负样本(~3万)测 FPR

用法
----
    python build_person_classifier.py --person 妈妈
    python build_person_classifier.py --all
    python build_person_classifier.py --person 乐仔 --eval-only
"""
import argparse
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from sklearn.cluster import KMeans
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import KFold

ROOT = Path(__file__).resolve().parents[2]
LIB_DB = ROOT / "19_统一相册库" / "library.db"
P07_DB = ROOT / "07_相册" / "相册&视频备份" / "_photo_index" / "photo_index.db"
OUT_BASE = ROOT / "照片人物判定"

FAMILY = ["乐仔", "乐仔小时候", "艳艳", "我", "妈妈", "七月", "爸爸"]
MUTEX_GROUPS = [{"乐仔", "乐仔小时候"}]   # 同一人不同年龄段，互不当负样本

# 07 库人脸中位仅 19px（大量远景小脸），尺寸门槛不能高；08/09 的 w/h 为 NULL
MIN_DET = 0.60
MIN_SIDE = 12
C_GRID = [0.03, 0.1, 0.3, 1.0, 3.0]
DEFAULT_TARGETS = [0.005, 0.01, 0.02]
HARD_CAP = 0.95          # 分数高过此值的负样本视为「漏标本人」，不纳入 hard
SEED = 42


# ---------------- 工具 ----------------
def l2n(X, axis=1):
    X = np.asarray(X, dtype=np.float32)
    n = np.linalg.norm(X, axis=axis, keepdims=True)
    n[n < 1e-9] = 1.0
    return X / n


def topk_mean(S, k):
    if k == 1:
        return S.max(axis=1)
    k = min(k, S.shape[1])
    idx = np.argpartition(-S, k - 1, axis=1)[:, :k]
    return np.take_along_axis(S, idx, axis=1).mean(axis=1)


def score_matrix(Q, G, block=4096):
    out = np.empty((len(Q), len(G)), dtype=np.float32)
    for i in range(0, len(Q), block):
        out[i:i + block] = Q[i:i + block] @ G.T
    return out


def pick_threshold(pos_s, neg_s, target_fpr):
    grid = np.arange(0.02, 0.9901, 0.005)
    best = None
    for th in grid:
        fpr = float((neg_s >= th).mean())
        if fpr <= target_fpr:
            rec = float((pos_s >= th).mean())
            if best is None or rec > best[1]:
                best = (float(th), rec, fpr)
    if best is None:
        th = float(np.quantile(neg_s, 1 - target_fpr))
        best = (th, float((pos_s >= th).mean()), float((neg_s >= th).mean()))
    return best


def purity(X):
    """簇内纯度：各样本与簇原型的余弦中位数"""
    proto = X.mean(0)
    proto /= (np.linalg.norm(proto) + 1e-9)
    return float(np.median(X @ proto))


# ---------------- 数据 ----------------
def ok_quality(r):
    """r = faces 行；det_score 必有，w/h 可能为 None（08/09 导入时未写）"""
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


def person_keys(db, person):
    keys = {r[0] for r in db.execute(
        "SELECT DISTINCT content_key FROM photo_person_tags WHERE person=?", (person,))}
    keys |= {r[0] for r in db.execute(
        "SELECT DISTINCT content_key FROM files WHERE lib='18' AND rel LIKE ?",
        (person + "/%",))}
    return keys


def group_keys(db, person):
    keys = person_keys(db, person)
    for grp in MUTEX_GROUPS:
        if person in grp:
            for o in grp:
                if o != person:
                    keys |= person_keys(db, o)
    return keys


def collect_candidates(faces, db, person, cmap):
    """候选池 {idx: src}，src ∈ {07_cluster, 18_group}"""
    inv = {v: k for k, v in cmap.items()}
    cl = inv.get(person)
    ck18 = {r[0] for r in db.execute(
        "SELECT DISTINCT content_key FROM files WHERE lib='18' AND rel LIKE ?",
        (person + "/%",))}
    cand = {}
    for i, r in enumerate(faces):
        if not ok_quality(r):
            continue
        if cl is not None and r[6] == cl and r[2] == "07":
            cand[i] = "07_cluster"
        elif r[1] in ck18:
            cand[i] = "18_group"
    return cand, ck18


def select_core(cand, ck18, faces, V, args):
    """KMeans 子簇切分 → 18目录锚定 → 迭代提纯"""
    idx = np.array(sorted(cand))
    if len(idx) < 40:
        return idx, {"mode": "too_few", "n": len(idx)}
    X = V[idx]
    is18 = np.array([faces[i][1] in ck18 for i in idx])
    k = min(args.subclusters, max(2, len(idx) // 60))
    km = KMeans(n_clusters=k, n_init=5, random_state=SEED).fit(X)
    lab = km.labels_
    info_sub = []
    for c in range(k):
        m = lab == c
        n = int(m.sum())
        if n < 20:
            continue
        info_sub.append({
            "cluster": c, "n": n,
            "purity": round(purity(X[m]), 3),
            "frac18": round(float(is18[m].mean()), 3),
            "n18": int(is18[m].sum()),
        })
    if not info_sub:
        return idx, {"mode": "no_subcluster", "n": len(idx)}
    # 锚定：18 占比最高者；占比接近(<5%差距)时取纯度更高者
    best = max(info_sub, key=lambda d: (round(d["frac18"], 1), d["purity"]))
    cand_sorted = sorted(info_sub, key=lambda d: (-d["frac18"], -d["purity"]))
    if len(cand_sorted) > 1 and abs(cand_sorted[0]["frac18"] - cand_sorted[1]["frac18"]) < 0.05:
        best = max(cand_sorted[:2], key=lambda d: d["purity"])
    sel = np.where(lab == best["cluster"])[0]
    core = idx[sel]
    # 迭代提纯
    removed = 0
    for _ in range(args.purify_rounds):
        Xc = V[core]
        if len(Xc) < 30:
            break
        S = Xc @ Xc.T
        np.fill_diagonal(S, -1.0)
        med = np.median(S, axis=1)
        thr = np.quantile(med, args.purify_pct)
        keep = np.where(med >= thr)[0]
        if len(keep) < 30 or len(keep) == len(core):
            break
        removed += len(core) - len(keep)
        core = core[keep]
    info = {"mode": "subcluster", "k": k, "subclusters": info_sub,
            "chosen": best, "n_cand": len(idx), "n_core": len(core),
            "removed_by_purify": removed,
            "core_purity": round(purity(V[core]), 3)}
    return core, info


# ---------------- 训练与评估 ----------------
def fit_lr(Xtr, ytr, C=1.0):
    return LogisticRegression(C=C, max_iter=3000, class_weight="balanced",
                              solver="lbfgs").fit(Xtr, ytr)


def cv_scores(Xpos, Xneg_pool, C, n_fold=5, neg_sample=6000, rng=None):
    """每折：正样本 4/5 训练 1/5 测试；负样本每折重采样，未用的作 clean holdout"""
    rng = rng or np.random.default_rng(SEED)
    kf = KFold(n_splits=n_fold, shuffle=True, random_state=SEED)
    pos_oof = np.zeros(len(Xpos))
    neg_all = []
    for tr_i, te_i in kf.split(Xpos):
        n_neg = min(neg_sample, len(Xneg_pool))
        sel = rng.choice(len(Xneg_pool), n_neg, replace=False)
        Xtr = np.vstack([Xpos[tr_i], Xneg_pool[sel]])
        ytr = np.r_[np.ones(len(tr_i)), np.zeros(n_neg)]
        clf = fit_lr(Xtr, ytr, C=C)
        pos_oof[te_i] = clf.decision_function(Xpos[te_i])
        mask = np.ones(len(Xneg_pool), dtype=bool)
        mask[sel] = False
        ho = Xneg_pool[mask]
        if len(ho) > 20000:
            ho = ho[rng.choice(len(ho), 20000, replace=False)]
        neg_all.append(clf.decision_function(ho))
    return pos_oof, np.concatenate(neg_all)


# ---------------- 主流程 ----------------
def build(person, faces, M, cmap, args):
    db = sqlite3.connect(str(LIB_DB))
    cand, ck18 = collect_candidates(faces, db, person, cmap)
    own_keys = group_keys(db, person)
    db.close()
    if len(cand) < 40:
        return None, f"{person}: 候选不足 ({len(cand)})"

    core, info = select_core(cand, ck18, faces, M["fused"], args)
    if len(core) < 30:
        return None, f"{person}: 核心 seed 不足 ({len(core)})"

    # 负样本池：全库 - 本人照片 - 该 cluster/18目录候选池(身份不明，既不当正也不当负)
    cand_set = set(cand.keys())
    neg_idx = np.array([i for i, r in enumerate(faces)
                        if i not in cand_set and r[1] not in own_keys and ok_quality(r)])
    rng = np.random.default_rng(SEED)

    print(f"\n{'='*96}")
    print(f"【{person}】候选 {info.get('n_cand', len(cand))} → 核心 seed {len(core)} "
          f"(剔除 {info.get('removed_by_purify', 0)}) | 负样本池 {len(neg_idx)}")
    if info.get("subclusters"):
        for s in info["subclusters"]:
            mark = "★" if s["cluster"] == info["chosen"]["cluster"] else " "
            print(f"   {mark} 子簇{s['cluster']}: n={s['n']:<5} 纯度={s['purity']:.3f} "
                  f"18目录占比={s['frac18']:.2f} ({s['n18']})")
    print(f"   核心 seed 纯度 {info.get('core_purity', 0):.3f}")

    # ---- kNN baseline（对比用）----
    knn_res = {}
    if args.baseline:
        for k in (1, 5):
            G = M["fused"][core]
            Sp = M["fused"][core] @ G.T
            np.fill_diagonal(Sp, -1.0)
            pos_s = topk_mean(Sp, k)
            neg_s = topk_mean(score_matrix(M["fused"][neg_idx], G), k)
            r = pick_threshold(pos_s, neg_s, 0.01)
            knn_res[k] = {"recall@1%": r[1], "th": r[0]}
        print(f"  [kNN baseline] top1 recall@FPR1%={knn_res[1]['recall@1%']:.3f} "
              f"| top5={knn_res[5]['recall@1%']:.3f}")

    # ---- 空间选择 ----
    space_scores = {}
    for sp in ("mbf", "r50", "fused"):
        Xp, Xn = M[sp][core], M[sp][neg_idx]
        best = None
        for C in C_GRID:
            po, no = cv_scores(Xp, Xn, C, n_fold=3, neg_sample=args.neg_sample, rng=rng)
            r01 = pick_threshold(po, no, 0.01)
            r005 = pick_threshold(po, no, 0.005)
            sc = r01[1] + 0.5 * r005[1]
            if best is None or sc > best[0]:
                best = (sc, C, r01[1], r005[1])
        space_scores[sp] = {"C": best[1], "r01": best[2], "r005": best[3]}
        print(f"  [空间 {sp:<6}] C={best[1]:<5} recall@FPR1%={best[2]:.3f} "
              f"@FPR0.5%={best[3]:.3f}")
    space = max(space_scores, key=lambda s: space_scores[s]["r01"]
                + 0.5 * space_scores[s]["r005"])
    C_best = space_scores[space]["C"]
    print(f"  → 选用空间 {space}, C={C_best}")

    Xpos_all, Xneg_all = M[space][core], M[space][neg_idx]

    # ---- 难负样本挖掘 ----
    hard_idx = np.array([], dtype=int)
    hist = []
    for rnd in range(1, args.rounds + 1):
        n_rand = min(args.neg_sample, len(Xneg_all))
        rnd_sel = rng.choice(len(Xneg_all), n_rand, replace=False)
        parts = [Xpos_all, Xneg_all[rnd_sel]]
        ys = [np.ones(len(Xpos_all)), np.zeros(n_rand)]
        if len(hard_idx):
            parts += [Xneg_all[hard_idx]] * 3
            ys += [np.zeros(len(hard_idx))] * 3
        clf = fit_lr(np.vstack(parts), np.r_[tuple(ys)], C=C_best)
        s_neg = clf.decision_function(Xneg_all)
        cand_h = np.argsort(-s_neg)[:args.hard_top * 3]
        cand_h = cand_h[s_neg[cand_h] < HARD_CAP][:args.hard_top]
        hard_idx = cand_h
        Xn_cv = (np.vstack([Xneg_all, Xneg_all[hard_idx]]) if len(hard_idx)
                 else Xneg_all)
        po, no = cv_scores(Xpos_all, Xn_cv, C_best, n_fold=5,
                           neg_sample=args.neg_sample, rng=rng)
        r005 = pick_threshold(po, no, 0.005)
        r01 = pick_threshold(po, no, 0.01)
        r02 = pick_threshold(po, no, 0.02)
        hist.append({"round": rnd, "hard": len(hard_idx),
                     "r005": round(r005[1], 4), "r01": round(r01[1], 4),
                     "r02": round(r02[1], 4)})
        print(f"  轮{rnd}: hard={len(hard_idx):<4} recall@FPR0.5%={r005[1]:.3f} "
              f"@1%={r01[1]:.3f} @2%={r02[1]:.3f}")

    # ---- 最终模型 ----
    n_rand = min(args.neg_sample, len(Xneg_all))
    rnd_sel = rng.choice(len(Xneg_all), n_rand, replace=False)
    parts = [Xpos_all, Xneg_all[rnd_sel]]
    ys = [np.ones(len(Xpos_all)), np.zeros(n_rand)]
    if len(hard_idx):
        parts += [Xneg_all[hard_idx]] * 3
        ys += [np.zeros(len(hard_idx))] * 3
    clf = fit_lr(np.vstack(parts), np.r_[tuple(ys)], C=C_best)

    mask = np.ones(len(Xneg_all), dtype=bool)
    mask[rnd_sel] = False
    mask[hard_idx] = False
    Xho = Xneg_all[mask]
    s_neg = clf.decision_function(Xho)
    s_pos = clf.decision_function(Xpos_all)
    ths = {}
    for t in DEFAULT_TARGETS:
        th, rec, fpr = pick_threshold(s_pos, s_neg, t)
        ths[f"fpr{t}"] = {"th": round(th, 3), "recall": round(rec, 4),
                          "fpr": round(fpr, 5)}
    po, no = cv_scores(Xpos_all, Xneg_all, C_best, n_fold=5,
                       neg_sample=args.neg_sample, rng=rng)
    cv_r01 = pick_threshold(po, no, 0.01)
    cv_r005 = pick_threshold(po, no, 0.005)

    base_r = knn_res.get(5, {}).get("recall@1%") if args.baseline else None
    gain = (f"  (kNN基线 {base_r:.3f} → {cv_r01[1]:.3f}, "
            f"{cv_r01[1]-base_r:+.3f})") if base_r is not None else ""
    print(f"  ★ 最终 CV: recall@FPR0.5%={cv_r005[1]:.3f} "
          f"@FPR1%={cv_r01[1]:.3f}{gain}")
    print(f"    阈值 conservative(FPR0.5%)={ths['fpr0.005']['th']} "
          f"balanced(FPR1%)={ths['fpr0.01']['th']} "
          f"aggressive(FPR2%)={ths['fpr0.02']['th']}")

    if args.eval_only:
        return {"person": person, "space": space, "recall": cv_r01[1],
                "th": ths["fpr0.01"]["th"], "n_pos": len(Xpos_all),
                "purity": info.get("core_purity", 0)}, None

    out_dir = OUT_BASE / person
    out_dir.mkdir(parents=True, exist_ok=True)
    model = {
        "person": person, "version": "clf_v1", "type": "logistic_regression",
        "space": space, "C": C_best,
        "n_pos": int(len(Xpos_all)), "n_neg_pool": int(len(Xneg_all)),
        "n_hard": int(len(hard_idx)),
        "core_purity": info.get("core_purity"),
        "seed_info": info,
        "coef": [round(float(x), 8) for x in clf.coef_[0]],
        "intercept": round(float(clf.intercept_[0]), 8),
        "thresholds": {
            "targets": DEFAULT_TARGETS,
            "recommended": {
                "conservative": ths["fpr0.005"]["th"],
                "balanced": ths["fpr0.01"]["th"],
                "aggressive": ths["fpr0.02"]["th"],
            },
            "table": [{"th": round(-4 + 0.5 * i, 2),
                       "recall": round(float((s_pos >= -4 + 0.5 * i).mean()), 4),
                       "fpr": round(float((s_neg >= -4 + 0.5 * i).mean()), 5)}
                      for i in range(21)],
        },
        "eval": {
            "cv_recall_at_fpr005": round(cv_r005[1], 4),
            "cv_recall_at_fpr01": round(cv_r01[1], 4),
            "space_compare": {s: {"C": space_scores[s]["C"],
                                  "recall@FPR1%": round(space_scores[s]["r01"], 4),
                                  "recall@FPR0.5%": round(space_scores[s]["r005"], 4)}
                              for s in space_scores},
            "hard_mining_history": hist,
            "knn_baseline": ({str(k): {"recall@FPR1%": round(v["recall@1%"], 4),
                                       "th": v["th"]}
                              for k, v in knn_res.items()} if args.baseline else {}),
            "pos_score_p05": round(float(np.quantile(s_pos, 0.05)), 3),
            "pos_score_p50": round(float(np.quantile(s_pos, 0.50)), 3),
            "neg_score_p99": round(float(np.quantile(s_neg, 0.99)), 3),
            "neg_score_max": round(float(s_neg.max()), 3),
        },
        "core_face_ids": [int(faces[i][0]) for i in core],
        "quality_filter": {"min_det": MIN_DET, "min_side": MIN_SIDE},
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "source": "07cluster∪18目录 候选 → 子簇锚定+提纯 → LR + 难负挖掘",
    }
    out = out_dir / f"{person}_clf_v1.json"
    json.dump(model, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"  → 已写入 {out.relative_to(ROOT)} ({out.stat().st_size/1024:.0f} KB)")
    return {"person": person, "space": space, "recall": cv_r01[1],
            "th": ths["fpr0.01"]["th"], "n_pos": len(Xpos_all),
            "purity": info.get("core_purity", 0)}, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--person")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--neg-sample", type=int, default=6000)
    ap.add_argument("--hard-top", type=int, default=500)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--subclusters", type=int, default=3)
    ap.add_argument("--purify-rounds", type=int, default=3)
    ap.add_argument("--purify-pct", type=float, default=0.05)
    ap.add_argument("--no-baseline", dest="baseline", action="store_false")
    ap.add_argument("--eval-only", action="store_true")
    args = ap.parse_args()

    persons = FAMILY if args.all else [args.person]
    if not persons or not persons[0]:
        ap.error("需要 --person 或 --all")

    t0 = time.time()
    print("加载全库人脸 …", flush=True)
    faces = load_faces()
    M = build_matrices(faces)
    cmap = cluster_map()
    print(f"  {len(faces)} 张脸，07 命名簇 {len(cmap)} 个", flush=True)

    summary = []
    for p in persons:
        r, err = build(p, faces, M, cmap, args)
        if err:
            print("SKIP:", err)
            continue
        summary.append(r)

    print(f"\n{'='*96}\n汇总（用时 {time.time()-t0:.0f}s）")
    print(f"  {'人物':<12}{'空间':<8}{'seed':<7}{'seed纯度':<10}"
          f"{'CV recall@FPR1%':<18}{'阈值'}")
    for r in summary:
        print(f"  {r['person']:<12}{r['space']:<8}{r['n_pos']:<7}"
              f"{r['purity']:<10.3f}{r['recall']:<18.3f}{r['th']}")


if __name__ == "__main__":
    main()

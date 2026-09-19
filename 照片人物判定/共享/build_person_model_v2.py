#!/usr/bin/env python3
"""为每个家庭成员构建判定模型 v2（准确率优化版）。

相对 v1 的关键改进：
  1. 负样本从「07 内部命名簇 ~2400 张」扩到「全库所有非本人脸 ~3.7 万张」，
     把 09 班级相册的大量幼童脸纳入校准 —— v1 的妈妈模型在 09 上把戴眼镜幼童
     判成 0.80 高分，根因就是负样本从未见过这些脸。
  2. 打分从 top1 max 改为 top-k 均值（沿用乐仔 v4 的 top5 做法），抑制单例噪声。
  3. 嵌入空间三选一：mbf / r50 / fused(分数平均)，按 recall@FPR 实测择优。
  4. gallery 经「互近邻中位数」迭代提纯 + 最远点采样保证多样性。
  5. 阈值按 FPR 严格校准，输出 conservative/balanced/aggressive 三档。

用法：
    python build_person_model_v2.py --person 妈妈
    python build_person_model_v2.py --all
    python build_person_model_v2.py --person 乐仔 --eval-only   # 只评估不写盘
"""
import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
LIB_DB = ROOT / "19_统一相册库" / "library.db"
P07_DB = ROOT / "07_相册" / "相册&视频备份" / "_photo_index" / "photo_index.db"
OUT_BASE = ROOT / "照片人物判定"

FAMILY = ["乐仔", "乐仔小时候", "艳艳", "我", "妈妈", "七月", "爸爸"]
# 互斥组：组内互为「同一人的不同形态」，不互相充当负样本
MUTEX_GROUPS = [{"乐仔", "乐仔小时候"}]

MIN_DET = 0.70      # 人脸检测分下限
MIN_SIDE = 40       # 人脸框最短边下限(px)
TOPK_GRID = [1, 3, 5, 8]
DEFAULT_TARGETS = [0.001, 0.005, 0.02]


# ---------- 基础工具 ----------
def l2n(X, axis=1):
    X = np.asarray(X, dtype=np.float32)
    n = np.linalg.norm(X, axis=axis, keepdims=True)
    n[n < 1e-9] = 1.0
    return X / n


def topk_mean(S, k):
    """S: (n, g) 相似度矩阵 → 每行 top-k 均值"""
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


def farthest_point_sample(X, n):
    """最远点采样：保证 gallery 覆盖不同角度/年龄，而非堆重复正脸"""
    m = len(X)
    if m <= n:
        return np.arange(m)
    proto = X.mean(0)
    proto /= (np.linalg.norm(proto) + 1e-9)
    start = int(np.argmax(X @ proto))
    sel = [start]
    max_sim = X @ X[start]
    for _ in range(n - 1):
        nxt = int(np.argmin(max_sim))
        sel.append(nxt)
        max_sim = np.maximum(max_sim, X @ X[nxt])
    return np.array(sel)


def pick_threshold(pos_s, neg_s, target_fpr):
    """给定目标 FPR，返回 (阈值, recall, 实际FPR)。取满足 FPR<=target 的最低阈值。"""
    grid = np.arange(0.30, 0.9001, 0.005)
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


def fmt(s):
    return np.asarray(s, dtype=np.float32)


# ---------- 数据加载 ----------
def load_faces():
    """返回 (face_id, content_key, lib, det_score, w, h, cluster, emb_mbf, emb_r50)"""
    db = sqlite3.connect(str(LIB_DB))
    rows = db.execute(
        "SELECT face_id, content_key, lib, det_score, w, h, cluster, emb_mbf, emb_r50 "
        "FROM faces WHERE emb_mbf IS NOT NULL AND emb_r50 IS NOT NULL"
    ).fetchall()
    db.close()
    return rows


def cluster_map():
    db = sqlite3.connect(str(P07_DB))
    m = {int(c): n for c, n in db.execute(
        "SELECT cluster, name FROM persons WHERE name IS NOT NULL")}
    db.close()
    return m


def person_content_keys(db, person):
    """该人已确认归属的照片 content_key 集合（用于排除负样本污染）"""
    keys = set()
    for (ck,) in db.execute(
            "SELECT DISTINCT content_key FROM photo_person_tags WHERE person=?", (person,)):
        keys.add(ck)
    for (ck,) in db.execute(
            "SELECT DISTINCT content_key FROM files WHERE lib='18' AND rel LIKE ?",
            (person + "/%",)):
        keys.add(ck)
    return keys


def group_content_keys(db, person):
    """互斥组内所有人的照片（互相不作为负样本）"""
    keys = set(person_content_keys(db, person))
    for grp in MUTEX_GROUPS:
        if person in grp:
            for other in grp:
                if other != person:
                    keys |= person_content_keys(db, other)
    return keys


# ---------- 建模主流程 ----------
def build(person, faces, cmap, args):
    db = sqlite3.connect(str(LIB_DB))
    cm = {v: k for k, v in cmap.items()}          # name -> cluster
    own_cluster = cm.get(person)

    # ---- 1) 收集 seed ----
    seeds = {}      # face_id -> (content_key, src)
    if own_cluster is not None:
        for fid, ck, lib, ds, w, h, cl, em, er in faces:
            if cl == own_cluster and lib == "07":
                seeds[fid] = (ck, "07_cluster")
    for fid, ck, lib, ds, w, h, cl, em, er in faces:
        pass
    # 18 人物目录（人工确认的单人照片）
    ck18 = {r[0] for r in db.execute(
        "SELECT DISTINCT content_key FROM files WHERE lib='18' AND rel LIKE ?",
        (person + "/%",))}
    for fid, ck, lib, ds, w, h, cl, em, er in faces:
        if ck in ck18:
            seeds.setdefault(fid, (ck, "18_group"))

    # ---- 2) 质量过滤 ----
    fid2face = {r[0]: r for r in faces}
    keep = [fid for fid in seeds
            if fid in fid2face and fid2face[fid][3] >= MIN_DET
            and min(fid2face[fid][4] or 0, fid2face[fid][5] or 0) >= MIN_SIDE]
    dropped_quality = len(seeds) - len(keep)
    if not keep:
        db.close()
        return None, f"{person}: 无可用 seed"

    idx = {fid: i for i, fid in enumerate([r[0] for r in faces])}
    order = [idx[fid] for fid in keep]

    # ---- 3) 三空间向量 ----
    def vecs(rows, field):
        return l2n(np.vstack([np.frombuffer(r[field], dtype=np.float32) for r in rows]))
    frows = [faces[i] for i in order]
    V = {"mbf": vecs(frows, 7), "r50": vecs(frows, 8)}
    V["fused"] = l2n(np.hstack([V["mbf"], V["r50"]]))

    # ---- 4) 迭代提纯（互近邻中位数，剔除簇内异类）----
    removed = []
    alive = np.ones(len(order), dtype=bool)
    for rnd in range(args.purify_rounds):
        X = V["fused"][alive]
        S = X @ X.T
        np.fill_diagonal(S, -1.0)
        med = np.median(S, axis=1)
        thr = np.quantile(med, args.purify_pct)
        bad_local = np.where(med < thr)[0]
        if len(bad_local) == 0:
            break
        alive_idx = np.where(alive)[0]
        for b in bad_local:
            removed.append(int(frows[alive_idx[b]][0]))
            alive[alive_idx[b]] = False
        if alive.sum() < 20:
            break

    seed_ids = [frows[i][0] for i in np.where(alive)[0]]
    srcs = [seeds[f][1] for f in seed_ids]
    V2 = {k: v[alive] for k, v in V.items()}

    # ---- 5) gallery 选取（FPS 保多样性）----
    n_sel = min(args.max_gallery, len(seed_ids))
    sel = farthest_point_sample(V2["fused"], n_sel)
    gallery_ids = [seed_ids[i] for i in sel]
    G = {k: v[sel] for k, v in V2.items()}
    # 未被选入 gallery 的 seed 仍可作为正样本评估（LOO 用全集更稳，这里用全集）

    # ---- 6) 负样本：全库非本人脸 ----
    own_keys = group_content_keys(db, person)
    neg_rows = []
    for r in faces:
        fid, ck = r[0], r[1]
        if fid in seed_ids:
            continue
        if ck in own_keys:
            continue
        if r[3] < MIN_DET or min(r[4] or 0, r[5] or 0) < MIN_SIDE:
            continue
        neg_rows.append(r)
    db.close()

    n_pos, n_neg = len(seed_ids), len(neg_rows)
    if n_neg < 100:
        return None, f"{person}: 负样本不足 ({n_neg})"

    NV = {"mbf": vecs(neg_rows, 7), "r50": vecs(neg_rows, 8)}
    NV["fused"] = l2n(np.hstack([NV["mbf"], NV["r50"]]))

    # ---- 7) 评估：空间 × topk ----
    results = {}
    for space in ("mbf", "r50", "fused"):
        Gs, Ns, Ps = G[space], NV[space], V2[space]
        # 正样本 LOO：每个 seed 从 gallery 中排除自己
        S_pos = Ps @ Gs.T                       # (n_pos, n_gal)
        for i, sid in enumerate(seed_ids):
            if sid in gallery_ids:
                j = gallery_ids.index(sid)
                S_pos[i, j] = -1.0
        S_neg_sp = score_matrix(Ns, Gs)
        for k in TOPK_GRID:
            pos_s = topk_mean(S_pos, k)
            neg_s = topk_mean(S_neg_sp, k)
            ths = {}
            for t in DEFAULT_TARGETS:
                th, rec, fpr = pick_threshold(pos_s, neg_s, t)
                ths[f"fpr{t}"] = {"th": round(th, 3), "recall": round(rec, 4),
                                  "fpr": round(fpr, 5)}
            results[(space, k)] = {
                "pos": pos_s, "neg": neg_s,
                "recall_at_fpr001": ths["fpr0.001"]["recall"],
                "ths": ths,
                "neg_p99": round(float(np.quantile(neg_s, 0.99)), 3),
                "neg_max": round(float(neg_s.max()), 3),
                "pos_p05": round(float(np.quantile(pos_s, 0.05)), 3),
                "pos_p50": round(float(np.quantile(pos_s, 0.50)), 3),
            }
        del S_neg_sp

    # 选最佳：以 recall@FPR=0.1% 为主指标，平手时看 neg_p99 更低者
    best_key = max(results, key=lambda kk: (results[kk]["recall_at_fpr001"],
                                            -results[kk]["neg_p99"]))
    best = results[best_key]
    space, topk = best_key

    print(f"\n{'='*92}")
    print(f"【{person}】 seed={len(seeds)} 质量过滤-{dropped_quality} 提纯-{len(removed)} "
          f"→ 正样本 {n_pos} | gallery {len(gallery_ids)} | 负样本 {n_neg}")
    print(f"  gallery 来源: " + ", ".join(
        f"{s}={srcs.count(s)}" for s in sorted(set(srcs))))
    print(f"  {'(space,topk)':<16}{'R@FPR.1%':<10}{'R@FPR.5%':<10}{'R@FPR2%':<10}"
          f"{'neg_p99':<9}{'neg_max':<9}{'pos_p05':<9}")
    for kk in sorted(results, key=lambda x: (x[0], x[1])):
        r = results[kk]
        print(f"  {str(kk):<16}{r['recall_at_fpr001']:<10.3f}"
              f"{r['ths']['fpr0.005']['recall']:<10.3f}"
              f"{r['ths']['fpr0.02']['recall']:<10.3f}"
              f"{r['neg_p99']:<9.3f}{r['neg_max']:<9.3f}{r['pos_p05']:<9.3f}")
    print(f"  ★ 最佳: space={space} topk={topk}  recall@FPR0.1%="
          f"{best['recall_at_fpr001']:.3f}  阈值 "
          f"conservative={best['ths']['fpr0.001']['th']} / "
          f"balanced={best['ths']['fpr0.005']['th']} / "
          f"aggressive={best['ths']['fpr0.02']['th']}")

    if args.eval_only:
        return {"person": person, "best": (space, topk), "recall": best["recall_at_fpr001"]}, None

    # ---- 8) 写盘 ----
    out_dir = OUT_BASE / person
    out_dir.mkdir(parents=True, exist_ok=True)
    gal = []
    for i, gid in enumerate(gallery_ids):
        gal.append({
            "face_id": gid,
            "photo": seeds[gid][0],
            "src": srcs[sel[i]] if False else srcs[np.where(np.array(seed_ids) == gid)[0][0]],
            "embedding": [round(float(x), 6) for x in G[space][i]],
        })
    proto = l2n(G[space].mean(0, keepdims=True))[0]
    model = {
        "person": person,
        "version": "v2",
        "space": space,
        "topk": topk,
        "n_gallery": len(gal),
        "n_seeds": n_pos,
        "n_neg_eval": n_neg,
        "proto": [round(float(x), 6) for x in proto],
        "gallery": gal,
        "thresholds": {
            "targets": DEFAULT_TARGETS,
            "recommended": {
                "conservative": best["ths"]["fpr0.001"]["th"],
                "balanced": best["ths"]["fpr0.005"]["th"],
                "aggressive": best["ths"]["fpr0.02"]["th"],
            },
            "table": [{"th": round(0.30 + 0.02 * i, 2),
                       "recall": round(float((best["pos"] >= 0.30 + 0.02 * i).mean()), 4),
                       "fpr": round(float((best["neg"] >= 0.30 + 0.02 * i).mean()), 5)}
                      for i in range(31)],
        },
        "eval": {
            "space_compare": {f"{s}|k{k}": {
                "recall_at_fpr001": results[(s, k)]["recall_at_fpr001"],
                "neg_p99": results[(s, k)]["neg_p99"],
                "neg_max": results[(s, k)]["neg_max"],
            } for s in ("mbf", "r50", "fused") for k in TOPK_GRID},
            "chosen": {"space": space, "topk": topk,
                       "recall_at_fpr001": best["recall_at_fpr001"],
                       "recall_at_fpr005": best["ths"]["fpr0.005"]["recall"],
                       "recall_at_fpr02": best["ths"]["fpr0.02"]["recall"],
                       "pos_p05": best["pos_p05"], "pos_p50": best["pos_p50"],
                       "neg_p99": best["neg_p99"], "neg_max": best["neg_max"]},
        },
        "removed_seeds": removed,
        "quality_filter": {"min_det": MIN_DET, "min_side": MIN_SIDE,
                           "dropped": dropped_quality},
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "source": "统一库全库人脸(双嵌入) + 07 cluster真值 + 18人物目录",
    }
    out = out_dir / f"{person}_model_v2.json"
    json.dump(model, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    size_mb = out.stat().st_size / 1e6
    print(f"  → 已写入 {out.relative_to(ROOT)} ({size_mb:.1f} MB)")
    return {"person": person, "space": space, "topk": topk,
            "recall": best["recall_at_fpr001"],
            "th": best["ths"]["fpr0.005"]["th"]}, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--person", help="人物名，如 妈妈")
    ap.add_argument("--all", action="store_true", help="全部家庭成员")
    ap.add_argument("--max-gallery", type=int, default=800)
    ap.add_argument("--purify-rounds", type=int, default=3)
    ap.add_argument("--purify-pct", type=float, default=0.05,
                    help="每轮剔除互近邻中位数最低的比例")
    ap.add_argument("--eval-only", action="store_true")
    args = ap.parse_args()

    persons = FAMILY if args.all else [args.person]
    if not persons or not persons[0]:
        ap.error("需要 --person 或 --all")

    t0 = time.time()
    print(f"加载全库人脸 …", flush=True)
    faces = load_faces()
    cmap = cluster_map()
    print(f"  {len(faces)} 张脸（双嵌入），07 命名簇 {len(cmap)} 个", flush=True)

    summary = []
    for p in persons:
        r, err = build(p, faces, cmap, args)
        if err:
            print("SKIP:", err)
            continue
        summary.append(r)

    print(f"\n{'='*92}\n汇总（用时 {time.time()-t0:.0f}s）")
    print(f"  {'人物':<10}{'空间':<8}{'topk':<6}{'recall@FPR0.1%':<16}{'balanced阈值'}")
    for r in summary:
        print(f"  {r['person']:<10}{r.get('space','-'):<8}{r.get('topk','-'):<6}"
              f"{r['recall']:<16.3f}{r.get('th','-')}")


if __name__ == "__main__":
    main()

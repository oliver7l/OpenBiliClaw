#!/usr/bin/env python3
"""统一人物判定建模（07_相册 库，r50 空间，乐仔 09 kNN 范式）。

对指定人物：
1. gallery = lib_tiers A 级脸（r50 嵌入，npz）
2. 种子提纯：自库 LOO kNN 弱(<0.30) 或 与他人 A 级脸高相似(>=0.70) 的种子剔除并记录
3. 负例评测：其他全部命名人物的 A 级脸（家人脸 = 最难干扰项）
4. 阈值表：正/负分布 + FPR<=1% 推荐阈值
5. 产出 <人>_model_v1.json + 建模过程.md

用法: build_person_model.py 妈妈 艳艳 我 爸爸 七月
"""
import sqlite3
import json
import os
import sys
import datetime
import numpy as np

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
PI = f"{ROOT}/07_相册/相册&视频备份/_photo_index"
OUT_BASE = f"{ROOT}/照片人物判定"

LOO_MIN = 0.30      # 自库 LOO top5 下限（低于则种子可疑）
CROSS_MAX = 0.70    # 与他人 A 级脸最大相似度上限（超过则可疑）
NEG_FPR = 0.01      # 推荐阈值的负例误报率上限
GRID = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65]


def l2n(v):
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


def top5_mean(S, k=5):
    """S: (n,m) 相似度矩阵 -> 每行 top-k 均值"""
    k = min(k, S.shape[1])
    part = np.partition(S, -k, axis=1)[:, -k:]
    return part.mean(axis=1)


def build(person, con, E):
    others = [r[0] for r in con.execute(
        "SELECT DISTINCT p.name FROM persons p JOIN faces f ON f.cluster=p.cluster "
        "JOIN lib_tiers t ON t.face_id=f.id WHERE p.name != ? AND t.tier='A'", (person,))]

    pos_rows = con.execute(
        """SELECT t.face_id, f.file_key, fi.path FROM lib_tiers t
           JOIN faces f ON f.id=t.face_id JOIN persons p ON p.cluster=f.cluster
           LEFT JOIN files fi ON fi.path=f.file_key
           WHERE p.name=? AND t.tier='A'""", (person,)).fetchall()
    pos = [(fid, path or fk) for fid, fk, path in pos_rows if fid in E]
    G = np.vstack([l2n(E[fid]) for fid, _ in pos])
    print(f"\n===== {person} =====\nA级 {len(pos_rows)}, npz覆盖 {len(pos)}")

    # 负例：其他命名人物全部 A 级
    neg_ids, neg_names = [], []
    for o in others:
        for (fid,) in con.execute(
                """SELECT t.face_id FROM lib_tiers t JOIN faces f ON f.id=t.face_id
                   JOIN persons p ON p.cluster=f.cluster WHERE p.name=? AND t.tier='A'""", (o,)):
            if fid in E:
                neg_ids.append(fid)
                neg_names.append(o)
    Xneg = np.vstack([l2n(E[i]) for i in neg_ids])
    print(f"负例 {len(neg_ids)} 张（来自 {len(others)} 人: {','.join(others)}）")

    # 种子提纯（LOO + 交叉）
    S_self = G @ G.T
    np.fill_diagonal(S_self, -1)
    loo = top5_mean(S_self)
    S_cross = Xneg @ G.T
    cross = S_cross.max(axis=0)  # 每个种子(列)与全部负例的最大相似度
    susp = (loo < LOO_MIN) | (cross >= CROSS_MAX)
    n_susp = int(susp.sum())
    keep_mask = ~susp
    G2 = G[keep_mask]
    pos_kept = [p for p, m in zip(pos, keep_mask) if m]
    susp_ids = [pos[i][0] for i in range(len(pos)) if susp[i]]
    print(f"提纯: 剔除可疑种子 {n_susp} 张（LOO弱 {int((loo<LOO_MIN).sum())} / 交叉撞 {int((cross>=CROSS_MAX).sum())}），"
          f"保留 {len(pos_kept)}")

    # 提纯后重算指标
    S2 = G2 @ G2.T
    np.fill_diagonal(S2, -1)
    loo2 = top5_mean(S2)
    neg2 = top5_mean(Xneg @ G2.T)
    pos_stats = np.percentile(loo2, [5, 50, 95])
    neg_stats = np.percentile(neg2, [50, 95, 100])
    print(f"正例 LOO top5: p5={pos_stats[0]:.3f} p50={pos_stats[1]:.3f} p95={pos_stats[2]:.3f}")
    print(f"负例 top5:     p50={neg_stats[0]:.3f} p95={neg_stats[1]:.3f} max={neg_stats[2]:.3f}")

    # 阈值表
    table, rec_th, rec = [], None, None
    for th in GRID:
        fp = float((neg2 >= th).mean())
        tp = float((loo2 >= th).mean())
        table.append({"th": th, "recall": round(tp, 4), "fpr": round(fp, 4)})
        if rec_th is None and fp <= NEG_FPR:
            rec_th, rec = th, (tp, fp)
    if rec_th is None:
        rec_th = min(GRID)
        rec = (float((loo2 >= rec_th).mean()), float((neg2 >= rec_th).mean()))
    print("阈值表:", table)
    print(f"推荐阈值 {rec_th}（召回 {rec[0]:.3f} / 误报 {rec[1]:.4f}）")

    proto = l2n(G2.mean(axis=0)).tolist()
    model = {
        "person": person, "version": "v1", "space": "buffalo_l ArcFace r50",
        "n_gallery": len(pos_kept), "n_removed_seeds": n_susp,
        "proto": proto,
        "gallery": [{"face_id": fid, "photo": ph, "embedding": np.round(l2n(E[fid]), 6).tolist()}
                    for fid, ph in pos_kept],
        "thresholds": {"recommended": rec_th, "fpr_target": NEG_FPR,
                       "table": table,
                       "neg_max": round(float(neg2.max()), 4)},
        "eval": {"pos_loo_p5": round(float(pos_stats[0]), 4),
                 "pos_loo_p50": round(float(pos_stats[1]), 4),
                 "pos_loo_p95": round(float(pos_stats[2]), 4),
                 "neg_p50": round(float(neg_stats[0]), 4),
                 "neg_p95": round(float(neg_stats[1]), 4),
                 "neg_max": round(float(neg_stats[2]), 4),
                 "neg_n": len(neg_ids), "neg_persons": others},
        "removed_seeds": susp_ids,
        "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": "07 lib_tiers A级 + r50_sample_emb.npz",
    }
    out_dir = f"{OUT_BASE}/{person}"
    os.makedirs(out_dir, exist_ok=True)
    mp = f"{out_dir}/{person}_model_v1.json"
    json.dump(model, open(mp, "w", encoding="utf-8"), ensure_ascii=False)
    print("写出", mp, os.path.getsize(mp) // 1024, "KB")

    md = f"""# {person} 判定模型 v1 · 建模过程

- 日期：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}
- 空间：buffalo_l ArcFace r50（与 07 `r50_sample_emb.npz`、09 乐仔模型同空间，可直接互比）
- 数据源：07 `photo_index.db` lib_tiers A 级 + `r50_sample_emb.npz`

## 1. 种子库
- A 级脸 {len(pos_rows)} 张（npz 覆盖 {len(pos)}）
- 种子提纯剔除 {n_susp} 张：自库 LOO top5 < {LOO_MIN}（{int((loo < LOO_MIN).sum())} 张）或
  与他人 A 级脸相似 >= {CROSS_MAX}（{int((cross >= CROSS_MAX).sum())} 张）
- 最终 gallery：{len(pos_kept)} 张

## 2. 评测（负例 = 其他命名人物全部 A 级脸 {len(neg_ids)} 张：{', '.join(others)}）
| 指标 | 数值 |
|---|---|
| 正例 LOO top5 p5 / p50 / p95 | {pos_stats[0]:.3f} / {pos_stats[1]:.3f} / {pos_stats[2]:.3f} |
| 负例 top5 p50 / p95 / max | {neg_stats[0]:.3f} / {neg_stats[1]:.3f} / {neg_stats[2]:.3f} |

## 3. 阈值表
| 阈值 | 召回 | 误报 FPR |
|---|---|---|
""" + "\n".join(f"| {t['th']} | {t['recall']} | {t['fpr']} |" for t in table) + f"""

**推荐工作阈值 {rec_th}**（FPR <= {NEG_FPR:.0%} 下召回 {rec[0]:.1%}；负例最高分 {neg2.max():.3f}）。

## 4. 判据（乐仔 09 范式）
对目标脸取与 gallery 的 kNN top-5 平均相似度：>= 高置信阈值直接判是；介于负例 max 与高置信之间为灰色带待复核；低于不动。

## 5. 注意
- 妈妈桶另有 872 张 B 级待用户纠错，纠错后应重建 v2。
- 此负例集为"家人脸"最难干扰项，全库未命名陌生人脸干扰更小，实际精度应更好。
"""
    open(f"{out_dir}/建模过程.md", "w", encoding="utf-8").write(md)
    print("写出", f"{out_dir}/建模过程.md")
    return rec_th


def main():
    con = sqlite3.connect(f"{PI}/photo_index.db")
    d = np.load(f"{PI}/r50_sample_emb.npz")
    E = {int(i): e for i, e in zip(d["ids"], d["emb"])}
    persons = sys.argv[1:] or ["妈妈", "艳艳", "我", "爸爸", "七月"]
    for p in persons:
        build(p, con, E)


if __name__ == "__main__":
    main()

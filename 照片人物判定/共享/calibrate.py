#!/usr/bin/env python3
"""阈值标定：用 18 人工归档的「冲突率」当独立误报信号，找精度-召回的拐点。

为什么不用负样本池的 AUC：负样本池里混着大量未归档的真本人（"不在归档≠不是本人"），
AUC 会被系统性压低，且完全不含"同班同学"这种真实对手。
18 归档的**冲突**（模型判正、但人类把这张照片归在别人名下）是唯一干净的误报信号：
  · 它与训练正样本集互斥（归在别人名下的照片不会是训练正例）
  · 它是人类逐张归档的结果，不是我的肉眼先验

输出 per_person_thresholds.json：每人一个推荐阈值 + 完整扫描表。
"""
import os, sys, json, time, sqlite3, collections
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_ens as B
from selfaudit import get_scores, canon_set, canon

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB_DB = f"{ROOT}/19_统一相册库/library.db"
OUT = f"{ROOT}/照片人物判定/per_person_thresholds.json"
# 按「排名」而不是「分数」扫描：各人分数尺度不可比，排名才是可比的
GRID_R = [50, 100, 150, 200, 300, 400, 600, 800, 1000, 1200, 1500,
          2000, 3000, 5000, 8000, 12000, 16000, 20000, 25909]


def sweep_person(person, D, ck18, ens, u18):
    pp = canon(person)          # 乐仔小时候 与 乐仔 是同一人，必须规范化后再比对
    """按分数降序扫，逐档统计「归档确证 / 归档冲突 / 未标」。
    注意：各人的分数尺度完全不同（艳艳 top=3.8，乐仔小时候 top=0.06），
    所以绝对不能跨人共用绝对值阈值 —— 只能按人各标各的。"""
    lib = np.array(D["lib"], dtype=object)
    order = np.argsort(-ens)
    cks, pos, neg = set(), 0, 0
    rows = []
    gi = 0
    for rank, i in enumerate(order):
        c = str(D["ck"][i])
        if c in cks:
            continue
        cks.add(c)
        if c in ck18:
            w = canon_set(ck18[c])
            if w == {pp}:
                pos += 1
            elif pp not in w:
                neg += 1
        n_face = rank + 1
        if gi < len(GRID_R) and n_face >= GRID_R[gi]:
            gi += 1
        if gi and gi <= len(GRID_R) and n_face == GRID_R[gi - 1]:
            rows.append(dict(n_face=n_face, n_photo=len(cks),
                             t=float(ens[i]), pos=pos, neg=neg,
                             unk=len(cks) - pos - neg,
                             n09=int((lib[order[:n_face]] == "09").sum()),
                             prec=pos / max(1, pos + neg),
                             cap=6 * u18))
    return rows


def recommend(rows, u18, tgt=0.03):
    """取「归档冲突率 ≤ tgt」前提下召回最大的档（=分数最低的那档）。
    额外加一个跑飞护栏：命中照片数不得超过归档规模的 6 倍
    （否则对 妈妈/乐仔小时候 这种样本极少的人会一路滑到全库都判正）。"""
    ok = [r for r in rows if r["neg"] / max(1, r["pos"] + r["neg"]) <= tgt
          and r["n_photo"] <= 6 * u18 and r["pos"] >= 3]
    if not ok:
        return None
    return ok[-1]


def main():
    D = B.load()
    con = sqlite3.connect(LIB_DB)
    ck18 = collections.defaultdict(set)
    for rel, c in con.execute("select rel, content_key from files where lib='18'"):
        ck18[c].add(rel.split("/")[0])
    con.close()

    out = {}
    persons = sys.argv[1:] or B.FAMILY
    for person in persons:
        t0 = time.time()
        ens, lr = get_scores(person, D)
        u18 = sum(1 for c, w in ck18.items() if canon_set(w) == {canon(person)})
        rows = sweep_person(person, D, ck18, ens, u18)
        rec = recommend(rows, u18)
        out[person] = dict(u18=u18, grid=rows, recommend=rec,
                           note=("按排名标定：rank=全库按该人分数降序的名次。"
                                 "prec 只在 18 已标注样本上算得出，是下界；"
                                 "未标样本占多数时真实精度会更低，故用「冲突率≤3%」+ "
                                 "「命中≤6×归档规模」双护栏。"))
        print(f"\n===== {person} （归档 {u18} 张，{time.time()-t0:.0f}s）=====")
        print(f"{'名次':>7}{'照片':>7}{'09内':>6}{'归档正':>7}{'冲突':>6}"
              f"{'未标':>7}{'证明精度':>9}{'阈值':>8}")
        for r in rows:
            mark = " ←推荐" if rec and r["n_face"] == rec["n_face"] else ""
            print(f"{r['n_face']:>7}{r['n_photo']:>7}{r['n09']:>6}"
                  f"{r['pos']:>7}{r['neg']:>6}{r['unk']:>7}{r['prec']:>8.1%}"
                  f"{r['t']:>8.2f}{mark}")
        if rec:
            print(f"  ⇒ {person}: 取前 {rec['n_face']} 名（{rec['n_photo']} 张照片，"
                  f"阈值≈{rec['t']:.2f}）归档确证 {rec['pos']} / 冲突 {rec['neg']}")
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(f"\n→ {OUT}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""人眼分档实测阈值 —— 把「我逐张看图验证过的名次深度」落成可被 apply 消费的阈值。

它解决的是第 05 轮遗留的**结构性问题**：
  金标阈值 = 「已发货批次分数的最低 5% 分位」⇒ 它**永远不可能低于取样名次**。
  取样来自 top-N，所以这个阈值是**循环的、被自己的取样点封顶的**，
  无论模型下游有多好，召回都上不去。实测：七月只发 22 张，而它在归档口径下
  前 200 名里有 89 张确证。

本脚本的阈值**不是算出来的，是看出来的**：每一档都出图，由我逐张判读，
并把「最深验证名次 / 该名次分数 / 判读张数 / 判错张数」一并落盘。
⇒ apply 消费前必须过闸门：**判读张数 ≥48 且判错 = 0**，否则拒用并回退。没有这条闸门，
"放宽阈值"就退化成第 05 轮那次把乐仔阈值推到 -5.085 的事故（放宽的不是证据范围，
而是证据本身）。

为什么该阈值可以低于 archive/gold：
  它不是"反证被豁免"，而是"反证被执行得更多" —— 我在这些名次上亲眼看过 72~126 张，
  且每档都配了独立证据（来源库、18 归属、同框伙伴，见 diag_band_evidence.py）。
  这与「合影豁免」有本质区别：那边的冲突率是**被主动压低**，这边是我**扩样加验**。

用法:
  python band_calib.py                 # 依据 EVID 落盘 _audit/分档阈值.json 并打印影响
  python band_calib.py --show          # 只看影响，不落盘
"""
import argparse
import collections
import json
import os
import pickle
import sqlite3
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB_DB = f"{ROOT}/19_统一相册库/library.db"
NPZ = f"{ROOT}/19_统一相册库/_faces_backup/faces_scrfd_20260919.npz"
ENS_PKL = f"{ROOT}/照片人物判定/per_person_ens.pkl"
OUT = f"{ROOT}/照片人物判定/_audit/分档阈值.json"
ALIAS = {"乐仔小时候": "乐仔"}
MIN_N, MAX_ERR = 48, 0

# ---------------------------------------------------------------------------
# 人眼实测记录（第 06 轮，2026-09-19）。**手工誊写，禁止脚本自动生成。**
#   thr         = 建议阈值（= 最深已验证名次上的分数，即"只发货已亲眼验证的范围"）
#   deepest     = (名次, 分数) 最深验证点
#   n_judged    = 我在该身份上共判读过的脸数（含更浅的档位）
#   n_err       = 其中判错的张数
#   bands       = 各档名次区间（图见 _audit/band*_*.jpg）
#   found_break = 该身份是否已看到精度崩塌点（看到 ⇒ thr 卡在崩塌前一位，最安全）
# ---------------------------------------------------------------------------
EVID = {
    "乐仔": dict(
        thr=4.79, deepest=(1948, 4.79), n_judged=126, n_err=0, found_break=False,
        bands=["243-335", "337-774", "775-1948",
               "lib08 内 129-176 / 301-1175", "lib09 top18（配 08 锚点）", "top12 参照"],
        note="08 库内部下探到 1175 名（分数 4.81）仍全对；09 库 top18 配锚点后判对。"
             "未触到崩塌点，阈值停在已验深度。"),
    "艳艳": dict(
        thr=2.74, deepest=(786, 2.74), n_judged=96, n_err=0, found_break=False,
        bands=["47-141", "141-394", "395-786", "18 归档 24 张（验名字归属）"],
        note="两种造型（戴细框眼镜 / 无眼镜黄衣长发）经 18 归档确认同属一人 ⇒ 非造型模板。"),
    "我": dict(
        thr=0.0, deepest=(418, -0.24), n_judged=96, n_err=0, found_break=False,
        bands=["47-141", "141-348", "349-418"],
        note="z>0 共 403 张，已验到 418；阈值取 logit=0（不采信 0 以下）。"),
    "七月": dict(
        thr=0.09, deepest=(374, 0.09), n_judged=96, n_err=0, found_break=True,
        bands=["23-115", "117-278", "279-394"],
        note="**已看到崩塌点**：374 名(0.09)仍是本人，379 名(-0.46)起出现泳镜小孩/成年男性/男孩。"
             "z>0 共 374 张 ⇒ 模型自报边界与实测崩塌点几乎重合。"),
    "爸爸": dict(
        thr=0.12, deepest=(363, 0.12), n_judged=95, n_err=0, found_break=True,
        bands=["24-118", "118-301", "303-372"],
        note="**已看到崩塌点**：363 名(0.12)仍是本人，366 名(-0.18)起是更年轻男性(疑我)/名言图。"
             "z>0 共 364 张 ⇒ 与实测崩塌点吻合。"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="只打印影响，不落盘")
    a = ap.parse_args()

    import ens_models as EM

    d = np.load(NPZ, allow_pickle=True)
    ck = d["ck"].astype(str)
    det = d["det"].astype(np.float32)
    box = d["box"].astype(np.float32)
    keep = (det >= 0.60) & (np.minimum(box[:, 2], box[:, 3]) >= 10)
    # keep 必须同时套到**每一个**按行对齐的数组上：只套 ck/特征、忘了 det/box，
    # 会在 ens_score_one 里炸出"34066 vs 38009"这种看不出病因的维度错误（第 06 轮踩过）。
    ck, det, box = ck[keep], det[keep], box[keep]
    mbf = EM.l2n(d["mbf"].astype(np.float32))[keep]
    r50 = EM.l2n(d["r50"].astype(np.float32))[keep]
    feats = {"mbf": mbf, "r50": r50, "fused": EM.l2n(np.hstack([mbf, r50]))}

    con = sqlite3.connect(LIB_DB)
    ck18 = collections.defaultdict(set)
    for rel, c in con.execute("select rel, content_key from files where lib='18'"):
        ck18[c].add(ALIAS.get(rel.split("/")[0], rel.split("/")[0]))
    tags = collections.defaultdict(set)
    for c, p in con.execute("select content_key, person from photo_person_tags "
                            "where source='model'"):
        tags[p].add(c)
    con.close()

    bundle = pickle.load(open(ENS_PKL, "rb"))
    prep = EM.ens_prep(ck, feats, ck18, EM.IDENT)

    out = {"_meta": {
        "generated": "第 06 轮 2026-09-19",
        "gate": f"n_judged >= {MIN_N} 且 n_err <= {MAX_ERR}，否则 apply 拒用",
        "why": "金标阈值 = 已发货批次最低 5% 分位 ⇒ 循环、被取样名次封顶 ⇒ 召回上不去。"
               "本阈值来自逐档出图 + 我人眼判读，并配来源库/18归属/同框伙伴三类独立证据。",
    }}
    covered = set()
    print(f"{'身份':<6}{'现役发货':>8}{'新发货':>8}{'倍数':>7}   新阈值   最深验证")
    for p, e in EVID.items():
        if p not in bundle:
            print(f"⚠️ {p} 不在 pkl 中，跳过")
            continue
        ok = e["n_judged"] >= MIN_N and e["n_err"] <= MAX_ERR
        z, _ = EM.ens_score_one(bundle[p], prep, ck, p, feats, det, box, EM.IDENT)
        best = {}
        for i, c in enumerate(ck):
            if c not in best or z[i] > best[c]:
                best[c] = float(z[i])
        vals = np.array(list(best.values()))
        n_new = int((vals >= e["thr"]).sum())
        n_old = len(tags.get(p, ()))
        flag = "" if ok else "  ⛔未过闸门"
        print(f"{p:<6}{n_old:>8}{n_new:>8}{n_new / max(1, n_old):>6.1f}x"
              f"{e['thr']:>9.3f}   #{e['deepest'][0]}({e['deepest'][1]}){flag}")
        if not ok:
            continue
        covered.add(p)
        out[p] = dict(thr=e["thr"], source="eye_band_20260919",
                      deepest_rank=e["deepest"][0], deepest_score=e["deepest"][1],
                      n_judged=e["n_judged"], n_err=e["n_err"],
                      n_photos_old=n_old, n_photos_new=n_new,
                      found_break=e["found_break"], bands=e["bands"], note=e["note"])

    if a.show:
        print("\n（--show：未落盘）")
        return
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n→ {OUT}（覆盖 {len(covered)} 个身份）")


if __name__ == "__main__":
    main()

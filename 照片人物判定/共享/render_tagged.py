#!/usr/bin/env python3
"""发货复核：把「模型真正写进库的判定」渲染成图，由我自己读图判断。

为什么需要它：
模型自报的 TPR/AUC 与"库里实际发的标签"是**两件事** —— 后者还要经过
幼童属性门控 → 阈值标定 → 照片级聚合 → 锚点护栏。唯一能确认"发出去的东西对不对"
的办法，就是把它们渲染出来逐张看图。指标再好，也替代不了看图。

做法：
  1. 读 library.db 的 photo_person_tags(source='model') 取命中照片（这就是发货结果）
  2. 用与 apply 完全同一套内核（import apply_per_person.predict_ens 所用的
     EM.ens_prep / EM.ens_score_one）重算脸级分数
  3. 每张照片取**该身份分数最高的那张脸**裁图拼接
  4. 我读图 → 数对错 → 得到"发货精度"

模式的差别（top/bottom 都要看）：
  top    = 分数最高的 n 张 —— 如果连这里都有错，说明模型坏得很彻底
  bottom = 分数**最低**的 n 张命中 —— 贴着阈值的那批，误报几乎都藏在这里
  rand   = 随机 n 张 —— 估计整体精度

用法:
  python render_tagged.py --person 乐仔 --mode top    --n 24 -o ../_audit/发货_乐仔_top.jpg
  python render_tagged.py --person 乐仔 --mode bottom --n 24 -o ../_audit/发货_乐仔_bottom.jpg
"""
import argparse
import collections
import json
import os
import pickle
import random
import sqlite3
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB_DB = f"{ROOT}/19_统一相册库/library.db"
NPZ = f"{ROOT}/19_统一相册库/_faces_backup/faces_scrfd_20260919.npz"
ENS_PKL = f"{ROOT}/照片人物判定/per_person_ens.pkl"
OUTDIR = f"{ROOT}/照片人物判定/_audit"
DIMS_CACHE = f"{OUTDIR}/_dims.json"

from audit import crop_face, make_sheet  # noqa: E402
import ens_models as EM                  # noqa: E402

ALIAS = {"乐仔小时候": "乐仔"}


def load(ck18):
    src = os.environ.get("OBC_FACES_NPZ", NPZ)
    d = np.load(src, allow_pickle=True)
    ck = d["ck"].astype(str)
    lib = d["lib"].astype(str)
    det = d["det"].astype(np.float32)
    box = d["box"].astype(np.float32)
    keep = (det >= 0.60) & (np.minimum(box[:, 2], box[:, 3]) >= 10)
    ck, lib, det, box = ck[keep], lib[keep], det[keep], box[keep]
    mbf = EM.l2n(d["mbf"].astype(np.float32))[keep]
    r50 = EM.l2n(d["r50"].astype(np.float32))[keep]
    feats = {"mbf": mbf, "r50": r50, "fused": EM.l2n(np.hstack([mbf, r50]))}
    return ck, lib, det, box, feats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--person", required=True)
    ap.add_argument("--mode",
                    choices=["top", "bottom", "rand", "global", "conflict"], default="top",
                    help="global=不看库标签、取全库最高分脸；conflict=只看「模型判正、但 18 把"
                         "该照片归在别人名下」的那批 —— 判定「模型错还是归档错」的关键批次")
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--skip", type=int, default=0,
                    help="跳过前 N 名（配合 global 模式看「次高分区」的精度衰减）")
    ap.add_argument("--stride", type=int, default=1,
                    help="隔 stride 张取 1 张 —— 配合 global 用来「均匀采样一个宽带」"
                         "（如 --skip 200 --stride 33 --n 24 = 覆盖第 201~1000 名，"
                         "比连续取 24 张更能代表整段平均精度；连续取样天然偏乐观）")
    ap.add_argument("--libs", default=None,
                    help="只在这个来源库里取样（逗号分隔，如 --libs 08）。"
                         "为什么必须有：**同一身份在不同库的分数分布差一大截**"
                         "（08 乐仔相册中位 +4.92 / 07 家庭库 -1.50 / 09 群相册 -4.86），"
                         "全局阈值会把某个库整库切掉；要单独验某库只能在该库内部取样。"
                         "lib 取 primary 副本（一个 ck 可能同时在多库有 primary）。")
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--size", type=int, default=260)
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--emit-verdicts", default=None,
                    help="把本批渲染的脸**落盘成人眼金标 jsonl**（默认 verdict=1），"
                         "键=(ck,cx,cy) 跨重扫可吸附；训练与标定都可复用")
    ap.add_argument("--neg-idx", default="",
                    help="图里我判成「不是本人」的格号（make_sheet 的 #k，0 起，逗号分隔），"
                         "这些条以 verdict=0 落盘 —— **全项目第一批负样本金标**，"
                         "没有它 precision 永远只能靠归档冲突率这个上界来猜")
    ap.add_argument("--q-idx", default="",
                    help="判不了/不该判的格号 → verdict=2（存疑，下游一律丢弃）。"
                         "**存疑必须留档不能省略**：省略等于默认它是正样本，"
                         "把「看不清」偷换成「是本人」（第 03 轮踩过）。"
                         "典型：脸边长 <45px 的插值放大脸（本项目硬纪律：<45px 不定案）。")
    ap.add_argument("--anchor-n", type=int, default=0,
                    help="**在图首插入 N 张锚点脸**（该身份得分最高、且来源库受 --anchor-libs 限制）。"
                         "为什么必须有：09 群相册是**同班孩子扎堆**的场景，孤立看一张脸"
                         "无法判断「这是本人还是同学」——判读必须有参照系。"
                         "把已知锚点和待判样本放进同一张图，才能判。")
    ap.add_argument("--anchor-libs", default=None,
                    help="锚点只从这个库取（如 08），默认全库最高分")
    a = ap.parse_args()

    con = sqlite3.connect(LIB_DB)
    con.row_factory = sqlite3.Row
    ck18 = collections.defaultdict(set)
    for rel, c in con.execute("select rel, content_key from files where lib='18'"):
        ck18[c].add(ALIAS.get(rel.split("/")[0], rel.split("/")[0]))
    tags = {}
    for r in con.execute("select content_key, score from photo_person_tags "
                         "where source='model' and person=?", (a.person,)):
        tags[r["content_key"]] = float(r["score"] or 0.0)
    paths = {}
    for r in con.execute("select content_key, path from files where is_primary=1"):
        paths.setdefault(r["content_key"], r["path"])
    cklib = {}
    for c, l in con.execute("select content_key, lib from files where is_primary=1"):
        cklib.setdefault(c, set()).add(l)
    con.close()

    want_libs = {x.strip() for x in a.libs.split(",")} if a.libs else None

    if not tags and a.mode != "global":
        print(f"库里没有 {a.person} 的 model 标签 —— 先跑 apply_per_person.py", file=sys.stderr)
        sys.exit(1)

    ck, lib, det, box, feats = load(ck18)
    bundle = pickle.load(open(ENS_PKL, "rb"))
    if a.person not in bundle:
        print(f"{a.person} 不在 {ENS_PKL} 中", file=sys.stderr)
        sys.exit(1)
    prep = EM.ens_prep(ck, feats, ck18, EM.IDENT)
    z, pos_idx = EM.ens_score_one(bundle[a.person], prep, ck, a.person,
                                 feats, det, box, EM.IDENT)

    # 「审的就是发货的」契约自检：库里存储分 vs 现算分对不上，说明 pkl/npz 在本批标签
    # 写库之后换过 —— 此时 top/bottom 验的就不是当前模型，整张复核结论作废。必须先报出来。
    if tags:
        diff = []
        zb = {}
        for i, c in enumerate(ck):
            if c in tags and (c not in zb or z[i] > zb[c][0]):
                zb[c] = (float(z[i]), i)
        for c, s in tags.items():
            if c in zb:
                diff.append(abs(zb[c][0] - s))
        if diff:
            mx, nbad = max(diff), sum(1 for d in diff if d > 1e-3)
            flag = "✅ 一致" if nbad == 0 else f"⚠️ {nbad} 张不一致（最大 {mx:.3f}）"
            print(f"[契约自检] 存储分 vs 现算分：{flag}（可比 {len(diff)}/{len(tags)} 张）")

    # 每张命中照片 → 该身份分数最高的那张脸
    pp = ALIAS.get(a.person, a.person)
    byck = collections.defaultdict(list)
    for i, c in enumerate(ck):
        if a.mode in ("global", "conflict") or c in tags:
            byck[c].append(i)
    rows = []
    for c, idxs in byck.items():
        if want_libs and not (want_libs & (cklib.get(c) or set())):
            continue
        best = max(idxs, key=lambda i: z[i])
        w = ck18.get(c)
        if a.mode == "conflict" and not (w is not None and pp not in w):
            continue          # 只留「模型判正、18 却把这张照片归在别人名下」的
        rows.append((c, float(z[best]), best, float(tags.get(c, float(z[best]))), w))

    # 排序键按模式选，不能混：
    #  · top/bottom = "发货结果里最高/最低的那批" ⇒ 必须用**发货时的存储分**（tags），
    #    否则验的不是真实发货边界。
    #  · global/conflict = "全库榜单往下扫" ⇒ 必须用**现算分**（z）；
    #    库里存储分来自上一次 apply，若 pkl/npz 之后换过就不同尺度，
    #    混进同一个榜单排序 = 名次错乱（第 06 轮踩过）。
    shipped_mode = a.mode in ("top", "bottom")
    key = (lambda r: r[3]) if shipped_mode else (lambda r: r[1])
    if a.mode == "bottom":
        rows.sort(key=key)
    elif a.mode in ("top", "global", "conflict"):
        rows.sort(key=key, reverse=True)
    else:
        random.Random(7).shuffle(rows)
    ranked = list(enumerate(rows, start=1))          # (榜单名次, row)
    picked = ranked[a.skip::max(1, a.stride)][:a.n]

    # 锚点行：**在候选池之外**独立取（不受 --libs/--skip 限制），保证锚点不会和待判样本重合。
    # 锚点只用来看"参照系"，不落金标（它不是本批要判的样本）。
    anchors = []
    if a.anchor_n > 0:
        alibs = {x.strip() for x in a.anchor_libs.split(",")} if a.anchor_libs else None
        acand = []
        for c, idxs in byck.items():
            if alibs and not (alibs & (cklib.get(c) or set())):
                continue
            i = max(idxs, key=lambda j: z[j])
            acand.append((c, float(z[i]), i))
        acand.sort(key=lambda r: -r[1])
        anchors = acand[:a.anchor_n]

    items, meta, nmiss = [], [], 0
    for rank, (c, zf, i, zc, w) in picked:
        who18 = "无" if w is None else "/".join(sorted(w))
        p = paths.get(c)
        if not p or not os.path.exists(p):
            nmiss += 1
            continue
        im = crop_face(p, box[i], size=a.size)
        if im is None:
            nmiss += 1
            continue
        side = min(box[i][2], box[i][3])
        # 题注里带上**候选序号**（= meta 下标，与 --neg-idx/--q-idx 同一个编号）。
        # 锚点行会插在图首，make_sheet 自带的 #k 是"图里的格号"，两者会差一个锚点数
        # ⇒ 一律以题注里的候选序号为准，避免又踩「下标错位」这类静默 bug。
        items.append((im, f"#{len(meta)} r{rank} p={zc:.2f} {side:.0f}px 18:{who18}"))
        meta.append(dict(ck=c, rank=rank, photo_score=zc, face_score=zf, face_idx=int(i),
                         who18=who18, side=float(side), path=p))
    if not items:
        print("没有可渲染条目", file=sys.stderr)
        sys.exit(1)
    # 锚点插到最前面（不改 meta，落金标时按 len(anchors) 偏移即可）
    n_anchor_ok = 0
    anchor_items = []
    for k, (c, zc, i) in enumerate(anchors):
        p = paths.get(c)
        if not p or not os.path.exists(p):
            continue
        im = crop_face(p, box[i], size=a.size)
        if im is None:
            continue
        anchor_items.append((im, f"★锚{k + 1} p={zc:.2f} 18:"
                                 f"{'无' if ck18.get(c) is None else '/'.join(sorted(ck18[c]))}"))
        n_anchor_ok += 1
    items = anchor_items + items
    out = a.out or f"{OUTDIR}/发货_{a.person}_{a.mode}.jpg"
    rng = f"名次 {picked[0][0]}~{picked[-1][0]}" if a.mode == "global" else a.mode
    if n_anchor_ok:
        rng = f"★锚{n_anchor_ok}张 + {rng}"
    make_sheet(items, a.cols, out,
               title=f"{a.person} · 榜单 {rng} · 待判 {len(meta)} 张"
                     f"（r=榜单名次 p=照片级分 ★=锚点 缺图 {nmiss}）")
    print(f"{a.person} {a.mode}: 待判 {len(meta)} 张（{rng}）→ {out}")
    if a.emit_verdicts:
        # 人眼金标落盘：这是全项目**唯一非循环真值**（模型自报指标都会被样本集污染）。
        # 键用 (ck, 归一化中心)，重扫后 rowid/box 都会变，只有归一化中心能吸附回来。
        dims = {}
        if os.path.exists(DIMS_CACHE):
            try:
                dims = json.load(open(DIMS_CACHE, encoding="utf-8"))
            except Exception:
                dims = {}
        from audit import imread_any
        neg = {int(x) for x in a.neg_idx.split(",") if x.strip() != ""}
        qq = {int(x) for x in a.q_idx.split(",") if x.strip() != ""}
        n_read = 0
        npos = nneg = nq = 0
        with open(a.emit_verdicts, "a", encoding="utf-8") as fh:
            for k, m in enumerate(meta):
                d = dims.get(m["ck"])
                if not d:
                    im2 = imread_any(m["path"])
                    d = (im2.shape[0], im2.shape[1]) if im2 is not None else None
                    if d:
                        dims[m["ck"]] = d
                    n_read += 1
                if not d:
                    continue
                ih, iw = d[0], d[1]
                bx = box[m["face_idx"]]
                v = 2 if k in qq else (0 if k in neg else 1)
                npos += v == 1
                nneg += v == 0
                nq += v == 2
                fh.write(json.dumps(dict(
                    person=a.person, ck=m["ck"],
                    cx=round(float((bx[0] + bx[2] / 2) / iw), 4),
                    cy=round(float((bx[1] + bx[3] / 2) / ih), 4),
                    verdict=v, source="eye_20260919",
                    # selfaudit.latest() 用 key 做索引（后写覆盖前写）——必须带，否则 eval 崩
                    key=f"{m['ck']}|{float((bx[0] + bx[2] / 2) / iw):.2f}"
                        f"|{float((bx[1] + bx[3] / 2) / ih):.2f}",
                    ens=round(float(m["photo_score"]), 4),
                    note=f"render_tagged {a.mode} 榜单名次{m['rank']} 人眼判定"),
                    ensure_ascii=False) + "\n")
        for nm, st in (("neg", neg), ("q", qq)):
            bad = sorted(x for x in st if x >= len(meta))
            if bad:
                print(f"⚠️ --{nm}-idx 越界（本表只有 {len(meta)} 格）：{bad}", file=sys.stderr)
        sc = np.array([m["photo_score"] for m in meta])
        print(f"→ 人眼金标 {len(meta)} 条（正 {npos} / 负 {nneg} / 存疑 {nq}）→ {a.emit_verdicts}"
              f"（分数 {sc.min():.3f}~{sc.max():.3f}，最低5%分位={np.quantile(sc, 0.05):.3f}"
              f"，新读尺寸 {n_read}）")

    print("分数区间：照片级 "
          f"{min(m['photo_score'] for m in meta):.3f}~{max(m['photo_score'] for m in meta):.3f}"
          f" / 脸级 {min(m['face_score'] for m in meta):.3f}~{max(m['face_score'] for m in meta):.3f}")


if __name__ == "__main__":
    main()

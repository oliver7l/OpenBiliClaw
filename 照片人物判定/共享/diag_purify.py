#!/usr/bin/env python3
"""验证「正样本提纯」有没有误剔：把被剔掉的脸和被留下的脸并排给我看。

为什么必须看：提纯会剔掉 10%~60% 的正样本，指标随之大涨——但**指标涨不等于剔对了**。
若被剔的是真本人（只是角度差/年龄跨度大），那就是在偷吃监督信号、把难度藏起来。
所以要么亲眼确认被剔的是别人，要么就该回退。

同时打印：每张被剔的脸**更像哪个身份**（子中心余弦），这直接给出"为什么剔它"。

用法: python diag_purify.py 乐仔 [--n 16]
"""
import argparse
import collections
import json
import os
import sqlite3

os.nice(10)
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import ens_models as EM

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB_DB = f"{ROOT}/19_统一相册库/library.db"
NPZ = f"{ROOT}/19_统一相册库/_faces_backup/faces_yunet_20260919.npz"
OUTDIR = f"{ROOT}/照片人物判定/_audit"
CHILD = f"{ROOT}/照片人物判定/child_model.json"
FONT = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
ALIAS = {"乐仔小时候": "乐仔", "妈妈小时候": "妈妈", "艳艳小时候": "艳艳"}
KIDS = {"乐仔", "七月"}

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass


def sheet(items, cols, out, title):
    S = 250
    rows = (len(items) + cols - 1) // cols
    g = Image.new("RGB", (cols * S, rows * (S + 26) + 36), (24, 24, 28))
    d = ImageDraw.Draw(g)
    d.text((8, 6), title, fill=(255, 214, 102), font=ImageFont.truetype(FONT, 22))
    f = ImageFont.truetype(FONT, 18)
    for k, (im, cap) in enumerate(items):
        r, c = divmod(k, cols)
        X, Y = c * S, r * (S + 26) + 36
        g.paste(im, (X + (S - im.size[0]) // 2, Y + 26 + (S - im.size[1]) // 2))
        d.rectangle([X, Y, X + S, Y + 25], fill=(40, 40, 48))
        d.text((X + 4, Y + 2), cap, fill=(240, 240, 248), font=f)
    g.save(out, quality=90)
    print(f"→ {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("person")
    ap.add_argument("--src", default=NPZ)
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--cols", type=int, default=8)
    a = ap.parse_args()

    d = np.load(a.src, allow_pickle=True)
    ck = d["ck"].astype(str)
    lib = d["lib"].astype(str)
    det = d["det"].astype(np.float32)
    box = d["box"].astype(np.float32)
    keep = (det >= 0.60) & (np.minimum(box[:, 2], box[:, 3]) >= 10)
    ck, lib, det, box = ck[keep], lib[keep], det[keep], box[keep]
    _fmbf = EM.l2n(d["mbf"].astype(np.float32))
    _fr50 = EM.l2n(d["r50"].astype(np.float32))
    # 幼童模型可能选 mbf 也可能选 fused —— 两个特征都备好，交给 EM.child_prob 按需取
    feats = {"mbf": _fmbf[keep], "r50": _fr50[keep],
             "fused": EM.l2n(np.hstack([_fmbf, _fr50]))[keep]}
    X = feats["fused"]

    con = sqlite3.connect(LIB_DB)
    ck18 = collections.defaultdict(set)
    for rel, c in con.execute("select rel, content_key from files where lib='18'"):
        ck18[c].add(ALIAS.get(rel.split("/")[0], rel.split("/")[0]))
    path_of = {}
    for c, p in con.execute("select content_key, path from files where is_primary=1"):
        path_of.setdefault(c, p)
    con.close()

    nface = collections.Counter(ck)
    mine = {c for c, w in ck18.items() if w == {a.person}}
    pos0 = [i for i, c in enumerate(ck) if c in mine and nface[c] == 1]
    cm = json.load(open(CHILD, encoding="utf-8"))
    pch = EM.child_prob(feats, cm)   # 按 cm["feature"] 取 mbf/fused，避免维度错配
    chi = float(cm["thresholds"]["child"])
    n_child0 = len(pos0)
    if a.person not in KIDS:
        pos0 = [i for i in pos0 if not (np.isfinite(pch[i]) and pch[i] >= chi)]
    centers = EM.xperson_centers(ck, X, ck18, EM.IDENT)
    c_other = [centers[q] for q in EM.IDENT if q != a.person and q in centers]
    order = [q for q in EM.IDENT if q != a.person and q in centers]
    # Call 是"子中心堆叠"：每个身份贡献 k 个中心，所以要记住每一行属于谁
    Call = np.vstack(c_other)
    owner = [q for q in order for _ in range(len(centers[q]))]

    kept, _ = EM.purify_loo(pos0, X, c_other, log=lambda *x: print(*x))
    dropped = sorted(set(pos0) - set(kept))
    print(f"{a.person}: 归档单脸 {n_child0} → 幼童剔除后 {len(pos0)} → 提纯后 {len(kept)}")

    def cap(i, tag):
        s = X[i] @ Call.T                # 每个「其他身份子中心」的余弦
        j = int(np.argmax(s))
        return f"{tag} {lib[i]} {int(max(box[i][2], box[i][3]))}px 像{owner[j]}{s[j]:.2f}"

    def img(i):
        p = path_of.get(ck[i])
        if not p:
            return None
        try:
            im = Image.open(p).convert("RGB")
        except Exception:
            return None
        x, y, w, h = box[i]
        pad = 0.6 * max(w, h)
        im = im.crop((max(0, x - pad), max(0, y - pad),
                      min(im.size[0], x + w + pad), min(im.size[1], y + h + pad)))
        w, h = im.size
        s = 250 / max(w, h)
        return im.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)

    rng = np.random.default_rng(3)
    for tag, sel in (("剔除", dropped), ("保留", list(kept))):
        if not sel:
            continue
        pick = rng.choice(len(sel), size=min(a.n, len(sel)), replace=False)
        items = []
        for k in pick:
            im = img(sel[k])
            if im is not None:
                items.append((im, cap(sel[k], tag)))
        sheet(items, a.cols, f"{OUTDIR}/提纯_{a.person}_{tag}.jpg",
              f"{a.person} · {tag}（共 {len(sel)} 张）")


if __name__ == "__main__":
    main()

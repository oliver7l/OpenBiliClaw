#!/usr/bin/env python3
"""一张图认全家人：每个身份一行，取「最可信来源」的若干张脸。

为什么要合集：`乐仔` 我已经认得（圆脸/单眼皮/短黑发），但 艳艳/妈妈/我/七月/爸爸
我还没有可靠参照，而它们的正样本最少（19~48）、模型最弱、最需要人眼真值。
分散看 5 张图要 5 次读图，合成 1 张只需 1 次。

可信来源优先级：18 归档里**整张照片只有这一张脸**的照片（这张脸必然是本人）
→ 退化为任意 18 归档照片（会标出"脸N"，提示可能不是本人）。

用法: python build_anchor_all.py [--n 6] [--out 锚点_全家.jpg]
"""
import argparse
import collections
import os
import sqlite3

os.nice(10)
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB_DB = f"{ROOT}/19_统一相册库/library.db"
OUTDIR = f"{ROOT}/照片人物判定/_audit"
FONT = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
IDENT = ["乐仔", "艳艳", "妈妈", "我", "七月", "爸爸", "乐仔小时候"]
ALIAS = {"乐仔小时候": "乐仔"}

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--cell", type=int, default=250)
    ap.add_argument("--out", default=f"{OUTDIR}/锚点_全家.jpg")
    a = ap.parse_args()

    con = sqlite3.connect(LIB_DB)
    # 归档：目录 → ck（目录名≠身份，乐仔小时候=乐仔）
    arch = collections.defaultdict(set)
    for rel, ck in con.execute("select rel, content_key from files where lib='18'"):
        arch[rel.split("/")[0]].add(ck)
    # 每张 ck 的脸数 + 一张脸的框（用于裁脸）
    nface = collections.Counter()
    face_of = {}
    for ck, x, y, w, h, lib in con.execute(
            "select content_key, x, y, w, h, lib from faces"):
        nface[ck] += 1
        face_of.setdefault(ck, (x, y, w, h, lib))
    path_of = {}
    for ck, p in con.execute("select content_key, path from files where is_primary=1"):
        path_of.setdefault(ck, p)
    con.close()

    cell, n = a.cell, a.n
    rows = len(IDENT)
    gap = 10
    W = cell * n + gap * (n + 1)
    H = (cell + 30) * rows + gap * (rows + 1) + 36
    g = Image.new("RGB", (W, H), (22, 22, 26))
    d = ImageDraw.Draw(g)
    fbig = ImageFont.truetype(FONT, 24)
    fsm = ImageFont.truetype(FONT, 17)
    d.text((8, 6), "家人锚点合集（单脸归档照片=必是本人；脸N>1 表示这张可能不是本人）",
           fill=(255, 214, 102), font=fbig)

    for ri, person in enumerate(IDENT):
        cks = sorted(arch.get(person, set()))
        single = [c for c in cks if nface.get(c) == 1]
        multi = [c for c in cks if nface.get(c, 0) > 1]
        rng = np.random.default_rng(ri * 977 + 13)
        pick = [("单脸", c) for c in (rng.choice(single, size=min(n, len(single)),
                                                replace=False).tolist() if single else [])]
        if len(pick) < n and multi:
            k = n - len(pick)
            pick += [("多脸", c) for c in rng.choice(
                multi, size=min(k, len(multi)), replace=False).tolist()]
        y0 = 36 + gap + ri * (cell + 30 + gap)
        d.text((10, y0), f"{person}", fill=(160, 230, 255), font=fbig)
        d.text((10, y0 + 24), f"归档{len(cks)} 单脸{len(single)}", fill=(150, 150, 165),
               font=fsm)
        for ci, (tag, ck) in enumerate(pick):
            x0 = gap + ci * (cell + gap)
            try:
                im = Image.open(path_of.get(ck, "")).convert("RGB")
            except Exception:
                continue
            # 裁脸（带上下文），没有脸信息就整图缩放
            fr = face_of.get(ck)
            if fr:
                x, y, w, h, _ = fr
                pad = 0.6 * max(w, h)
                cx0, cy0 = max(0, x - pad), max(0, y - pad)
                cx1, cy1 = min(im.size[0], x + w + pad), min(im.size[1], y + h + pad)
                im = im.crop((cx0, cy0, cx1, cy1))
            w, h = im.size
            s = cell / max(w, h)
            im = im.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
            g.paste(im, (x0 + (cell - im.size[0]) // 2,
                         y0 + 30 + (cell - im.size[1]) // 2))
            d.rectangle([x0, y0 + 30, x0 + cell, y0 + 30 + cell], outline=(60, 60, 74))
            d.text((x0 + 4, y0 + 32), tag, fill=(255, 255, 255), font=fsm)

    g.save(a.out, quality=90)
    print(f"→ {a.out}  ({g.size[0]}x{g.size[1]})")


if __name__ == "__main__":
    main()

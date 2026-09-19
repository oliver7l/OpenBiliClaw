#!/usr/bin/env python3
"""锚点图：把某个人「最可信来源」的照片拼成一张图，用来确认这个人到底长什么样。

为什么需要：18 归档是**照片级**标注（一张合影归在该人名下），
不等于照片里每张脸都是他/她。要审模型，先得有一组可信的「本人长相」参照。

来源优先级（越靠前越可信）:
  1. 18 归档里「单脸照片」（整张照片只有一张脸 → 这张脸必是本人）
  2. 08_乐仔相册（用户专门为乐仔整理的相册目录）
  3. 07_相册

用法: python build_anchor.py 乐仔 [--n 24]
"""
import os
import sys
import json
import sqlite3
import argparse
import collections

os.nice(10)
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB_DB = f"{ROOT}/19_统一相册库/library.db"
OUTDIR = f"{ROOT}/照片人物判定/_audit"
FONT = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass


def load_img(path, size=300):
    try:
        im = Image.open(path)
        im = im.convert("RGB")
    except Exception:
        return None
    w, h = im.size
    s = size / max(w, h)
    im = im.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
    return im


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("person")
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--cols", type=int, default=6)
    a = ap.parse_args()
    person = a.person
    os.makedirs(OUTDIR, exist_ok=True)

    con = sqlite3.connect(LIB_DB)
    rows = con.execute(
        "select rel, path, content_key from files where lib='18' and rel like ?",
        (f"{person}/%",)).fetchall()
    nface = {ck: n for ck, n in con.execute(
        "select content_key, count(*) from faces group by content_key")}
    con.close()

    # 分层挑：08_乐仔相册 / 07_相册 / 其它，各自内部优先单脸照片
    buckets = collections.defaultdict(list)
    for rel, path, ck in rows:
        src = rel.split("/")[2] if len(rel.split("/")) > 2 else "other"
        buckets[src].append((path, nface.get(ck, 0), ck))

    items, meta = [], []
    per = max(1, a.n // max(1, len(buckets)))
    for src, lst in buckets.items():
        single = [x for x in lst if x[1] == 1]
        multi = [x for x in lst if x[1] != 1]
        rng = np.random.default_rng(hash(src) % 2 ** 31)
        pick = []
        if single:
            k = min(per, len(single))
            pick += [single[i] for i in rng.choice(len(single), size=k, replace=False)]
        if len(pick) < per and multi:
            k = min(per - len(pick), len(multi))
            pick += [multi[i] for i in rng.choice(len(multi), size=k, replace=False)]
        for path, nf, ck in pick:
            im = load_img(path)
            if im is None:
                continue
            j = len(items)
            items.append((im, f"#{j} {src[:6]} 脸{nf}"))
            meta.append(dict(no=j, src=src, ck=ck, path=path, n_face=nf))

    if not items:
        print("没取到图"); return
    S = 300
    cols = a.cols
    rowsn = (len(items) + cols - 1) // cols
    g = Image.new("RGB", (cols * S, rowsn * (S + 26) + 34), (24, 24, 28))
    d = ImageDraw.Draw(g)
    f = ImageFont.truetype(FONT, 20)
    d.text((8, 6), f"{person} 锚点（可信来源抽样，不用来判模型，只用来认人）",
           fill=(255, 214, 102), font=ImageFont.truetype(FONT, 24))
    for k, (im, cap) in enumerate(items):
        r, c = divmod(k, cols)
        X, Y = c * S, r * (S + 26) + 34
        g.paste(im, (X + (S - im.size[0]) // 2, Y + 26 + (S - im.size[1]) // 2))
        d.rectangle([X, Y, X + S, Y + 25], fill=(40, 40, 48))
        d.text((X + 4, Y + 2), cap, fill=(240, 240, 248), font=f)
    out = f"{OUTDIR}/锚点_{person}.jpg"
    g.save(out, quality=92)
    json.dump(meta, open(f"{OUTDIR}/锚点_{person}.json", "w"), ensure_ascii=False, indent=1)
    print(f"{len(items)} 张 → {out}")
    for src, lst in buckets.items():
        print(f"  {src}: {len(lst)} 张（单脸 {sum(1 for x in lst if x[1] == 1)}）")


if __name__ == "__main__":
    main()

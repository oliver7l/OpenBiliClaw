#!/usr/bin/env python3
"""对比图：把「确定是本人的锚点脸」和「待判脸」并排，肉眼看同一性。

锚点脸的取法（必须是强真值）：本人归档里第 1 行（首行）放 08_乐仔相册等
单脸照片裁出的脸；下面放待判候选。看的时候只问一句：
「下面这些和孩子爸/妈认的那个人是不是同一个」——幼童脸嵌入重叠严重，
只有肉眼能分辨，这也正是这一步存在的意义。

用法:
  python compare_sheet.py 乐仔 --audit _audit/乐仔_F_09库topN_q0.99.json --n 18
"""
import os
import sys
import json
import sqlite3
import argparse
import collections

os.nice(10)
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit import crop_face

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB_DB = f"{ROOT}/19_统一相册库/library.db"
OUTDIR = f"{ROOT}/照片人物判定/_audit"
FONT = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"


def anchor_faces(person, n=6):
    """锚点：本人归档里「单脸照片」的脸（整张照片只有一张脸 → 必是本人）。"""
    con = sqlite3.connect(LIB_DB)
    nface = dict(con.execute("select content_key, count(*) from faces group by content_key"))
    rows = con.execute(
        "select f.content_key, f.path from files f where f.lib='18' and f.rel like ?",
        (f"{person}/%",)).fetchall()
    out = []
    for ck, path in rows:
        if nface.get(ck) != 1:
            continue
        r = con.execute("select x,y,w,h from faces where content_key=? limit 1", (ck,)).fetchone()
        if not r:
            continue
        out.append((path, tuple(float(v) for v in r), ck))
    con.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("person")
    ap.add_argument("--audit", required=True, help="audit.py 产出的 json")
    ap.add_argument("--n", type=int, default=18)
    ap.add_argument("--na", type=int, default=6)
    a = ap.parse_args()

    meta = json.load(open(a.audit))
    items = meta["items"][:a.n]

    anchors = anchor_faces(a.person, a.na)
    print(f"锚点可取 {len(anchors)}（单脸照片）")
    S = 320
    cols = 6
    rows = 1 + (len(items) + cols - 1) // cols
    g = Image.new("RGB", (cols * S, rows * (S + 26) + 60), (24, 24, 28))
    d = ImageDraw.Draw(g)
    f = ImageFont.truetype(FONT, 19)
    ft = ImageFont.truetype(FONT, 24)
    d.text((8, 6), f"{a.person} · 上排=锚点(必是本人) 下排=待判({os.path.basename(a.audit)})",
           fill=(255, 214, 102), font=ft)
    rng = np.random.default_rng(5)
    sel = rng.choice(len(anchors), size=min(a.na, len(anchors)), replace=False)
    for k, i in enumerate(sel):
        path, box, ck = anchors[i]
        im = crop_face(path, box, size=S, pad=0.6)
        if im is None:
            continue
        X, Y = k * S, 40
        g.paste(im, (X, Y + 26))
        d.rectangle([X, Y, X + S, Y + 25], fill=(58, 44, 20))
        d.text((X + 4, Y + 2), f"锚{k}", fill=(255, 214, 102), font=f)
    for k, it in enumerate(items):
        im = crop_face(it["path"], it["box"], size=S, pad=0.6)
        if im is None:
            continue
        X, Y = (k % cols) * S, (1 + k // cols) * (S + 26) + 40
        g.paste(im, (X, Y + 26))
        d.rectangle([X, Y, X + S, Y + 25], fill=(40, 40, 48))
        d.text((X + 4, Y + 2), f"#{it['no']} E={it['ens']:.1f} {it['lib']}", fill=(240, 240, 248), font=f)
    out = a.audit.replace(".json", "_对比.jpg")
    g.save(out, quality=92)
    print(f"→ {out}")


if __name__ == "__main__":
    main()

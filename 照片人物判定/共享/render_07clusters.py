#!/usr/bin/env python3
"""渲染 07 photo_index 的簇样张（该库保存了 crop_path 裁图，与 library.db 不同源）。

用途：07 的 `photo_index.db` 是最早的人工聚类成果（簇 4=妈妈 1587 张、1=乐仔 997、
2=艳艳 917、10=爸爸 220…），但**从未被人肉复核过是否被自动归位污染**。
本脚本把指定簇随机抽样渲染出来，供人（我）看图判断该簇是否纯。

用法:
  python render_07clusters.py --clusters 4,2,1 --n 12 --seed 3
  python render_07clusters.py --clusters 4 --n 24 --cols 8 --out ../_audit/07簇4_大样.jpg
"""
import os
import sys
import random
import sqlite3
import argparse

os.nice(10)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image
from audit import make_sheet, font  # 复用拼图与字体（字体候选列表在 audit 里）

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
DB7 = f"{ROOT}/07_相册/相册&视频备份/_photo_index/photo_index.db"
OUTDIR = f"{ROOT}/照片人物判定/_audit"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clusters", default="4")
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--size", type=int, default=170)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    con = sqlite3.connect(DB7)
    con.row_factory = sqlite3.Row
    names = {r["cluster"]: r["name"] for r in con.execute("SELECT cluster,name FROM persons")}

    for cl in [int(v) for v in a.clusters.split(",")]:
        rows = list(con.execute(
            "SELECT id, crop_path FROM faces WHERE cluster=? AND crop_path IS NOT NULL", (cl,)))
        tot = len(rows)
        random.Random(a.seed).shuffle(rows)
        items = []
        for r in rows[:a.n]:
            p = r["crop_path"]
            if not p or not os.path.exists(p):
                continue
            try:
                im = Image.open(p).convert("RGB").resize((a.size, a.size))
            except Exception:
                continue
            items.append((im, f"#{r['id']}"))
        if not items:
            print(f"簇{cl}: 无可渲染"); continue
        out = a.out or f"{OUTDIR}/07簇{cl}.jpg"
        make_sheet(items, a.cols, out,
                   title=f"07 photo_index 簇{cl}（{names.get(cl) or '未命名'}）"
                         f" 全簇{tot}张 随机{len(items)}张 seed={a.seed}")
        print(f"簇{cl} 全簇{tot}张 → {out}")


if __name__ == "__main__":
    main()

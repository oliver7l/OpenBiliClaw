#!/usr/bin/env python3
"""把任意目录的图片渲染成缩略图拼图（分批），供人肉看图。

与 cluster_probe / audit 的区别：这两个都依赖 faces 表（坐标/簇），
而"某人的归档目录里到底拍了谁"这个问题**不需要检测结果**就能回答——
直接看图最快。归档照片的脸可能还没进库（例如重扫尚未轮到 lib=18）。

用法:
  python render_dir.py --dir ../18_人物分组/妈妈 --per-sheet 72 --cols 12
  python render_dir.py --dir <dir> --start 0 --per-sheet 36 --cols 9 --size 220
"""
import os
import sys
import json
import argparse

os.nice(10)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image
from audit import make_sheet, imread_any  # 统一读图（含 HEIC）与拼图/字体

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
OUTDIR = f"{ROOT}/照片人物判定/_audit"
IMG_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".bmp"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--cols", type=int, default=12)
    ap.add_argument("--per-sheet", type=int, default=72)
    ap.add_argument("--size", type=int, default=200)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--sheets", type=int, default=0, help="只渲染前 N 张 sheet，0=全部")
    ap.add_argument("--tag", default=None, help="输出文件名前缀")
    a = ap.parse_args()

    d = a.dir if os.path.isabs(a.dir) else os.path.abspath(a.dir)
    files = sorted(f for f in os.listdir(d) if os.path.splitext(f)[1].lower() in IMG_EXT)
    if not files:
        print(f"{d} 无图片"); return
    files = files[a.start:]
    name = a.tag or os.path.basename(d.rstrip("/"))
    print(f"{d}：{len(files)} 张")

    aspect = 1.0
    sheets = 0
    for s in range(0, len(files), a.per_sheet):
        if a.sheets and sheets >= a.sheets:
            break
        batch = files[s:s + a.per_sheet]
        items, meta = [], []
        for k, fn in enumerate(batch):
            p = os.path.join(d, fn)
            im = imread_any(p)
            if im is None:
                continue
            h, w = im.shape[:2]
            # 装进 size×size 的方格（contain）：make_sheet 是等格拼图，格子不方会错位
            sc = a.size / max(w, h)
            im = Image.fromarray(im[:, :, ::-1]).resize(
                (max(1, int(w * sc)), max(1, int(h * sc))))
            cell = Image.new("RGB", (a.size, a.size), (18, 18, 22))
            cell.paste(im, ((a.size - im.size[0]) // 2, (a.size - im.size[1]) // 2))
            items.append((cell, f"#{s+k} {fn[:14]}"))
            meta.append(dict(no=s + k, file=fn, path=p, w=w, h=h))
        if not items:
            continue
        out = f"{OUTDIR}/目录_{name}_{s:04d}-{s+len(batch)-1:04d}.jpg"
        make_sheet(items, a.cols, out, title=f"{name}  #{s}–{s+len(batch)-1}  ({len(items)}张)")
        json.dump(meta, open(out.replace(".jpg", ".json"), "w"),
                  ensure_ascii=False, indent=1)
        print(f"  → {out}")
        sheets += 1


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""统一相册库 CLIP 语义嵌入。

用 open_clip ViT-B-32-quickgelu(openai) 给每张主副本照片算 512 维向量，
存 library.db 的 clips 表（按 content_key，重复副本天然共享）。
之后就能用自然语言搜图："小孩骑滑板车"、"生日蛋糕"、"雪地"。

- 只读 DISK + 写 clips 表，不动任何照片。
- 按 content_key 去重：只算 is_primary=1 的主副本。
- 断点续跑：已算过的 content_key 自动跳过。

用法:
  python tools/clip_embed.py --limit 200     # 小样本/测速
  python tools/clip_embed.py                 # 全量
  python tools/clip_embed.py --stats         # 看进度
"""
import os
import sys
import time
import sqlite3
import argparse

os.nice(10)
import numpy as np
import torch
import open_clip
from PIL import Image
import pillow_heif

pillow_heif.register_heif_opener()

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
DB = f"{ROOT}/19_统一相册库/library.db"
MODEL_NAME, PRETRAINED = "ViT-B-32-quickgelu", "openai"


def pick_device():
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_image(path, size=224):
    with Image.open(path) as im:
        im = im.convert("RGB")
        short = min(im.size)
        # 居中裁剪成正方形，保持主体（比直接拉伸更贴近 CLIP 训练分布）
        if im.size[0] != im.size[1]:
            l = (im.size[0] - short) // 2
            t = (im.size[1] - short) // 2
            im = im.crop((l, t, l + short, t + short))
        return im.resize((size, size), Image.BICUBIC)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    db = sqlite3.connect(DB)
    db.execute("""CREATE TABLE IF NOT EXISTS clips (
        content_key TEXT PRIMARY KEY,
        vec BLOB NOT NULL,
        model TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    db.commit()

    if args.stats:
        done = db.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
        tot = db.execute("SELECT COUNT(DISTINCT content_key) FROM files "
                         "WHERE kind='photo'").fetchone()[0]
        print(f"已算 {done} / {tot} 个唯一内容")
        return

    have = {r[0] for r in db.execute("SELECT content_key FROM clips")}
    rows = db.execute(
        """SELECT content_key, path FROM files
           WHERE kind='photo' AND is_primary=1 ORDER BY lib, path""").fetchall()
    todo = [(ck, p) for ck, p in rows if ck not in have and os.path.exists(p)]
    if args.limit:
        todo = todo[:args.limit]
    if not todo:
        print("全部已完成，没有待算内容")
        return

    device = pick_device()
    print(f"待算 {len(todo)} 张 · device={device}", file=sys.stderr)
    model, _, preprocess = open_clip.create_model_and_transforms(
        MODEL_NAME, pretrained=PRETRAINED)
    model = model.to(device).eval()
    tok = open_clip.get_tokenizer(MODEL_NAME)

    t0 = time.time()
    n_done = n_err = 0
    buf_img, buf_key = [], []
    for i, (ck, p) in enumerate(todo):
        try:
            buf_img.append(preprocess(load_image(p)))
            buf_key.append(ck)
        except Exception as e:
            n_err += 1
            print(f"  ⚠️ {os.path.basename(p)}: {e}", file=sys.stderr)

        if len(buf_img) == args.batch or i == len(todo) - 1:
            if buf_img:
                x = torch.stack(buf_img).to(device)
                with torch.no_grad():
                    feat = model.encode_image(x)
                    feat = feat / feat.norm(dim=-1, keepdim=True)
                v = feat.float().cpu().numpy()
                db.executemany(
                    "INSERT OR REPLACE INTO clips VALUES(?,?,?,CURRENT_TIMESTAMP)",
                    [(k, v[j].astype(np.float32).tobytes(), f"{MODEL_NAME}/{PRETRAINED}")
                     for j, k in enumerate(buf_key)])
                db.commit()
                n_done += len(buf_img)
                buf_img, buf_key = [], []
        if (i + 1) % (args.batch * 20) == 0:
            el = time.time() - t0
            print(f"[{i+1}/{len(todo)}] {n_done} 张 错={n_err} {el:.0f}s "
                  f"({n_done/max(el,1):.1f}/s) 剩余约 "
                  f"{el/(i+1)*(len(todo)-i-1)/60:.1f}min", file=sys.stderr)

    print(f"完成 {n_done} 张 CLIP 嵌入，错 {n_err}，用时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

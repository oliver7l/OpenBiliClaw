#!/usr/bin/env python3
"""全库 AdaFace(IR-101) 嵌入提取 —— 第 7 轮启动的后台产线（供第 8 轮集成扩特征）。

为什么值得做：嵌入横评（_嵌入模型横评_20260919.md）结论是"四模型清晰档打平、
瓶颈在输入质量"，但 AdaFace 与 mbf/r50 **训练数据不同源**（WebFace12M）且
margin 质量自适应 —— 作为集成的**第三嵌入通道**，价值在"误差不相关"而非"单点更强"。

对齐妥协（必须知道的局限）：mbf/r50 是扫描时用 SCRFD 关键点 norm_crop 对齐的；
本脚本只有 box，只能做「框+50%边距 → 112²」的近似对齐。bench_emb 里 AdaFace
是在标准对齐裁剪上测的 ⇒ 本脚本产出的嵌入会略低于其横评上限，横向比较时记住这点。
（若第 8 轮证明 adaface 通道有效，正解是改 scan_faces 在检测时同步提 adaface。）

产物：19_统一相册库/_faces_backup/faces_adaface_20260919.npz
  ck(38009,) emb(38009,512 float16)  —— 行序与 faces_scrfd_20260919.npz 严格一致
断点续跑：每 2000 张存一次 .part；完成后原子改名。
"""
import os
import sys
import time

import cv2
import numpy as np
import onnxruntime as ort

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
NPZ = f"{ROOT}/19_统一相册库/_faces_backup/faces_scrfd_20260919.npz"
DB = f"{ROOT}/19_统一相册库/library.db"
MODEL = f"{ROOT}/照片人物判定/models/adaface_ir_101.onnx"
OUT = f"{ROOT}/19_统一相册库/_faces_backup/faces_adaface_20260919.npz"
PART = OUT + ".part"
CKPT = OUT + ".done_idx"

sys.path.insert(0, HERE)
from audit import imread_any  # noqa: E402


def crop112(im, box, size=112, margin=0.5):
    x, y, w, h = [float(v) for v in box]
    mx, my = w * margin, h * margin
    x0, y0 = max(0, x - mx), max(0, y - my)
    x1, y1 = min(im.shape[1], x + w + mx), min(im.shape[0], y + h + my)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    c = im[int(y0):int(y1), int(x0):int(x1)]
    if c.size == 0:
        return None
    return cv2.resize(c, (size, size), interpolation=cv2.INTER_AREA)


def main():
    d = np.load(NPZ, allow_pickle=True)
    cks = d["ck"].astype(str)
    box = d["box"].astype(np.float32)
    n = len(cks)

    import sqlite3
    con = sqlite3.connect(DB)
    paths = {}
    for p, c in con.execute(
            "select path, content_key from files "
            "order by is_primary desc"):       # primary 优先命中
        paths.setdefault(c, p)
    con.close()

    start = 0
    if os.path.exists(PART) and os.path.exists(CKPT):
        start = int(open(CKPT).read().strip())
        print(f"断点续跑：从 {start}/{n} 继续", flush=True)

    sess = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0].name
    emb = np.zeros((n, 512), dtype=np.float16)
    if start > 0 and os.path.exists(PART):
        emb[:start] = np.load(PART)["emb"][:start]

    t0 = time.time()
    done = start
    buf = []
    i = start
    while i < n:
        ck = cks[i]
        p = paths.get(ck)
        im = imread_any(p) if p else None
        c = crop112(im, box[i]) if im is not None else None
        if c is None:
            emb[i] = 0          # 读不到图/裁不出 ⇒ 零向量，下游 keep 过滤掉
        else:
            x = np.transpose(c.astype(np.float32) / 127.5 - 1.0, (2, 0, 1))[::-1]  # BGR
            buf.append((i, x))
        i += 1
        if len(buf) == 64 or i == n:
            if buf:
                xs = np.stack([x for _, x in buf])
                outs = sess.run(None, {inp: xs})[0]
                for (j, _), e in zip(buf, outs):
                    emb[j] = e.astype(np.float16)
                buf = []
        done = i
        if done % 2000 < 64:
            np.savez_compressed(PART, emb=emb)
            open(CKPT, "w").write(str(done))
            rate = done / max(1, time.time() - t0)
            print(f"[{done}/{n}] {rate:.1f} 脸/秒，剩 {(n - done) / max(rate, 1) / 60:.0f} 分钟",
                  flush=True)

    np.savez_compressed(OUT, ck=cks, emb=emb)
    if os.path.exists(PART):
        os.remove(PART)
    if os.path.exists(CKPT):
        os.remove(CKPT)
    nz = int((np.abs(emb).sum(axis=1) > 0).sum())
    print(f"✅ {OUT}（{n} 行，有效嵌入 {nz}，耗时 {(time.time() - t0) / 60:.0f} 分钟）", flush=True)


if __name__ == "__main__":
    main()

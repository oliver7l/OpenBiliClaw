#!/usr/bin/env python3
"""判定模型的「看图验证」——指标会撒谎，照片不会。

思路：两个模型对同一张脸给出相反结论时，必有一方错。
把这些**分歧样本**裁出来人工看，就能判断谁真的更准，
而不是只看 AUC 数字（自举/难负挖掘都会让 AUC 虚高）。

同时输出三类高危样本：
  A. 分歧样本：甲判是、乙判不是（|Δp| 最大）
  B. 09 班级库 top 命中：这里最容易出「陌生小孩被判成家人」
  C. 边界样本：概率落在阈值附近（最容易错的区间）

用法:
  python verify_clf.py 妈妈            # 单人
  python verify_clf.py 妈妈 --n 24
"""
import os
import sys
import json
import time
import sqlite3
import collections
import argparse
import subprocess
import tempfile

os.nice(10)
import cv2
import numpy as np
from PIL import Image, ImageDraw
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bench_clf import load, build_set, TorchMLP, CentroidNN, l2n   # 复用评估协议

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB_DB = f"{ROOT}/19_统一相册库/library.db"
OUTDIR = f"{ROOT}/照片人物判定/_verify"


def expit(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def fit_full(make, X, y):
    m = make()
    m.fit(X, y)
    return m


def proba(m, X):
    if hasattr(m, "predict_proba"):
        return m.predict_proba(X)[:, 1]
    if hasattr(m, "net"):
        import torch
        with torch.no_grad():
            return torch.sigmoid(
                m.net(torch.tensor(np.asarray(X, dtype=np.float32))).squeeze(1)).numpy()
    raise TypeError(type(m))


def crop_face(path, box, size=190, pad=0.45):
    """裁脸。两个坑（踩过）：
       1. 必须用 (faces.lib, content_key) 对应的物理文件，primary 副本尺寸不同、坐标不通用
       2. 不能 exif_transpose —— 检测时用的是 cv2.imread 的原始方向，转正会错位
    """
    x, y, w, h = box
    buf = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        return None
    H, W = img.shape[:2]
    px = int(max(w, h) * pad)
    x0, y0 = max(0, int(x - px)), max(0, int(y - px))
    x1, y1 = min(W, int(x + w + px)), min(H, int(y + h + px))
    c = img[y0:y1, x0:x1]
    if c.size == 0:
        return None
    c = cv2.resize(c, (size, size), interpolation=cv2.INTER_AREA)
    return Image.fromarray(cv2.cvtColor(c, cv2.COLOR_BGR2RGB))


def make_sheet(items, cols, out, title=""):
    S = items[0][0].size[0] if items else 190
    rows = (len(items) + cols - 1) // cols
    g = Image.new("RGB", (cols * S, rows * (S + 24)), (28, 28, 32))
    d = ImageDraw.Draw(g)
    for k, (im, cap) in enumerate(items):
        r, c = divmod(k, cols)
        g.paste(im, (c * S, r * (S + 24) + 24))
        d.text((c * S + 3, r * (S + 24) + 6), cap, fill=(235, 235, 242))
    g.save(out, quality=90)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("person")
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--feature", default="fused")
    a = ap.parse_args()
    person = a.person

    os.makedirs(OUTDIR, exist_ok=True)
    t0 = time.time()
    rid, cks, feats, ck_person, ck_lib = load()
    X = feats[a.feature]
    # 坐标（裁图用）
    con = sqlite3.connect(LIB_DB)
    box_of = {r: (x, y, w, h) for r, x, y, w, h in
              con.execute("select rowid, x, y, w, h from faces")}
    file_of = {}
    for lib, ck, path in con.execute("select lib, content_key, path from files"):
        file_of[(lib, ck)] = path
    lib_of = {r: l for r, l in con.execute("select rowid, lib from faces")}
    con.close()
    print(f"加载 {len(rid)} 张脸，用时 {time.time() - t0:.0f}s")

    pos, neg = build_set(person, rid, cks, feats, ck_person, ck_lib)
    idx = np.concatenate([pos, neg])
    Xi = X[idx]
    yi = np.concatenate([np.ones(len(pos)), np.zeros(len(neg))])
    print(f"训练：正 {len(pos)} 负 {len(neg)}")

    models = {}
    models["LR"] = fit_full(lambda: LogisticRegression(C=1.0, max_iter=5000), Xi, yi)
    models["MLP"] = fit_full(lambda: TorchMLP(Xi.shape[1], (256, 64)), Xi, yi)
    models["中心NN"] = fit_full(lambda: CentroidNN(), Xi, yi)
    print(f"训练完成，用时 {time.time() - t0:.0f}s")

    # 全库打分
    P = {k: proba(m, X) for k, m in models.items()}
    mine = {ck for ck, p in ck_person.items() if p == person}

    # ---------- A. 分歧样本 ----------
    d_lr_mlp = (P["LR"] - P["MLP"])
    # 只关心「LR 判是但 MLP 判不是」和反之，且不在自己的归档里（归档内大家都对，没信息量）
    cand = np.array([i for i in range(len(rid)) if cks[i] not in mine])
    order = cand[np.argsort(-np.abs(d_lr_mlp[cand]))][:a.n]
    items = []
    for i in order:
        box = box_of.get(int(rid[i]))
        lib = lib_of.get(int(rid[i]))
        path = file_of.get((lib, cks[i]))
        if not box or not path:
            continue
        im = crop_face(path, box)
        if im is None:
            continue
        who = "LR↑" if d_lr_mlp[i] > 0 else "MLP↑"
        items.append((im, f"#{len(items)} {who} LR={P['LR'][i]:.2f} MLP={P['MLP'][i]:.2f} "
                          f"{ck_lib.get(cks[i], '?')}"))
    p = make_sheet(items, 6, f"{OUTDIR}/{person}_A_分歧样本.jpg")
    print(f"\n[A] 分歧样本 {len(items)} 张 → {p}")
    for k, i in enumerate(order):
        box = box_of.get(int(rid[i]))
        if not box:
            continue
        print(f"   #{k} LR={P['LR'][i]:.3f} MLP={P['MLP'][i]:.3f} 中心={P['中心NN'][i]:.3f} "
              f"lib={ck_lib.get(cks[i], '?')} side={int(max(box[2], box[3]))}")

    # ---------- B. 09 班级库 top 命中 ----------
    idx09 = np.array([i for i in range(len(rid)) if ck_lib.get(cks[i]) == "09"])
    sel = idx09[np.argsort(-P["LR"][idx09])][:a.n]
    items = []
    for i in sel:
        box = box_of.get(int(rid[i])); lib = lib_of.get(int(rid[i]))
        path = file_of.get((lib, cks[i]))
        if not box or not path:
            continue
        im = crop_face(path, box)
        if im is None:
            continue
        items.append((im, f"#{len(items)} LR={P['LR'][i]:.2f} MLP={P['MLP'][i]:.2f} 09"))
    p = make_sheet(items, 6, f"{OUTDIR}/{person}_B_09命中.jpg")
    print(f"\n[B] 09 命中 top {len(items)} → {p}")
    for k, i in enumerate(sel):
        box = box_of.get(int(rid[i]))
        print(f"   #{k} LR={P['LR'][i]:.3f} MLP={P['MLP'][i]:.3f} "
              f"side={int(max(box[2], box[3])) if box else '?'}")

    # ---------- C. 边界样本 ----------
    thr = float(np.quantile(P["LR"], 0.999))
    edge = np.array([i for i in range(len(rid))
                     if cks[i] not in mine and abs(P["LR"][i] - thr) < 0.08])
    rng = np.random.default_rng(5)
    sel = rng.choice(edge, size=min(a.n, len(edge)), replace=False) if len(edge) else []
    items = []
    for i in sel:
        box = box_of.get(int(rid[i])); lib = lib_of.get(int(rid[i]))
        path = file_of.get((lib, cks[i]))
        if not box or not path:
            continue
        im = crop_face(path, box)
        if im is None:
            continue
        items.append((im, f"#{len(items)} LR={P['LR'][i]:.2f} MLP={P['MLP'][i]:.2f} thr≈{thr:.2f}"))
    p = make_sheet(items, 6, f"{OUTDIR}/{person}_C_边界样本.jpg")
    print(f"\n[C] 边界样本 {len(items)} 张（阈值≈{thr:.3f}）→ {p}")
    print(f"\n总用时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()

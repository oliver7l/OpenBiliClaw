#!/usr/bin/env python3
"""无监督簇探针：不依赖任何"归档/模型标签"，把家庭库的人脸聚类后渲染成拼图，
由我（人）看图认人 → 得到**干净的**身份种子。

为什么需要它（第 04 轮的核心动机）：
  `18_照片分组` 的目录语义是"与这个名字**相关**的照片"而非"这个人的脸"，
  所以 妈妈/我/爸爸 的"归档正样本"里混着乐仔、截图、名人。拿它当种子 =
  垃圾进垃圾出（第 03 轮实测：妈妈 TPR 仅 0.174，且锚点里出现马云截图）。
  唯一不循环的办法：**从原始人脸出发，用无监督聚类分组，再由人看图命名**。

输出的簇代表图给我看；同目录下的 JSON 保存每个簇的成员（含 ck/box/归一化中心），
认人后可直接写进 `_audit/verdicts.jsonl` 当训练真值。

用法:
  python cluster_probe.py --libs 07,08 --k 40 --top 12 --per 6
  python cluster_probe.py --libs 07  --k 30 --top 10 --sheet 08_簇探针
"""
import os
import sys
import json
import time
import sqlite3
import argparse

os.nice(10)
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit import crop_face, make_sheet, font, imread_any  # 复用裁脸/拼图（保证与审核台同一渲染口径）

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB_DB = f"{ROOT}/19_统一相册库/library.db"
OUTDIR = f"{ROOT}/照片人物判定/_audit"

MIN_SIDE = 18      # 太小的脸（07 中位只有 19px）聚类时是噪声，但仍留着标记
MIN_DET = 0.6


def load_faces(libs, emb="mbf"):
    """从 library.db 取人脸。返回 list[dict]。**唯一键用 rowid**（face_id 会复用）。"""
    con = sqlite3.connect(LIB_DB)
    con.row_factory = sqlite3.Row
    q = f"""SELECT rowid AS rid, lib, content_key, face_idx, x, y, w, h, det_score,
                   emb_{emb} AS emb
            FROM faces WHERE lib IN ({','.join('?' * len(libs))})
            ORDER BY rowid"""   # 必须 ORDER BY：否则行序不定，labels 与行无法对齐
    rows = []
    for r in con.execute(q, list(libs)):
        if r["emb"] is None:
            continue
        rows.append(dict(rid=r["rid"], lib=r["lib"], ck=r["content_key"],
                         fi=r["face_idx"], box=[r["x"], r["y"], r["w"], r["h"]],
                         det=r["det_score"],
                         emb=np.frombuffer(r["emb"], dtype=np.float32)))
    con.close()
    return rows


def primary_paths(cks):
    """ck → primary 物理路径（faces 只扫了 is_primary=1）。"""
    con = sqlite3.connect(LIB_DB)
    con.row_factory = sqlite3.Row
    out = {}
    CH = 900
    cks = list(cks)
    for i in range(0, len(cks), CH):
        part = cks[i:i + CH]
        for r in con.execute(
                f"""SELECT content_key, path FROM files
                    WHERE is_primary=1 AND content_key IN ({','.join('?' * len(part))})""",
                part):
            out.setdefault(r["content_key"], r["path"])
    con.close()
    return out


def l2n(X):
    n = np.linalg.norm(X, axis=1, keepdims=True)
    n[n < 1e-9] = 1.0
    return X / n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--libs", default="07,08")
    ap.add_argument("--emb", default="mbf", choices=["mbf", "r50"])
    ap.add_argument("--k", type=int, default=40, help="簇数")
    ap.add_argument("--top", type=int, default=12, help="渲染最大的前 N 个簇")
    ap.add_argument("--per", type=int, default=6, help="每簇渲染几张")
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--sheet", default="簇探针")
    ap.add_argument("--min-side", type=int, default=MIN_SIDE)
    a = ap.parse_args()

    libs = a.libs.split(",")
    t0 = time.time()
    faces = load_faces(libs, a.emb)
    print(f"载入 {len(faces)} 张脸（libs={libs}, emb={a.emb}, {time.time()-t0:.0f}s）")
    if not faces:
        print("没人脸，退出"); return

    X = l2n(np.stack([f["emb"] for f in faces]))
    side = np.array([min(f["box"][2], f["box"][3]) for f in faces])

    # 聚类只喂"够大的脸"（小脸在嵌入空间噪声大，会把簇搅浑）；小脸仍保留在图里待判
    keep = side >= a.min_side
    print(f"参与聚类 {int(keep.sum())} 张（min_side≥{a.min_side}），"
          f"过小 {int((~keep).sum())} 张")

    from sklearn.cluster import KMeans
    km = KMeans(n_clusters=a.k, n_init=4, random_state=0).fit(X[keep])
    lab = np.full(len(faces), -1)
    lab[keep] = km.labels_
    C = l2n(km.cluster_centers_)

    sizes = np.bincount(km.labels_, minlength=a.k)
    order = np.argsort(-sizes)

    paths = primary_paths({f["ck"] for f in faces})
    os.makedirs(OUTDIR, exist_ok=True)

    # 一张大拼图：**每簇占一行**（cols=per），全局编号 #k；cluster_of[k]=簇号，
    # 认人时只需说"#3 #7 #11 是妈妈"，回来按编号落库。
    items, meta, cluster_of = [], [], []
    n_fail = 0
    for ci in order[:a.top]:
        idx = np.where(lab == ci)[0]
        sims = X[idx] @ C[ci]
        # 取最像中心的和最不像中心的各一半 —— 一张图同时看"纯度"和"边界"
        o = np.argsort(-sims)
        n_h = a.per // 2
        pick = (list(idx[o[:a.per - n_h]]) + list(idx[o[-n_h:]])) if n_h else list(idx[o[:a.per]])
        n_before = len(items)
        for i in pick:
            f = faces[i]
            p = paths.get(f["ck"])
            if not p:
                n_fail += 1
                continue
            im = crop_face(p, f["box"])
            if im is None:
                n_fail += 1
                continue
            j = len(items)
            s = float(X[i] @ C[ci])
            items.append((im, f"s={s:.2f} {min(f['box'][2], f['box'][3]):.0f}px"))
            cluster_of.append(dict(no=j, cluster=int(ci), size=int(sizes[ci]),
                                   med_sim=float(np.median(sims))))
            _im = imread_any(p)
            iw, ih = (_im.shape[1], _im.shape[0]) if _im is not None else (0, 0)
            bx = f["box"]
            meta.append(dict(no=j, row=int(j // a.per), cluster=int(ci),
                             rid=int(f["rid"]), ck=f["ck"], face_idx=int(f["fi"]),
                             lib=f["lib"], box=[float(v) for v in bx],
                             cx=float((bx[0] + bx[2] / 2) / iw) if iw else None,
                             cy=float((bx[1] + bx[3] / 2) / ih) if ih else None,
                             iw=int(iw), ih=int(ih), sim=s, path=p))
        print(f"  C{ci:<4} n={sizes[ci]:<5} med_sim={np.median(sims):.3f} "
              f"渲染{len(items)-n_before}/{len(pick)}张")
    if n_fail:
        print(f"  ⚠️ 裁图失败 {n_fail} 张（HEIC 未注册 / 路径缺失 / box 越界）")

    if not items:
        print("无可渲染人脸"); return
    out = f"{OUTDIR}/{a.sheet}.jpg"
    make_sheet(items, a.per, out,
               title=f"簇探针 {libs} k={a.k} 每行一个簇（左半=最像中心 / 右半=最不像）"
                     f"  行序=簇大小降序")
    json.dump(dict(libs=libs, emb=a.emb, k=a.k, top=a.top, per=a.per,
                   order=[int(v) for v in order[:a.top]],
                   sizes=[int(v) for v in sizes],
                   cluster_of=cluster_of, items=meta),
              open(f"{OUTDIR}/{a.sheet}.json", "w"), ensure_ascii=False, indent=1)
    # labels 落盘（含 rowid 与 lib/ck），供后续"簇↔归档投票""簇→verdicts"复用，避免重算
    np.savez_compressed(
        f"{OUTDIR}/{a.sheet}.labels.npz",
        rid=np.array([f["rid"] for f in faces], dtype=np.int64),
        ck=np.array([f["ck"] for f in faces]),
        lib=np.array([f["lib"] for f in faces]),
        lab=lab, keep=keep, k=a.k, emb=a.emb)
    print(f"\n拼图 → {out}\n清单 → {OUTDIR}/{a.sheet}.json"
          f"\n簇标签 → {OUTDIR}/{a.sheet}.labels.npz")
    print(f"总用时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

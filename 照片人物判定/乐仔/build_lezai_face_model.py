#!/usr/bin/env python3
"""乐仔（童言）人脸模型构建。

思路：
1. 扫描「乐仔相册」目录，用 InsightFace(buffalo_l) 检测人脸 + 提取 512-d ArcFace 向量
2. 贪心聚类（余弦阈值 CLUSTER_THRESH），把相册里的人物分成若干簇
   —— 相册里除乐仔外还可能有家人/同学，需要分开
3. 输出每簇的样本数、代表图（居中裁好的缩略图），供人眼/多模态确认哪一簇是乐仔
4. 用 --pick 指定簇号后，合并这些簇生成 乐仔原型向量 写入 lezai_face_model.json

用法：
  # 第一步：扫描 + 聚类 + 生成代表图
  .venv-face/bin/python build_lezai_face_model.py scan --src <乐仔相册目录> --out lezai_scan
  # 第二步：确认簇号后（可多簇，逗号分隔），生成模型
  .venv-face/bin/python build_lezai_face_model.py build --scan lezai_scan --pick 0,3 --name 童言
"""
import argparse
import json
import os
import shutil
import sys

import cv2
import numpy as np
from PIL import Image

DET_SIZE = (640, 640)
CLUSTER_THRESH = 0.55          # 同一个人（跨年龄）建议放宽；先用 0.55
MIN_FACE_PX = 60               # 小于该像素的人脸丢弃（太糊/太远）


def load_app():
    from insightface.app import FaceAnalysis
    app = FaceAnalysis(name="buffalo_l", root=".", allowed_modules=["detection", "recognition"])
    app.prepare(ctx_id=-1, det_size=DET_SIZE)
    return app


def l2(x):
    n = np.linalg.norm(x)
    return x / (n if n else 1.0)


def scan_dir(app, src, out_dir):
    crops_dir = os.path.join(out_dir, "face_crops")
    os.makedirs(crops_dir, exist_ok=True)
    files = []
    for root, _dirs, fs in os.walk(src):
        for f in fs:
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp")):
                files.append(os.path.join(root, f))
    files.sort()
    print(f"待扫描图片: {len(files)}", flush=True)

    recs = []
    for i, p in enumerate(files):
        try:
            img = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            continue
        if img is None:
            continue
        try:
            faces = app.get(img)
        except Exception as e:
            print(f"  [warn] {p}: {e}", flush=True)
            continue
        for fi, f in enumerate(faces):
            x1, y1, x2, y2 = [int(v) for v in f.bbox]
            w, h = x2 - x1, y2 - y1
            if w < MIN_FACE_PX or h < MIN_FACE_PX:
                continue
            pad = int(max(w, h) * 0.35)
            H, W = img.shape[:2]
            cx1, cy1 = max(0, x1 - pad), max(0, y1 - pad)
            cx2, cy2 = min(W, x2 + pad), min(H, y2 + pad)
            crop = img[cy1:cy2, cx1:cx2]
            cname = f"f{len(recs):05d}.jpg"
            cpath = os.path.join(crops_dir, cname)
            try:
                cv2.imencode(".jpg", crop)[1].tofile(cpath)
            except Exception:
                continue
            recs.append({
                "id": len(recs),
                "src": p,
                "face_idx": fi,
                "bbox": [x1, y1, x2, y2],
                "det_score": float(f.det_score),
                "crop": cname,
                "emb": f.normed_embedding.tolist() if hasattr(f, "normed_embedding") else l2(f.embedding).tolist(),
            })
        if (i + 1) % 50 == 0:
            print(f"  已处理 {i+1}/{len(files)}，累计人脸 {len(recs)}", flush=True)

    print(f"有效人脸: {len(recs)}", flush=True)

    # 贪心聚类
    embs = np.array([r["emb"] for r in recs], dtype=np.float32)
    embs = embs / (np.linalg.norm(embs, axis=1, keepdims=True) + 1e-9)
    labels = [-1] * len(recs)
    centers = []
    for i in range(len(recs)):
        if labels[i] != -1:
            continue
        ci = len(centers)
        centers.append(embs[i])
        labels[i] = ci
        # 一次性把与中心足够近的都拉进来
        sims = embs @ centers[ci]
        for j in np.where((sims > CLUSTER_THRESH) & (np.array(labels) == -1))[0]:
            labels[j] = ci
            centers[ci] = l2(centers[ci] * 0.5 + embs[j] * 0.5)

    clusters = []
    for ci in range(len(centers)):
        idxs = [i for i, l in enumerate(labels) if l == ci]
        if not idxs:
            continue
        # 代表图：取与该簇中心最相似的 6 张
        sims = embs[idxs] @ centers[ci]
        order = np.argsort(-sims)
        reps = [recs[idxs[k]] for k in order[:6]]
        # 拼一张代表图
        tiles = []
        for r in reps:
            im = Image.open(os.path.join(crops_dir, r["crop"])).convert("RGB").resize((160, 160))
            tiles.append(im)
        sheet = Image.new("RGB", (160 * len(tiles), 160))
        for k, t in enumerate(tiles):
            sheet.paste(t, (160 * k, 0))
        spath = os.path.join(out_dir, f"cluster_{ci:02d}_n{len(idxs)}.jpg")
        sheet.save(spath, quality=90)
        clusters.append({
            "cluster": ci,
            "size": len(idxs),
            "center": l2(centers[ci]).tolist(),
            "sheet": os.path.basename(spath),
            "samples": [recs[i]["crop"] for i in idxs[:20]],
            "srcs": sorted({recs[i]["src"] for i in idxs})[:20],
        })
    clusters.sort(key=lambda c: -c["size"])

    meta = {"src": src, "n_faces": len(recs), "clusters": clusters,
            "records": [{k: v for k, v in r.items() if k != "emb"} for r in recs]}
    with open(os.path.join(out_dir, "scan_meta.json"), "w", encoding="utf-8") as fp:
        json.dump(meta, fp, ensure_ascii=False, indent=1)
    np.save(os.path.join(out_dir, "embs.npy"), embs)
    with open(os.path.join(out_dir, "labels.json"), "w") as fp:
        json.dump(labels, fp)
    print(f"\n簇数: {len(clusters)}")
    for c in clusters[:15]:
        print(f"  簇{c['cluster']:>2}  样本{c['size']:>4}  代表图 {c['sheet']}")
    print(f"\n结果目录: {out_dir} —— 请查看 cluster_XX_*.jpg 确认哪一簇是乐仔")


def build_model(scan_dir, picks, name):
    meta = json.load(open(os.path.join(scan_dir, "scan_meta.json"), encoding="utf-8"))
    embs = np.load(os.path.join(scan_dir, "embs.npy"))
    labels = json.load(open(os.path.join(scan_dir, "labels.json")))
    picks = set(picks)
    idxs = [i for i, l in enumerate(labels) if l in picks]
    if not idxs:
        print("没有选中任何样本", file=sys.stderr)
        sys.exit(1)
    sub = embs[idxs]
    proto = l2(sub.mean(axis=0))
    sims = sub @ proto
    print(f"合并簇 {sorted(picks)}，样本 {len(idxs)}")
    print(f"类内相似度: mean={sims.mean():.3f} min={sims.min():.3f} max={sims.max():.3f}")
    model = {
        "name": name,
        "alias": "乐仔",
        "proto": proto.tolist(),
        "n_samples": len(idxs),
        "clusters": sorted(picks),
        "sim_mean": float(sims.mean()),
        "sim_min": float(sims.min()),
        "source": meta["src"],
        "scan_dir": os.path.abspath(scan_dir),
    }
    out = os.path.join(scan_dir, "lezai_face_model.json")
    json.dump(model, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"模型已写入: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan")
    s.add_argument("--src", required=True)
    s.add_argument("--out", default="lezai_scan")

    b = sub.add_parser("build")
    b.add_argument("--scan", default="lezai_scan")
    b.add_argument("--pick", required=True, help="簇号，逗号分隔，如 0,3")
    b.add_argument("--name", default="童言")

    a = ap.parse_args()
    app = load_app()
    if a.cmd == "scan":
        scan_dir(app, a.src, a.out)
    else:
        picks = [int(x) for x in a.pick.split(",") if x.strip() != ""]
        build_model(a.scan, picks, a.name)

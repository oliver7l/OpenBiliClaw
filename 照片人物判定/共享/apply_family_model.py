#!/usr/bin/env python3
"""用 family_clf_model.json 对全库人脸预测，打照片级标签 + 生成审核页。

标签规则（photo_person_tags, source='clf'）：
  照片中任意一张脸对某人的预测概率 >= 阈值 → 该照片打上该人标签。
  默认 0.90（严格档）；--th 可调。重跑会先清掉旧 'clf' 标签。

审核页（_review/家人判定审核_clf.html）：
  每人 top-12 高置信 + 8 张边缘(0.5~0.85)样本，按来源库标注，
  重点核验 09（班级相册）里的预测是否可靠。
"""
import argparse
import base64
import io
import json
import sqlite3
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
LIB_DB = ROOT / "19_统一相册库" / "library.db"
MODEL = ROOT / "照片人物判定" / "family_clf_model.json"
OUT_HTML = ROOT / "19_统一相册库" / "_review" / "家人判定审核_clf.html"
LIB_TAG = ROOT / "19_统一相册库" / "library.db"

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass


def l2n(X, axis=1):
    X = np.asarray(X, dtype=np.float32)
    n = np.linalg.norm(X, axis=axis, keepdims=True)
    n[n < 1e-9] = 1.0
    return X / n


def thumb_b64(path, max_side=320, face=None):
    try:
        im = Image.open(path)
        im = im.convert("RGB")
        if face is not None and all(v is not None for v in face):
            x, y, w, h = face
            cx, cy = x + w / 2, y + h / 2
            s = max(w, h) * 2.5
            W, H = im.size
            box = (max(0, int(cx - s)), max(0, int(cy - s)),
                   min(W, int(cx + s)), min(H, int(cy + s)))
            im = im.crop(box)
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=72)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--th", type=float, default=0.90, help="打标签概率阈值")
    ap.add_argument("--no-html", action="store_true")
    ap.add_argument("--no-tags", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    m = json.loads(MODEL.read_text(encoding="utf-8"))
    W = np.array(m["coef"], dtype=np.float32)
    b = np.array(m["intercept"], dtype=np.float32)
    names = m["classes"]

    db = sqlite3.connect(str(LIB_DB))
    faces = db.execute(
        "SELECT face_id, content_key, lib, det_score, w, h, emb_mbf, emb_r50 "
        "FROM faces WHERE emb_mbf IS NOT NULL AND emb_r50 IS NOT NULL").fetchall()
    mbf = l2n(np.vstack([np.frombuffer(r[6], dtype=np.float32) for r in faces]))
    r50 = l2n(np.vstack([np.frombuffer(r[7], dtype=np.float32) for r in faces]))
    X = l2n(np.hstack([mbf, r50]))
    Z = X @ W.T + b
    Z = Z - Z.max(axis=1, keepdims=True)
    P = np.exp(Z)
    P /= P.sum(axis=1, keepdims=True)
    print(f"预测完成 {P.shape}，用时 {time.time()-t0:.0f}s")

    paths = {}
    for ck, p in db.execute(
            "SELECT content_key, path FROM files WHERE is_primary=1"):
        paths[ck] = p

    # ---- 打标签 ----
    if not args.no_tags:
        db.execute("DELETE FROM photo_person_tags WHERE source='clf'")
        cluster_pairs = {t for t in db.execute(
            "SELECT content_key, person FROM photo_person_tags WHERE source='cluster'")}
        n_tag = 0
        best = {}   # content_key -> {person: max_prob}
        for i, r in enumerate(faces):
            ck = r[1]
            for ci, pn in enumerate(names):
                if P[i, ci] >= args.th:
                    if P[i, ci] > best.get(ck, {}).get(pn, 0):
                        best.setdefault(ck, {})[pn] = float(P[i, ci])
        for ck, d in best.items():
            for pn, p in d.items():
                if (ck, pn) in cluster_pairs:
                    continue   # cluster 人工真值优先
                db.execute(
                    "INSERT OR REPLACE INTO photo_person_tags VALUES (?,?,?,?)",
                    (ck, pn, "clf", round(p, 4)))
                n_tag += 1
        db.commit()
        print(f"标签(th={args.th}): {n_tag} 条，覆盖 {len(best)} 张照片 "
              f"(跳过 cluster 真值 {len(best) and sum(1 for ck,d in best.items() for pn in d if (ck,pn) in cluster_pairs)} 条)")

    # ---- 审核页 ----
    if not args.no_html:
        OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
        html = ["""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>家人判定审核（多类分类器）</title>
<style>
body{font-family:-apple-system,'PingFang SC',sans-serif;margin:24px;background:#fafafa}
h2{border-left:4px solid #4a78c2;padding-left:10px;margin-top:36px}
.grid{display:flex;flex-wrap:wrap;gap:8px}
.card{background:#fff;border:1px solid #ddd;border-radius:8px;padding:6px;width:158px;font-size:12px}
.card img{width:146px;height:146px;object-fit:cover;border-radius:4px;background:#eee}
.p{color:#c0392b;font-weight:600}.edge{color:#8e6d1a}
.lib09 .card{border-color:#e8a}
.note{color:#666;font-size:13px;margin:6px 0 12px}
</style></head><body>
<h1>家人判定审核（多类分类器）</h1>
<p class="note">红字=高置信(top12)，棕字=边缘样本(0.5~0.85)。<b>粉边卡片=来自09班级相册，请重点核验</b>（此前 v1 模型就是栽在 09 的陌生幼童上）。</p>"""]
        for ci, pn in enumerate(names):
            col = P[:, ci]
            order = np.argsort(-col)
            top = [int(j) for j in order[:12] if col[j] >= 0.5]
            band = [int(j) for j in order
                    if 0.5 <= col[j] < 0.85][:60]
            rng = np.random.default_rng(0)
            edge = list(rng.choice(band, min(8, len(band)), replace=False)) if band else []
            html.append(f"<h2>{pn} <small style='font-size:13px;color:#888'>"
                        f"≥0.9: {(col>=0.9).sum()} 张脸 | ≥{args.th} 已打标</small></h2>")
            html.append("<div class='grid'>")
            for tag, idxs in (("p", top), ("edge", edge)):
                for i in idxs:
                    r = faces[i]
                    p = paths.get(r[1])
                    if not p:
                        continue
                    cls = "card" + (" lib09" if r[2] == "09" else "")
                    b64 = thumb_b64(p, face=(r[4], r[5], r[6], r[7])
                                    if r[4] is not None else None)
                    if not b64:
                        continue
                    prob_txt = f"<span class='{tag}'>{pn} {col[i]:.3f}</span>"
                    lib_tag = r[2]
                    rel_show = p.split("originals/")[-1][:60]
                    html.append(
                        f"<div class='{cls}'><img src='{b64}'><br>{prob_txt} "
                        f"<span style='color:#999'>[{lib_tag}]</span><br>"
                        f"<span style='color:#aaa'>{rel_show}</span></div>")
            html.append("</div>")
        html.append("</body></html>")
        OUT_HTML.write_text("\n".join(html), encoding="utf-8")
        print(f"审核页: {OUT_HTML.relative_to(ROOT)} "
              f"({OUT_HTML.stat().st_size/1e6:.1f} MB)")
    db.close()
    print(f"完成，用时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

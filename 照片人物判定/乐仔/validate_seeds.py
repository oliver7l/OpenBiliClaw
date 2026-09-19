#!/usr/bin/env python3
"""08 单脸种子提取 + 09 gallery 第三通道 kNN 验证。"""
import sqlite3
import json
import numpy as np
from collections import defaultdict

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"


def l2(v):
    if isinstance(v, (bytes, bytearray, memoryview)):
        v = np.frombuffer(v, dtype=np.float32)
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


def load_gal(path):
    m = json.load(open(path))
    gal = m.get("gallery") or m.get("faces") or []
    return np.vstack([l2(g["embedding"] if isinstance(g, dict) else g) for g in gal])


def knn(G, X, k=5):
    return np.array([np.sort(G @ v)[::-1][:k].mean() for v in X])


con = sqlite3.connect(f"{ROOT}/08_乐仔相册/_face_index/faces08.db")
cur = con.cursor()
meta = {item.get("lib", ""): item
        for item in json.load(open(f"{ROOT}/08_乐仔相册/乐仔相片库/data.json", encoding="utf-8"))}

rows = cur.execute("SELECT photo, id, category, det_score, emb_mbf, emb_r50 FROM faces").fetchall()
byphoto = defaultdict(list)
for r in rows:
    byphoto[r[0]].append(r)

seeds = [fs[0] for p, fs in byphoto.items()
         if int(meta.get(p, {}).get("n_faces", 99)) <= 1 and len(fs) == 1]
print("单脸种子:", len(seeds))
cats = defaultdict(int)
for s in seeds:
    cats[s[2]] += 1
print("  按类:", dict(cats))
print("  det p10/p50: %.3f / %.3f" % tuple(np.percentile([s[3] for s in seeds], [10, 50])))

S = np.vstack([l2(s[5]) for s in seeds])
G_main = load_gal(f"/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/照片人物判定/乐仔/lezai_model_v3.json")
G_inf = load_gal(f"/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/照片人物判定/乐仔/lezai_aux_infant.json")
km, ki = knn(G_main, S), knn(G_inf, S)
for name, k in [("主库(317)", km), ("婴儿库(190)", ki)]:
    print("种子 kNN %s: p5=%.3f p50=%.3f p95=%.3f | >=0.55: %d >=0.45: %d >=0.35: %d" % (
        name, *np.percentile(k, [5, 50, 95]),
        int((k >= 0.55).sum()), int((k >= 0.45).sum()), int((k >= 0.35).sum())))

d = np.load(f"{ROOT}/07_相册/相册&视频备份/_photo_index/r50_sample_emb.npz")
E2 = {int(f): l2(e) for f, e in zip(d["ids"], d["emb"])}
con7 = sqlite3.connect(f"{ROOT}/07_相册/相册&视频备份/_photo_index/photo_index.db")
neg = np.vstack([E2[r[0]] for r in con7.execute(
    "SELECT id FROM faces WHERE cluster=4 AND det_score>=0.6 ORDER BY RANDOM() LIMIT 200")])
kmn, kin = knn(G_main, neg), knn(G_inf, neg)
print("负例(07妈妈200) 主库: p50=%.3f p95=%.3f >=0.45: %d" % (
    *np.percentile(kmn, [50, 95]), int((kmn >= 0.45).sum())))
print("负例 婴儿库: p50=%.3f p95=%.3f >=0.45: %d" % (
    *np.percentile(kin, [50, 95]), int((kin >= 0.45).sum())))

low = int(((km < 0.35) & (ki < 0.35)).sum())
print("种子中双库 kNN 均<0.35（可疑，需人工抽验）:", low)

# 把种子 face id 存进 08 库，供后续建模用
cur.execute("CREATE TABLE IF NOT EXISTS seed_faces(face_id INTEGER PRIMARY KEY, source TEXT)")
cur.execute("DELETE FROM seed_faces")
cur.executemany("INSERT OR REPLACE INTO seed_faces VALUES(?,?)",
                [(s[1], "single_face") for s in seeds])
con.commit()
print("seed_faces 已落库:", cur.execute("SELECT COUNT(*) FROM seed_faces").fetchone()[0])

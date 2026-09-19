#!/usr/bin/env python3
"""18_照片分组 · 第一步扫描（dry-run，不移动）。

规则（用户已确认）：
- 范围 07 + 08（09 幼儿园照片不动）
- 照片人物集合 = {A 级脸人物} ∪ {六模型高置信命中(非A脸, det>=0.6)}
- |集合| == 1 → 单人照，进移动清单；|集合| >= 2 → 合照不动；0 → 不动
- 方式：真移动（mv），目标 18_照片分组/<人>/<来源>/<文件名>

高置信阈值（模型推荐阈值 + 0.10）：
  乐仔 0.65 / 妈妈 0.65 / 艳艳 0.45 / 我 0.50 / 爸爸 0.45 / 七月 0.55
"""
import sqlite3
import json
import os
import hashlib
from collections import defaultdict
import numpy as np

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
PI = f"{ROOT}/07_相册/相册&视频备份/_photo_index"
F08 = f"{ROOT}/08_乐仔相册/_face_index/faces08.db"
MODELS = f"{ROOT}/照片人物判定"
OUT = f"{ROOT}/18_照片分组"

# (目录名, 模型JSON, 高置信阈值)
PERSONS = [
    ("乐仔", f"{MODELS}/乐仔/lezai_model_v4.json", 0.65),
    ("妈妈", f"{MODELS}/妈妈/妈妈_model_v1.json", 0.65),
    ("艳艳", f"{MODELS}/艳艳/艳艳_model_v1.json", 0.45),
    ("我", f"{MODELS}/我/我_model_v1.json", 0.50),
    ("爸爸", f"{MODELS}/爸爸/爸爸_model_v1.json", 0.45),
    ("七月", f"{MODELS}/七月/七月_model_v1.json", 0.55),
]
DET_MIN = 0.60


def l2n(v):
    if isinstance(v, (bytes, bytearray, memoryview)):
        v = np.frombuffer(v, dtype=np.float32)
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


def load_gal(path):
    m = json.load(open(path, encoding="utf-8"))
    out = []
    for g in m["gallery"]:
        out.append(l2n(g["embedding"]) if isinstance(g, dict) else l2n(g))
    return np.vstack(out)


def top5(G, X):
    S = X @ G.T
    k = min(5, S.shape[1])
    return np.partition(S, -k, axis=1)[:, -k:].mean(axis=1)


GALS = [(name, load_gal(p), th) for name, p, th in PERSONS]
# 乐仔 gallery 并入 08 单脸种子（同 08 扫描一致）
con08 = sqlite3.connect(F08)
seed_ids = {r[0] for r in con08.execute("SELECT face_id FROM seed_faces")}
seed_emb = [l2n(r[0]) for r in con08.execute(
    "SELECT emb_r50 FROM faces WHERE id IN (SELECT face_id FROM seed_faces)")]
names, mats, ths = zip(*GALS)
GALS = list(zip(names, [np.vstack([m, np.vstack(seed_emb)]) if n == "乐仔" else m
                        for n, m in zip(names, mats)], ths))
print("gallery 尺寸:", [(n, g.shape[0]) for n, g, _ in GALS])

# ---------- 07 ----------
con = sqlite3.connect(f"{PI}/photo_index.db")
d = np.load(f"{PI}/r50_sample_emb.npz")
E = {int(i): e for i, e in zip(d["ids"], d["emb"])}
# file_key = md5(relpath(new_path or path, 相册备份根))[:16]，已反向验证 3766/3766 命中
R07 = f"{ROOT}/07_相册/相册&视频备份"
k2p = {}
for p, np_ in con.execute("SELECT path, new_path FROM files WHERE kind='image'"):
    cur = np_ or p
    k2p[hashlib.md5(os.path.relpath(cur, R07).encode()).hexdigest()[:16]] = cur
rows = con.execute("""SELECT f.id, f.file_key, f.det_score, p.name, t.tier
 FROM faces f LEFT JOIN persons p ON p.cluster=f.cluster
 LEFT JOIN lib_tiers t ON t.face_id=f.id""").fetchall()
rows = [r for r in rows if r[0] in E]
print(f"07 脸: {len(rows)}, file_key 反查 miss: {sum(1 for r in rows if r[1] not in k2p)}")

X = np.vstack([l2n(E[r[0]]) for r in rows])
scores = {n: top5(g, X) for n, g, _ in GALS}
th_map = {n: th for n, _, th in GALS}

# A 级脸人物（不经 det 门，种子即确认）
a_person = {}
for r in rows:
    if r[4] == "A" and r[3]:
        a_person.setdefault(r[1], set()).add(r[3])

photos = defaultdict(set)
for i, r in enumerate(rows):
    fk = r[1]
    if fk in a_person:
        photos[fk] |= a_person[fk]
    if r[2] >= DET_MIN:
        for n, _, _ in GALS:
            if scores[n][i] >= th_map[n]:
                photos[fk].add(n)

single, group = [], []
for fk, ps in photos.items():
    path = k2p.get(fk)
    if len(ps) == 1:
        single.append((path, ps.pop(), "07_相册"))
    elif len(ps) >= 2:
        group.append((path, sorted(ps), "07_相册"))
n07_nophoto = len(photos)
print(f"07: 有命中的照片 {n07_nophoto}，单人 {len(single)}，合照不动 {len(group)}")

# ---------- 08 ----------
rows8 = con08.execute("SELECT id, photo, lib, det_score FROM faces ORDER BY id").fetchall()
X8 = np.vstack([l2n(e[0]) for e in con08.execute("SELECT emb_r50 FROM faces ORDER BY id")])
scores8 = {n: top5(g, X8) for n, g, _ in GALS}
id2row = {r[0]: i for i, r in enumerate(rows8)}
# A 级照片（08 的 A = lezai_scan08 tier A）
a08 = {r[0] for r in con08.execute(
    "SELECT f.photo FROM lezai_scan08 s JOIN faces f ON f.id=s.face_id WHERE s.tier='A'")}
seed08 = {r[0] for r in con08.execute(
    "SELECT f.photo FROM faces f WHERE f.id IN (SELECT face_id FROM seed_faces)")}

photos8 = defaultdict(set)
for pid, photo, lib, det in rows8:
    if photo in a08 or photo in seed08:
        photos8[photo].add("乐仔")
    if det >= DET_MIN:
        i = id2row[pid]
        for n, _, th in GALS:
            if n != "乐仔" and scores8[n][i] >= th:
                photos8[photo].add(n)

single8, group8 = [], []
for photo, ps in photos8.items():
    if len(ps) == 1:
        single8.append((f"{ROOT}/08_乐仔相册/乐仔相片库/{photo}", ps.pop(), "08_乐仔相册"))
    elif len(ps) >= 2:
        group8.append((f"{ROOT}/08_乐仔相册/乐仔相片库/{photo}", sorted(ps), "08_乐仔相册"))
print(f"08: 有命中 {len(photos8)}，单人 {len(single8)}，合照不动 {len(group8)}")

# ---------- 汇总 ----------
all_single = single + single8
by_person = defaultdict(list)
for path, p, src in all_single:
    by_person[p].append(path)
print("\n=== 移动清单（dry-run）===")
for n in ["乐仔", "妈妈", "艳艳", "我", "爸爸", "七月"]:
    print(f"  {n}: {len(by_person.get(n, []))} 张")
print(f"  合计: {len(all_single)} 张（合照不动 {len(group)+len(group8)} 张）")
missing = [p for p, _, _ in all_single if not p or not os.path.exists(p)]
print("路径缺失/文件不存在:", len(missing))

os.makedirs(OUT, exist_ok=True)
json.dump({"single": all_single, "group": group + group8},
          open(f"{OUT}/_scan_plan.json", "w", encoding="utf-8"), ensure_ascii=False)
print("清单已存 18_照片分组/_scan_plan.json")

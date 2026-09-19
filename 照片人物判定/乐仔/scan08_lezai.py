#!/usr/bin/env python3
"""08_乐仔相册 全库乐仔扫描（待办①）：
- 判定库 = 09 lezai_model_v4.json(507=主库317+婴儿库190) + 08 单脸种子(剔除可疑34)
- 扫 faces08.db 全部非种子脸（r50 空间，kNN top-5 判据）
- 阈值沿用已验证分离带：>=0.65 高置信 / 0.45-0.65 待人工复核 / <0.45 不动
- 同照互斥：一张照片只允许分数最高的那张脸进 A 级，其余 >=0.65 降 B 并标记
- 只落 lezai_scan08 表 + 生成审核页，不做任何归位（准字当头，小步推进）
"""
import sqlite3
import json
import os
import numpy as np

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
DB08 = f"{ROOT}/08_乐仔相册/_face_index/faces08.db"
V4 = f"/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/照片人物判定/乐仔/lezai_model_v4.json"
OUT_DIR = f"{ROOT}/08_乐仔相册/_face_index/lezai_scan08"

A_TH, B_TH = 0.65, 0.45
DET_MIN_A = 0.60


def l2(v):
    if isinstance(v, (bytes, bytearray, memoryview)):
        v = np.frombuffer(v, dtype=np.float32)
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


def knn(G, X, k=5):
    """X: (n,512) @ G.T: (512,m) -> 每行取 top-k 相似度均值与 top1"""
    S = X @ G.T
    S_sorted = np.sort(S, axis=1)[:, ::-1]
    return S_sorted[:, :k].mean(axis=1), S_sorted[:, 0]


# ---------- 1. 合并判定库 ----------
g4 = np.vstack([l2(g) for g in json.load(open(V4))["gallery"]])

con = sqlite3.connect(DB08)
cur = con.cursor()
rows = cur.execute(
    "SELECT f.id, f.photo, f.lib, f.category, f.face_idx, f.det_score, f.crop_path, f.emb_r50 "
    "FROM faces f").fetchall()
seed_ids = {r[0] for r in cur.execute("SELECT face_id FROM seed_faces")}

byphoto = {}
for r in rows:
    byphoto.setdefault(r[1], []).append(r)

# 种子嵌入（剔除可疑：对 507 库 kNN < 0.35）
seed_rows = [r for r in rows if r[0] in seed_ids]
S = np.vstack([l2(r[7]) for r in seed_rows])
s_sc, _ = knn(g4, S)
keep = s_sc >= 0.35
bad_seeds = [seed_rows[i] for i in range(len(seed_rows)) if not keep[i]]
seed_keep = [seed_rows[i] for i in range(len(seed_rows)) if keep[i]]
G = np.vstack([g4, np.vstack([l2(r[7]) for r in seed_keep])])
print(f"判定库: v4={g4.shape[0]} + 08种子={len(seed_keep)} (剔除可疑{len(bad_seeds)}) = {G.shape[0]}")

# ---------- 2. 负例对照（07 妈妈 200 张，r50 同空间） ----------
d = np.load(f"{ROOT}/07_相册/相册&视频备份/_photo_index/r50_sample_emb.npz")
E2 = {int(f): l2(e) for f, e in zip(d["ids"], d["emb"])}
con7 = sqlite3.connect(f"{ROOT}/07_相册/相册&视频备份/_photo_index/photo_index.db")
neg_ids = [r[0] for r in con7.execute(
    "SELECT id FROM faces WHERE cluster=4 AND det_score>=0.6 ORDER BY RANDOM() LIMIT 200")]
neg = np.vstack([E2[i] for i in neg_ids])
neg_sc, _ = knn(G, neg)
print("负例对照(07妈妈200): p50=%.3f p95=%.3f max=%.3f | >=0.45: %d" % (
    *np.percentile(neg_sc, [50, 95]), neg_sc.max(), int((neg_sc >= B_TH).sum())))

# ---------- 3. 扫全部非种子脸 ----------
photos_with_seed = set(r[1] for r in seed_rows)
scan_rows = [r for r in rows if r[0] not in seed_ids]
X = np.vstack([l2(r[7]) for r in scan_rows])
sc, top1 = knn(G, X)
print(f"扫描脸数: {len(scan_rows)}（种子 {len(seed_rows)} 张已跳过）")

results = []  # (row, top5, top1, tier, note)
for r, s5, s1 in zip(scan_rows, sc, top1):
    results.append({"row": r, "s5": float(s5), "s1": float(s1), "tier": None, "note": ""})

# A 候选初筛 + 同照互斥（每照只有最高分可进 A）
a_cands = [x for x in results if x["s5"] >= A_TH and x["row"][5] >= DET_MIN_A]
by_photo_best = {}
for x in a_cands:
    p = x["row"][1]
    if p not in by_photo_best or x["s5"] > by_photo_best[p]["s5"]:
        by_photo_best[p] = x
n_mutex = 0
for x in a_cands:
    p = x["row"][1]
    if by_photo_best[p] is x:
        x["tier"] = "A"
    else:
        x["tier"] = "B"
        x["note"] = "同照互斥(该照已有更高分乐仔脸)"
        n_mutex += 1
for x in results:
    if x["tier"] is None and x["s5"] >= B_TH:
        x["tier"] = "B"
    elif x["tier"] is None:
        x["tier"] = "-"

nA = sum(1 for x in results if x["tier"] == "A")
nB = sum(1 for x in results if x["tier"] == "B")
photos_A = set(x["row"][1] for x in results if x["tier"] == "A")
photos_B_only = set(x["row"][1] for x in results if x["tier"] == "B") - photos_A
print(f"\n结果: A高置信 {nA} 张脸 / {len(photos_A)} 张照片；B待复核 {nB} 张脸（含同照互斥 {n_mutex}）")
print(f"  覆盖照片: A {len(photos_A)} + 仅B {len(photos_B_only)}；全库照片 {len(byphoto)}（种子照 {len(photos_with_seed)}）")

# 种子照里多脸的其它脸分布（应几乎全在 - 桶，因为已确认是同学/家人）
seed_photo_others = [x for x in results if x["row"][1] in photos_with_seed]
print(f"  种子照的其它脸 {len(seed_photo_others)}: "
      f"A={sum(1 for x in seed_photo_others if x['tier']=='A')} "
      f"B={sum(1 for x in seed_photo_others if x['tier']=='B')}")

# ---------- 4. 落库 ----------
cur.execute("""CREATE TABLE IF NOT EXISTS lezai_scan08(
  face_id INTEGER PRIMARY KEY, top5 REAL, top1 REAL, tier TEXT, note TEXT)""")
cur.execute("DELETE FROM lezai_scan08")
cur.executemany("INSERT OR REPLACE INTO lezai_scan08 VALUES(?,?,?,?,?)",
                [(x["row"][0], x["s5"], x["s1"], x["tier"], x["note"]) for x in results])
con.commit()
n_chk = cur.execute("SELECT COUNT(*) FROM lezai_scan08").fetchone()[0]
print(f"\nlezai_scan08 落库复核: {n_chk}（应={len(results)}）")
con.close()

# ---------- 5. 审核页 ----------
os.makedirs(OUT_DIR, exist_ok=True)


def rel(path, base=OUT_DIR):
    return os.path.relpath(path, base)


# 18_照片分组移动映射（moved_photos: lib 相对路径 -> 新绝对位置）
MOVED08 = {}
try:
    _c = sqlite3.connect(DB08)
    MOVED08 = dict(_c.execute("SELECT photo, dst FROM moved_photos"))
    _c.close()
except sqlite3.OperationalError:
    pass


def orig_path(lib):
    """原图当前路径：原位存在用原位，否则查移动映射。"""
    p = os.path.join(ROOT, "08_乐仔相册/乐仔相片库", lib)
    if os.path.exists(p):
        return p
    return MOVED08.get(lib, p)


def page(fname, title, items, desc):
    items = sorted(items, key=lambda x: -x["s5"])
    cards = []
    for x in items:
        r = x["row"]
        fid, photo, lib, cat, fidx, det, crop = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
        note = f'<div class="note">{x["note"]}</div>' if x["note"] else ""
        cards.append(f'''<div class="card">
  <a href="{rel(crop)}" target="_blank"><img class="face" src="{rel(crop)}" loading="lazy" onerror="this.style.display='none'"></a>
  <div class="info">
    <div class="score">{x["s5"]:.3f} <span class="t1">top1={x["s1"]:.3f}</span></div>
    <div>face #{fidx} · det {det:.2f} · {x["tier"]}级</div>
    <div class="cat">{cat}</div>
    {note}
    <a class="orig" href="{rel(orig_path(lib))}" target="_blank">看原图</a>
    <div class="fn">{photo}</div>
  </div>
</div>''')
    html = f'''<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>{title}</title><style>
body{{font-family:-apple-system,"PingFang SC",sans-serif;background:#f5f5f7;margin:0;padding:20px;color:#1d1d1f}}
h1{{font-size:20px}} .desc{{color:#666;font-size:13px;margin-bottom:16px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px}}
.card{{background:#fff;border-radius:10px;padding:10px;box-shadow:0 1px 3px rgba(0,0,0,.08);font-size:12px}}
.card img.face{{width:100%;aspect-ratio:1;object-fit:cover;border-radius:8px;background:#eee}}
.score{{font-size:16px;font-weight:700;color:#e5484d}} .t1{{font-size:11px;color:#999;font-weight:400}}
.cat{{color:#0969da}} .note{{color:#b54708}} .fn{{color:#aaa;word-break:break-all;font-size:10px}}
.orig{{display:inline-block;margin-top:4px;color:#0969da;text-decoration:none}}
</style></head><body>
<h1>{title}</h1><div class="desc">{desc}</div>
<div class="grid">{''.join(cards)}</div></body></html>'''
    open(os.path.join(OUT_DIR, fname), "w", encoding="utf-8").write(html)
    print(f"  生成 {fname}: {len(items)} 张")


print("\n生成审核页:")
page("index.html", "08乐仔扫描 · A 高置信",
     [x for x in results if x["tier"] == "A"],
     f"判定库 v4(507)+08种子({len(seed_keep)})，kNN top5≥{A_TH} 且 det≥{DET_MIN_A}，同照互斥后最高分脸。"
     f"共 {nA} 张脸 / {len(photos_A)} 张照片。如有不是乐仔的请记 face 编号。")
page("review.html", "08乐仔扫描 · B 待人工复核",
     [x for x in results if x["tier"] == "B"],
     f"kNN top5 介于 {B_TH}~{A_TH} 或同照互斥降级。共 {nB} 张，按分数从高到低排，前面的大概率是真乐仔。")

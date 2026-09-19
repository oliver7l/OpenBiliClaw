#!/usr/bin/env python3
"""抽样核对页：A/B 分层抽样，base64 内嵌图片，自包含单文件。"""
import sqlite3
import base64
import random
import os

R = '/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw'
OUT = f'{R}/08_乐仔相册/_face_index/lezai_scan08'
random.seed(42)

con = sqlite3.connect(f'{R}/08_乐仔相册/_face_index/faces08.db')
# face_id, top5, tier, note, photo, category, det_score, face_idx, crop_path
rows = con.execute("""SELECT s.face_id, s.top5, s.tier, s.note, f.photo, f.category,
 f.det_score, f.face_idx, f.crop_path
 FROM lezai_scan08 s JOIN faces f ON f.id=s.face_id""").fetchall()
A = [r for r in rows if r[2] == 'A']
B = [r for r in rows if r[2] == 'B' and '同照互斥' not in (r[3] or '')]
Bm = [r for r in rows if r[2] == 'B' and '同照互斥' in (r[3] or '')]


def stratified(items, bands, per):
    out = []
    for lo, hi in bands:
        seg = [x for x in items if lo <= x[1] < hi]
        random.shuffle(seg)
        out += seg[:per]
    return out


A_samp = stratified(A, [(0.65, 0.68), (0.68, 0.71), (0.71, 0.74), (0.74, 0.78), (0.78, 99)], 6)
B_samp = stratified(B, [(0.45, 0.50), (0.50, 0.55), (0.55, 0.60), (0.60, 0.65)], 8)
random.shuffle(Bm)
Bm_samp = Bm[:4]
print(f'A抽样{len(A_samp)} B抽样{len(B_samp)} 互斥抽样{len(Bm_samp)}')


def b64(p):
    with open(p, 'rb') as f:
        return base64.b64encode(f.read()).decode()


cards = []
n = 0
for tag, items in [('A 高置信', A_samp), ('B 灰色带', B_samp), ('B 同照互斥', Bm_samp)]:
    for r in sorted(items, key=lambda x: -x[1]):
        n += 1
        fid, s5, tr, note, photo, cat, det, fx, cp = r
        border = '#30a46c' if tag.startswith('A') else '#e5484d'
        img = b64(cp)
        cards.append(f'''<div class="card" style="border-top:3px solid {border}">
<img src="data:image/jpeg;base64,{img}">
<div class="id">样本 {n:02d} · {tag}</div>
<div class="sc">{s5:.3f}</div>
<div class="meta">{cat} · 脸#{fx} · det {det:.2f}</div>
<div class="fn">{photo}</div></div>''')

html = f'''<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>08乐仔扫描 · 抽样核对</title><style>
body{{font-family:-apple-system,"PingFang SC",sans-serif;background:#f5f5f7;margin:0;padding:20px;color:#1d1d1f}}
h1{{font-size:20px}} .legend{{font-size:13px;color:#444;margin:8px 0 16px}}
.legend b{{color:#30a46d}} .legend i{{color:#e5484d;font-style:normal}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}}
.card{{background:#fff;border-radius:10px;padding:10px;box-shadow:0 1px 3px rgba(0,0,0,.08);font-size:12px;text-align:center}}
.card img{{width:100%;aspect-ratio:1;object-fit:cover;border-radius:8px;background:#eee}}
.id{{font-weight:700;margin-top:6px}} .sc{{font-size:15px;font-weight:700}}
.meta{{color:#666;font-size:11px}} .fn{{color:#aaa;word-break:break-all;font-size:9px;margin-top:4px}}
h2{{font-size:15px;margin:24px 0 8px;color:#555}}
</style></head><body>
<h1>08乐仔扫描 · 抽样核对（共 {n} 张，图片已内嵌）</h1>
<div class="legend">请翻看，<b>绿框 = A 高置信</b>（应全是乐仔），<i>红框 = B 待复核</i>。
发现不是乐仔的，报「样本 07 不是」即可。</div>
<h2>── A 高置信抽样 {len(A_samp)} 张（按分数分段随机）──</h2>
<div class="grid">{''.join(cards[:len(A_samp)])}</div>
<h2>── B 待复核抽样 {len(B_samp) + len(Bm_samp)} 张（灰色带 + 同照互斥）──</h2>
<div class="grid">{''.join(cards[len(A_samp):])}</div>
</body></html>'''
path = f'{OUT}/sample.html'
open(path, 'w', encoding='utf-8').write(html)
print('sample.html', os.path.getsize(path) // 1024, 'KB')

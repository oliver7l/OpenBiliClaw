#!/usr/bin/env python3
"""生成种子标注页：请用户确认每类「单人脸照片」种子是否本人。

背景：07 聚类真值与 18_人物分组 互相污染（实测'妈妈'种子相似度最高的
一张是乐仔）。自动提纯只能到 0.5~0.6 纯度，唯一切断污染的办法是
人工确认一轮种子。本页每人抽 24 张（最典型8 + 中间8 + 最离群8），
点击切换 ✓/✗，最后一键复制结果发回对话即可。

用法：python gen_labeling_page.py [--per 24]
"""
import argparse
import base64
import io
import json
import sqlite3
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
LIB_DB = ROOT / "19_统一相册库" / "library.db"
P07_DB = ROOT / "07_相册" / "相册&视频备份" / "_photo_index" / "photo_index.db"
OUT = ROOT / "照片人物判定" / "_种子标注页.html"
STATE = ROOT / "照片人物判定" / "_种子标注结果.json"

TARGETS = ["妈妈", "爸爸", "乐仔小时候", "艳艳", "我"]
MIN_DET, MIN_SIDE = 0.60, 12

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass


def l2n(X):
    X = np.asarray(X, dtype=np.float32)
    n = np.linalg.norm(X, axis=1, keepdims=True)
    n[n < 1e-9] = 1
    return X / n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=24)
    args = ap.parse_args()

    db = sqlite3.connect(str(LIB_DB))
    faces = db.execute(
        "SELECT face_id, content_key, lib, det_score, w, h, cluster, emb_mbf, emb_r50 "
        "FROM faces WHERE emb_mbf IS NOT NULL AND emb_r50 IS NOT NULL").fetchall()
    r50 = l2n(np.vstack([np.frombuffer(r[8], np.float32) for r in faces]))
    qcnt = Counter(r[1] for r in faces
                   if r[3] and r[3] >= MIN_DET
                   and (r[4] is None or min(r[4] or 0, r[5] or 0) >= MIN_SIDE))
    db7 = sqlite3.connect(str(P07_DB))
    inv = {n: int(c) for c, n in db7.execute(
        "SELECT cluster, name FROM persons WHERE name IS NOT NULL")}

    data = {}
    for p in TARGETS:
        cl = inv.get(p)
        ck18 = {r[0] for r in db.execute(
            "SELECT DISTINCT content_key FROM files WHERE lib='18' AND rel LIKE ?",
            (p + "/%",))}
        cand = []
        for i, r in enumerate(faces):
            if not (r[3] and r[3] >= MIN_DET):
                continue
            if r[4] is not None and min(r[4] or 0, r[5] or 0) < MIN_SIDE:
                continue
            if qcnt[r[1]] != 1:
                continue
            if (cl is not None and r[6] == cl and r[2] == "07") or r[1] in ck18:
                cand.append(i)
        if len(cand) < 8:
            print(f"跳过 {p}: 候选仅 {len(cand)}")
            continue
        X = r50[cand]
        proto = X.mean(0)
        proto /= np.linalg.norm(proto)
        sim = X @ proto
        order = np.argsort(-sim)
        n8 = min(8, len(cand) // 3)
        pick = list(order[:n8]) + list(order[len(order)//2 - n8//2 : len(order)//2 + n8//2]) + list(order[-n8:])
        items = []
        for j in pick:
            i = cand[int(j)]
            ck = faces[i][1]
            path = db.execute(
                "SELECT path FROM files WHERE content_key=? LIMIT 1", (ck,)).fetchone()
            if not path:
                continue
            items.append({"face_id": int(faces[i][0]), "path": path[0],
                          "sim": round(float(sim[j]), 3)})
        data[p] = items
        print(f"{p}: 抽样 {len(items)} / 候选 {len(cand)}")
    db.close()
    STATE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # HTML
    cards = []
    for p, items in data.items():
        cards.append(f"<h2>{p}</h2><div class='grid' data-person='{p}'>")
        for k, it in enumerate(items):
            try:
                im = Image.open(it["path"]).convert("RGB")
                im.thumbnail((220, 220))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=68)
                b64 = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
            except Exception:
                continue
            src = "18目录" if "18_人物分组" in it["path"] else "07cluster"
            cards.append(
                f"<div class='card' data-id='{it['face_id']}'><img src='{b64}'>"
                f"<div>#{k} sim={it['sim']:.2f} [{src}]</div></div>")
        cards.append("</div>")

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>家人种子标注</title><style>
body{{font-family:-apple-system,'PingFang SC',sans-serif;margin:20px;background:#fafafa}}
h2{{border-left:4px solid #c25b4a;padding-left:10px;margin:30px 0 8px}}
.grid{{display:flex;flex-wrap:wrap;gap:8px}}
.card{{width:150px;background:#fff;border:3px solid #ddd;border-radius:8px;
padding:4px;font-size:11px;color:#666;cursor:pointer;user-select:none;text-align:center}}
.card img{{width:140px;height:140px;object-fit:cover;border-radius:4px;background:#eee}}
.card.yes{{border-color:#2e9e4f}} .card.yes::after{{content:'✓ 本人';color:#2e9e4f;font-weight:700}}
.card.no{{border-color:#c0392b}} .card.no::after{{content:'✗ 不是';color:#c0392b;font-weight:700}}
.hint{{background:#fff8e6;border:1px solid #e8d48a;padding:10px 14px;border-radius:8px;font-size:13px}}
button{{padding:10px 22px;font-size:15px;border-radius:8px;border:0;background:#2e6fd0;color:#fff;cursor:pointer}}
textarea{{width:100%%;height:90px;margin-top:8px}}
</style></head><body>
<h1>家人种子标注（约5分钟）</h1>
<div class="hint">
<b>点一下=✓本人（绿），再点=✗不是（红），再点恢复未标。</b><br>
重点标红：凡是<b>小孩、其他大人、背影、纯风景</b>都标 ✗。拿不准就点两下留空。<br>
标注完成后点下方按钮，把文本框内容复制发给助手。
</div>
{''.join(cards)}
<p><button onclick="exportRes()">生成结果（复制发回对话）</button></p>
<textarea id="out" readonly></textarea>
<script>
document.querySelectorAll('.card').forEach(c=>c.onclick=()=>{{
  c.classList.toggle('yes'); if(c.classList.contains('yes'))c.classList.remove('no'); else c.classList.add('no');
}});
function exportRes(){{
  let out=[];
  document.querySelectorAll('.grid').forEach(g=>{{
    const p=g.dataset.person; let yes=[],no=[];
    g.querySelectorAll('.card').forEach((c,i)=>{{
      if(c.classList.contains('yes'))yes.push(c.dataset.id);
      if(c.classList.contains('no'))no.push(c.dataset.id);
    }});
    out.push(p+'\\n  是: '+(yes.join(',')||'无')+'\\n  否: '+(no.join(',')||'无'));
  }});
  document.getElementById('out').value=out.join('\\n');
  document.getElementById('out').select();
}}
</script></body></html>"""
    OUT.write_text(html, encoding="utf-8")
    print(f"\n标注页: {OUT.relative_to(ROOT)} ({OUT.stat().st_size/1e6:.1f} MB)")
    print(f"结果缓存: {STATE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

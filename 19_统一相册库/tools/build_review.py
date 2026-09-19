#!/usr/bin/env python3
"""生成人物标签抽样审核页（本地 HTML，直链原图）。

按人物 × 来源分组，每组抽 N 张（默认按分数从高到低与随机各半），
鼠标悬停看分数，点击新窗口打开原图。用于肉眼判定标签准不准。

用法:
  python tools/build_review.py                 # 全部来源、每人 24 张
  python tools/build_review.py --source scan   # 只看扫描新增的
  python tools/build_review.py --per 40
输出: 19_统一相册库/_review/标签审核.html
"""
import os
import sqlite3
import argparse
import html
import random
from urllib.parse import quote

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
DB = f"{ROOT}/19_统一相册库/library.db"
OUT_DIR = f"{ROOT}/19_统一相册库/_review"


def file_url(path):
    return "file://" + quote(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=None, help="cluster/scan_A/scan_B/model/scan")
    ap.add_argument("--per", type=int, default=24, help="每人+每来源抽样数")
    ap.add_argument("--name", default="标签审核", help="输出文件名（不含 .html）")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    random.seed(args.seed)

    db = sqlite3.connect(DB)
    where = " AND t.source=?" if args.source else ""
    params = [args.source] if args.source else []
    persons = [r[0] for r in db.execute(
        f"""SELECT t.person, COUNT(*) c FROM photo_person_tags t
            WHERE 1=1{where} GROUP BY t.person ORDER BY c DESC""", params)]
    print("人物:", persons)

    parts = []
    for p in persons:
        q = f"""SELECT f.path, f.lib, t.source, t.score, f.rel
                FROM photo_person_tags t JOIN files f ON f.content_key=t.content_key
                WHERE t.person=?{where} AND f.is_primary=1"""
        rows = db.execute(q, [p] + params).fetchall()
        scored = [r for r in rows if r[3] is not None]
        scored.sort(key=lambda r: -r[3])
        top = scored[:args.per // 2]
        rest = rows[len(top):]
        samp = top + random.sample(rest, min(args.per - len(top), len(rest)))
        cards = []
        for path, lib, src, score, rel in samp:
            if not os.path.exists(path):
                continue
            tip = html.escape(f"{lib} | {src} | {score if score is None else round(score,3)} | {rel}")
            cards.append(
                f'<a class="card" href="{file_url(path)}" target="_blank" title="{tip}">'
                f'<img loading="lazy" src="{file_url(path)}">'
                f'<span class="tag">{html.escape(lib)}'
                f'{"" if score is None else f" {score:.2f}"}</span></a>')
        parts.append(
            f'<details open><summary><h2>{html.escape(p)} '
            f'<em>{len(rows)} 张</em></h2></summary><div class="grid">'
            + "".join(cards) + "</div></details>")

    doc = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>统一相册库 · 人物标签审核</title><style>
body{font-family:-apple-system,"PingFang SC",sans-serif;background:#f6f7f9;color:#1c1e21;margin:0;padding:24px}
h1{font-size:20px;margin:0 0 4px}p.sub{color:#666;margin:0 0 20px;font-size:13px}
details{margin-bottom:22px;background:#fff;border-radius:10px;padding:12px 16px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
summary{cursor:pointer}h2{display:inline;font-size:16px;margin:0}
h2 em{font-style:normal;color:#888;font-weight:400;font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-top:12px}
.card{position:relative;display:block;height:150px;overflow:hidden;border-radius:8px;background:#eee}
.card img{width:100%;height:100%;object-fit:cover;display:block}
.tag{position:absolute;left:6px;bottom:6px;background:rgba(0,0,0,.62);color:#fff;
font-size:11px;padding:2px 6px;border-radius:4px}
</style></head><body>
<h1>人物标签抽样审核</h1>
<p class="sub">每张图为原图直链（点击新窗口打开）；角标为 来源 · 相似度分数。CTRL+F 可搜人名。</p>
""" + "".join(parts) + "</body></html>"

    os.makedirs(OUT_DIR, exist_ok=True)
    out = f"{OUT_DIR}/{args.name}.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)
    print("已生成:", out)


if __name__ == "__main__":
    main()

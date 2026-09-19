#!/usr/bin/env python3
"""统一相册库 检索入口。

按人物（AND=同框 / OR=任一）、来源、时间、类型组合过滤，输出路径清单或本地画廊页。

用法:
  python tools/query.py --persons 乐仔,七月            # 两人同框
  python tools/query.py --persons 乐仔,七月 --mode or  # 任一出现
  python tools/query.py --person 妈妈 --without 我     # 有妈妈且没我
  python tools/query.py --person 乐仔 --lib 09 --from 2026-01 --to 2026-06
  python tools/query.py --source cluster               # 只按高置信来源
  python tools/query.py --no-person                    # 完全没人脸标签的
  python tools/query.py --person 七月 --html           # 生成画廊页并打印路径
  python tools/query.py --person 乐仔 --out /tmp/a.txt # 结果写文件
"""
import os
import sys
import html
import sqlite3
import argparse
from urllib.parse import quote

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
DB = f"{ROOT}/19_统一相册库/library.db"
OUT_DIR = f"{ROOT}/19_统一相册库/_review"

LIB_NAMES = {"07": "夸克手机备份", "08": "夸克乐仔照片", "09": "QQ群相册", "18": "人物分组"}


def build_sql(a):
    w, params = [], []
    if a.persons:
        ps = [p.strip() for p in a.persons.split(",") if p.strip()]
        if a.mode == "and":
            for p in ps:
                sub = "EXISTS (SELECT 1 FROM photo_person_tags t WHERE t.content_key=f.content_key AND t.person=?"
                if a.source:
                    sub += " AND t.source=?"
                    params.extend([p, a.source])
                else:
                    params.append(p)
                w.append(sub + ")")
        else:
            ph = ",".join("?" * len(ps))
            sub = f"""EXISTS (SELECT 1 FROM photo_person_tags t
                      WHERE t.content_key=f.content_key AND t.person IN ({ph})"""
            if a.source:
                sub += " AND t.source=?"
                params.extend(ps + [a.source])
            else:
                params.extend(ps)
            w.append(sub + ")")
    for p in (a.without or []):
        w.append("""NOT EXISTS (SELECT 1 FROM photo_person_tags t
                    WHERE t.content_key=f.content_key AND t.person=?)""")
        params.append(p)
    if a.no_person:
        w.append("""NOT EXISTS (SELECT 1 FROM photo_person_tags t
                    WHERE t.content_key=f.content_key)""")
    if a.lib:
        libs = a.lib.split(",")
        w.append(f"f.lib IN ({','.join('?'*len(libs))})")
        params.extend(libs)
    if a.kind:
        w.append("f.kind=?")
        params.append(a.kind)
    if a.from_:
        w.append("COALESCE(f.ym,'') >= ?")
        params.append(a.from_)
    if a.to:
        w.append("COALESCE(f.ym,'') <= ?")
        params.append(a.to)
    if a.primary:
        w.append("f.is_primary=1")
    where = ("WHERE " + " AND ".join(w)) if w else ""
    sql = f"""SELECT f.path, f.lib, f.rel, f.ym, f.size, f.source FROM files f
              {where} ORDER BY COALESCE(f.ym,'') DESC, f.path"""
    if a.limit:
        sql += f" LIMIT {a.limit}"
    return sql, params


def write_html(rows, path, title):
    cards = []
    for p, lib, rel, ym, size, src in rows:
        if not os.path.exists(p):
            continue
        u = "file://" + quote(p)
        tip = html.escape(f"{LIB_NAMES.get(lib, lib)} | {ym or '?'} | {src} | {rel}")
        cards.append(f'<a class="card" href="{u}" target="_blank" title="{tip}">'
                     f'<img loading="lazy" src="{u}">'
                     f'<span class="tag">{html.escape(LIB_NAMES.get(lib, lib))} {html.escape(ym or "?")}</span></a>')
    doc = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>%s</title><style>
body{font-family:-apple-system,"PingFang SC",sans-serif;background:#f6f7f9;color:#1c1e21;margin:0;padding:20px}
h1{font-size:18px}p.sub{color:#666;font-size:13px;margin:4px 0 16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px}
.card{position:relative;display:block;height:160px;overflow:hidden;border-radius:8px;background:#eee}
.card img{width:100%%;height:100%%;object-fit:cover;display:block}
.tag{position:absolute;left:6px;bottom:6px;background:rgba(0,0,0,.62);color:#fff;font-size:11px;
padding:2px 6px;border-radius:4px}</style></head><body><h1>%s</h1><p class="sub">%d 张 · 点击看原图</p>
<div class="grid">%s</div></body></html>""" % (html.escape(title), html.escape(title), len(cards), "".join(cards))
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persons", "-p")
    ap.add_argument("--person", dest="persons")
    ap.add_argument("--without", "-w", action="append", default=[])
    ap.add_argument("--mode", choices=["and", "or"], default="and")
    ap.add_argument("--lib", help="07/08/09/18，逗号分隔")
    ap.add_argument("--kind")
    ap.add_argument("--from", dest="from_", help="YYYY-MM 起")
    ap.add_argument("--to", help="YYYY-MM 止")
    ap.add_argument("--source", help="cluster/scan_A/scan_B/model/scan")
    ap.add_argument("--no-person", action="store_true")
    ap.add_argument("--primary", action="store_true", help="只看主副本（去重）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--count-only", action="store_true")
    ap.add_argument("--html", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()

    db = sqlite3.connect(DB)
    sql, params = build_sql(a)
    if a.count_only:
        print(db.execute(f"SELECT COUNT(*) FROM files f {sql[sql.find('WHERE'):] if 'WHERE' in sql else ''}",
                         params).fetchone()[0])
        return
    rows = db.execute(sql, params).fetchall()
    print(f"命中 {len(rows)} 个文件", file=sys.stderr)
    lines = [p for p, *_ in rows]
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("已写入", a.out)
    if a.html:
        title = "检索：" + " ".join(filter(None, [a.persons, a.lib, a.from_, a.to]))
        out = write_html(rows, f"{OUT_DIR}/检索结果.html", title)
        print("画廊页:", out)
    if not a.out or a.html:
        for p in lines[:200]:
            print(p)


if __name__ == "__main__":
    main()

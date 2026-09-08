#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重建幻灯片笔记全文索引（FTS5），并提供检索：python3 slides_index.py 查 <关键词>"""
import os, sys, sqlite3
BASE="/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/求职知识库/_系统_知识库引擎/数据/幻灯片笔记.db"
conn=sqlite3.connect(BASE); c=conn.cursor()
c.execute("INSERT INTO slide_fts(slide_fts) VALUES('delete-all')") if False else None
try:
    c.execute("INSERT INTO slide_fts(slide_fts) VALUES('delete-all')")
except Exception: pass
c.execute("DELETE FROM slide_fts") if False else None
try:
    rows=c.execute("SELECT part_no, slide_no, ocr_text FROM slide").fetchall()
    c.execute("DELETE FROM slide_fts")
    c.executemany("INSERT INTO slide_fts(part_no, slide_no, ocr_text) VALUES (?,?,?)", rows)
    conn.commit()
    print("索引重建：%d 页" % len(rows))
except Exception as e:
    print("FTS 不可用：", e)
if len(sys.argv)>2 and sys.argv[1]=="查":
    kw=sys.argv[2]
    try:
        r=c.execute("SELECT part_no, slide_no, snippet(slide_fts, 2, '[', ']', '…', 12) s FROM slide_fts WHERE slide_fts MATCH ? LIMIT 20",(kw,)).fetchall()
    except Exception:
        r=c.execute("SELECT part_no, slide_no, substr(ocr_text,1,120) s FROM slide WHERE ocr_text LIKE ? LIMIT 20",('%'+kw+'%',)).fetchall()
    for x in r: print("[%s p%s] %s" % (x[0], x[1], x[2].replace('\n',' ')[:200]))
    print("命中 %d 条" % len(r))

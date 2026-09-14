#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把幻灯片笔记.db 里已完成的 OCR 结果回填到总库（避免重复 OCR），
并统计尚未 OCR 的图片型幻灯片页数。"""
import os, re, sqlite3, zipfile, datetime
KB="/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/求职知识库"
TOTAL=os.path.join(KB,"_系统_知识库引擎/数据/面试资料总库.db")
SLIDE=os.path.join(KB,"_系统_知识库引擎/数据/幻灯片笔记.db")
now=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

conn=sqlite3.connect(TOTAL); conn.row_factory=sqlite3.Row
s=sqlite3.connect(SLIDE); s.row_factory=sqlite3.Row

# 1) 回填 part01-17
parts={}
for r in s.execute("SELECT part_no, slide_no, ocr_text FROM slide ORDER BY part_no, slide_no"):
    parts.setdefault(r["part_no"],[]).append((r["slide_no"], r["ocr_text"] or ""))
n=0
for p in sorted(parts):
    rows=parts[p]
    txt="\n".join("--- Slide %d ---\n%s"%(no,t) for no,t in rows)
    path=os.path.join(KB,"01_原始资料库/02_我的笔记/01_程序化广告/原稿/程序化广告学习笔记_拆分")
    hits=[f for f in os.listdir(path) if f.startswith(p+"_")]
    if not hits: continue
    fp=os.path.join(path,hits[0])
    conn.execute("""UPDATE doc SET content=?, char_count=?, extract_method='ocr-linked(slides_pipeline)',
                    status='ok', updated_at=? WHERE path=?""",(txt,len(txt),now,fp))
    n+=1
    print("回填 %-8s %3d页 %7d字  %s"%(p,len(rows),len(txt),hits[0][:45]))
conn.commit()
print("回填完成 %d 份"%n)

# 2) 统计未处理大文件的图片页数
print("\n=== 未 OCR 的图片型幻灯片 ===")
for f in ["2026年08月12日-程序化广告-书籍阅读.pptx","2026年08月12日-程序化广告-学习笔记.pptx",
          "2026年08月15日-自我对话剖析.pptx","8月12日-程序化广告-学习笔记.pptx"]:
    fp=os.path.join(KB,"01_原始资料库/01_书籍",f)
    if not os.path.exists(fp): continue
    z=zipfile.ZipFile(fp)
    sl=[x for x in z.namelist() if re.search(r'ppt/slides/slide\d+\.xml$',x)]
    md=len([x for x in z.namelist() if 'ppt/media/' in x])
    mb=os.path.getsize(fp)/1024/1024
    print("  %-46s %6.1fMB  页数=%4d  媒体=%4d"%(f[:46],mb,len(sl),md))

# 3) 刷新未处理表
conn.execute("DELETE FROM unprocessed")
for r in conn.execute("SELECT rel_path,ext,status,extract_method FROM doc WHERE status!='ok'"):
    reason={"need_ocr":"无文本层（扫描件/图片型），需 OCR","empty":"抽取结果为空",
            "failed":"解析失败: %s"%r["extract_method"]}.get(r["status"],r["status"])
    conn.execute("INSERT INTO unprocessed(rel_path,kind,reason,status,updated_at) VALUES(?,?,?,?,?)",
                 (r["rel_path"],r["ext"],reason,r["status"],now))
conn.commit()
print("\n未处理剩余：%d 条"%conn.execute("SELECT COUNT(*) FROM unprocessed").fetchone()[0])

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""处理台账：把三层数据框架的产出与未处理项登记入库"""
import os, sqlite3, datetime
KB="/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/求职知识库"
DB=os.path.join(KB,"_系统_知识库引擎/数据/幻灯片笔记.db")
conn=sqlite3.connect(DB); c=conn.cursor()
c.execute("""CREATE TABLE IF NOT EXISTS processed_notes(
 id INTEGER PRIMARY KEY AUTOINCREMENT, layer TEXT, topic TEXT, file_path TEXT,
 source_parts TEXT, lines INTEGER, chars INTEGER, created_at TEXT)""")
c.execute("""CREATE TABLE IF NOT EXISTS unprocessed(
 id INTEGER PRIMARY KEY AUTOINCREMENT, item TEXT, kind TEXT, reason TEXT, status TEXT, updated_at TEXT)""")
c.execute("DELETE FROM processed_notes")
items=[
 ("原始数据","17份幻灯片OCR原文(435页)","01_原始资料库/08_解码文本/书籍__程序化广告学习笔记_拆分__part*.txt","part01-17",None,157371),
 ("加工数据","RTB竞价与oCPC出价","02_方向知识库/幻灯片笔记_加工/01_RTB竞价与oCPC出价.md","part01,08",400,None),
 ("加工数据","DSP机制与冷启动与归因","02_方向知识库/幻灯片笔记_加工/02_DSP机制与冷启动与归因.md","part10,11,12,13",693,None),
 ("加工数据","指标体系与项目话术","02_方向知识库/幻灯片笔记_加工/03_指标体系与项目话术.md","part02,04,05",721,None),
 ("加工数据","算法与模型(对比学习·大模型·迁移)","02_方向知识库/幻灯片笔记_加工/04_算法与模型（对比学习·大模型·迁移）.md","part03,07,09,16",494,None),
 ("加工数据","行为面与出海与职业规划","02_方向知识库/幻灯片笔记_加工/05_行为面与出海与职业规划.md","part06,14,15,17",408,None),
 ("应用数据","比亚迪二面笔记弹药应用包","03_岗位弹药库/比亚迪-面试准备/03_速成包/比亚迪二面_笔记弹药应用包.md","加工层01-05提炼",None,None),
]
now=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
for layer,topic,path,src,lines,chars in items:
    p=os.path.join(KB,path)
    n=lines
    ch=chars
    if os.path.exists(p) and p.endswith(".md"):
        t=open(p,encoding="utf-8").read(); n=len(t.splitlines()); ch=len(t)
    c.execute("INSERT INTO processed_notes(layer,topic,file_path,source_parts,lines,chars,created_at) VALUES(?,?,?,?,?,?,?)",
              (layer,topic,path,src,n,ch,now))
c.execute("DELETE FROM unprocessed")
un=[
 ("8月12日-程序化广告-学习笔记.pptx(59页)","图片型幻灯片","未OCR（与2026-08-12版重复度高，待确认是否需要）","待处理"),
 ("2026年08月12日-程序化广告-书籍阅读.pptx","图片型幻灯片","未OCR（书籍摘录，优先级低）","待处理"),
 ("2026年08月15日-自我对话剖析.pptx","图片型幻灯片","未OCR（自我剖析，非面试弹药）","待处理"),
 ("解码文本中其余45个<1KB空文件","历史抽取产物","纯文本抽取拿不到内容（图片型/扫描件），需OCR重做","待处理"),
 ("工作资料_腾讯/247个pptx","腾讯内部材料","未纳入本轮，如需可复用同一流水线","未处理"),
]
for item,kind,reason,status in un:
    c.execute("INSERT INTO unprocessed(item,kind,reason,status,updated_at) VALUES(?,?,?,?,?)",(item,kind,reason,status,now))
conn.commit()
print("已登记：处理项 %d 条，未处理项 %d 条" % (
 c.execute("SELECT COUNT(*) FROM processed_notes").fetchone()[0],
 c.execute("SELECT COUNT(*) FROM unprocessed").fetchone()[0]))
for r in c.execute("SELECT layer,topic,lines,chars FROM processed_notes"): print("  ",r[0],r[1],r[2],r[3])

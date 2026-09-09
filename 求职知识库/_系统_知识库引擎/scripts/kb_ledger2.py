#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三层台账登记：把加工层/应用层的产出登记进总库 processed_notes 表（可重复执行，按路径去重）"""
import os, sqlite3, datetime, hashlib
KB="/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/求职知识库"
DB=os.path.join(KB,"_系统_知识库引擎/数据/面试资料总库.db")
now=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
conn=sqlite3.connect(DB,timeout=120)
conn.execute("""CREATE TABLE IF NOT EXISTS processed_notes(
 id INTEGER PRIMARY KEY AUTOINCREMENT, layer TEXT, topic TEXT, file_path TEXT,
 source_parts TEXT, lines INTEGER, chars INTEGER, created_at TEXT, UNIQUE(file_path))""")

def add(layer,topic,rel,src):
    p=os.path.join(KB,rel)
    if not os.path.exists(p): return None
    t=open(p,encoding="utf-8",errors="ignore").read()
    conn.execute("""INSERT INTO processed_notes(layer,topic,file_path,source_parts,lines,chars,created_at)
                    VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(file_path) DO UPDATE SET lines=excluded.lines,chars=excluded.chars,
                    source_parts=excluded.source_parts,updated_at=excluded.created_at""",
                 (layer,topic,rel,src,len(t.splitlines()),len(t),now))
    return len(t.splitlines())

# 加工层
proc=[("幻灯片笔记加工·索引","02_方向知识库/幻灯片笔记_加工/00_索引.md","part01-17"),
 ("RTB竞价与oCPC出价","02_方向知识库/幻灯片笔记_加工/01_RTB竞价与oCPC出价.md","part01,08"),
 ("DSP机制与冷启动与归因","02_方向知识库/幻灯片笔记_加工/02_DSP机制与冷启动与归因.md","part10-13"),
 ("指标体系与项目话术","02_方向知识库/幻灯片笔记_加工/03_指标体系与项目话术.md","part02,04,05"),
 ("算法与模型(对比学习·大模型·迁移)","02_方向知识库/幻灯片笔记_加工/04_算法与模型（对比学习·大模型·迁移）.md","part03,07,09,16"),
 ("行为面与出海与职业规划","02_方向知识库/幻灯片笔记_加工/05_行为面与出海与职业规划.md","part06,14,15,17")]
# 加工层·历史积累（按目录整体登记代表文档）
hist=[]
for d,label in [("推荐系统","推荐系统"),("数据科学","数据科学"),("产品化项目弹药","产品化项目弹药"),
                ("广告算法","广告算法"),("面试方法论","面试方法论"),
                ("机器学习与LLM基础（参考题库）","ML与LLM题库")]:
    base=os.path.join(KB,"02_方向知识库",d)
    if not os.path.isdir(base): continue
    fs=[f for f in os.listdir(base) if f.endswith(".md")]
    if not fs: continue
    tot=sum(len(open(os.path.join(base,f),encoding="utf-8",errors="ignore").read().splitlines()) for f in fs)
    hist.append((label,"02_方向知识库/%s/（%d篇）"%(d,len(fs)),"历史积累",tot))

# 应用层
app=[("比亚迪·二面反问与情报清单","03_岗位弹药库/比亚迪-面试准备/03_速成包/比亚迪二面_反问与情报清单.md","加工层03,05+一面复盘"),
 ("比亚迪·模型速复习卡","03_岗位弹药库/比亚迪-面试准备/03_速成包/比亚迪二面_模型速复习卡.html","简历全部模型"),
 ("比亚迪·对比学习与迁移复习卡","03_岗位弹药库/比亚迪-面试准备/03_速成包/比亚迪二面_对比学习与迁移学习复习卡.html","part03,07,09,16"),
 ("比亚迪·笔记弹药应用包","03_岗位弹药库/比亚迪-面试准备/03_速成包/比亚迪二面_笔记弹药应用包.md","加工层01-05"),
 ("比亚迪·腾讯答辩深挖弹药卡","03_岗位弹药库/比亚迪-面试准备/03_速成包/比亚迪二面_腾讯答辩深挖弹药卡.html","腾讯10级答辩PPT×4")]

n=0
for t,rel,s in proc:
    if add("加工数据",t,rel,s): n+=1; print("  [加工] %-42s %s" % (t[:42],rel.split('/')[-1]))
for label,rel,s,tot in hist:
    conn.execute("""INSERT INTO processed_notes(layer,topic,file_path,source_parts,lines,chars,created_at)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(file_path) DO UPDATE SET lines=excluded.lines""",
                 ("加工数据",label,rel,s,tot,0,now)); n+=1
    print("  [加工] %-42s %d行" % (label[:42],tot))
for t,rel,s in app:
    if add("应用数据",t,rel,s): n+=1; print("  [应用] %-42s %s" % (t[:42],rel.split('/')[-1]))
conn.commit()
print("\n登记 %d 条，累计 %d 条" % (n, conn.execute("SELECT COUNT(*) FROM processed_notes").fetchone()[0]))

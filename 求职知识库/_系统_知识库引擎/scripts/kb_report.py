#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成资料入库台账报告（Markdown）+ 登记到数据库 processed_notes 表。

产出：/求职知识库/_系统_知识库引擎/资料入库总览.md
用法：python3 kb_report.py
"""
import os
import sqlite3
from collections import defaultdict
from datetime import datetime

KB = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/求职知识库"
DB = os.path.join(KB, "_系统_知识库引擎/数据/面试资料总库.db")
OUT = os.path.join(KB, "_系统_知识库引擎/资料入库总览.md")
now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

conn = sqlite3.connect(DB, timeout=60)
conn.row_factory = sqlite3.Row
c = conn.cursor()

c.execute("""CREATE TABLE IF NOT EXISTS processed_notes(
 id INTEGER PRIMARY KEY AUTOINCREMENT, layer TEXT, topic TEXT, file_path TEXT,
 source_parts TEXT, lines INTEGER, chars INTEGER, created_at TEXT)""")

lines = []
w = lines.append

total = c.execute("SELECT COUNT(*) n, SUM(char_count) s FROM doc").fetchone()
ok = c.execute("SELECT COUNT(*) n, SUM(char_count) s FROM doc WHERE status='ok'").fetchone()
w("# 求职知识库 · 资料入库总览")
w("")
w("> 更新时间：%s　|　数据库：`_系统_知识库引擎/数据/面试资料总库.db`　|　"
  "入库脚本：`_系统_知识库引擎/scripts/kb_*.py`" % now)
w("")
w("## 一、总体")
w("")
w("| 指标 | 数值 |")
w("|---|---|")
w("| 已登记文档 | %d 篇 |" % total["n"])
w("| 成功抽取正文 | %d 篇（%.1f%%） |" % (ok["n"], 100.0 * ok["n"] / max(total["n"], 1)))
w("| 正文总字数 | %s 字 |" % format(ok["s"] or 0, ","))
w("| 数据库体积 | %.0f MB |" % (os.path.getsize(DB) / 1048576))
w("")

w("## 二、按分类")
w("")
w("| 分类 | 总数 | 成功 | 失败/待OCR | 字数 | 说明 |")
w("|---|---:|---:|---:|---:|---|")
DESC = {
    "简历": "7 份历史简历 + 各投递版本",
    "岗位弹药": "8 个岗位的备战资料、复盘、速记卡",
    "方向知识库": "推荐/数据科学/广告/SQL/面试方法论 + 幻灯片加工笔记",
    "幻灯片笔记": "436 页幻灯片 OCR 原文（part01-17）+ 解码文本",
    "腾讯文档": "腾讯文档导出的 md 资料",
    "公开情报": "BOSS 岗位数据 + 公开情报搜集",
    "工作资料": "腾讯看点/微视内部资料、答辩材料、技术分享",
    "书籍": "程序化广告学习笔记原件 + 拆分版",
    "早期材料": "早期找工作的项目材料",
    "上传截图": "用户上传截图",
    "系统引擎": "知识库引擎自身文档",
    "其他": "未归类",
}
for r in c.execute("""SELECT category, COUNT(*) n,
                      SUM(CASE WHEN status IN ('ok','duplicate') THEN 1 ELSE 0 END) ok,
                      SUM(CASE WHEN status IN ('ok','duplicate') THEN char_count ELSE 0 END) ch
                      FROM doc GROUP BY category ORDER BY ch DESC"""):
    pend = r["n"] - r["ok"]
    w("| **%s** | %d | %d | %d | %s | %s |"
      % (r["category"], r["n"], r["ok"], pend, format(r["ch"] or 0, ","),
         DESC.get(r["category"], "")))
w("")

w("## 三、按状态")
w("")
w("| 状态 | 篇数 | 含义 |")
w("|---|---:|---|")
MEANING = {
    "ok": "正文已入库，可全文检索",
    "duplicate": "内容已被另一版本覆盖（如 436 页母版 → part01-17）",
    "data": "数据文件（训练样本 CSV 等），只留元信息",
    "empty": "源文件本身无内容（空壳/占位）",
    "need_ocr": "扫描件或图片型，无文本层，需 OCR（队列中）",
    "failed": "解析失败（多为加密或损坏文件）",
    "skipped": "Office 临时锁文件，无需处理",
}
for r in c.execute("SELECT status, COUNT(*) n FROM doc GROUP BY status ORDER BY n DESC"):
    w("| `%s` | %d | %s |" % (r["status"], r["n"], MEANING.get(r["status"], "")))
w("")

w("## 四、提取方式")
w("")
w("| 方式 | 篇数 | 说明 |")
w("|---|---:|---|")
for r in c.execute("""SELECT extract_method, COUNT(*) n FROM doc
                      WHERE status IN ('ok','duplicate') GROUP BY extract_method ORDER BY n DESC LIMIT 15"""):
    w("| `%s` | %d | |" % (r["extract_method"], r["n"]))
w("")

w("## 五、未处理 / 待处理清单")
w("")
rows = c.execute("SELECT rel_path, ext, status, extract_method FROM doc "
                 "WHERE status NOT IN ('ok','duplicate') ORDER BY status, rel_path").fetchall()
if rows:
    w("共 %d 项：" % len(rows))
    w("")
    by = defaultdict(list)
    for r in rows:
        by[r["status"]].append(r)
    for st in ["need_ocr", "failed", "empty", "data", "skipped"]:
        if st not in by:
            continue
        w("### %s（%d 项）" % (MEANING.get(st, st), len(by[st])))
        w("")
        for r in by[st][:60]:
            w("- `%s`　<small>%s</small>" % (r["rel_path"], r["extract_method"]))
        if len(by[st]) > 60:
            w("- ……（其余 %d 项见数据库 `unprocessed` 表）" % (len(by[st]) - 60))
        w("")
else:
    w("全部处理完成 ✅")
    w("")

w("## 六、检索方法")
w("")
w("```bash")
w("cd 求职知识库/_系统_知识库引擎/scripts")
w("P=/Users/imac/.workbuddy/binaries/python/envs/default/bin/python")
w("")
w("$P kb_ingest.py 查 对比学习            # 全文检索（≥3字走 FTS5，否则 LIKE）")
w("$P kb_ingest.py 查 汤普森采样 --分类 岗位弹药   # 限定分类")
w("$P kb_ingest.py 统计                   # 入库总览")
w("$P kb_ingest.py 未处理                 # 未处理清单")
w("$P kb_ingest.py 运行                   # 增量入库（断点续跑）")
w("```")
w("")
w("三层数据框架：")
w("")
w("| 层 | 位置 | 说明 |")
w("|---|---|---|")
w("| 原始数据 | `01_原始资料库/` + 本库 `doc.content` | 一字未改的抽取原文 |")
w("| 加工数据 | `02_方向知识库/` | 按主题重组的笔记（含幻灯片笔记_加工 5 份） |")
w("| 应用数据 | `03_岗位弹药库/` | 可直接背诵的面试弹药 |")
w("")

# 登记到 processed_notes
c.execute("DELETE FROM processed_notes WHERE layer='入库台账'")
for r in c.execute("""SELECT category, COUNT(*) n, SUM(char_count) ch FROM doc
                      WHERE status='ok' GROUP BY category"""):
    c.execute("INSERT INTO processed_notes(layer,topic,file_path,source_parts,lines,chars,created_at)"
              " VALUES(?,?,?,?,?,?,?)",
              ("入库台账", "%s（%d篇）" % (r["category"], r["n"]), "面试资料总库.db",
               "doc表", r["n"], r["ch"], now))
conn.commit()

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("报告已生成：%s（%d 行）" % (OUT, len(lines)))
print("成功 %d 篇 / 总 %d 篇，%s 字" % (ok["n"], total["n"], format(ok["s"] or 0, ",")))

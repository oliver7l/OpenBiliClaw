#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""老版 .ppt (OLE2/二进制) 文本提取：无 LibreOffice 时的兜底。
思路：读 PowerPoint Document 流，按 UTF-16LE 抽取连续可打印中文/ASCII 串。
用法: python3 kb_ppt_ole.py
"""
import os, re, sqlite3, datetime, olefile

KB = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/求职知识库"
DB = os.path.join(KB, "_系统_知识库引擎/数据/面试资料总库.db")
OK = re.compile(r"[\u4e00-\u9fff\u3000-\u303fA-Za-z0-9%s]" % re.escape(" .,:;%+-=()[]/\\\"'|&_<>?#*！，。：；、？（）【】—～")) 
CJK = re.compile(r"[\u4e00-\u9fff]")

def extract(path):
    if not olefile.isOleFile(path):
        return "", "not-ole"
    ole = olefile.OleFileIO(path)
    buf = b""
    for name in ["PowerPoint Document", "PP97_DUALSTORAGE", "Current User"]:
        try:
            if ole.exists(name):
                buf += ole.openstream(name).read()
        except Exception:
            pass
    ole.close()
    if not buf:
        return "", "ole-empty"
    # UTF-16LE 抽取
    out = []
    try:
        s = buf.decode("utf-16-le", "ignore")
    except Exception:
        s = ""
    for m in re.finditer(r"[\u4e00-\u9fffA-Za-z0-9][\u4e00-\u9fff\u3000-\u303fA-Za-z0-9 .,:;%+\-=/()]{3,}", s):
        t = m.group().strip()
        if CJK.search(t) or (len(t) >= 8 and " " in t):
            out.append(t)
    # 去重保序
    seen = set(); uniq = []
    for t in out:
        if t in seen: continue
        seen.add(t); uniq.append(t)
    txt = "\n".join(uniq)
    return txt, "ole-ppt-text" if txt else "ole-nodata"

conn = sqlite3.connect(DB, timeout=60); conn.row_factory = sqlite3.Row
now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
n = 0
for r in conn.execute("SELECT id, path, rel_path FROM doc WHERE status='failed'"):
    p = r["path"]
    if not os.path.exists(p): continue
    with open(p, "rb") as f:
        if f.read(4) != b"\xd0\xcf\x11\xe0":
            continue
    txt, m = extract(p)
    txt = re.sub(r"[\ud800-\udfff]", "", txt).strip()
    if len(txt) >= 100:
        conn.execute("UPDATE doc SET content=?, char_count=?, extract_method=?, status='ok', updated_at=? WHERE id=?",
                     (txt, len(txt), m, now, r["id"]))
        print("[提取] %7d字 %-14s %s" % (len(txt), m, r["rel_path"][-52:])); n += 1
    else:
        print("[无内容] %-14s %s" % (m, r["rel_path"][-52:]))
conn.commit()
print("完成 %d 个" % n)

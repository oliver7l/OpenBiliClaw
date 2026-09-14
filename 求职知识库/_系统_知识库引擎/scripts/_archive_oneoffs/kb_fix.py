#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复解析失败项：
1. Office 临时锁文件 ~$*  → skipped
2. 老 .ppt（OLE2）        → textutil
3. docx 缺 content-types  → 直读 word/document.xml
4. pdf DependencyError    → 重装依赖后重试
5. 436 页母版             → 标记 duplicate，回填解码文本并指向 part01-17
用法：python3 kb_fix.py
"""
import os
import re
import sqlite3
import subprocess
import zipfile
from datetime import datetime

KB = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/求职知识库"
DB = os.path.join(KB, "_系统_知识库引擎/数据/面试资料总库.db")
now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
conn = sqlite3.connect(DB, timeout=60)
conn.row_factory = sqlite3.Row

TEXTUTIL = "/usr/bin/textutil"


def txt_docx(path):
    try:
        z = zipfile.ZipFile(path)
        x = z.read("word/document.xml").decode("utf-8", "ignore")
    except Exception:
        try:
            z = zipfile.ZipFile(path)
            name = [n for n in z.namelist() if n.endswith("document.xml")][0]
            x = z.read(name).decode("utf-8", "ignore")
        except Exception as e:
            return "", "fail:%s" % type(e).__name__
    x = re.sub(r"</w:p>", "\n", x)
    x = re.sub(r"<[^>]+>", "", x)
    return re.sub(r"\n{3,}", "\n\n", x).strip(), "docx-xml"


def txt_textutil(path):
    try:
        r = subprocess.run([TEXTUTIL, "-convert", "txt", "-stdout", path],
                           capture_output=True, timeout=90)
        return r.stdout.decode("utf-8", "ignore").strip(), "textutil"
    except Exception as e:
        return "", "fail:%s" % type(e).__name__


def txt_pdf(path):
    try:
        from pypdf import PdfReader
        r = PdfReader(path)
        t = "\n".join((p.extract_text() or "") for p in r.pages)
        return t.strip(), "pypdf"
    except Exception as e:
        return "", "fail:%s" % type(e).__name__


fixed = {"skipped": 0, "ok": 0, "duplicate": 0, "still": 0}
for r in conn.execute("SELECT id, path, rel_path, ext FROM doc WHERE status='failed'").fetchall():
    base = os.path.basename(r["path"])
    if base.startswith("~$") or base.startswith(".~"):
        conn.execute("UPDATE doc SET status='skipped', extract_method='office临时锁文件', "
                     "content='', char_count=0, updated_at=? WHERE id=?", (now, r["id"]))
        fixed["skipped"] += 1
        print("[锁文件] %s" % r["rel_path"][-60:])
        continue
    txt, method = "", "fail"
    if r["ext"] in ("ppt", "doc"):
        txt, method = txt_textutil(r["path"])
    elif r["ext"] == "docx":
        txt, method = txt_docx(r["path"])
    elif r["ext"] == "pdf":
        txt, method = txt_pdf(r["path"])
    txt = re.sub(r"[\ud800-\udfff]", "", txt)
    if len(txt) >= 30:
        conn.execute("UPDATE doc SET content=?, char_count=?, extract_method=?, status='ok', "
                     "updated_at=? WHERE id=?", (txt, len(txt), method, now, r["id"]))
        fixed["ok"] += 1
        print("[已修复] %-10s %6d字  %s" % (method, len(txt), r["rel_path"][-58:]))
    else:
        fixed["still"] += 1
        conn.execute("UPDATE doc SET extract_method=? WHERE id=?", (method, r["id"]))
        print("[仍失败] %-10s %s" % (method[:10], r["rel_path"][-58:]))

# 436 页母版：内容已被 part01-17 覆盖，标记 duplicate
big = os.path.join(KB, "01_原始资料库/01_书籍/2026年08月12日-程序化广告-学习笔记.pptx")
dec = os.path.join(KB, "01_原始资料库/08_解码文本/书籍__2026年08月12日-程序化广告-学习笔记.pptx.txt")
if os.path.exists(big):
    head = ""
    if os.path.exists(dec):
        head = open(dec, encoding="utf-8", errors="ignore").read()
    note = ("[母版说明] 本文件 436 页，已拆分为 part01~part17 并全部完成 OCR（435 页 / 157,371 字）。\n"
            "完整正文见同目录 程序化广告学习笔记_拆分/part01~part17（总库中可检索）。\n\n"
            "以下为本文件可直接抽取的文本层：\n\n")
    content = note + head
    conn.execute("UPDATE doc SET content=?, char_count=?, status='duplicate', "
                 "extract_method='covered-by-part01-17', updated_at=? WHERE path=?",
                 (content, len(content), now, big))
    fixed["duplicate"] += 1
    print("[母版] 已标记 duplicate，指向 part01-17")
conn.commit()
print("\n修复结果：跳过锁文件 %d，成功修复 %d，母版 %d，仍失败 %d"
      % (fixed["skipped"], fixed["ok"], fixed["duplicate"], fixed["still"]))

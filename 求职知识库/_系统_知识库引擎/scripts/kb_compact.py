#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""压缩清洗：把超大内容做规范化 + 截断，数据类文件只留元信息，最后 VACUUM 回收空间。

规则：
- csv/txt 且 >3MB：判定为训练数据文件，内容清空，status='data'
- 其他文档：清洗空白行与重复垃圾行，超过 MAX_CHARS 截断（头 80% + 尾 20%）
用法：python3 kb_compact.py [--上限 100000] [--执行]
"""
import argparse
import os
import re
import sqlite3
import time
from datetime import datetime

KB = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/求职知识库"
DB = os.path.join(KB, "_系统_知识库引擎/数据/面试资料总库.db")
now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def clean(txt):
    lines = [l.rstrip() for l in txt.split("\n")]
    out, blank = [], 0
    for l in lines:
        s = l.strip()
        if not s:
            blank += 1
            if blank <= 1:
                out.append("")
            continue
        blank = 0
        # 去掉由单一字符重复构成的垃圾行
        if len(s) > 30 and len(set(s)) <= 2:
            continue
        out.append(l)
    return "\n".join(out).strip()


def truncate(txt, max_chars):
    if len(txt) <= max_chars:
        return txt
    head = int(max_chars * 0.8)
    tail = max_chars - head
    return "%s\n\n……[原文共 %d 字，入库截断]……\n\n%s" % (txt[:head], len(txt), txt[-tail:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--上限", type=int, default=100000)
    ap.add_argument("--执行", action="store_true")
    a = ap.parse_args()
    conn = sqlite3.connect(DB, timeout=60)
    conn.row_factory = sqlite3.Row
    before = os.path.getsize(DB) / 1048576
    rows = conn.execute("SELECT id, rel_path, ext, content, char_count FROM doc "
                        "WHERE char_count > ? OR (ext IN ('csv','txt') AND size > 3145728) "
                        "ORDER BY char_count DESC", (a.上限,)).fetchall()
    print("待处理 %d 篇（DB 当前 %.0f MB）" % (len(rows), before))
    total_saved = 0
    n_data = n_trunc = 0
    for r in rows:
        rel, ext, content, cc = r["rel_path"], r["ext"], r["content"] or "", r["char_count"]
        if ext in ("csv", "txt") and cc > 3_000_000:
            if a.执行:
                conn.execute("UPDATE doc SET content=?, char_count=0, status='data', "
                             "extract_method='data-file', updated_at=? WHERE id=?",
                             ("[数据文件，未入库正文：原始 %d 字]" % cc, now, r["id"]))
            total_saved += cc
            n_data += 1
            print("  [数据]  %-58s %10d 字" % (rel[-58:], cc))
            continue
        new = truncate(clean(content), a.上限)
        if len(new) < cc:
            total_saved += cc - len(new)
            n_trunc += 1
            if a.执行:
                conn.execute("UPDATE doc SET content=?, char_count=?, extract_method="
                             "extract_method||'+cleaned', updated_at=? WHERE id=?",
                             (new, len(new), now, r["id"]))
            print("  [截断]  %-58s %10d → %d 字" % (rel[-58:], cc, len(new)))
    if a.执行:
        conn.commit()
        print("\n清理 + VACUUM 中（可能需要几分钟）...")
        t0 = time.time()
        conn.execute("DELETE FROM doc_fts")
        conn.executemany("INSERT INTO doc_fts(rel_path,title,content) VALUES(?,?,?)",
                         conn.execute("SELECT rel_path,title,content FROM doc "
                                      "WHERE status='ok' AND content!=''").fetchall())
        conn.commit()
        conn.isolation_level = None
        conn.execute("VACUUM")
        print("VACUUM 用时 %.0fs" % (time.time() - t0))
    after = os.path.getsize(DB) / 1048576
    print("\n处理：数据文件 %d 个，截断 %d 篇，预计回收 %.0f 万字" % (n_data, n_trunc, total_saved / 10000))
    print("DB：%.0f MB → %.0f MB" % (before, after))


if __name__ == "__main__":
    main()

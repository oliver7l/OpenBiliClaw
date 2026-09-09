#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_ingest_new.py —— 定向入库「磁盘上存在但数据库里没有」的文件。

背景：kb_ingest.py 运行 默认按 rel_path 排序扫全库，配合 --限制 时
总是先扫到已入库的文件，导致真正未入库的文件轮不上。本脚本先算出
差集，再逐个入库。

用法：
    python3 kb_ingest_new.py 预览        # 只列出未入库文件，不写库
    python3 kb_ingest_new.py 执行 [N]    # 入库（N 为可选上限）
"""
import os
import sys
import time
import sqlite3
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kb_ingest as K  # 复用 extract / classify / guess_title / init_db

KB = K.KB
DB = K.DB

# 只认这些内容型扩展名（json/csv/xml 等数据文件不算“资料”）
DOC_EXT = {"md", "txt", "doc", "docx", "pdf", "pptx", "ppt", "xls", "xlsx", "html", "htm", "rtf"}

SKIP_DIR = {".Trashes", ".git", "node_modules", "__pycache__", ".ipynb_checkpoints",
            ".DS_Store", "_系统_知识库引擎", ".workbuddy", "数据"}


def find_new_files():
    conn = sqlite3.connect(DB, timeout=180)
    known = set(r[0] for r in conn.execute("SELECT path FROM doc"))
    conn.close()
    out = []
    for root, dirs, files in os.walk(KB):
        dirs[:] = [d for d in dirs if d not in SKIP_DIR and not d.startswith(".")]
        for f in files:
            if f.startswith(".") or f.startswith("~$"):
                continue
            ext = os.path.splitext(f)[1].lower().lstrip(".")
            if ext not in DOC_EXT:
                continue
            p = os.path.join(root, f)
            if p in known:
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            out.append((p, os.path.relpath(p, KB), ext, st.st_size, st.st_mtime))
    return sorted(out, key=lambda x: x[1])


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "预览"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    files = find_new_files()
    if limit:
        files = files[:limit]
    print("未入库文件：%d 个" % len(files))
    if cmd == "预览":
        for p, rel, ext, size, _ in files[:40]:
            print("  %-10s %8.1fKB  %s" % (ext, size / 1024, rel))
        if len(files) > 40:
            print("  … 其余 %d 个" % (len(files) - 40))
        return

    conn = K.init_db()
    cur = conn.cursor()
    stat = dict(ok=0, empty=0, need_ocr=0, failed=0)
    t0 = time.time()
    for i, (p, rel, ext, size, mtime) in enumerate(files, 1):
        try:
            txt, method = K.extract(p, ext)
        except Exception as e:
            txt, method = "", "fail:%s" % type(e).__name__
        n = len(txt)
        if method.startswith("fail") or method.startswith("no-") or method == "unsupported":
            status = "failed"; stat["failed"] += 1
        elif method == "need_ocr":
            status = "need_ocr"; stat["need_ocr"] += 1
        elif n < 30:
            status = "empty"; stat["empty"] += 1
        else:
            status = "ok"; stat["ok"] += 1
        title = K.guess_title(rel, txt)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("""INSERT INTO doc(path,rel_path,category,ext,size,mtime,sha,title,content,
                       char_count,extract_method,status,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(path) DO UPDATE SET
                       rel_path=excluded.rel_path, category=excluded.category, ext=excluded.ext,
                       size=excluded.size, mtime=excluded.mtime, title=excluded.title,
                       content=excluded.content, char_count=excluded.char_count,
                       extract_method=excluded.extract_method, status=excluded.status,
                       updated_at=excluded.updated_at""",
                    (p, rel, K.classify(rel), ext, size, mtime,
                     __import__("hashlib").md5(p.encode()).hexdigest()[:12],
                     title, txt, n, method, status, now))
        if i % 50 == 0:
            conn.commit()
            print("  … %d/%d" % (i, len(files)))
    conn.commit()
    print("\n入库完成：ok %d / 空 %d / 需OCR %d / 失败 %d，用时 %.1fs"
          % (stat["ok"], stat["empty"], stat["need_ocr"], stat["failed"], time.time() - t0))
    tot = conn.execute("SELECT COUNT(*) FROM doc WHERE status='ok'").fetchone()[0]
    w = conn.execute("SELECT SUM(char_count) FROM doc WHERE status='ok'").fetchone()[0] or 0
    print("库内现有：%d 篇 / %.0f 万字" % (tot, w / 10000))


if __name__ == "__main__":
    main()

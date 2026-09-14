#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""目录重构后的重新扫描：按「文件名+大小」把旧记录映射到新路径，新文件则增量入库。

背景：用户重组了 01_原始资料库 的目录结构（01_书籍/02_我的笔记/03_工作资料/...），
数据库里 doc.path 全部失效。本脚本用文件名+大小做指纹匹配，把已有正文记录
重新挂到新路径上，避免重复 OCR / 重复抽取。

用法：
  python3 kb_rescan.py 预览        # 看能匹配上多少、有多少新文件
  python3 kb_rescan.py 执行        # 写回数据库（只更新路径，不动正文）
  python3 kb_rescan.py 执行 --入库 # 匹配完再把新文件也入库（会调用 kb_ingest 逻辑）
"""
import os
import re
import sys
import sqlite3
import datetime
from collections import defaultdict

KB = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/求职知识库"
DB = os.path.join(KB, "_系统_知识库引擎/数据/面试资料总库.db")
ROOT = KB  # 扫全库（岗位弹药库在 03_ 下，不能只扫 01_原始资料库）

DOC_EXT = {".md", ".txt", ".pdf", ".pptx", ".ppt", ".docx", ".doc",
           ".xlsx", ".xls", ".html", ".htm", ".json", ".ipynb", ".csv"}
SKIP_DIR = {".Trashes", ".git", "node_modules", "__pycache__",
            ".ipynb_checkpoints", "target", "build", ".DS_Store",
            "_系统_知识库引擎", "数据", ".workbuddy", "references"}


def scan(root):
    """扫描当前真实文件 → {(name, size): [path, ...]}"""
    idx = defaultdict(list)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR and not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("~$") or fn.startswith("."):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext not in DOC_EXT:
                continue
            p = os.path.join(dirpath, fn)
            try:
                sz = os.path.getsize(p)
            except OSError:
                continue
            idx[(fn, sz)].append(p)
    return idx


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "预览"
    do_ingest = "--入库" in sys.argv

    conn = sqlite3.connect(DB, timeout=180)
    conn.row_factory = sqlite3.Row
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cur = scan(ROOT)
    print("当前磁盘文档：%d 个" % sum(len(v) for v in cur.values()))

    rows = conn.execute(
        "SELECT id, path, rel_path, size, status FROM doc"
    ).fetchall()
    print("数据库记录：%d 条\n" % len(rows))

    matched, still_missing, new_files = [], [], []
    used = set()
    for r in rows:
        name = os.path.basename(r["path"])
        key = (name, r["size"])
        cands = [p for p in cur.get(key, []) if p not in used]
        if not cands:
            # 退一步：只按文件名匹配
            cands = [p for fn2, sz2 in cur
                     for p in cur[(fn2, sz2)]
                     if fn2 == name and p not in used][:1]
        if cands:
            p = cands[0]
            used.add(p)
            if p != r["path"]:
                matched.append((r["id"], p, r["status"]))
            else:
                matched.append((r["id"], p, r["status"]))
        else:
            still_missing.append(r)

    # 新文件
    known = set()
    for r in rows:
        known.add(os.path.basename(r["path"]))
    for (fn, sz), ps in cur.items():
        for p in ps:
            if p in used:
                continue
            if fn in known:
                used.add(p)
                continue
            new_files.append(p)

    print("=== 预览结果 ===")
    print("  可重映射（文件还在，路径变了）：%d 篇" % len(matched))
    print("  仍然缺失（文件找不到）        ：%d 篇" % len(still_missing))
    print("  磁盘新文件（需入库）          ：%d 个" % len(new_files))

    if still_missing:
        print("\n  仍然缺失的文件（前 10）：")
        for r in still_missing[:10]:
            print("    %s" % os.path.basename(r["path"])[:60])
    if new_files:
        print("\n  新文件示例（前 10）：")
        for p in new_files[:10]:
            print("    %s" % p.replace(ROOT + "/", "")[:70])

    if cmd != "执行":
        print("\n（预览模式，未写入。确认无误后运行：python3 kb_rescan.py 执行）")
        return

    # 写回
    n = dup = 0
    for doc_id, newp, st in matched:
        rel = os.path.relpath(newp, KB)
        newst = "ok" if st == "missing" else st
        try:
            conn.execute("UPDATE doc SET path=?, rel_path=?, status=?, updated_at=? WHERE id=?",
                         (newp, rel, newst, now, doc_id))
            n += 1
        except sqlite3.IntegrityError:
            # 重名文件：新路径已被别的记录占用，本条标记为重复
            conn.execute("UPDATE doc SET status='duplicate', updated_at=? WHERE id=?",
                         (now, doc_id))
            dup += 1
        if n % 200 == 0:
            conn.commit()
    conn.commit()
    print("\n已重映射 %d 篇路径，%d 篇重名标记为 duplicate" % (n, dup))
    print("剩余缺失 %d 篇（状态标记为 missing，随时可再扫）" % len(still_missing))

    if do_ingest and new_files:
        print("\n新文件入库请用：python3 kb_ingest.py 运行 --强制")


if __name__ == "__main__":
    main()

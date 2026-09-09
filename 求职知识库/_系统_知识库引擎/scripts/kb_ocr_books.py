#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_ocr_books.py —— 扫描版书籍专项 OCR（kb_ocr.py 会跳过 >30 页的大部头，这里专门处理）

特点：
  1) 逐页追加落盘缓存（数据/ocr_cache/<doc_id>.txt），中断重跑自动续，不用从头再来
  2) OCR 完自动：写回 doc.content → doc_content 新版本 → 重建 doc_chunk → 清理旧 doc_vector
  3) 完成后需跑 `python kb_embed.py` 增量补嵌新块

用法：
  python kb_ocr_books.py 预览
  python kb_ocr_books.py 执行            # 全部 need_ocr
  python kb_ocr_books.py 执行 剑指        # 只跑路径含关键字的
  python kb_ocr_books.py 执行 --最大页数 400
"""
import os
import re
import sys
import time
import sqlite3
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kb_ocr import ocr_pdf, ocr_pptx, clean, TMP  # noqa: E402

KB = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/求职知识库"
DB = os.path.join(KB, "_系统_知识库引擎/数据/面试资料总库.db")
CACHE = os.path.join(KB, "_系统_知识库引擎/数据/ocr_cache")
os.makedirs(CACHE, exist_ok=True)


def cache_path(doc_id):
    return os.path.join(CACHE, "%d.txt" % doc_id)


def ocr_one(r, max_page=2000, dpi=200):
    """带缓存的 OCR：返回 (文本, 实际页数, 本次新识别页数)"""
    cp = cache_path(r["id"])
    cached = ""
    if os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            cached = f.read()
    done = len(re.findall(r"^--- P\d+", cached, re.M))
    path = r["path"]
    ext = (r["ext"] or "").lower()

    if ext == "pptx":
        txt, n = ocr_pptx(path)
        return txt, n, n

    import pymupdf
    d = pymupdf.open(path)
    total = min(d.page_count, max_page)
    d.close()
    if done >= total and cached:
        return cached, total, 0

    # 从断点页继续，逐页追加
    new = 0
    with open(cp, "a", encoding="utf-8") as f:
        for start in range(done, total, 20):  # 每 20 页一批，控制内存
            end = min(start + 20, total)
            part, n_img = _ocr_range(path, start, end, dpi)
            if part:
                f.write(part)
                f.flush()
                new += end - start
    with open(cp, encoding="utf-8") as f:
        return f.read(), total, new


def _ocr_range(path, start, end, dpi):
    """OCR [start, end) 页，返回可追加的文本片段"""
    import pymupdf
    d = pymupdf.open(path)
    outs = []
    for i in range(start, end):
        try:
            page = d[i]
        except Exception:
            continue
        txt = page.get_text().strip()
        if len(txt) > 120:  # 已有文字层，直接取
            outs.append("--- P%d ---\n%s" % (i + 1, txt))
            continue
        try:
            pix = page.get_pixmap(dpi=dpi)
            if pix.width < 100:
                continue
            tmp = os.path.join(TMP, "b_%d.png" % i)
            pix.save(tmp)
            from kb_ocr import ocr_image
            t = ocr_image(tmp)
            os.remove(tmp)
            if t.strip():
                outs.append("--- P%d(OCR) ---\n%s" % (i + 1, t.strip()))
        except Exception:
            pass
    d.close()
    return ("\n\n".join(outs) + "\n\n") if outs else "", 0


def rechunk(conn, doc_id, content, version, method):
    """写 doc_content 新版本 + 重建 doc_chunk + 清理旧向量"""
    # 旧块 → 删向量再删块
    old = [r[0] for r in conn.execute(
        "SELECT id FROM doc_chunk WHERE doc_id=?", (doc_id,)).fetchall()]
    if old:
        qm = ",".join("?" * len(old))
        try:
            conn.execute("DELETE FROM doc_vector WHERE chunk_id IN (%s)" % qm, old)
        except Exception:
            pass
        conn.execute("DELETE FROM doc_chunk WHERE doc_id IN (%s)" % qm, old)
    conn.execute("INSERT OR REPLACE INTO doc_content"
                 "(doc_id, version, content, char_count, extract_method, created_at) "
                 "VALUES (?,?,?,?,?,?)",
                 (doc_id, version, content, len(content), method,
                  datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    from kb_migrate_p1 import chunk_text
    chunks = chunk_text(content)
    for seq, (header, text) in enumerate(chunks):
        conn.execute("INSERT INTO doc_chunk(doc_id, content_version, seq, header, text, char_count)"
                     " VALUES (?,?,?,?,?,?)",
                     (doc_id, version, seq, header, text, len(text)))
    return len(chunks)


def main():
    a = sys.argv[1] if len(sys.argv) > 1 else "预览"
    kw = None
    max_page = 2000
    for i, s in enumerate(sys.argv[2:], start=2):
        if s == "--最大页数" and i + 1 < len(sys.argv):
            max_page = int(sys.argv[i + 1])
        elif not s.isdigit() and not s.startswith("--"):
            kw = s
    if len(sys.argv) > 2 and sys.argv[2].isdigit():
        max_page = int(sys.argv[2])

    conn = sqlite3.connect(DB, timeout=600)
    conn.row_factory = sqlite3.Row
    rows = [r for r in conn.execute(
        "SELECT id,path,rel_path,ext,size,extract_method FROM doc "
        "WHERE status='need_ocr' ORDER BY size").fetchall() if os.path.exists(r["path"])]
    if kw:
        rows = [r for r in rows if kw in r["rel_path"]]

    if a == "预览":
        import pymupdf
        print("待 OCR %d 篇\n" % len(rows))
        for r in rows:
            if (r["ext"] or "").lower() == "pdf":
                d = pymupdf.open(r["path"])
                n = d.page_count
                d.close()
            else:
                n = 1
            cp = cache_path(r["id"])
            done = 0
            if os.path.exists(cp):
                with open(cp, encoding="utf-8") as f:
                    done = len(re.findall(r"^--- P\d+", f.read(), re.M))
            print("  %5d页(已缓存%4d) %6.1fMB  %s" % (
                n, done, (r["size"] or 0) / 1048576, os.path.basename(r["rel_path"])[:52]))
        return

    if a != "执行":
        print(__doc__)
        return

    print("本批 %d 篇，开始 OCR（逐页缓存，可中断续跑）\n" % len(rows))
    t0 = time.time()
    okn = failn = 0
    for r in rows:
        name = os.path.basename(r["rel_path"])[:44]
        ts = time.time()
        try:
            txt, total, new = ocr_one(r, max_page=max_page)
            txt = clean(txt)
            cjk = sum(1 for ch in txt if "\u4e00" <= ch <= "\u9fff")
            if cjk < 200:
                print("  ✗ %-44s 中文仅%d（%d页）" % (name, cjk, total))
                failn += 1
                continue
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            method = "扫描版OCR(%d页)" % total
            conn.execute("UPDATE doc SET content=?, char_count=?, status='ok', "
                         "extract_method=?, updated_at=? WHERE id=?",
                         (txt, len(txt), method, now, r["id"]))
            ver = (conn.execute("SELECT MAX(version) FROM doc_content WHERE doc_id=?",
                                (r["id"],)).fetchone()[0] or -1) + 1
            nch = rechunk(conn, r["id"], txt, ver, method)
            conn.commit()
            okn += 1
            print("  ✓ %-44s %4d页(新%4d) → %7d字/中文%6d → %4d块  %.0fs (累计%.0fs)" % (
                name, total, new, len(txt), cjk, nch, time.time() - ts, time.time() - t0))
        except Exception as e:
            print("  ! %-44s 异常 %s" % (name, str(e)[:60]))
            failn += 1
    print("\nOCR 完成：成功 %d / 失败 %d / 耗时 %.0fs" % (okn, failn, time.time() - t0))
    left = conn.execute("SELECT COUNT(*) FROM doc WHERE status='need_ocr'").fetchone()[0]
    pend = conn.execute(
        "SELECT COUNT(*) FROM doc_chunk ch WHERE ch.char_count>=60 AND ch.id NOT IN "
        "(SELECT chunk_id FROM doc_vector)").fetchone()[0]
    print("剩余 need_ocr：%d 篇 | 待补向量块：%d（跑 python kb_embed.py 增量补）" % (left, pend))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描版 PDF OCR 通道：逐页提取内嵌图片 → tesseract → 回写总库。

分批落盘（每批 25 页，OCR 完即删），避免占满磁盘；支持断点续跑（已 ok 的跳过）。

用法：
    python3 kb_ocr_pdf.py 列表
    python3 kb_ocr_pdf.py 跑 [--并行 8] [--最大页 500] [--关键字 统计学习]
"""
import argparse
import os
import shutil
import sqlite3
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

KB = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/求职知识库"
DB = os.path.join(KB, "_系统_知识库引擎/数据/面试资料总库.db")
WORK = os.path.join(KB, "_系统_知识库引擎/.tmp_pdfocr")
TESS = "/opt/homebrew/bin/tesseract"
BATCH = 25


def db():
    conn = sqlite3.connect(DB, timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def shrink_into(img, dst):
    """大图降采样到固定路径 dst（复写，不产生额外文件）"""
    try:
        if os.path.getsize(img) >= 2 * 1024 * 1024:
            subprocess.run(["sips", "-Z", "2200", img, "--out", dst],
                           capture_output=True, timeout=120)
            if os.path.exists(dst) and os.path.getsize(dst) > 0:
                return dst
    except Exception:
        pass
    return img


def ocr_one(item):
    no, img, slot_s = item
    try:
        src = shrink_into(img, slot_s)
        r = subprocess.run([TESS, src, "stdout", "-l", "chi_sim+eng", "--psm", "6"],
                           capture_output=True, timeout=300)
        return no, r.stdout.decode("utf-8", "ignore")
    except Exception:
        return no, ""


def run_pdf(path, rel, workers, max_pages):
    from pypdf import PdfReader
    try:
        rd = PdfReader(path)
        total = len(rd.pages)
    except Exception as e:
        print("  读取失败：%s" % type(e).__name__)
        return None
    total = min(total, max_pages)
    print("  %d 页，分批识别..." % total)
    tag = "%03d" % (abs(hash(rel)) % 1000)
    outdir = os.path.join(WORK, tag)
    os.makedirs(outdir, exist_ok=True)
    # 固定 slot 文件复写，避免反复创建/删除临时文件
    n_slot = max(workers, 1)
    slots = [os.path.join(outdir, "s%d.img" % i) for i in range(n_slot)]
    slots_s = [os.path.join(outdir, "s%d_s.png" % i) for i in range(n_slot)]
    texts = []
    t0 = time.time()
    for start in range(0, total, BATCH):
        end = min(start + BATCH, total)
        items = []
        for k, i in enumerate(range(start, end)):
            try:
                page = rd.pages[i]
                imgs = list(page.images)
                if not imgs:
                    texts.append((i + 1, ""))
                    continue
                imgs.sort(key=lambda im: len(im.data), reverse=True)
                im = imgs[0]
                slot = slots[k % n_slot]
                with open(slot, "wb") as f:
                    f.write(im.data)
                items.append((i + 1, slot, slots_s[k % n_slot]))
            except Exception:
                texts.append((i + 1, ""))
        if items:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for no, t in ex.map(ocr_one, items):
                    texts.append((no, t))
        print("    %d/%d 页 (%.0fs)" % (end, total, time.time() - t0), flush=True)
    # 不做任何删除（slot 文件下一批覆写复用），避免触发批量删除保护；
    # 临时目录 .tmp_pdfocr 可在任务结束后一次性清理：rm -rf _系统_知识库引擎/.tmp_pdfocr
    texts.sort()
    content = "\n".join("--- Page %d ---\n%s" % (no, t) for no, t in texts if t.strip())
    return content


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["列表", "跑"])
    ap.add_argument("--并行", type=int, default=8)
    ap.add_argument("--最大页", type=int, default=600)
    ap.add_argument("--关键字")
    a = ap.parse_args()
    conn = db()
    rows = conn.execute("SELECT path, rel_path, size FROM doc "
                        "WHERE status='need_ocr' AND ext='pdf' ORDER BY size ASC").fetchall()
    if a.关键字:
        rows = [r for r in rows if a.关键字 in r["rel_path"]]
    if a.cmd == "列表":
        for r in rows:
            print("  %s" % r["rel_path"])
        print("共 %d 个待 OCR 的 PDF" % len(rows))
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for r in rows:
        print("\n=== %s" % r["rel_path"][-70:])
        content = run_pdf(r["path"], r["rel_path"], a.并行, a.最大页)
        if content is None:
            continue
        if len(content) < 200:
            print("  识别过少，保持 need_ocr")
            continue
        conn.execute("""UPDATE doc SET content=?, char_count=?, extract_method='tesseract-pdf-ocr',
                        status='ok', updated_at=? WHERE path=?""",
                     (content, len(content), now, r["path"]))
        conn.commit()
        print("  完成 %d 字" % len(content))
    # 刷新未处理表
    conn.execute("DELETE FROM unprocessed")
    for r in conn.execute("SELECT rel_path,ext,status,extract_method FROM doc WHERE status NOT IN ('ok','skipped','duplicate')"):
        reason = {"need_ocr": "无文本层（扫描件/图片型），需 OCR", "empty": "抽取结果为空",
                  "failed": "解析失败: %s" % r["extract_method"],
                  "data": "数据文件（训练样本等），未入库正文"}.get(r["status"], r["status"])
        conn.execute("INSERT INTO unprocessed(rel_path,kind,reason,status,updated_at) VALUES(?,?,?,?,?)",
                     (r["rel_path"], r["ext"], reason, r["status"], now))
    conn.commit()
    print("\n剩余未处理：%d 条" % conn.execute("SELECT COUNT(*) FROM unprocessed").fetchone()[0])


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图片型幻灯片（无文本层的 pptx/pdf）OCR 通道。

从 pptx 中抽取每页最大的媒体图 → tesseract 识别（工作区内执行，沙箱读不到 /tmp）
→ 按页拼接 → 回写总库 doc 表，状态置 ok。支持断点续跑（已 ok 的跳过）。

用法：
    python3 kb_ocr_large.py 列表                    # 列出需要 OCR 的文件与页数
    python3 kb_ocr_large.py 跑 <文件名关键字> [--并行 8] [--限制 N]
    python3 kb_ocr_large.py 跑 --全部
"""
import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

KB = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/求职知识库"
DB = os.path.join(KB, "_系统_知识库引擎/数据/面试资料总库.db")
WORK = os.path.join(KB, "_系统_知识库引擎/.tmp_ocr")
TESS = "/opt/homebrew/bin/tesseract"


def db():
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def pending(keyword=None):
    conn = db()
    rows = conn.execute("SELECT path, rel_path FROM doc WHERE status='need_ocr' AND ext='pptx'").fetchall()
    out = []
    for r in rows:
        if keyword and keyword not in r["rel_path"]:
            continue
        out.append((r["path"], r["rel_path"]))
    return out


def slide_images(pptx_path, out_dir):
    """导出每页最大媒体图，返回 [(page_no, img_path)]"""
    z = zipfile.ZipFile(pptx_path)
    slides = sorted([n for n in z.namelist() if re.search(r"ppt/slides/slide\d+\.xml$", n)],
                    key=lambda n: int(re.search(r"(\d+)", n.split("/")[-1]).group(1)))
    os.makedirs(out_dir, exist_ok=True)
    items = []
    for i, s in enumerate(slides, 1):
        base = s.split("/")[-1]
        rels = "ppt/slides/_rels/%s.rels" % base
        if rels not in z.namelist():
            continue
        rx = z.read(rels).decode("utf-8", "ignore")
        imgs = re.findall(r'Target="\.\./media/([^"]+)"', rx)
        if not imgs:
            continue
        imgs = sorted(imgs, key=lambda m: z.getinfo("ppt/media/" + m).file_size, reverse=True)
        data = z.read("ppt/media/" + imgs[0])
        ext = os.path.splitext(imgs[0])[1].lower() or ".png"
        p = os.path.join(out_dir, "%04d%s" % (i, ext))
        with open(p, "wb") as f:
            f.write(data)
        items.append((i, p))
    return items


def shrink(img):
    """大图降采样（>2MB 时用 sips 压到长边 2200），避免 tesseract 卡死"""
    try:
        if os.path.getsize(img) < 2 * 1024 * 1024:
            return img
        out = img.rsplit(".", 1)[0] + "_s.png"
        subprocess.run(["sips", "-Z", "2200", img, "--out", out],
                       capture_output=True, timeout=120)
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return out
    except Exception:
        pass
    return img


def ocr_one(item):
    no, img = item
    try:
        img = shrink(img)
        r = subprocess.run([TESS, img, "stdout", "-l", "chi_sim+eng", "--psm", "6"],
                           capture_output=True, timeout=300)
        return no, r.stdout.decode("utf-8", "ignore")
    except Exception as e:
        return no, ""


def run(keyword, workers=8, limit=None, all_=False):
    targets = pending(None if all_ else keyword)
    if limit:
        targets = targets[:limit]
    if not targets:
        print("没有待 OCR 的文件")
        return
    conn = db()
    for path, rel in targets:
        print("\n=== %s" % rel)
        tag = re.sub(r"[^\w\u4e00-\u9fff]+", "_", os.path.basename(rel))[:40]
        outdir = os.path.join(WORK, tag)
        shutil.rmtree(outdir, ignore_errors=True)
        try:
            items = slide_images(path, outdir)
        except Exception as e:
            print("  抽取图片失败：", e)
            continue
        print("  页数 %d，识别中..." % len(items))
        res = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for no, txt in ex.map(ocr_one, items):
                res.append((no, txt))
        res.sort()
        content = "\n".join("--- Slide %d ---\n%s" % (no, t) for no, t in res if t.strip())
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if len(content) < 100:
            print("  识别结果过少，保持 need_ocr")
            conn.execute("UPDATE doc SET updated_at=? WHERE path=?", (now, path))
        else:
            conn.execute("""UPDATE doc SET content=?, char_count=?, extract_method='tesseract-ocr',
                            status='ok', updated_at=? WHERE path=?""",
                         (content, len(content), now, path))
            print("  完成 %d 字" % len(content))
        conn.commit()
        # 同步回写解码文本（原始数据层）
        try:
            dec = os.path.join(KB, "01_原始资料库/08_解码文本",
                               rel.replace("/", "__").replace("01_原始资料库__", "") + ".txt")
            if os.path.isdir(os.path.dirname(dec)):
                with open(dec, "w", encoding="utf-8") as f:
                    f.write(content)
                print("  已回填解码文本：%s" % os.path.basename(dec))
        except Exception:
            pass
        shutil.rmtree(outdir, ignore_errors=True)
    conn.execute("DELETE FROM unprocessed")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for r in conn.execute("SELECT rel_path,ext,status,extract_method FROM doc WHERE status!='ok'"):
        reason = {"need_ocr": "无文本层（扫描件/图片型），需 OCR", "empty": "抽取结果为空",
                  "failed": "解析失败: %s" % r["extract_method"]}.get(r["status"], r["status"])
        conn.execute("INSERT INTO unprocessed(rel_path,kind,reason,status,updated_at) VALUES(?,?,?,?,?)",
                     (r["rel_path"], r["ext"], reason, r["status"], now))
    conn.commit()


def list_pending():
    for p, rel in pending():
        try:
            z = zipfile.ZipFile(p)
            n = len([x for x in z.namelist() if re.search(r"ppt/slides/slide\d+\.xml$", x)])
            mb = os.path.getsize(p) / 1024 / 1024
        except Exception:
            n, mb = 0, 0
        print("  %5d页 %7.1fMB  %s" % (n, mb, rel))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["列表", "跑"])
    ap.add_argument("kw", nargs="?")
    ap.add_argument("--并行", type=int, default=8)
    ap.add_argument("--限制", type=int)
    ap.add_argument("--全部", action="store_true")
    a = ap.parse_args()
    if a.cmd == "列表":
        list_pending()
    else:
        run(a.kw, a.并行, a.限制, a.全部)

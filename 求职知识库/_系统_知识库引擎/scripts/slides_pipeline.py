#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
幻灯片笔记 · 三层数据处理流水线（可断点续跑）

用法：
    python3 slides_pipeline.py            处理全部未完成的部分
    python3 slides_pipeline.py part16     只处理指定部分
    python3 slides_pipeline.py --status   查看处理台账
    python3 slides_pipeline.py --rebuild  清空重来

三层数据框架：
    ① 原始数据层 -> 解码文本/书籍__程序化广告学习笔记_拆分__partXX.pptx.txt（回填 OCR 原文，按页编号）
    ② 加工数据层 -> 02_方向知识库/幻灯片笔记_加工/（主题化重组，人工/AI 产出）
    ③ 应用数据层 -> 03_岗位弹药库/<岗位>/03_速成包/（面试弹药）
    台账与全文检索 -> _系统_知识库引擎/数据/幻灯片笔记.db
"""
import os
import re
import sys
import json
import time
import sqlite3
import zipfile
import subprocess
import datetime

BASE = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
KB = os.path.join(BASE, "求职知识库")
SRC = os.path.join(KB, "01_原始资料库/书籍/程序化广告学习笔记_拆分")
WORK = os.path.join(BASE, ".tmp_ocr")
DECODED = os.path.join(KB, "01_原始资料库/解码文本")
DB_PATH = os.path.join(KB, "_系统_知识库引擎/数据/幻灯片笔记.db")
TESS = "/opt/homebrew/bin/tesseract"


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS source (
        part_no     TEXT PRIMARY KEY,
        file_name   TEXT,
        title       TEXT,
        slide_count INTEGER DEFAULT 0,
        image_count INTEGER DEFAULT 0,
        ocr_done    INTEGER DEFAULT 0,
        char_total  INTEGER DEFAULT 0,
        status      TEXT,
        updated_at  TEXT
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS slide (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        part_no    TEXT,
        slide_no   INTEGER,
        ocr_text   TEXT,
        char_count INTEGER DEFAULT 0,
        quality    TEXT,
        updated_at TEXT,
        UNIQUE(part_no, slide_no)
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS run_log (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        ts       TEXT,
        part_no  TEXT,
        action   TEXT,
        status   TEXT,
        detail   TEXT
    )""")
    try:
        c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS slide_fts USING fts5(part_no, slide_no, ocr_text)")
    except Exception:
        pass
    conn.commit()


def log(conn, part, action, status, detail=""):
    conn.execute("INSERT INTO run_log(ts, part_no, action, status, detail) VALUES (?,?,?,?,?)",
                 (now(), part, action, status, detail))
    conn.commit()


def slide_num(n):
    m = re.search(r"(\d+)", n.split("/")[-1])
    return int(m.group(1)) if m else 0


def extract_images(pptx_path, out_dir):
    """从 pptx 抽出每页主图，返回 [(slide_no, img_path)]"""
    os.makedirs(out_dir, exist_ok=True)
    z = zipfile.ZipFile(pptx_path)
    slides = sorted([n for n in z.namelist() if re.search(r"ppt/slides/slide\d+\.xml$", n)], key=slide_num)
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
        imgs.sort(key=lambda m: z.getinfo("ppt/media/" + m).file_size, reverse=True)
        ext = os.path.splitext(imgs[0])[1].lower() or ".png"
        dst = os.path.join(out_dir, "%03d%s" % (i, ext))
        if not os.path.exists(dst):
            with open(dst, "wb") as w:
                w.write(z.read("ppt/media/" + imgs[0]))
        items.append((i, dst))
    return items


def ocr(img_path):
    try:
        r = subprocess.run([TESS, img_path, "stdout", "-l", "chi_sim+eng", "--psm", "6"],
                           capture_output=True, timeout=120)
        return r.stdout.decode("utf-8", "ignore").strip()
    except Exception as e:
        return ""


def grade(text):
    if not text:
        return "empty"
    if len(text) < 20:
        return "low"
    if len(text) < 80:
        return "mid"
    return "good"


def process_part(conn, part_no, force=False):
    files = [f for f in os.listdir(SRC) if f.startswith(part_no) and f.endswith(".pptx")]
    if not files:
        log(conn, part_no, "find", "fail", "源文件不存在")
        return None
    fn = files[0]
    title = fn[len(part_no) + 1:-len(".pptx")]
    path = os.path.join(SRC, fn)
    out_dir = os.path.join(WORK, part_no)

    cur = conn.execute("SELECT * FROM source WHERE part_no=?", (part_no,)).fetchone()
    if cur and cur["ocr_done"] and not force:
        return dict(cur)

    items = extract_images(path, out_dir)
    log(conn, part_no, "extract", "ok", "图片 %d 张" % len(items))

    todo = []
    total = 0
    for slide_no, img in items:
        exist = conn.execute("SELECT char_count FROM slide WHERE part_no=? AND slide_no=?",
                             (part_no, slide_no)).fetchone()
        if exist and exist["char_count"] > 0 and not force:
            total += exist["char_count"]
        else:
            todo.append((slide_no, img))

    if todo:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(lambda t: (t[0], ocr(t[1])), todo))
        for slide_no, txt in results:
            q = grade(txt)
            conn.execute("""INSERT INTO slide(part_no, slide_no, ocr_text, char_count, quality, updated_at)
                            VALUES (?,?,?,?,?,?)
                            ON CONFLICT(part_no, slide_no) DO UPDATE SET
                            ocr_text=excluded.ocr_text, char_count=excluded.char_count,
                            quality=excluded.quality, updated_at=excluded.updated_at""",
                         (part_no, slide_no, txt, len(txt), q, now()))
            total += len(txt)
        conn.commit()

    done = conn.execute("SELECT COUNT(*) c FROM slide WHERE part_no=? AND char_count>0", (part_no,)).fetchone()["c"]
    conn.execute("""INSERT INTO source(part_no, file_name, title, slide_count, image_count, ocr_done, char_total, status, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(part_no) DO UPDATE SET
                    file_name=excluded.file_name, title=excluded.title, slide_count=excluded.slide_count,
                    image_count=excluded.image_count, ocr_done=excluded.ocr_done, char_total=excluded.char_total,
                    status=excluded.status, updated_at=excluded.updated_at""",
                 (part_no, fn, title, len(items), len(items), 1 if done == len(items) else 0,
                  total, "done" if done == len(items) else "partial", now()))
    conn.commit()
    log(conn, part_no, "ocr", "done" if done == len(items) else "partial", "%d/%d 页，%d 字" % (done, len(items), total))
    return conn.execute("SELECT * FROM source WHERE part_no=?", (part_no,)).fetchone()


def flush_raw(conn, part_no):
    """原始数据层：把 OCR 原文回填到解码文本"""
    rows = conn.execute("SELECT slide_no, ocr_text FROM slide WHERE part_no=? ORDER BY slide_no", (part_no,)).fetchall()
    if not rows:
        return None
    src_file = conn.execute("SELECT file_name FROM source WHERE part_no=?", (part_no,)).fetchone()["file_name"]
    target = os.path.join(DECODED, "书籍__程序化广告学习笔记_拆分__" + src_file + ".txt")
    with open(target, "w", encoding="utf-8") as w:
        for r in rows:
            w.write("--- Slide %d ---\n" % r["slide_no"])
            w.write((r["ocr_text"] or "") + "\n")
    log(conn, part_no, "flush_raw", "ok", os.path.basename(target))
    return target


def status(conn):
    rows = conn.execute("SELECT * FROM source ORDER BY part_no").fetchall()
    print("%-8s %-42s %5s %6s %8s %s" % ("part", "标题", "页数", "已OCR", "字数", "状态"))
    for r in rows:
        print("%-8s %-42s %5d %6d %8d %s" % (r["part_no"], (r["title"] or "")[:40], r["slide_count"],
                                             r["image_count"], r["char_total"] or 0, r["status"]))
    print("\n合计：%d 个部分，%d 页，%d 字" % (
        len(rows),
        sum(r["slide_count"] or 0 for r in rows),
        sum(r["char_total"] or 0 for r in rows)))


def main():
    conn = connect()
    init_db(conn)
    args = sys.argv[1:]
    if "--status" in args:
        status(conn)
        return
    if "--rebuild" in args:
        conn.execute("DELETE FROM slide"); conn.execute("DELETE FROM source"); conn.commit()
        print("已清空，重新处理")
    parts = [a for a in args if a.startswith("part")]
    if not parts:
        parts = sorted("part%02d" % i for i in range(1, 18))
    for p in parts:
        r = process_part(conn, p)
        if r:
            flush_raw(conn, p)
            print("[OK] %s %s  页数=%d  字数=%d  状态=%s" % (p, r["title"][:34], r["slide_count"], r["char_total"], r["status"]))
    status(conn)


if __name__ == "__main__":
    main()

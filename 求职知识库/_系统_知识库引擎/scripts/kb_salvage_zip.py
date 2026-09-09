#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""截断/损坏 pptx 抢救提取器。

背景：一批 .pptx 文件尾部被截断（缺 EOCD 或 EOCD 不完整），
zipfile 严格校验直接 BadZipFile，但本地文件头（PK\\x03\\x04）完好，
且 flag=0x0006 表示未启用 data descriptor，csize 有效 —— 可流式逐个提取。

用法：
  python3 kb_salvage_zip.py 列表
  python3 kb_salvage_zip.py 修 <关键字>      # 抢救并回填总库
"""
import os
import re
import sys
import zlib
import sqlite3
import struct
import datetime

KB = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/求职知识库"
DB = os.path.join(KB, "_系统_知识库引擎/数据/面试资料总库.db")

LOCAL = b"PK\x03\x04"
CENTRAL = b"PK\x01\x02"


def scan_entries(path, limit_mb=None):
    """扫描本地文件头，返回 [(name, offset_of_data, csize, usize, method, flags)]"""
    size = os.path.getsize(path)
    out = []
    with open(path, "rb") as f:
        pos = 0
        buf_start = 0
        chunk = 8 * 1024 * 1024
        tail_keep = b""
        while True:
            f.seek(buf_start)
            data = f.read(chunk)
            if not data:
                break
            blob = tail_keep + data
            base = buf_start - len(tail_keep)
            i = 0
            while True:
                j = blob.find(LOCAL, i)
                if j < 0:
                    break
                off = base + j
                if off + 30 > size:
                    break
                (ver, flags, method, _t, _d, crc, csize, usize,
                 nlen, elen) = struct.unpack("<HHHHHIIIHH", blob[j + 4:j + 30])
                noff = off + 30
                if nlen == 0 or noff + nlen > size:
                    i = j + 4
                    continue
                f.seek(noff)
                name = f.read(nlen)
                try:
                    name = name.decode("utf-8")
                except UnicodeDecodeError:
                    name = name.decode("latin-1", "ignore")
                dstart = noff + nlen + elen
                if csize == 0 or dstart + csize > size:
                    i = j + 4
                    continue
                out.append((name, dstart, csize, usize, method, flags, crc))
                i = j + 4
            buf_start += len(data)
            tail_keep = data[-64:]
    # 去重：同名保留第一个
    seen = set()
    uniq = []
    for e in out:
        if e[0] in seen:
            continue
        seen.add(e[0])
        uniq.append(e)
    return uniq


def read_entry(path, e):
    name, dstart, csize, _usize, method, _flags, _crc = e
    with open(path, "rb") as f:
        f.seek(dstart)
        raw = f.read(csize)
    if method == 0:
        return raw
    if method == 8:
        try:
            return zlib.decompress(raw, -15)
        except zlib.error:
            try:
                return zlib.decompressobj(-15).decompress(raw)
            except Exception:
                return b""
    return b""


def slides_text(path):
    """提取全部 slide 文本，返回 (文本, 页数, 总条目数)"""
    entries = scan_entries(path)
    sl = sorted([e for e in entries if re.search(r"ppt/slides/slide\d+\.xml$", e[0])],
                key=lambda e: int(re.search(r"(\d+)", e[0].split("/")[-1]).group(1)))
    out = []
    for i, e in enumerate(sl, 1):
        xml = read_entry(path, e).decode("utf-8", "ignore")
        ts = [t.strip() for t in re.findall(r"<a:t>(.*?)</a:t>", xml, re.S)]
        ts = [t for t in ts if t]
        if ts:
            out.append("--- Slide %d ---\n%s" % (i, "\n".join(ts)))
    if out:
        return "\n".join(out), len(sl), len(entries)
    # 兜底：notesSlide + 其他 xml
    buf = []
    for e in entries:
        if e[0].endswith(".xml") and ("notesSlide" in e[0] or "ppt/slides" in e[0]):
            x = read_entry(path, e).decode("utf-8", "ignore")
            ts = [t.strip() for t in re.findall(r"<a:t>(.*?)</a:t>", x, re.S) if t.strip()]
            if ts:
                buf.append("\n".join(ts))
    return "\n".join(buf), len(sl), len(entries)


def cmd_list():
    conn = sqlite3.connect(DB, timeout=60)
    conn.row_factory = sqlite3.Row
    for r in conn.execute("SELECT path, rel_path, size FROM doc WHERE status='failed' ORDER BY size DESC"):
        p = r["path"]
        if not os.path.exists(p):
            print("  [缺文件] %s" % r["rel_path"])
            continue
        with open(p, "rb") as f:
            h = f.read(4)
        if h[:2] != b"PK":
            print("  [非zip] %s" % r["rel_path"][-55:])
            continue
        try:
            ents = scan_entries(p)
            print("  %3d 条目  %6.1fMB  %s" % (len(ents), r["size"] / 1048576, r["rel_path"][-58:]))
        except Exception as e:
            print("  [扫描失败 %s] %s" % (type(e).__name__, r["rel_path"][-50:]))


def cmd_fix(kw=""):
    conn = sqlite3.connect(DB, timeout=60)
    conn.row_factory = sqlite3.Row
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = [r for r in conn.execute(
        "SELECT id, path, rel_path, size FROM doc WHERE status='failed' ORDER BY size ASC")
        if kw in r["rel_path"]]
    n = 0
    for r in rows:
        p = r["path"]
        if not os.path.exists(p):
            continue
        with open(p, "rb") as f:
            if f.read(2) != b"PK":
                print("[跳过 非zip] %s" % r["rel_path"][-50:])
                continue
        try:
            txt, pages, total = slides_text(p)
        except Exception as e:
            print("[失败 %s] %s" % (type(e).__name__, r["rel_path"][-50:]))
            continue
        txt = re.sub(r"[\ud800-\udfff]", "", txt).strip()
        if len(txt) >= 50:
            conn.execute(
                "UPDATE doc SET content=?, char_count=?, extract_method=?, status='ok', updated_at=? WHERE id=?",
                (txt, len(txt), "pptx-salvage-localhdr", now, r["id"]))
            print("[抢救] %3d页/%3d条目 %8d字  %s" % (pages, total, len(txt), r["rel_path"][-52:]))
            n += 1
        else:
            print("[无文本] %3d页/%3d条目  %s" % (pages, total, r["rel_path"][-52:]))
    conn.commit()
    print("抢救成功 %d 个" % n)


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "列表"
    if a == "列表":
        cmd_list()
    elif a == "修":
        cmd_fix(sys.argv[2] if len(sys.argv) > 2 else "")
    else:
        print(__doc__)

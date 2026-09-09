#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复 Confluence 导出文档：quoted-printable 解码 + HTML 剥离，重新提取正文并更新总库。

背景：腾讯内部资料大量是 .doc 实为 Confluence 导出的 HTML（quoted-printable 编码），
kb_ingest 直接读文本只拿到编码垃圾。本脚本识别并还原真正的正文。

用法：
  python3 kb_fix_confluence.py 检测          # 统计有多少篇需要修复
  python3 kb_fix_confluence.py 修复 [--限 N] # 重新提取并更新
"""
import os
import re
import sys
import quopri
import sqlite3
import datetime
import html as htmlmod

KB = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/求职知识库"
DB = os.path.join(KB, "_系统_知识库引擎/数据/面试资料总库.db")

TAG_STRIP = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
TAG = re.compile(r"<[^>]+>")
BLANK = re.compile(r"\n{3,}")


def looks_confluence(raw: bytes) -> bool:
    return b"Content-Transfer-Encoding: quoted-printable" in raw[:4000] \
        or b"Exported From Confluence" in raw[:4000]


def decode(raw: bytes) -> str:
    """quoted-printable 解码 → HTML 剥离 → 纯文本"""
    txt = raw.decode("utf-8", "ignore")
    # 只取 text/html 部分（可能有多个 part）
    parts = re.split(r"------=_Part_\d+_\d+\.\d+", txt)
    buf = []
    for p in parts:
        if "Content-Type: text/html" not in p:
            continue
        body = p.split("\n\n", 1)[-1]
        try:
            body = quopri.decodestring(body).decode("utf-8", "ignore")
        except Exception:
            pass
        buf.append(body)
    html = "\n".join(buf) if buf else txt
    html = TAG_STRIP.sub(" ", html)
    # 块级标签转换行
    html = re.sub(r"<(br|/p|/div|/tr|/h[1-6]|/li)[^>]*>", "\n", html, flags=re.I)
    text = TAG.sub(" ", html)
    text = htmlmod.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = BLANK.sub("\n\n", text)
    lines = [l.strip() for l in text.split("\n")]
    return "\n".join(l for l in lines if l)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "检测"
    limit = 10 ** 9
    if "--限" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--限") + 1])

    conn = sqlite3.connect(DB, timeout=180)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, path, rel_path, char_count, content FROM doc WHERE status='ok'"
    ).fetchall()

    need = []
    for r in rows:
        p = r["path"]
        if not os.path.exists(p):
            continue
        try:
            with open(p, "rb") as f:
                head = f.read(4000)
        except Exception:
            continue
        if looks_confluence(head):
            need.append(r)

    print("检测到 Confluence 编码文档：%d 篇" % len(need))
    if cmd == "检测":
        for r in need[:15]:
            print("  %8d字  %s" % (r["char_count"], os.path.basename(r["rel_path"])[:60]))
        return

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n = better = 0
    for r in need[:limit]:
        try:
            with open(r["path"], "rb") as f:
                raw = f.read()
        except Exception:
            continue
        txt = decode(raw)
        txt = re.sub(r"[\ud800-\udfff]", "", txt).strip()
        if len(txt) < 200:
            continue
        # 只在明显更好时更新（去掉 HTML 垃圾后应更短且更纯）
        if txt != (r["content"] or ""):
            conn.execute(
                "UPDATE doc SET content=?, char_count=?, extract_method='confluence-decode', updated_at=? WHERE id=?",
                (txt, len(txt), now, r["id"]))
            n += 1
            if len(txt) < r["char_count"]:
                better += 1
        if n % 30 == 0:
            conn.commit()
            print("  …已处理 %d 篇" % n, flush=True)
    conn.commit()
    print("修复 %d 篇（其中 %d 篇正文更纯净），最后更新时间 %s" % (n, better, now))


if __name__ == "__main__":
    main()

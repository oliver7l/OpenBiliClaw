# -*- coding: utf-8 -*-
"""
kb_decode_mime.py —— 解码内部资料里的 MIME/quoted-printable/Confluence 垃圾正文

部分内部资料（28 篇 / 867 万字）入库时存的是原始 MIME 邮件或 Confluence 导出体
（Content-Transfer-Encoding: quoted-printable + HTML 标签 + base64 图片块），
导致正文是乱码。本脚本用标准库 email + quopri 解码，剥离 HTML/CSS/base64，
把干净中文正文写回 doc.content 与 doc_content 新版本。

用法：
  python kb_decode_mime.py 预览       # 列出疑似垃圾的文档
  python kb_decode_mime.py 执行       # 解码并写回
  python kb_decode_mime.py 统计       # 解码前后字数对比
"""
import os
import re
import sys
import sqlite3
import argparse
import quopri
import email
from email import policy

DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "数据", "面试资料总库.db"))
SRC_PREFIX = "01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/"

BASE64_RE = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")
CN_RE = re.compile(r"[\u4e00-\u9fa5]")
GARBAGE_HINT = ("Exported From Confluence", "quoted-printable", "Content-Type: text/html",
                "boundary=", "Message-ID", "@@BASE64@@")


def is_garbage(text):
    if not text:
        return False
    head = text[:4000]
    if any(h in head for h in GARBAGE_HINT):
        return True
    # base64 密度：前 4000 字里长 base64 串占比高
    runs = BASE64_RE.findall(head)
    if runs and sum(len(r) for r in runs) > 1500:
        return True
    return False


def decode_raw(raw):
    """尽力从 MIME/QP/HTML 垃圾中解出纯文本。"""
    txt = ""
    try:
        msg = email.message_from_string(raw, policy=policy.default)
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct in ("text/plain", "text/html"):
                    try:
                        p = part.get_payload(decode=True)
                        if p:
                            txt += p.decode("utf-8", "ignore")
                    except Exception:
                        pass
        else:
            try:
                p = msg.get_payload(decode=True)
                if p:
                    txt += p.decode("utf-8", "ignore")
            except Exception:
                pass
    except Exception:
        pass
    # 单列 QP 文本（非 multipart 但标了 quoted-printable）
    if not txt.strip() and "quoted-printable" in raw.lower():
        try:
            txt = quopri.decodestring(
                raw.encode("utf-8", "ignore") if isinstance(raw, str) else raw
            ).decode("utf-8", "ignore")
        except Exception:
            pass
    # 剥离 HTML / CSS / Confluence 残留 / base64 块
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"[A-Za-z0-9+/]{40,}={0,2}", " ", txt)
    txt = re.sub(r"^\s*(size|margin|border|padding|width|height|color|background|"
                 r"display|position|page|Subject|Exported From Confluence)[^;]*;?",
                 " ", txt, flags=re.M)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def pending(c):
    rows = c.execute(
        "select id, rel_path, char_count, length(content) from doc "
        "where rel_path like ? and status='ok'", (SRC_PREFIX + "%",)).fetchall()
    return [r for r in rows if is_garbage(
        (c.execute("select substr(content,1,4000) from doc where id=?", (r[0],))
         .fetchone()[0] or ""))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["预览", "执行", "统计"])
    args = ap.parse_args()
    c = sqlite3.connect(DB)
    if args.cmd == "预览":
        rows = pending(c)
        print(f"疑似垃圾（需解码）：{len(rows)} 篇")
        for i, p, w, L in rows[:20]:
            print(f"  [{i}] {w or 0}字  {p[len(SRC_PREFIX):]}")
        return
    if args.cmd == "统计":
        rows = pending(c)
        print(f"待解码：{len(rows)} 篇")
        return
    # 执行
    rows = pending(c)
    ok = 0
    for i, p, w, L in rows:
        raw = c.execute("select content from doc where id=?", (i,)).fetchone()[0] or ""
        decoded = decode_raw(raw)
        cn = len(CN_RE.findall(decoded))
        if cn < 50:
            # 解码后仍无实质中文：标记为图片/二进制型
            c.execute("update doc set status='no_text', extract_method='MIME/QP解码后仍无实质文本(疑似图片/二进制型)', updated_at=? where id=?", (datetime_now(), i))
            print(f"  ✗ [{i}] 解码后仅 {cn} 中文字，标记 no_text：{p[len(SRC_PREFIX):]}")
            continue
        # 写回 doc.content + doc_content 新版本
        c.execute("update doc set content=?, char_count=?, extract_method='MIME/QP解码+HTML剥离', updated_at=? where id=?",
                  (decoded, len(decoded), datetime_now(), i))
        maxv = c.execute("select max(version) from doc_content where doc_id=?", (i,)).fetchone()[0] or 0
        c.execute("insert into doc_content (doc_id, version, content) values (?,?,?)",
                  (i, maxv + 1, decoded))
        ok += 1
        print(f"  ✓ [{i}] {w or 0}→{len(decoded)}字(中文{cn})  {p[len(SRC_PREFIX):]}")
    c.commit()
    print(f"\n解码完成：{ok} 篇写回，其余标记 no_text")


def datetime_now():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    main()

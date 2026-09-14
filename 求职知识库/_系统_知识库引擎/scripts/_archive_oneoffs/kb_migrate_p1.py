#!/usr/bin/env python3
"""P1 迁移：doc_content 版本表 + doc_chunk 分块表

- doc_content: (doc_id, version, content, char_count, extract_method) — 正文版本化
- doc_chunk:    (doc_id, content_version, seq, header, text, char_count) — 标题感知分块
用法: python kb_migrate_p1.py [分块]
"""
import os
import re
import sys
import sqlite3
import datetime

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "数据", "面试资料总库.db")
NOW = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

DDL = """
CREATE TABLE IF NOT EXISTS doc_content (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES doc(id),
  version INTEGER NOT NULL,
  content TEXT,
  char_count INTEGER,
  extract_method TEXT,
  created_at TEXT,
  UNIQUE(doc_id, version)
);
CREATE INDEX IF NOT EXISTS idx_doc_content_doc ON doc_content(doc_id);

CREATE TABLE IF NOT EXISTS doc_chunk (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES doc(id),
  content_version INTEGER NOT NULL,
  seq INTEGER NOT NULL,
  header TEXT,
  text TEXT NOT NULL,
  char_count INTEGER,
  UNIQUE(doc_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_doc_chunk_doc ON doc_chunk(doc_id);
"""


def chunk_text(text, max_chars=1200, min_chars=300):
    """标题感知分块：按 Markdown 标题层级切块，无标题按段落累积。

    返回 [(header链, 块文本), ...]，header 如 "技术攻坚奖 > 三端短内容推荐优化"
    """
    if not text:
        return []
    chunks = []
    cur_lines = []
    cur_len = 0
    headers = []  # (level, title) 栈

    def flush():
        nonlocal cur_lines, cur_len
        t = "\n".join(cur_lines).strip()
        if t and len(t) >= min_chars * 0.1:  # 至少 30 字
            chunks.append((" > ".join(h[1] for h in headers), t))
        cur_lines = []
        cur_len = 0

    for ln in text.split("\n"):
        s = ln.strip()
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            headers = [h for h in headers if h[0] < level]
            headers.append((level, title))
            continue
        if not s:
            if cur_len > max_chars:
                flush()
            continue
        if len(ln) > max_chars:  # 超长行（无换行的 JSON/HTML）硬切
            flush()
            for i in range(0, len(ln), max_chars):
                piece = ln[i:i + max_chars].strip()
                if piece:
                    chunks.append((" > ".join(h[1] for h in headers), piece))
            continue
        cur_lines.append(ln)
        cur_len += len(ln)
        if cur_len >= max_chars:
            flush()
    flush()
    return chunks


def main():
    conn = sqlite3.connect(DB, timeout=600)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.executescript(DDL)
    conn.commit()

    # 1) 回填 doc_content version=0（当前正文快照）
    c.execute("DELETE FROM doc_content WHERE version=0")
    rows = c.execute("SELECT id, content, extract_method FROM doc WHERE status='ok'").fetchall()
    c.executemany(
        "INSERT INTO doc_content(doc_id, version, content, char_count, extract_method, created_at) "
        "VALUES(?,0,?,?,?,?)",
        [(r["id"], r["content"] or "", len(r["content"] or ""), r["extract_method"] or "", NOW)
         for r in rows])
    conn.commit()
    print("① doc_content v0 快照：%d 篇" % len(rows))

    # 2) 分块
    if len(sys.argv) > 1 and sys.argv[1] == "分块":
        c.execute("DELETE FROM doc_chunk")
        total_chunks = 0
        total_chars = 0
        for r in rows:
            txt = r["content"] or ""
            if len(txt) < 200:  # 太短不分块，直接一块
                chunks = [("", txt)]
            else:
                chunks = chunk_text(txt)
            for seq, (header, t) in enumerate(chunks):
                c.execute(
                    "INSERT INTO doc_chunk(doc_id, content_version, seq, header, text, char_count) "
                    "VALUES(?,0,?,?,?,?)",
                    (r["id"], seq, header, t, len(t)))
            total_chunks += len(chunks)
            total_chars += len(txt)
        conn.commit()
        # 统计
        stat = c.execute("""SELECT COUNT(*) n, AVG(char_count) avgc,
                                  MAX(char_count) maxc, MIN(char_count) minc
                           FROM doc_chunk""").fetchone()
        docs = c.execute("SELECT COUNT(DISTINCT doc_id) FROM doc_chunk").fetchone()[0]
        print("② 分块完成：%d 块 / %d 篇文档 | 平均 %.0f 字, 最大 %d, 最小 %d" % (
            stat["n"], docs, stat["avgc"] or 0, stat["maxc"] or 0, stat["minc"] or 0))
        print("   分块覆盖率: %.0f%%（块字符和 / 全文字符和）" %
              (c.execute("SELECT SUM(char_count) FROM doc_chunk").fetchone()[0] * 100 / max(total_chars, 1)))
        print("   空标题块占比: %.0f%%" %
              (c.execute("SELECT COUNT(*)*100.0/(SELECT COUNT(*) FROM doc_chunk) FROM doc_chunk WHERE header=''").fetchone()[0]))
    else:
        print("② 跳过分块（传「分块」参数执行）")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""求职知识库 · 三层库构建（原始库 → 处理库 → 弹药库）

三层库架构（与 01/02/03 目录分层一一对应）：
- L0 原始库  面试资料总库.db   全量文档抽取原文 + 整篇 FTS（由 kb_ingest.py 维护，本脚本只读）
- L1 处理库  面试处理库.db     按段落/标题切块（chunk），段落级 FTS，为精确定位与后续向量化打底
- L2 弹药库  面试弹药库.db     结构化备战事实（岗位/项目/数字/题库/日志/概念，源自 CSV）
                              + 岗位备战文档全文（03_岗位弹药库 md/txt）+ FTS

用法：
    python3 kb_layers.py 构建 [--库 处理|弹药|全部] [--强制]
    python3 kb_layers.py 统计
    python3 kb_layers.py 查 <关键词> [--库 处理|弹药|全部] [--岗位 X] [--限 N]

设计要点：
- 原始库永远只读；处理库按 (doc_id, mtime) 增量续跑，重复执行只处理新增/变更文档
- 处理库分块：标题感知、目标 ~1500 字/块，块间不重叠；heading 记录所属章节
- 弹药库可反复重建（数据源是 CSV + 03 目录，幂等覆盖）
"""
import argparse
import csv
import os
import re
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from kb_ingest import KB, classify  # noqa: E402

DATA = os.path.join(KB, "_系统_知识库引擎/数据")
RAW_DB = os.path.join(DATA, "面试资料总库.db")
PROC_DB = os.path.join(DATA, "面试处理库.db")
AMMO_DB = os.path.join(DATA, "面试弹药库.db")
AMMO_DIR = os.path.join(KB, "03_岗位弹药库")

CHUNK_TARGET = 1500      # 单块目标字数
CHUNK_MIN = 30           # 低于该字数的块丢弃（除非整篇仅此一块）

NOW = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731


def fts_tokenizer(cur, table, columns):
    """trigram 优先（中文子串检索），不支持时回退 unicode61"""
    for tok in ("trigram", "unicode61"):
        try:
            cur.execute("CREATE VIRTUAL TABLE IF NOT EXISTS %s USING fts5("
                        "%s, tokenize='%s')" % (table, columns, tok))
            return tok
        except Exception:
            continue
    raise RuntimeError("FTS5 不可用")


# ============ L1 处理库 ============
def split_chunks(content):
    """把整篇文本切成 (heading, text) 列表：标题感知 + 目标长度 accumulate"""
    paragraphs, buf, heading = [], [], ""
    for line in content.splitlines():
        if re.match(r"#{1,6}\s", line):
            if buf:
                paragraphs.append((heading, "\n".join(buf)))
                buf = []
            heading = line.lstrip("#").strip()[:80]
            buf.append(line)
        elif not line.strip():
            if buf:
                paragraphs.append((heading, "\n".join(buf)))
                buf = []
        else:
            buf.append(line)
    if buf:
        paragraphs.append((heading, "\n".join(buf)))

    chunks = []
    for heading, para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(para) <= CHUNK_TARGET * 2:
            chunks.append((heading, para))
            continue
        # 超长段落：按句子边界硬切
        piece = ""
        for sent in re.split(r"(?<=[。！？；!?;])\s*", para):
            if piece and len(piece) + len(sent) > CHUNK_TARGET:
                chunks.append((heading, piece.strip()))
                piece = sent
            else:
                piece += sent
        if piece.strip():
            chunks.append((heading, piece.strip()))

    # 合并过短块，保持总量可控
    merged, acc_h, acc = [], "", ""
    for heading, text in chunks:
        if len(acc) + len(text) < CHUNK_TARGET and acc:
            acc += "\n" + text
        else:
            if acc:
                merged.append((acc_h, acc))
            acc_h, acc = heading, text
    if acc:
        merged.append((acc_h, acc))
    return [(h, t) for h, t in merged if len(t) >= CHUNK_MIN] or \
           [(chunks[0][0], chunks[0][1])] if chunks else []


def build_processed(conn_raw, force=False):
    t0 = time.time()
    if force and os.path.exists(PROC_DB):
        os.remove(PROC_DB)
    conn = sqlite3.connect(PROC_DB)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS chunk(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_id INTEGER, rel_path TEXT, category TEXT, title TEXT,
        chunk_no INTEGER, heading TEXT, text TEXT, char_count INTEGER,
        doc_mtime REAL, created_at TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_chunk_doc ON chunk(doc_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_chunk_cat ON chunk(category)")
    # 外部内容 FTS：索引只存分词，正文复用 chunk 表（省约一半空间）；rowid=chunk.id
    for tok in ("trigram", "unicode61"):
        try:
            c.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                rel_path, title, heading, text,
                content='chunk', content_rowid='id', tokenize='%s')""" % tok)
            break
        except Exception:
            continue
    c.execute("""CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)""")

    done = {}
    for r in c.execute("SELECT doc_id, doc_mtime FROM chunk GROUP BY doc_id, doc_mtime"):
        done[r[0]] = r[1]
    rows = conn_raw.execute(
        "SELECT id, rel_path, category, title, content, char_count, mtime "
        "FROM doc WHERE status='ok' ORDER BY id").fetchall()
    print("处理库：待扫 %d 篇 ok 文档" % len(rows))

    stat = dict(docs=0, skipped=0, chunks=0, chars=0)
    next_id = (c.execute("SELECT COALESCE(MAX(id),0) FROM chunk").fetchone()[0] or 0) + 1
    buf, fbuf = [], []
    for doc_id, rel_path, category, title, content, n_chars, mtime in rows:
        stat["docs"] += 1
        if not force and doc_id in done and abs(done[doc_id] - (mtime or 0)) < 1:
            stat["skipped"] += 1
            continue
        # 外部内容 FTS 的删除要先于正文删除（需要回读正文反向解索引）
        c.execute("DELETE FROM chunk_fts WHERE rowid IN (SELECT id FROM chunk WHERE doc_id=?)", (doc_id,))
        c.execute("DELETE FROM chunk WHERE doc_id=?", (doc_id,))
        pieces = split_chunks(content or "")
        if not pieces:
            continue
        for no, (heading, text) in enumerate(pieces, 1):
            buf.append((next_id, doc_id, rel_path, category, title, no, heading, text,
                        len(text), mtime, NOW()))
            fbuf.append((next_id, rel_path, title, heading, text))
            next_id += 1
            stat["chunks"] += 1
            stat["chars"] += len(text)
        if len(buf) >= 4000:
            _flush(c, conn, buf, fbuf)
    _flush(c, conn, buf, fbuf)
    c.execute("INSERT OR REPLACE INTO meta VALUES('built_at',?)", (NOW(),))
    c.execute("INSERT OR REPLACE INTO meta VALUES('source',?)", (RAW_DB,))
    c.execute("INSERT OR REPLACE INTO meta VALUES('chunk_target',?)", (str(CHUNK_TARGET),))
    conn.commit()
    print("处理库完成：扫 %d 篇 / 跳过 %d 篇 / %d 块 / %s 字，用时 %.0fs"
          % (stat["docs"], stat["skipped"], stat["chunks"],
             format(stat["chars"], ","), time.time() - t0))


def _flush(c, conn, buf, fbuf):
    if not buf:
        return
    c.executemany("""INSERT INTO chunk(id,doc_id,rel_path,category,title,chunk_no,
                     heading,text,char_count,doc_mtime,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)""", buf)
    c.executemany("""INSERT INTO chunk_fts(rowid,rel_path,title,heading,text)
                     VALUES(?,?,?,?,?)""", fbuf)
    conn.commit()
    buf.clear()
    fbuf.clear()


# ============ L2 弹药库 ============
CSV_TABLES = [
    ("01_岗位表.csv", "job",
     ["company", "role", "interview_at", "status", "direction", "prep_dir", "resume_ver", "note"],
     ["公司", "岗位", "面试时间", "状态", "主打方向", "备战目录", "简历版本", "备注"]),
    ("02_项目表.csv", "project",
     ["name", "company", "stack", "numbers", "source", "talking_points"],
     ["项目名", "公司", "技术栈", "核心数字", "来源", "可讲要点"]),
    ("03_真实数字表.csv", "number",
     ["value", "metric", "company_project", "source", "ingested_at"],
     ["数字", "口径", "公司/项目", "来源", "入库时间"]),
    ("04_面试日志.csv", "log",
     ["date", "company", "round", "interviewer", "asked", "review", "review_doc"],
     ["日期", "公司", "轮次", "面试官角色", "被问要点", "复盘", "复盘文档"]),
    ("05_面试题索引.csv", "question",
     ["topic", "direction", "company", "answer_loc"],
     ["题目", "方向", "公司", "答案位置"]),
    ("07_概念关系表.csv", "concept",
     ["concept", "ctype", "related", "relation", "numbers", "source_doc"],
     ["概念", "类型", "关联概念", "关系类型", "关联数字", "出处文档"]),
]

COMPANY_SUFFIX = re.compile(r"[-_]?(面试准备|面试.*?\d{4}-\d{2}-\d{2}.*?|准备)$")


def _company_from_dir(name):
    return COMPANY_SUFFIX.sub("", name).strip("-_ ") or name


def _kind_from_subdir(first_dir):
    m = re.match(r"^0(\d)_(.+)", first_dir or "")
    if m:
        return m.group(2)
    return first_dir or "根目录"


def build_ammo(force=False):
    t0 = time.time()
    if force and os.path.exists(AMMO_DB):
        os.remove(AMMO_DB)
    conn = sqlite3.connect(AMMO_DB)
    c = conn.cursor()
    for _, table, cols, _zh in CSV_TABLES:
        c.execute("CREATE TABLE IF NOT EXISTS %s(%s)" % (table, ",".join(cols)))
    c.execute("""CREATE TABLE IF NOT EXISTS ammo_doc(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company TEXT, kind TEXT, rel_path TEXT UNIQUE, title TEXT,
        content TEXT, char_count INTEGER, mtime REAL, updated_at TEXT)""")
    fts_tokenizer(c, "ammo_fts", "company, title, content")

    # --- CSV 结构化事实 ---
    csv_stat = {}
    for fname, table, cols, zh in CSV_TABLES:
        path = os.path.join(DATA, fname)
        n = 0
        if os.path.exists(path):
            with open(path, encoding="utf-8-sig") as f:
                rows = list(csv.reader(f))
            if rows:
                header = rows[0]
                c.execute("DELETE FROM %s" % table)
                for raw in rows[1:]:
                    if not any(x.strip() for x in raw):
                        continue
                    vals = []
                    for i, col in enumerate(cols):
                        idx = header.index(zh[i]) if zh[i] in header and i < len(zh) else i
                        vals.append(raw[idx].strip() if idx < len(raw) else "")
                    c.execute("INSERT INTO %s VALUES(%s)" % (table, ",".join("?" * len(cols))), vals)
                    n += 1
        csv_stat[table] = n

    # --- 岗位备战文档全文 ---
    n_doc, n_upd = 0, 0
    for dirpath, dirnames, filenames in os.walk(AMMO_DIR):
        dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__"}]
        for fn in filenames:
            if not fn.lower().endswith((".md", ".txt")):
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, KB)
            st = os.stat(p)
            parts = os.path.relpath(p, AMMO_DIR).split(os.sep)
            company = _company_from_dir(parts[0]) if len(parts) > 1 else "通用"
            kind = _kind_from_subdir(parts[1]) if len(parts) > 2 else "根目录"
            text = ""
            for enc in ("utf-8", "gb18030", "latin-1"):
                try:
                    text = open(p, encoding=enc).read()
                    break
                except UnicodeDecodeError:
                    continue
            text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
            m = re.search(r"^#\s+(.+)$", text, re.M)
            title = m.group(1).strip() if m else os.path.splitext(fn)[0]
            exist = c.execute("SELECT mtime FROM ammo_doc WHERE rel_path=?", (rel,)).fetchone()
            if exist and abs(exist[0] - st.st_mtime) < 1:
                continue
            c.execute("""INSERT INTO ammo_doc(company,kind,rel_path,title,content,
                         char_count,mtime,updated_at) VALUES(?,?,?,?,?,?,?,?)
                         ON CONFLICT(rel_path) DO UPDATE SET
                         company=excluded.company, kind=excluded.kind, title=excluded.title,
                         content=excluded.content, char_count=excluded.char_count,
                         mtime=excluded.mtime, updated_at=excluded.updated_at""",
                      (company, kind, rel, title, text, len(text), st.st_mtime, NOW()))
            if not exist:
                n_doc += 1
            else:
                n_upd += 1
    conn.commit()
    # FTS 全量对账（文档量 ~百级，简单可靠）
    c.execute("DELETE FROM ammo_fts")
    c.execute("INSERT INTO ammo_fts(rowid,company,title,content) "
              "SELECT id, company, title, content FROM ammo_doc")
    conn.commit()
    print("弹药库完成：CSV %s；备战文档 新%d/更%d，用时 %.1fs"
          % (" ".join("%s=%d" % kv for kv in csv_stat.items()), n_doc, n_upd, time.time() - t0))


# ============ 统计 / 查询 ============
def stats():
    print("=== 三层库总览 ===")
    for label, db in (("L0 原始库", RAW_DB), ("L1 处理库", PROC_DB), ("L2 弹药库", AMMO_DB)):
        ok = os.path.exists(db)
        size = os.path.getsize(db) / 1e6 if ok else 0
        print("  %-8s %-14s %8.1f MB" % (label, os.path.basename(db) + ("" if ok else "（未构建）"), size))
    if os.path.exists(RAW_DB):
        conn = sqlite3.connect("file:%s?mode=ro" % RAW_DB, uri=True)
        n, chars = conn.execute("SELECT COUNT(*), SUM(char_count) FROM doc WHERE status='ok'").fetchone()
        print("    L0: %d 篇 ok / %s 字" % (n, format(chars or 0, ",")))
        conn.close()
    if os.path.exists(PROC_DB):
        conn = sqlite3.connect("file:%s?mode=ro" % PROC_DB, uri=True)
        n, chars = conn.execute("SELECT COUNT(*), SUM(char_count) FROM chunk").fetchone()
        print("    L1: %d 块 / %s 字" % (n, format(chars or 0, ",")))
        for r in conn.execute("SELECT category, COUNT(*) FROM chunk GROUP BY category ORDER BY 2 DESC LIMIT 6"):
            print("        %-8s %6d 块" % r)
        conn.close()
    if os.path.exists(AMMO_DB):
        conn = sqlite3.connect("file:%s?mode=ro" % AMMO_DB, uri=True)
        for table in ("job", "project", "number", "question", "log", "concept"):
            print("    L2 %-8s %4d 行" % (table, conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]))
        print("    L2 备战文档 %d 篇 / %s 字"
              % (conn.execute("SELECT COUNT(*) FROM ammo_doc").fetchone()[0],
                 format(conn.execute("SELECT SUM(char_count) FROM ammo_doc").fetchone()[0] or 0, ",")))
        conn.close()


def _q_proc(kw, limit):
    conn = sqlite3.connect("file:%s?mode=ro" % PROC_DB, uri=True)
    like = "%" + kw + "%"
    rows = []
    # trigram 分词要求 ≥3 字符；更短关键词直接走 LIKE
    if len(kw) >= 3:
        try:
            rows = conn.execute(
                "SELECT c.category, c.rel_path, c.heading, snippet(chunk_fts,3,'[',']','…',12) s "
                "FROM chunk_fts JOIN chunk c ON c.id=chunk_fts.rowid "
                "WHERE chunk_fts MATCH ? ORDER BY rank LIMIT ?", (kw, limit)).fetchall()
        except Exception:
            rows = []
    if not rows:
        rows = conn.execute(
            "SELECT category, rel_path, heading, substr(text,1,160) FROM chunk "
            "WHERE text LIKE ? LIMIT ?", (like, limit)).fetchall()
    for cat, rel, heading, s in rows:
        print("[处理·%s] %s §%s\n    %s\n" % (cat, rel, (heading or "")[:30], s.replace("\n", " ")[:200]))
    print("处理库命中 %d 条" % len(rows))
    conn.close()


def _q_ammo(kw, company, limit):
    conn = sqlite3.connect("file:%s?mode=ro" % AMMO_DB, uri=True)
    like = "%" + kw + "%"
    hits = 0
    for table, cols in (("job", ("company", "role", "direction", "note")),
                        ("number", ("value", "metric", "company_project")),
                        ("question", ("topic", "direction", "company")),
                        ("project", ("name", "company", "stack", "talking_points"))):
        where = " OR ".join("%s LIKE ?" % c for c in cols)
        params = [like] * len(cols)
        if company and "company" in cols:
            where = "(%s) AND company LIKE ?" % where
            params.append("%" + company + "%")
        for r in conn.execute("SELECT * FROM %s WHERE %s LIMIT ?" % (table, where),
                              params + [limit]).fetchall():
            print("[弹药·%s] %s" % (table, " | ".join(str(x) for x in r if x not in (None, ""))[:200]))
            hits += 1
    sql = ("SELECT d.company, d.kind, d.title, snippet(ammo_fts,2,'[',']','…',12) s "
           "FROM ammo_fts JOIN ammo_doc d ON d.id=ammo_fts.rowid "
           "WHERE ammo_fts MATCH ?")
    params = [kw]
    if company:
        sql += " AND d.company LIKE ?"
        params.append("%" + company + "%")
    try:
        docs = []
        if len(kw) >= 3:   # trigram 需要 ≥3 字符，否则直接 LIKE
            docs = conn.execute(sql + " LIMIT ?", params + [limit]).fetchall()
    except Exception:
        docs = []
    if not docs:
        conds, fparams = ["content LIKE ?"], [like]
        if company:
            conds.append("company LIKE ?")
            fparams.append("%" + company + "%")
        docs = conn.execute("SELECT company, kind, title, substr(content,1,160) FROM ammo_doc "
                            "WHERE %s LIMIT ?" % " AND ".join(conds), fparams + [limit]).fetchall()
    for comp, kind, title, s in docs:
        print("[弹药·文档] %s/%s %s\n    %s\n" % (comp, kind, title, s.replace("\n", " ")[:200]))
        hits += 1
    print("弹药库命中 %d 条" % hits)
    conn.close()


def query(kw, layer="全部", company=None, limit=8):
    if layer in ("弹药", "全部"):
        _q_ammo(kw, company, limit)
    if layer in ("处理", "全部"):
        _q_proc(kw, limit)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["构建", "统计", "查"])
    ap.add_argument("kw", nargs="?")
    ap.add_argument("--库", default="全部", choices=["处理", "弹药", "全部"])
    ap.add_argument("--岗位", default=None)
    ap.add_argument("--限", type=int, default=8)
    ap.add_argument("--强制", action="store_true")
    a = ap.parse_args()
    if a.cmd == "统计":
        stats()
    elif a.cmd == "查":
        if not a.kw:
            raise SystemExit("用法：kb_layers.py 查 <关键词>")
        query(a.kw, a.库, a.岗位, a.限)
    else:
        if a.库 in ("弹药", "全部"):
            build_ammo(a.强制)
        if a.库 in ("处理", "全部"):
            conn_raw = sqlite3.connect("file:%s?mode=ro" % RAW_DB, uri=True)
            build_processed(conn_raw, a.强制)
            conn_raw.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将「面试资料总库.db」中 category='工作资料' 的文档，按主题细粒度拆成多个独立库。

工作资料本质 = 腾讯 2019-2020 工作档案。按源目录析出 10 个主题库：
  微视推荐库 / 算法面试库 / 内部资料库 / 技术分享库 / 看点库 /
  十级答辩库 / 图神经网络库 / UGC推荐库 / 无量ModelZoo库 / 腾讯综合工作库
另：50 篇垃圾/错分文档（~$临时文件、.ipynb_checkpoints、误入的 .workbuddy/memory、
错分索引/体检报告/README、06项目代码）从总库删除（不入库）。

纪律：
  - 真库执行前自动备份总库到 数据/_archive/
  - 支持 --selftest：在 /tmp 拷贝上跑通并校验，不碰真库
  - 新库 schema 与总库一致（含 doc_fts 触发器，自动维护 FTS）
  - 保留原始 doc_id / chunk_id
"""
import sqlite3
import sqlite_vec
import os
import sys
import shutil
import datetime

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/求职知识库/_系统_知识库引擎"
MASTER = os.path.join(ROOT, "数据", "面试资料总库.db")
ARCHIVE = os.path.join(ROOT, "数据", "_archive")
OUTDIR = os.path.join(ROOT, "数据")

# ---- 新库 schema（与总库一致） ----
DDL = """
CREATE TABLE IF NOT EXISTS doc(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        path TEXT UNIQUE, rel_path TEXT, category TEXT, ext TEXT,
        size INTEGER, mtime REAL, sha TEXT,
        title TEXT, content TEXT, char_count INTEGER,
        extract_method TEXT, status TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS doc_chunk (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES doc(id),
  content_version INTEGER NOT NULL,
  seq INTEGER NOT NULL,
  header TEXT,
  text TEXT NOT NULL,
  char_count INTEGER,
  UNIQUE(doc_id, seq));
CREATE TABLE IF NOT EXISTS doc_content (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES doc(id),
  version INTEGER NOT NULL,
  content TEXT,
  char_count INTEGER,
  extract_method TEXT,
  created_at TEXT,
  UNIQUE(doc_id, version));
CREATE VIRTUAL TABLE IF NOT EXISTS doc_vector USING vec0(
    chunk_id INTEGER PRIMARY KEY, embedding float[1024]);
CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5(
    rel_path, title, content,
    content='doc', content_rowid='id',
    tokenize='trigram');
CREATE INDEX IF NOT EXISTS idx_doc_cat ON doc(category);
CREATE INDEX IF NOT EXISTS idx_doc_chunk_doc ON doc_chunk(doc_id);
CREATE INDEX IF NOT EXISTS idx_doc_content_doc ON doc_content(doc_id);
CREATE INDEX IF NOT EXISTS idx_doc_status ON doc(status);
CREATE TRIGGER IF NOT EXISTS doc_fts_ad AFTER DELETE ON doc BEGIN
  INSERT INTO doc_fts(doc_fts, rowid, rel_path, title, content) VALUES ('delete', OLD.id, OLD.rel_path, OLD.title, OLD.content);
END;
CREATE TRIGGER IF NOT EXISTS doc_fts_ai AFTER INSERT ON doc BEGIN
  INSERT INTO doc_fts(rowid, rel_path, title, content) VALUES (NEW.id, NEW.rel_path, NEW.title, NEW.content);
END;
CREATE TRIGGER IF NOT EXISTS doc_fts_au AFTER UPDATE OF rel_path, title, content ON doc BEGIN
  INSERT INTO doc_fts(doc_fts, rowid, rel_path, title, content) VALUES ('delete', OLD.id, OLD.rel_path, OLD.title, OLD.content);
  INSERT INTO doc_fts(rowid, rel_path, title, content) VALUES (NEW.id, NEW.rel_path, NEW.title, NEW.content);
END;
"""

# 垃圾/错分判定（这些文档从总库删除，不入库）
JUNK_SQL = """
rel_path LIKE '.workbuddy/%' OR rel_path LIKE '%~$%'
OR rel_path LIKE '%.ipynb_checkpoints%'
OR rel_path LIKE '%00_总索引.md' OR rel_path LIKE '%知识库体检报告_2026-09-09.md'
OR rel_path LIKE '%01_原始资料库/README.md' OR rel_path LIKE '%02_我的笔记/README.md'
OR rel_path LIKE '%06 项目代码%'
"""

# 9 个具名主题（按优先级，非重叠）。腾讯综合工作 = 其余
THEMES = [
    ("微视推荐库", "工作资料_微视", "rel_path LIKE '%工作资料_微视%'"),
    ("算法面试库", "算法面试", "rel_path LIKE '%候选人面试%'"),
    ("内部资料库", "内部资料", "rel_path LIKE '%2020年09月-内部资料%'"),
    ("技术分享库", "技术分享", "rel_path LIKE '%技术分享%'"),
    ("看点库", "看点", "rel_path LIKE '%看点图集%' OR rel_path LIKE '%看点搜索%'"),
    ("十级答辩库", "10级答辩", "rel_path LIKE '%10级答辩%'"),
    ("图神经网络库", "图神经网络", "rel_path LIKE '%图神经网络%'"),
    ("UGC推荐库", "UGC推荐", "rel_path LIKE '%UGC推荐%'"),
    ("无量ModelZoo库", "无量ModelZoo", "rel_path LIKE '%无量ModelZoo%'"),
]
RESIDUAL_NAME = "腾讯综合工作库"


def connect(path):
    c = sqlite3.connect(path)
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    return c


def ensure_schema(conn):
    conn.executescript(DDL)
    conn.commit()


def copy_docs(master, target, ids):
    """把给定 doc id 集合从 master 复制进 target（保留原 id），含 chunk/content/vector。"""
    if not ids:
        return 0
    ph = ",".join("?" * len(ids))
    # doc
    rows = master.execute(f"SELECT id,path,rel_path,category,ext,size,mtime,sha,title,content,char_count,extract_method,status,updated_at FROM doc WHERE id IN ({ph})", ids).fetchall()
    target.executemany("INSERT OR IGNORE INTO doc(id,path,rel_path,category,ext,size,mtime,sha,title,content,char_count,extract_method,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    # doc_chunk
    rows = master.execute(f"SELECT id,doc_id,content_version,seq,header,text,char_count FROM doc_chunk WHERE doc_id IN ({ph})", ids).fetchall()
    target.executemany("INSERT OR IGNORE INTO doc_chunk(id,doc_id,content_version,seq,header,text,char_count) VALUES(?,?,?,?,?,?,?)", rows)
    chunk_ids = [r[0] for r in master.execute(f"SELECT id FROM doc_chunk WHERE doc_id IN ({ph})", ids).fetchall()]
    # doc_content
    rows = master.execute(f"SELECT id,doc_id,version,content,char_count,extract_method,created_at FROM doc_content WHERE doc_id IN ({ph})", ids).fetchall()
    target.executemany("INSERT OR IGNORE INTO doc_content(id,doc_id,version,content,char_count,extract_method,created_at) VALUES(?,?,?,?,?,?,?)", rows)
    # doc_vector (by chunk_id)
    if chunk_ids:
        cph = ",".join("?" * len(chunk_ids))
        rows = master.execute(f"SELECT chunk_id,embedding FROM doc_vector WHERE chunk_id IN ({cph})", chunk_ids).fetchall()
        target.executemany("INSERT OR IGNORE INTO doc_vector(chunk_id,embedding) VALUES(?,?)", rows)
    # 更新 autoincrement 序列
    for tbl in ("doc", "doc_chunk", "doc_content"):
        mx = target.execute(f"SELECT MAX(id) FROM {tbl}").fetchone()[0]
        if mx is not None:
            target.execute("INSERT OR REPLACE INTO sqlite_sequence(name,seq) VALUES(?,?)", (tbl, mx))
    target.commit()
    return len(ids)


def delete_from_master(master, ids):
    if not ids:
        return
    ph = ",".join("?" * len(ids))
    chunk_ids = [r[0] for r in master.execute(f"SELECT id FROM doc_chunk WHERE doc_id IN ({ph})", ids).fetchall()]
    if chunk_ids:
        cph = ",".join("?" * len(chunk_ids))
        master.execute(f"DELETE FROM doc_vector WHERE chunk_id IN ({cph})", chunk_ids)
    master.execute(f"DELETE FROM doc_content WHERE doc_id IN ({ph})", ids)
    master.execute(f"DELETE FROM doc_chunk WHERE doc_id IN ({ph})", ids)
    master.execute(f"DELETE FROM doc WHERE id IN ({ph})", ids)  # 触发器自动维护 doc_fts
    master.commit()


def verify_lib(path, expect_docs):
    c = connect(path)
    doc = c.execute("SELECT count(*) FROM doc").fetchone()[0]
    chunk = c.execute("SELECT count(*) FROM doc_chunk").fetchone()[0]
    vec = c.execute("SELECT count(*) FROM doc_vector").fetchone()[0]
    fts = c.execute("SELECT count(*) FROM doc_fts").fetchone()[0]
    integ = c.execute("PRAGMA integrity_check").fetchone()[0]
    c.close()
    ok = (doc == expect_docs and fts == doc and integ == "ok")
    return doc, chunk, vec, fts, integ, ok


def is_junk(rel):
    import re
    return (rel.startswith(".workbuddy/") or "~$" in rel or ".ipynb_checkpoints" in rel
            or rel.endswith("00_总索引.md") or rel.endswith("知识库体检报告_2026-09-09.md")
            or rel.endswith("01_原始资料库/README.md") or rel.endswith("02_我的笔记/README.md")
            or "06 项目代码" in rel)


def assign_theme(rel):
    """优先级单归属：每篇只归一个主题库。"""
    if "工作资料_微视" in rel: return "微视推荐库"
    if "候选人面试" in rel: return "算法面试库"
    if "10级答辩" in rel: return "十级答辩库"
    if "技术分享" in rel: return "技术分享库"
    if "2020年09月-内部资料" in rel: return "内部资料库"
    if "看点图集" in rel or "看点搜索" in rel: return "看点库"
    if "无量ModelZoo" in rel: return "无量ModelZoo库"
    if "图神经网络" in rel: return "图神经网络库"
    if "UGC推荐" in rel: return "UGC推荐库"
    return RESIDUAL_NAME


def run(master_path, out_dir, archive_dir, do_backup=True, real=False):
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    if do_backup and real:
        os.makedirs(archive_dir, exist_ok=True)
        bak = os.path.join(archive_dir, f"面试资料总库.db.bak-{ts}")
        shutil.copy2(master_path, bak)
        print(f"[backup] {bak}")

    master = connect(master_path)
    rows = master.execute("SELECT id,rel_path FROM doc WHERE category='工作资料'").fetchall()
    all_ids = [r[0] for r in rows]
    junk_ids = set(r[0] for r in rows if is_junk(r[1]))
    print(f"[scan] 工作资料总 {len(all_ids)} 篇；其中垃圾/错分 {len(junk_ids)} 篇")

    # 优先级单归属分组（排除垃圾）
    groups = {name: [] for name, _, _ in THEMES}
    groups[RESIDUAL_NAME] = []
    for id_, rel in rows:
        if id_ in junk_ids:
            continue
        groups[assign_theme(rel)].append(id_)

    summary = []
    total_copied = 0
    for libname, key, where in THEMES + [(RESIDUAL_NAME, "", "")]:
        ids = groups[libname]
        if not ids:
            continue
        path = os.path.join(out_dir, f"{libname}.db")
        t = connect(path)
        ensure_schema(t)
        n = copy_docs(master, t, ids)
        doc, chunk, vec, fts, integ, ok = verify_lib(path, n)
        t.close()
        total_copied += n
        summary.append((libname, n, doc, chunk, vec, fts, integ, ok))
        print(f"  -> {libname:14s} 复制 {n:4d} 篇 | 校验 doc={doc} chunk={chunk} vec={vec} fts={fts} integ={integ} {'OK' if ok else 'FAIL'}")

    print(f"[groups] 复制合计 {total_copied} 篇 + 删除垃圾 {len(junk_ids)} 篇 = {total_copied + len(junk_ids)} (期望 {len(all_ids)})")
    assert total_copied + len(junk_ids) == len(all_ids), "分组总数不等于工作资料总数！"

    if real:
        # 删除全部工作资料（主题库 + 垃圾一并清）
        delete_from_master(master, all_ids)
        print(f"[master] 删除 {len(all_ids)} 篇工作资料（已搬出 + 垃圾）")
        master.execute("VACUUM")
        master.commit()
        # 校验总库
        rem = master.execute("SELECT count(*) FROM doc WHERE category='工作资料'").fetchone()[0]
        mdoc = master.execute("SELECT count(*) FROM doc").fetchone()[0]
        mchunk = master.execute("SELECT count(*) FROM doc_chunk").fetchone()[0]
        mvec = master.execute("SELECT count(*) FROM doc_vector").fetchone()[0]
        mfts = master.execute("SELECT count(*) FROM doc_fts").fetchone()[0]
        minteg = master.execute("PRAGMA integrity_check").fetchone()[0]
        print(f"[master] 剩余 工作资料={rem} | doc={mdoc} chunk={mchunk} vec={mvec} fts={mfts} integ={minteg}")
        # 总库体积
        sz = os.path.getsize(master_path)
        print(f"[master] 体积 {sz/1024/1024:.1f} MB")
    master.close()
    print("[done]")
    return summary


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        tmp = "/tmp/kb_selftest_master.db"
        if os.path.exists(tmp):
            os.remove(tmp)
        shutil.copy2(MASTER, tmp)
        tmpout = "/tmp/kb_selftest_out"
        os.makedirs(tmpout, exist_ok=True)
        print("=== SELFTEST on temp copy ===")
        run(tmp, tmpout, "/tmp/kb_selftest_archive", do_backup=False, real=True)
    else:
        run(MASTER, OUTDIR, ARCHIVE, do_backup=True, real=True)

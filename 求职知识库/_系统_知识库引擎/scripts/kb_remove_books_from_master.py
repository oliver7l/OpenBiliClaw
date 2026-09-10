#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 面试资料总库.db 移除 category ∈ ('书籍','技术书籍') 的 87 篇文档及其全部派生数据：
doc / doc_chunk / doc_content / doc_vector(向量) / doc_fts(外部内容全文索引)。

注意：doc_fts 是 external-content FTS5（content='doc'），删除内容行时必须同步
DELETE FROM doc_fts 清索引，否则查询会撞到已删 rowid 报错。doc_vector 是 vec0，
DELETE 由 sqlite-vec 接管影子表。

安全机制
- 执行前自动备份总库到 data/_archive/（带时间戳）
- 支持 --target 指向副本做验证（默认指向真实总库）
- 默认 检查（只报告），需 执行 才真正删

用法
  python kb_remove_books_from_master.py 检查
  python kb_remove_books_from_master.py 执行 [--target /tmp/copy.db]
"""
import os
import sys
import sqlite3
import sqlite_vec
import shutil

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
SRC = os.path.join(ROOT, "求职知识库/_系统_知识库引擎/数据/面试资料总库.db")
ARCHIVE = os.path.join(ROOT, "data/_archive")
BOOK_CATS = ("书籍", "技术书籍")


def connect(db_path):
    c = sqlite3.connect(db_path)
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    return c


def ids_of(db, which):
    if which == "doc":
        ph = ",".join("?" * len(BOOK_CATS))
        return [r[0] for r in db.execute(
            f"SELECT id FROM doc WHERE category IN ({ph}) ORDER BY id", BOOK_CATS)]
    # chunk
    ph = ",".join("?" * len(BOOK_CATS))
    return [r[0] for r in db.execute(
        f"SELECT id FROM doc_chunk WHERE doc_id IN (SELECT id FROM doc WHERE category IN ({ph}))",
        BOOK_CATS)]


def cmd_check(db):
    doc_ids = ids_of(db, "doc")
    chunk_ids = ids_of(db, "chunk")
    ph = ",".join("?" * len(doc_ids))
    cph = ",".join("?" * len(chunk_ids))
    print("=== 将从 总库 移除的数据 ===")
    print(f"  doc(书籍)        : {len(doc_ids)}")
    print(f"  doc_chunk        : {len(chunk_ids)}")
    print(f"  doc_content      : {db.execute(f'SELECT count(*) FROM doc_content WHERE doc_id IN ({ph})', doc_ids).fetchone()[0]}")
    print(f"  doc_vector(向量) : {db.execute(f'SELECT count(*) FROM doc_vector WHERE chunk_id IN ({cph})', chunk_ids).fetchone()[0]}")
    print(f"  doc_fts 索引行   : {db.execute(f'SELECT count(*) FROM doc_fts WHERE rowid IN ({ph})', doc_ids).fetchone()[0]}")
    tot_doc = db.execute("SELECT count(*) FROM doc").fetchone()[0]
    tot_chunk = db.execute("SELECT count(*) FROM doc_chunk").fetchone()[0]
    print(f"\n  移除后 总库 将剩: doc={tot_doc-len(doc_ids)} | chunk={tot_chunk-len(chunk_ids)}")
    print(f"  当前总库大小: {os.path.getsize(SRC)/1024/1024:.1f} MB")


def cmd_run(target):
    db_path = target or SRC
    print(f"[目标] {db_path}")
    if db_path == SRC:
        bak = os.path.join(ARCHIVE, f"面试资料总库_移除书籍前_{__import__('time').strftime('%Y%m%d_%H%M%S')}.db")
        os.makedirs(ARCHIVE, exist_ok=True)
        print(f"[备份] {shutil.copy2(SRC, bak)}")
    db = connect(db_path)
    doc_ids = ids_of(db, "doc")
    chunk_ids = ids_of(db, "chunk")
    ph = ",".join("?" * len(doc_ids))
    cph = ",".join("?" * len(chunk_ids))
    assert doc_ids, "未找到书籍，终止"

    before = dict(
        doc=db.execute("SELECT count(*) FROM doc").fetchone()[0],
        chunk=db.execute("SELECT count(*) FROM doc_chunk").fetchone()[0],
        vec=db.execute("SELECT count(*) FROM doc_vector").fetchone()[0],
        fts=db.execute("SELECT count(*) FROM doc_fts").fetchone()[0],
    )

    # 删除顺序：先子后父（vector/content/chunk/doc）
    # ⚠️ doc_fts 是外部内容 FTS5：不能手动 DELETE 索引行（会与后续 doc 删除冲突报
    # "database disk image is malformed"）。正确做法：删完 doc 后对整个 doc_fts 执行
    # rebuild，从剩余 doc 重建索引、自动丢弃已删书籍的条目。
    db.execute(f"DELETE FROM doc_vector WHERE chunk_id IN ({cph})", chunk_ids)
    db.execute(f"DELETE FROM doc_content WHERE doc_id IN ({ph})", doc_ids)
    db.execute(f"DELETE FROM doc_chunk WHERE doc_id IN ({ph})", doc_ids)
    db.execute(f"DELETE FROM doc WHERE id IN ({ph})", doc_ids)
    db.commit()
    db.execute("INSERT INTO doc_fts(doc_fts) VALUES('rebuild')")
    db.commit()

    after = dict(
        doc=db.execute("SELECT count(*) FROM doc").fetchone()[0],
        chunk=db.execute("SELECT count(*) FROM doc_chunk").fetchone()[0],
        vec=db.execute("SELECT count(*) FROM doc_vector").fetchone()[0],
        fts=db.execute("SELECT count(*) FROM doc_fts").fetchone()[0],
    )
    print("\n=== 执行结果（前 → 后）===")
    print(f"  doc   : {before['doc']} → {after['doc']}  {'OK' if before['doc']-after['doc']==len(doc_ids) else 'CHECK'}")
    print(f"  chunk : {before['chunk']} → {after['chunk']}  {'OK' if before['chunk']-after['chunk']==len(chunk_ids) else 'CHECK'}")
    print(f"  vec   : {before['vec']} → {after['vec']}  {'OK' if before['vec']-after['vec']==len(chunk_ids) else 'CHECK'}")
    print(f"  fts   : {before['fts']} → {after['fts']}  (rebuild 后变化，正常)")
    print(f"  integrity: {db.execute('PRAGMA integrity_check').fetchone()[0]}")
    # 残留核查
    rem = db.execute(f"SELECT count(*) FROM doc WHERE category IN ({','.join('?'*len(BOOK_CATS))})", BOOK_CATS).fetchone()[0]
    print(f"  残留书籍: {rem}（应为 0）")
    # 抽样 FTS 查询仍可用
    smoke = db.execute("SELECT count(*) FROM doc_fts WHERE doc_fts MATCH ?", ("推荐系统",)).fetchone()[0]
    print(f"  FTS 冒烟 '推荐系统' 命中: {smoke} 篇（应不含书籍，且 >0）")
    if db_path == SRC:
        print(f"\n[完成] 总库已移除书籍；新大小 {os.path.getsize(SRC)/1024/1024:.1f} MB；备份: {bak}")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("检查", "执行"):
        print(__doc__); sys.exit(1)
    if sys.argv[1] == "检查":
        cmd_check(connect(SRC))
    else:
        tgt = None
        if "--target" in sys.argv:
            tgt = sys.argv[sys.argv.index("--target") + 1]
        cmd_run(tgt)


if __name__ == "__main__":
    main()

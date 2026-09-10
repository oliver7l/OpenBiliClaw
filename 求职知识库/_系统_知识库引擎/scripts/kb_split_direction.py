#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 面试资料总库.db 里 category = '方向知识库' 的文档，连同其
doc_chunk / doc_content / doc_vector(向量) / doc_fts(全文索引) 完整复制进独立的 方向知识库.db。

设计原则（同 kb_split_books.py）
- 只增不删：源库（面试资料总库.db）只读 ATTACH，绝不修改；新库为独立新建文件。
- 保留原始 id：doc / doc_chunk / doc_vector 的 id 全部原样复制，FK 关联不重排。
- 自包含：新库重建 doc_fts（外部内容 FTS，rebuild 自 doc）与 doc_vector（vec0 自动建影子表），
  所以方向知识库可独立做全文检索 + 语义向量检索。
- 不复制全局运维表（ingest_log / processed_notes / unprocessed）。

用法
  python kb_split_direction.py 检查            # 统计将复制哪些数据（dry-run）
  python kb_split_direction.py 执行 [--force]  # 真正生成 方向知识库.db
"""
import os
import sys
import sqlite3
import sqlite_vec

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
SRC = os.path.join(ROOT, "求职知识库/_系统_知识库引擎/数据/面试资料总库.db")
TGT = os.path.join(ROOT, "求职知识库/_系统_知识库引擎/数据/方向知识库.db")
DIRECTION_CATS = ("方向知识库",)
CONTENT_TABLES = ("doc", "doc_chunk", "doc_content")
VEC_TABLE = "doc_vector"
FTS_TABLE = "doc_fts"


def connect(db_path, read_only=False):
    if read_only:
        uri = f"file:{db_path}?mode=ro"
        c = sqlite3.connect(uri, uri=True)
    else:
        c = sqlite3.connect(db_path)
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    return c


def cat_ids(src):
    ph = ",".join("?" * len(DIRECTION_CATS))
    return [r[0] for r in src.execute(
        f"SELECT id FROM doc WHERE category IN ({ph}) ORDER BY id", DIRECTION_CATS)]


def cat_chunk_ids(src, ids):
    ph = ",".join("?" * len(ids))
    return [r[0] for r in src.execute(
        f"SELECT id FROM doc_chunk WHERE doc_id IN ({ph})", ids)]


def cmd_check(src):
    ids = cat_ids(src)
    cids = cat_chunk_ids(src, ids)
    cph = ",".join("?" * len(cids))
    ph = ",".join("?" * len(ids))
    doc_n = len(ids)
    chunk_n = len(cids)
    content_n = src.execute(
        f"SELECT count(*) FROM doc_content WHERE doc_id IN ({ph})", ids).fetchone()[0]
    vec_n = src.execute(
        f"SELECT count(*) FROM doc_vector WHERE chunk_id IN ({cph})", cids).fetchone()[0]
    char_n = src.execute(
        f"SELECT coalesce(sum(char_count),0) FROM doc WHERE id IN ({ph})", ids).fetchone()[0]
    print("=== 检查：将复制到 方向知识库.db 的数据 ===")
    print(f"  doc(方向知识库)  : {doc_n}")
    print(f"  doc_chunk        : {chunk_n}")
    print(f"  doc_content      : {content_n}")
    print(f"  doc_vector(向量) : {vec_n}")
    print(f"  抽取文本合计     : {char_n:,} 字")
    tot_doc = src.execute("SELECT count(*) FROM doc").fetchone()[0]
    tot_chunk = src.execute("SELECT count(*) FROM doc_chunk").fetchone()[0]
    print(f"\n  占 总库 比例: doc {doc_n/tot_doc*100:.1f}% | chunk {chunk_n/tot_chunk*100:.1f}%")
    print(f"  目标库: {TGT}")


def cmd_run(force):
    if os.path.exists(TGT):
        if not force:
            print(f"[中止] 目标已存在: {TGT}\n      加 --force 覆盖重建，或先手动改名。")
            return
        print(f"[warn] --force：删除已存在的目标库并重建")
        os.remove(TGT)

    src = connect(SRC, read_only=True)
    tgt = connect(TGT)
    tgt.execute("ATTACH DATABASE ? AS src", (SRC,))

    ids = cat_ids(src)
    cids = cat_chunk_ids(src, ids)
    assert ids, "未找到方向知识库文档，终止"
    ph = ",".join("?" * len(ids))
    cph = ",".join("?" * len(cids))

    # 1) 建 schema
    for name in CONTENT_TABLES + (VEC_TABLE, FTS_TABLE):
        sql = src.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
        tgt.execute(sql)
    print("[1] schema 已建 (doc / doc_chunk / doc_content / doc_vector / doc_fts)")

    # 2) 复制内容表（保留原始 id）
    tgt.execute(f"INSERT INTO doc SELECT * FROM src.doc WHERE id IN ({ph})", ids)
    tgt.execute(f"INSERT INTO doc_chunk SELECT * FROM src.doc_chunk WHERE doc_id IN ({ph})", ids)
    tgt.execute(f"INSERT INTO doc_content SELECT * FROM src.doc_content WHERE doc_id IN ({ph})", ids)
    print(f"[2] 内容复制: doc={len(ids)} doc_chunk={len(cids)} "
          f"doc_content={tgt.execute('SELECT count(*) FROM doc_content').fetchone()[0]}")

    # 3) 复制向量
    rows = tgt.execute(
        f"SELECT chunk_id, embedding FROM src.doc_vector WHERE chunk_id IN ({cph})", cids).fetchall()
    tgt.executemany("INSERT INTO doc_vector(chunk_id, embedding) VALUES (?,?)", rows)
    print(f"[3] 向量复制: {len(rows)} 条")

    # 4) 重建全文索引
    tgt.execute(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('rebuild')")
    print("[4] doc_fts 全文索引已重建")

    # 5) 修正 sqlite_sequence
    for t in CONTENT_TABLES:
        mx = tgt.execute(f"SELECT max(id) FROM {t}").fetchone()[0]
        if mx:
            tgt.execute("INSERT OR REPLACE INTO sqlite_sequence(name, seq) VALUES (?,?)", (t, mx))
    tgt.commit()

    # 6) 校验
    ok_doc = tgt.execute("SELECT count(*) FROM doc").fetchone()[0]
    ok_chunk = tgt.execute("SELECT count(*) FROM doc_chunk").fetchone()[0]
    ok_vec = tgt.execute("SELECT count(*) FROM doc_vector").fetchone()[0]
    src_doc = len(ids)
    src_chunk = len(cids)
    src_vec = tgt.execute(f"SELECT count(*) FROM src.doc_vector WHERE chunk_id IN ({cph})", cids).fetchone()[0]
    print("\n=== 校验（源 vs 目标）===")
    print(f"  doc      : {src_doc} -> {ok_doc}  {'OK' if src_doc==ok_doc else 'MISMATCH'}")
    print(f"  doc_chunk: {src_chunk} -> {ok_chunk}  {'OK' if src_chunk==ok_chunk else 'MISMATCH'}")
    print(f"  doc_vector: {src_vec} -> {ok_vec}  {'OK' if src_vec==ok_vec else 'MISMATCH'}")

    # 7) 语义 + 全文 冒烟测试
    import urllib.request, json
    q = "推荐系统 工程实践 算法 召回 排序"
    qv = json.loads(urllib.request.urlopen(urllib.request.Request(
        "http://127.0.0.1:11434/api/embed",
        data=json.dumps({"model": "bge-m3", "input": q}).encode(),
        headers={"Content-Type": "application/json"})).read())["embeddings"][0]
    knn = tgt.execute("SELECT chunk_id, distance FROM doc_vector WHERE embedding MATCH ? ORDER BY distance LIMIT 3",
                      (sqlite_vec.serialize_float32(qv),)).fetchall()
    fts = tgt.execute("SELECT count(*) FROM doc_fts WHERE doc_fts MATCH ?", ("推荐系统",)).fetchone()[0]
    print("\n=== 冒烟测试（方向知识库独立检索）===")
    print(f"  语义 KNN Top3: {knn}")
    print(f"  全文 FTS '推荐系统' 命中: {fts} 篇")
    print(f"\n[完成] 方向知识库已生成: {TGT}  ({os.path.getsize(TGT)/1024/1024:.1f} MB)")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("检查", "执行"):
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "检查":
        src = connect(SRC, read_only=True)
        cmd_check(src)
    else:
        cmd_run(force=("--force" in sys.argv))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""P2 hybrid 检索：FTS5 关键词 + bge-m3 向量 + RRF 融合

用法: python kb_search_hybrid.py "自然语言问题" [--topk 10] [--hybrid 20]
输出: chunk 级结果（标题链 + 片段 + 分数），按 RRF 融合排序
"""
import os
import sys
import json
import sqlite3
import urllib.request
import sqlite_vec

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "数据", "面试资料总库.db")
OLLAMA = "http://localhost:11434/api/embed"
MODEL = "bge-m3"
K = 10          # 向量 top-k
FTS_K = 20      # 关键词 top-k
RRF_K = 60      # RRF 常数


def embed_one(text):
    req = urllib.request.Request(
        OLLAMA, data=json.dumps({"model": MODEL, "input": [text[:1500]]}).encode(),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=180).read())["embeddings"][0]


def rrf_merge(fts_hits, vec_hits, k=RRF_K):
    scores = {}
    for rank, chunk_id in enumerate(fts_hits):
        scores[chunk_id] = scores.get(chunk_id, 0) + 1.0 / (k + rank + 1)
    for rank, chunk_id in enumerate(vec_hits):
        scores[chunk_id] = scores.get(chunk_id, 0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: -x[1])


def main():
    q = sys.argv[1] if len(sys.argv) > 1 else ""
    if not q:
        print("用法: python kb_search_hybrid.py \"问题\" [--topk N]")
        return
    topk = K
    if "--topk" in sys.argv:
        topk = int(sys.argv[sys.argv.index("--topk") + 1])

    c = sqlite3.connect(DB, timeout=300)
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    c.enable_load_extension(False)
    c.row_factory = sqlite3.Row

    # 1) FTS 关键词召回（doc 级 → 块级）
    fts_chunks = []
    try:
        docs = c.execute(
            "SELECT d.id FROM doc_fts JOIN doc d ON d.id=doc_fts.rowid "
            "WHERE doc_fts MATCH ? AND d.status='ok' LIMIT ?", (q, FTS_K)).fetchall()
        doc_ids = [r["id"] for r in docs]
        if doc_ids:
            qm = ",".join("?" * len(doc_ids))
            fts_chunks = [r["id"] for r in c.execute(
                "SELECT id FROM doc_chunk WHERE doc_id IN (%s) AND text LIKE ? "
                "ORDER BY char_count DESC LIMIT ?" % qm,
                doc_ids + ["%" + q + "%", FTS_K])]
    except Exception:
        pass

    # 2) 向量召回（bge-m3 query embedding → doc_vector top-k）
    vec_chunks = []
    try:
        qv = embed_one(q)
        rows = c.execute(
            "SELECT chunk_id FROM doc_vector WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (sqlite_vec.serialize_float32(qv), topk)).fetchall()
        vec_chunks = [r["chunk_id"] for r in rows]
    except Exception as e:
        print("向量召回失败:", e)

    # 3) RRF 融合
    merged = rrf_merge(fts_chunks, vec_chunks)
    print("FTS 命中 %d 块 | 向量命中 %d 块 | RRF 融合 %d\n" % (
        len(fts_chunks), len(vec_chunks), len(merged)))
    for chunk_id, score in merged[:topk]:
        r = c.execute("""SELECT ch.header, ch.text, d.rel_path, d.category
                         FROM doc_chunk ch JOIN doc d ON d.id=ch.doc_id WHERE ch.id=?""",
                      (chunk_id,)).fetchone()
        if not r:
            continue
        head = " > ".join((r["header"] or "").split(" > ")[-2:]) if r["header"] else "(无标题)"
        print("[%.3f] %s" % (score, r["rel_path"]))
        print("     ↳ %s" % head[-50:])
        print("       %s" % (r["text"] or "")[:160].replace("\n", " "))
        print()


if __name__ == "__main__":
    main()

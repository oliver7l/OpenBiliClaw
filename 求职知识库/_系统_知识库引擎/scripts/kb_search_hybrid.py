#!/usr/bin/env python3
"""P2 hybrid 检索：FTS5 关键词 + bge-m3 向量 + RRF 融合

用法: python kb_search_hybrid.py "自然语言问题" [--topk 10] [--hybrid 20]
输出: chunk 级结果（标题链 + 片段 + 分数），按 RRF 融合排序
"""
import os
import sys
import re
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


# trigram 分词下，整句 MATCH 等于要求整句连续出现 → 恒 0 命中。
# 须把问题切成"词元"再 OR：中文滑窗取 3-gram，英文/数字取整词。
STOP = set("的了是有哪些什么怎么做和与或在及我你他这那吧呢啊请帮我讲下")


def query_terms(q):
    lat = re.findall(r"[A-Za-z][A-Za-z0-9_+#.\-]*", q)
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]+", q))
    cjk = "".join(ch for ch in cjk if ch not in STOP)
    grams = []
    if len(cjk) <= 3:
        grams.append(cjk)
    else:
        grams.extend(cjk[i:i + 3] for i in range(len(cjk) - 2))
    # 去重保序
    seen, out = set(), []
    for t in lat + grams:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def cjk_clean(q):
    return "".join(ch for ch in "".join(re.findall(r"[\u4e00-\u9fff]+", q))
                   if ch not in STOP)


def score_chunk(text, q_clean, terms):
    """块级打分 = 最长连续匹配×100 + 命中词元数
    只看"命中几个词元"会让简历（恰好也写了多目标排序）压过加工过的知识，
    所以先比"能连着匹配多长"，越长说明越贴题。"""
    if not text:
        return 0
    best = 0
    n = len(q_clean)
    for L in range(n, 3, -1):
        if any(q_clean[i:i + L] in text for i in range(n - L + 1)):
            best = L
            break
    return best * 100 + sum(1 for t in terms if t in text)


# 来源权重：加工过的知识优先于原始资料（简历/原始 dump 常含同名词但无信息量）
SOURCE_W = (("02_方向知识库", 1.6), ("03_岗位弹药库", 1.4), ("01_原始资料库", 0.8))


def src_weight(rel_path):
    for pre, w in SOURCE_W:
        if rel_path.startswith(pre):
            return w
    return 1.0


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
    terms = query_terms(q)
    try:
        # 词元 OR：bm25 会让"命中词元多"的文档自然排前
        fts_q = " OR ".join('"%s"' % t for t in terms[:40])
        docs = c.execute(
            "SELECT d.id FROM doc_fts JOIN doc d ON d.id=doc_fts.rowid "
            "WHERE doc_fts MATCH ? AND d.status='ok' "
            "ORDER BY bm25(doc_fts, 10.0, 5.0, 1.0) LIMIT ?", (fts_q, FTS_K)).fetchall()
        doc_ids = [r["id"] for r in docs]
        if doc_ids:
            qm = ",".join("?" * len(doc_ids))
            rows = c.execute(
                """SELECT ch.id, ch.text, d.rel_path FROM doc_chunk ch
                   JOIN doc d ON d.id=ch.doc_id
                   WHERE ch.doc_id IN (%s) AND ch.char_count>=60""" % qm, doc_ids).fetchall()
            qc = cjk_clean(q)
            scored = sorted(((score_chunk(r["text"], qc, terms) * src_weight(r["rel_path"]),
                              r["id"]) for r in rows), key=lambda x: (-x[0], x[1]))
            fts_chunks = [cid for s, cid in scored if s > 0][:FTS_K]
    except Exception as e:
        print("FTS 召回失败:", e)

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

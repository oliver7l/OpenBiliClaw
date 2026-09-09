#!/usr/bin/env python3
"""P2 向量检索：bge-m3(Ollama) + sqlite-vec

- doc_vector 表（vec0 虚拟表）：chunk_id -> embedding float[1024]
- 断点续跑：只 embed 缺失的块
用法：
  python kb_embed.py          # 增量补缺（默认）
  python kb_embed.py 全部     # 全量重建
"""
import os
import sys
import json
import time
import sqlite3
import urllib.request

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "数据", "面试资料总库.db")
OLLAMA = "http://localhost:11434/api/embed"
MODEL = "bge-m3"
BATCH = 16
MAX_CHARS = 1500   # 超长截断
MIN_CHARS = 60     # 碎块不 embed

import sqlite_vec


def conn():
    c = sqlite3.connect(DB, timeout=600)
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    c.enable_load_extension(False)
    return c


def embed_batch(msgs):
    req = urllib.request.Request(
        OLLAMA, data=json.dumps({"model": MODEL, "input": msgs}).encode(),
        headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=180).read())["embeddings"]
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(2 + attempt * 3)


def main():
    c = conn()
    c.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS doc_vector USING vec0(
        chunk_id INTEGER PRIMARY KEY, embedding float[1024])""")
    c.commit()

    full = len(sys.argv) > 1 and sys.argv[1] == "全部"
    if full:
        c.execute("DROP TABLE IF EXISTS doc_vector")
        c.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS doc_vector USING vec0(
            chunk_id INTEGER PRIMARY KEY, embedding float[1024])""")
        c.commit()

    # 待 embed 块：doc_chunk 中不在 doc_vector 的（断点续跑）
    sql = """SELECT ch.id, ch.text FROM doc_chunk ch
             WHERE ch.char_count >= ? AND ch.id NOT IN (SELECT chunk_id FROM doc_vector)
             ORDER BY ch.id"""
    rows = c.execute(sql, (MIN_CHARS,)).fetchall()
    if not rows:
        n = c.execute("SELECT COUNT(*) FROM doc_vector").fetchone()[0]
        print("无待处理块，doc_vector 已有 %d 条" % n)
        return
    print("待 embed %d 块（batch=%d, 预计 %.0f 分钟）" % (
        len(rows), BATCH, len(rows) / BATCH * 0.26 / 60))

    t0 = time.time()
    done = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        msgs = [(r[1] or "")[:MAX_CHARS] for r in batch]
        try:
            vecs = embed_batch(msgs)
        except Exception as e:
            print("\n❌ batch@%d 失败: %s（进度已保存，重跑续传）" % (i, e))
            break
        for (chunk_id, _), v in zip(batch, vecs):
            c.execute("INSERT OR IGNORE INTO doc_vector(chunk_id, embedding) VALUES(?,?)",
                      (chunk_id, sqlite_vec.serialize_float32(v)))
        done += len(batch)
        if done % 512 < BATCH:
            c.commit()
            el = time.time() - t0
            print("  %5d/%d 块  %.0fs  (%.1f/s)" % (done, len(rows), el, done / max(el, 1)), flush=True)
    c.commit()
    n = c.execute("SELECT COUNT(*) FROM doc_vector").fetchone()[0]
    print("\n✅ doc_vector：%d 条，耗时 %.0fs" % (n, time.time() - t0))


if __name__ == "__main__":
    main()

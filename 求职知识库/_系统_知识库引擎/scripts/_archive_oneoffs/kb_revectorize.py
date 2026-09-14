# -*- coding: utf-8 -*-
"""
kb_revectorize.py —— 重向量化 面试资料总库.db 的 doc_chunk -> doc_vector

doc_vector 是 vec0 虚拟表（embedding float[1024]，配 bge-m3 的 1024 维）。
本机之前没装 sqlite-vec，导致该表无法加载（no such module: vec0），向量检索坏掉。
本脚本把每个 doc_chunk 的文本用本地 Ollama 的 bge-m3 嵌入，写入 doc_vector，
使语义检索可被上层（未来 wiki_builder / 检索功能）使用。

依赖：项目 .venv 已装 sqlite-vec；Ollama 已拉 bge-m3:latest。
注意：必须用项目 .venv 的 python 运行（3.11），否则找不到 sqlite_vec。

用法：
  .venv/bin/python kb_revectorize.py 检查            # 当前 doc_vector 行数 / doc_chunk 总数
  .venv/bin/python kb_revectorize.py 预览 [--limit N] # 列出待向量化块
  .venv/bin/python kb_revectorize.py 执行 [--limit N] [--doc ID] [--batch B] [--no-backup]
"""
import os
import sys
import json
import time
import shutil
import sqlite3
import argparse
import urllib.request
import sqlite_vec

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
DB = os.path.join(ROOT, "求职知识库", "_系统_知识库引擎", "数据", "面试资料总库.db")
OLLAMA = "http://127.0.0.1:11434/api/embed"
MODEL = "bge-m3"
DIM = 1024
CALL_TIMEOUT = 120


def embed(texts):
    """texts: list[str] -> list[list[float]]（bge-m3 1024 维）。"""
    payload = json.dumps({"model": MODEL, "input": texts}).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=CALL_TIMEOUT) as r:
        d = json.loads(r.read().decode("utf-8"))
    return d["embeddings"]


def db():
    c = sqlite3.connect(DB)
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["检查", "预览", "执行"])
    ap.add_argument("--limit", type=int, default=0, help="限制处理块数（0=不限制，缺省补全部缺口）")
    ap.add_argument("--doc", type=int, default=0)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--all", action="store_true", help="强制重向量化全部（先 DELETE 现有向量）")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    c = db()
    if args.cmd == "检查":
        try:
            n = c.execute("SELECT count(*) FROM doc_vector").fetchone()[0]
            print(f"doc_vector 当前行数: {n}")
        except Exception as e:
            print("doc_vector 查询失败（sqlite-vec 未装？）:", e)
        print("doc_chunk 总行数:", c.execute("SELECT count(*) FROM doc_chunk").fetchone()[0])
        return

    # 默认只补「缺失」的块（doc_vector 里没有的），幂等、可重复跑、不触发 UNIQUE 冲突。
    # --all 才强制重向量化全部（会先 DELETE 现有向量）。
    missing_only = not args.all
    if args.doc:
        base = "SELECT id, text FROM doc_chunk WHERE doc_id=? AND length(text)>20"
        params = [args.doc]
    else:
        base = "SELECT id, text FROM doc_chunk WHERE length(text)>20"
        params = []
    if missing_only:
        base += " AND id NOT IN (SELECT chunk_id FROM doc_vector)"
    base += " ORDER BY id"
    if args.limit and not args.all:
        base += " LIMIT ?"
        params.append(args.limit)
    rows = c.execute(base, params).fetchall()
    print(f"待向量化(缺失缺口): {len(rows)} 块  [missing_only={missing_only}]")

    if args.cmd == "预览":
        for cid, txt in rows[:5]:
            print(f"  [{cid}] {txt[:40]!r}")
        return

    # 执行
    if args.all:
        print("强制模式：先清空 doc_vector ...")
        c.execute("DELETE FROM doc_vector")
        c.commit()
    if not args.no_backup:
        bak = DB + f".revbak_{time.strftime('%Y%m%d_%H%M%S')}"
        print("备份:", shutil.copy2(DB, bak))
    ok = fail = 0
    for i in range(0, len(rows), args.batch):
        batch = rows[i:i + args.batch]
        try:
            vecs = embed([t for _, t in batch])
        except Exception as e:
            print("  embed 失败:", e)
            fail += len(batch)
            continue
        for (cid, _), v in zip(batch, vecs):
            try:
                c.execute(
                    "INSERT INTO doc_vector(chunk_id, embedding) VALUES (?,?)",
                    (cid, sqlite_vec.serialize_float32(v)))
                ok += 1
            except Exception as e:
                print(f"  insert 失败 [{cid}]:", e)
                fail += 1
        c.commit()
        print(f"  …已处理 {min(i + args.batch, len(rows))}/{len(rows)} (ok={ok} fail={fail})")
    print(f"\n完成: ok={ok} fail={fail}")

    # 验证 KNN
    print("=== KNN 自检 ===")
    q = rows[0][1][:200]
    qv = embed([q])[0]
    res = c.execute(
        "SELECT chunk_id, distance FROM doc_vector WHERE embedding MATCH ? ORDER BY distance LIMIT 3",
        (sqlite_vec.serialize_float32(qv),)).fetchall()
    print(f"query: {q[:40]!r} -> top3: {res}")


if __name__ == "__main__":
    main()

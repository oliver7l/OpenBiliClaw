#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 面试资料总库.db 移除 category='方向知识库' 的文档（已复制到独立 方向知识库.db）。

顺序（同 kb_remove_books_from_master.py，关键坑已踩过）：
  - 执行前自动备份总库到 data/_archive/
  - doc_vector -> doc_content -> doc_chunk -> doc（按 id 删除）
  - 不手动删 FTS 行，删完 doc 后对 doc_fts 整体 rebuild（外部内容 FTS5：手动删 FTS 行再删 doc 会 malformed）
  - VACUUM 回收空闲页
"""
import argparse
import os
import shutil
import sqlite3
import sqlite_vec
import time

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
SRC = os.path.join(ROOT, "求职知识库/_系统_知识库引擎/数据/面试资料总库.db")
ARCHIVE = os.path.join(ROOT, "data/_archive")
CATS = ("方向知识库",)
CAT_PH = ",".join("?" * len(CATS))   # 动态占位符，兼容单/多类别


def connect(db):
    c = sqlite3.connect(db)
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["检查", "执行"])
    ap.add_argument("--target", default=SRC)
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    if args.mode == "检查":
        c = connect(args.target)
        n = c.execute(f"SELECT count(*) FROM doc WHERE category IN ({CAT_PH})", CATS).fetchone()[0]
        ch = c.execute(
            f"SELECT count(*) FROM doc_chunk WHERE doc_id IN (SELECT id FROM doc WHERE category IN ({CAT_PH}))",
            CATS).fetchone()[0]
        print(f"将移除: doc={n} / chunk={ch}")
        return

    db = args.target
    if db == SRC and not args.no_backup:
        os.makedirs(ARCHIVE, exist_ok=True)
        bak = os.path.join(ARCHIVE, f"面试资料总库_移除方向知识库前_{time.strftime('%Y%m%d_%H%M%S')}.db")
        print("备份:", shutil.copy2(db, bak), f"({os.path.getsize(bak)/1024/1024:.1f}MB)")

    c = connect(db)
    doc_ids = [r[0] for r in c.execute(f"SELECT id FROM doc WHERE category IN ({CAT_PH})", CATS)]
    chunk_ids = [r[0] for r in c.execute(
        f"SELECT id FROM doc_chunk WHERE doc_id IN (SELECT id FROM doc WHERE category IN ({CAT_PH}))", CATS)]
    ph = ",".join("?" * len(doc_ids))
    cph = ",".join("?" * len(chunk_ids))
    before = {
        "doc": c.execute("SELECT count(*) FROM doc").fetchone()[0],
        "chunk": c.execute("SELECT count(*) FROM doc_chunk").fetchone()[0],
        "vec": c.execute("SELECT count(*) FROM doc_vector").fetchone()[0],
        "方向知识库": c.execute(f"SELECT count(*) FROM doc WHERE category IN ({CAT_PH})", CATS).fetchone()[0],
    }

    c.execute(f"DELETE FROM doc_vector WHERE chunk_id IN ({cph})", chunk_ids)
    c.execute(f"DELETE FROM doc_content WHERE doc_id IN ({ph})", doc_ids)
    c.execute(f"DELETE FROM doc_chunk WHERE doc_id IN ({ph})", doc_ids)
    c.execute(f"DELETE FROM doc WHERE id IN ({ph})", doc_ids)
    c.commit()
    print(f"  [OK] 移除 方向知识库 doc={len(doc_ids)} / chunk={len(chunk_ids)}")

    c.execute("INSERT INTO doc_fts(doc_fts) VALUES('rebuild')")
    c.commit()
    print("  [OK] doc_fts rebuild 完成")

    after = {
        "doc": c.execute("SELECT count(*) FROM doc").fetchone()[0],
        "chunk": c.execute("SELECT count(*) FROM doc_chunk").fetchone()[0],
        "vec": c.execute("SELECT count(*) FROM doc_vector").fetchone()[0],
        "方向知识库": c.execute(f"SELECT count(*) FROM doc WHERE category IN ({CAT_PH})", CATS).fetchone()[0],
    }
    print("\n=== 结果（前 → 后）===")
    for k in before:
        print(f"  {k:8s}: {before[k]} → {after[k]}  (减 {before[k]-after[k]})  {'OK' if (k!='方向知识库' and before[k]-after[k]==(len(doc_ids) if k=='doc' else len(chunk_ids))) or (k=='方向知识库' and after[k]==0) else ''}")
    print("  integrity:", c.execute("PRAGMA integrity_check").fetchone()[0])

    c.execute("VACUUM")
    print("  VACUUM 完成, integrity:", c.execute("PRAGMA integrity_check").fetchone()[0])
    print(f"  总库文件大小: {os.path.getsize(db)/1024/1024:.1f} MB")


if __name__ == "__main__":
    main()

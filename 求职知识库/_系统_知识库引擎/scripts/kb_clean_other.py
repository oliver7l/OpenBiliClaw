#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理 面试资料总库.db 中 category='其他' 的脏数据。

背景：移除书籍后，'其他'类 7 篇却占 9.7% 的 chunk —— 其中 1 篇
「个人生活对话导出.json」(id=2426, 2455 chunks, 5.2MB) 是隐私+无关的脏数据，
其余 6 篇是 README/索引/体检报告，应归位到 '工作资料'。

策略（遵守"先备份、后删除、不手动删 FTS 行"）：
  - 对真库执行前自动备份到 data/_archive/
  - 移出 2426：doc_vector -> doc_content -> doc_chunk -> doc（均按 id），
    最后对 doc_fts 整体 rebuild（外部内容 FTS5 已知坑：手动删 FTS 行再删 doc 会 malformed）
  - 归位 6 篇：UPDATE category='工作资料'（仅改标签，保留内容）
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

REMOVE_ID = 2426                      # 个人生活对话导出.json（脏数据）
RECLASSIFY_IDS = [1297, 2469, 583, 582, 1307, 569]  # 归位到 工作资料


def connect(db, read_only=False):
    c = sqlite3.connect(db)
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    c.row_factory = None
    return c


def check(c):
    rows = c.execute(
        "SELECT id, title, ext, count(dc.id) FROM doc d LEFT JOIN doc_chunk dc "
        "ON dc.doc_id=d.id WHERE d.category='其他' GROUP BY d.id ORDER BY 4 DESC"
    ).fetchall()
    print("=== '其他'类当前 7 篇 ===")
    for did, title, ext, ch in rows:
        tag = "移除" if did == REMOVE_ID else "归位->工作资料"
        print(f"  id={did:5d} ch={ch:5d} ext={str(ext):5s} {tag}  {title[:40]}")
    n_other = c.execute("SELECT count(*) FROM doc WHERE category='其他'").fetchone()[0]
    n_chunk = c.execute(
        "SELECT count(*) FROM doc_chunk WHERE doc_id IN (SELECT id FROM doc WHERE category='其他')"
    ).fetchone()[0]
    print(f"\n合计: '其他'类 doc={n_other} / chunk={n_chunk}")
    print(f"计划: 移除 1 篇(id={REMOVE_ID}) + 归位 {len(RECLASSIFY_IDS)} 篇 -> '工作资料'")


def apply(db, do_backup=True):
    if do_backup:
        os.makedirs(ARCHIVE, exist_ok=True)
        bak = os.path.join(ARCHIVE, f"面试资料总库_清理其他前_{time.strftime('%Y%m%d_%H%M%S')}.db")
        print("备份:", shutil.copy2(db, bak), f"({os.path.getsize(bak)/1024/1024:.1f}MB)")
    c = connect(db)

    before = {
        "doc": c.execute("SELECT count(*) FROM doc").fetchone()[0],
        "chunk": c.execute("SELECT count(*) FROM doc_chunk").fetchone()[0],
        "vec": c.execute("SELECT count(*) FROM doc_vector").fetchone()[0],
        "其他": c.execute("SELECT count(*) FROM doc WHERE category='其他'").fetchone()[0],
    }

    # 1) 移出脏数据（按正确顺序，不手动删 FTS 行）
    chunk_ids = [r[0] for r in c.execute("SELECT id FROM doc_chunk WHERE doc_id=?", (REMOVE_ID,))]
    cph = ",".join("?" * len(chunk_ids))
    c.execute(f"DELETE FROM doc_vector WHERE chunk_id IN ({cph})", chunk_ids)
    c.execute("DELETE FROM doc_content WHERE doc_id=?", (REMOVE_ID,))
    c.execute("DELETE FROM doc_chunk WHERE doc_id=?", (REMOVE_ID,))
    c.execute("DELETE FROM doc WHERE id=?", (REMOVE_ID,))
    c.commit()
    print(f"  [OK] 移除 id={REMOVE_ID} 及其 {len(chunk_ids)} 个 chunk")

    # 2) 归位其余 6 篇
    ph = ",".join("?" * len(RECLASSIFY_IDS))
    c.execute(f"UPDATE doc SET category='工作资料' WHERE id IN ({ph})", RECLASSIFY_IDS)
    c.commit()
    print(f"  [OK] 归位 {len(RECLASSIFY_IDS)} 篇 -> '工作资料'")

    # 3) 重建外部内容 FTS（自动丢弃已删文档的索引行）
    c.execute("INSERT INTO doc_fts(doc_fts) VALUES('rebuild')")
    c.commit()
    print("  [OK] doc_fts rebuild 完成")

    after = {
        "doc": c.execute("SELECT count(*) FROM doc").fetchone()[0],
        "chunk": c.execute("SELECT count(*) FROM doc_chunk").fetchone()[0],
        "vec": c.execute("SELECT count(*) FROM doc_vector").fetchone()[0],
        "其他": c.execute("SELECT count(*) FROM doc WHERE category='其他'").fetchone()[0],
    }
    print("\n=== 执行结果（前 → 后）===")
    for k in before:
        delta = before[k] - after[k]
        print(f"  {k:6s}: {before[k]} → {after[k]}  (减 {delta})  {'OK' if (k in ('doc','chunk','vec') and delta==1) or (k=='其他' and after[k]==0) else ''}")
    print("  integrity:", c.execute("PRAGMA integrity_check").fetchone()[0])
    # FTS 冒烟
    smoke = c.execute("SELECT count(*) FROM doc_fts WHERE doc_fts MATCH ?", ("推荐系统",)).fetchone()[0]
    print("  FTS '推荐系统' 命中:", smoke, "(应>0)")


def main():
    ap = argparse.ArgumentParser(description="清理 其他类 脏数据")
    ap.add_argument("mode", choices=["检查", "执行"], help="检查=预览；执行=带备份实际清理")
    ap.add_argument("--target", default=SRC, help="目标库（默认总库；可传副本路径试跑）")
    ap.add_argument("--no-backup", action="store_true", help="执行时不自动备份（试跑副本用）")
    args = ap.parse_args()

    if args.mode == "检查":
        check(connect(args.target))
    else:
        if args.target == SRC and not args.no_backup:
            apply(args.target, do_backup=True)
        else:
            apply(args.target, do_backup=not args.no_backup)


if __name__ == "__main__":
    main()

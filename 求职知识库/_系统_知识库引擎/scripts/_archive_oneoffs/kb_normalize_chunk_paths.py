# -*- coding: utf-8 -*-
"""
kb_normalize_chunk_paths.py —— 归一化 面试处理库.chunk.rel_path 路径前缀

问题：面试处理库.chunk 的 1909 个 rel_path 中，1310 个用旧约定（缺 `03_工作资料/` 层，
或落在别的目录编号下），与磁盘 / 面试资料总库.doc 当前约定不一致，导致搜索结果指向不存在的路径。
这些不是数据丢失，而是路径指针过期。

做法：对每个孤儿 rel_path，按 精确 -> 插入 03_工作资料 -> basename 唯一 -> basename 多命中择优
解析到磁盘真实文件，把 chunk.rel_path 修正为磁盘真实相对路径，并重建 chunk_fts。

用法：
  python kb_normalize_chunk_paths.py            # dry-run（只统计，不改库）
  python kb_normalize_chunk_paths.py --apply    # 执行（先自动备份到 _archive）
"""
import os
import sys
import shutil
import sqlite3
import argparse
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.abspath(os.path.join(HERE, "..", ".."))          # 求职知识库根
DATA = os.path.join(HERE, "..", "数据")
PROC_DB = os.path.join(DATA, "面试处理库.db")
RAW_DB = os.path.join(DATA, "面试资料总库.db")
ARCHIVE = os.path.abspath(os.path.join(KB, "..", "data", "_archive"))
PREFIX = "01_原始资料库/"


def build_disk_index():
    bn_map = {}
    all_disk = set()
    for root, _, files in os.walk(KB):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), KB)
            all_disk.add(rel)
            bn_map.setdefault(f, []).append(rel)
    return all_disk, bn_map


def resolve(rp, all_disk, bn_map):
    # 1 精确
    if rp in all_disk:
        return rp, "exact"
    # 2 插入 03_工作资料（在 01_原始资料库/ 之后）
    if rp.startswith(PREFIX) and "03_工作资料" not in rp:
        cand = PREFIX + "03_工作资料/" + rp[len(PREFIX):]
        if cand in all_disk:
            return cand, "insert03"
    # 3 basename 唯一
    bn = os.path.basename(rp)
    if bn in bn_map:
        matches = bn_map[bn]
        if len(matches) == 1:
            return matches[0], "basename1"
        if len(matches) > 1:
            best = max(matches, key=lambda m: len(os.path.commonprefix([m, rp])))
            return best, "basenameN"
    return None, "unresolved"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="执行修改（默认 dry-run）")
    args = ap.parse_args()

    all_disk, bn_map = build_disk_index()
    raw = sqlite3.connect(RAW_DB)
    doc_paths = set(r[0] for r in raw.execute("select rel_path from doc"))
    proc = sqlite3.connect(PROC_DB)
    chunk_paths = [r[0] for r in proc.execute("select distinct rel_path from chunk")]
    orphan = [p for p in chunk_paths if p not in doc_paths]
    print(f"chunk distinct rel_paths: {len(chunk_paths)}  孤儿(不在总库): {len(orphan)}")
    print(f"磁盘文件数: {len(all_disk)}  总库 doc 数: {len(doc_paths)}")

    plan = {}     # old -> (new, how)
    stats = {}
    for rp in orphan:
        new, how = resolve(rp, all_disk, bn_map)
        stats[how] = stats.get(how, 0) + 1
        if new and new != rp:
            plan[rp] = (new, how)

    print("\n解析结果:")
    for k in ("exact", "insert03", "basename1", "basenameN", "unresolved"):
        if k in stats:
            print(f"  {k:12s} {stats[k]}")
    resolved = sum(stats.get(k, 0) for k in ("exact", "insert03", "basename1", "basenameN"))
    print(f"\n可解析: {resolved}  不可解析(无对应文件): {stats.get('unresolved', 0)}")

    hit_doc = sum(1 for _, (new, _) in plan.items() if new in doc_paths)
    print(f"解析后能与 总库.doc 对齐: {hit_doc}/{len(plan)}")

    if not args.apply:
        print("\n[dry-run] 未修改任何数据。加 --apply 执行。")
        print("例证:")
        n = 0
        for old, (new, how) in plan.items():
            if how == "insert03" and n < 3:
                print(f"  {how}  {old[:60]} -> {new[:60]}"); n += 1
        return

    # ---- 执行 ----
    os.makedirs(ARCHIVE, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = os.path.join(ARCHIVE, "面试处理库_归一化前_" + stamp + ".db")
    shutil.copy2(PROC_DB, bak)
    print(f"\n[apply] 已备份: {bak}")

    for old, (new, _) in plan.items():
        proc.execute("update chunk set rel_path=? where rel_path=?", (new, old))
    proc.commit()

    # chunk_fts 是外部内容 FTS5 表(content='chunk')，不能用 DELETE；
    # 用 'rebuild' 命令从 chunk 表重建索引（已更新的 rel_path 会被重新索引）
    proc.execute("INSERT INTO chunk_fts(chunk_fts) VALUES('rebuild')")
    proc.commit()
    print(f"[apply] 已更新 chunk.rel_path: {len(plan)} 个不同值；已重建 chunk_fts")

    chunk_paths2 = [r[0] for r in proc.execute("select distinct rel_path from chunk")]
    orphan2 = [p for p in chunk_paths2 if p not in doc_paths]
    print(f"[apply] 复核: 孤儿 {len(orphan)} -> {len(orphan2)}")
    proc.close()
    raw.close()
    print("[apply] 完成。")


if __name__ == "__main__":
    main()

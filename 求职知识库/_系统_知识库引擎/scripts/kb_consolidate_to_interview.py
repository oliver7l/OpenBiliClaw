# -*- coding: utf-8 -*-
"""
kb_consolidate_to_interview.py
把求职知识库离线侧的结构化面试数据合并进主程序的 data/interview.db。

原则：
  1. 原始数据（面试资料总库.db）保持不变，不在本脚本范围内。
  2. 只【新增表】到 data/interview.db；绝不改动它已有的应用表
     （interview_questions / interview_reviews / kb_documents 及其 *_fts）。
     若目标已存在同名表则跳过，避免覆盖主程序数据。
  3. FTS5 表通过「建虚拟表 → 灌内容表 → rebuild」方式迁移，保证搜索可用。
  4. --apply 前自动把目标库备份到 data/_archive/，可随时回滚。

数据源（只读，不被修改）：
  - 求职知识库/_系统_知识库引擎/数据/面试弹药库.db
  - 求职知识库/_系统_知识库引擎/数据/幻灯片笔记.db

用法：
  python kb_consolidate_to_interview.py              # dry-run：仅统计待处理表
  python kb_consolidate_to_interview.py --apply       # 执行（先自动备份）
  python kb_consolidate_to_interview.py --apply --target /path/to/copy.db  # 对副本试跑
"""
import os
import re
import shutil
import sqlite3
import argparse
import datetime

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
TGT_DEFAULT = os.path.join(ROOT, "data", "interview.db")
ARCHIVE = os.path.join(ROOT, "data", "_archive")
SOURCES = [
    ("面试弹药库", os.path.join(ROOT, "求职知识库/_系统_知识库引擎/数据/面试弹药库.db")),
    ("幻灯片笔记", os.path.join(ROOT, "求职知识库/_系统_知识库引擎/数据/幻灯片笔记.db")),
]

# 主程序已有、绝不可碰的应用表（仅作二次保险）
PROTECTED = {
    "interview_questions", "interview_reviews", "kb_documents",
    "interview_questions_fts", "interview_reviews_fts", "kb_documents_fts",
}


def is_fts(sql):
    return bool(sql) and "fts5" in sql.lower()


def copy_source(tgt, src_path, label, apply):
    sp = os.path.abspath(src_path)
    tgt.execute("ATTACH DATABASE ? AS src", (sp,))
    rows = tgt.execute(
        "select name, sql from src.sqlite_master "
        "where type='table' and name not like 'sqlite_%' order by name"
    ).fetchall()

    plan = []
    for name, sql in rows:
        if name in PROTECTED:
            plan.append((name, "skip(protected)"))
            continue
        exists = tgt.execute("select 1 from sqlite_master where name=?", (name,)).fetchone()
        if exists:
            plan.append((name, "skip(exists)"))
            continue
        plan.append((name, "fts" if is_fts(sql) else "table"))

    if not apply:
        n = len([p for p in plan if not p[1].startswith("skip")])
        print(f"\n[{label}] 待处理 {n} 张表：")
        for n_, how in plan:
            print(f"   {how:14s} {n_}")
        tgt.execute("DETACH DATABASE src")
        return

    for name, sql in rows:
        if name in PROTECTED:
            continue
        exists = tgt.execute("select 1 from sqlite_master where name=?", (name,)).fetchone()
        if exists:
            continue
        if is_fts(sql):
            tgt.execute(sql)  # 建虚拟表 + 影子表
            m = re.search(r"content='([^']+)'", sql or "")
            if m:
                cname = m.group(1)
                tgt.execute(f"INSERT INTO {cname} SELECT * FROM src.{cname}")
                tgt.execute(f"INSERT INTO {name}({name}) VALUES('rebuild')")
                try:
                    tgt.execute(
                        f"INSERT OR IGNORE INTO {name}_config SELECT * FROM src.{name}_config"
                    )
                except Exception:
                    pass
            else:
                cols = [c[1] for c in tgt.execute(f"PRAGMA table_info({name})").fetchall()
                        if c[1] not in ("rowid",)]
                cl = ",".join(cols)
                tgt.execute(f"INSERT INTO {name}({cl}) SELECT {cl} FROM src.{name}")
        else:
            tgt.execute(sql)
            tgt.execute(f"INSERT INTO {name} SELECT * FROM src.{name}")
    tgt.commit()
    tgt.execute("DETACH DATABASE src")


def verify(tgt):
    print("\n=== 校验：源 vs 目标 行数 ===")
    ok = True
    for label, src in SOURCES:
        s = sqlite3.connect(src)
        for (name,) in s.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
        ):
            sc = s.execute(f"select count(*) from \"{name}\"").fetchone()[0]
            try:
                tc = tgt.execute(f"select count(*) from \"{name}\"").fetchone()[0]
            except Exception:
                tc = -1
            flag = "OK" if tc == sc else "MISMATCH"
            if tc != sc:
                ok = False
            print(f"  [{label}] {name:24s} src={sc:5d} tgt={tc:5d}  {flag}")
        s.close()
    return ok


def fts_smoke(tgt):
    print("\n=== FTS 冒烟测试（面试弹药库 ammo_fts / 幻灯片 slide_fts）===")
    for tbl, q in [("ammo_fts", "推荐"), ("slide_fts", "算法")]:
        try:
            n = tgt.execute(
                f"select count(*) from {tbl} where {tbl} match ?", (q,)
            ).fetchone()[0]
            print(f"  {tbl} 搜索 '{q}': {n} 条")
        except Exception as e:
            print(f"  {tbl} 搜索失败: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="执行修改（默认 dry-run）")
    ap.add_argument("--target", default=TGT_DEFAULT, help="目标库路径（试跑可用副本）")
    args = ap.parse_args()

    tgt_path = os.path.abspath(args.target)
    if not os.path.exists(tgt_path):
        print(f"[错误] 目标库不存在: {tgt_path}")
        return

    tgt = sqlite3.connect(tgt_path)
    if args.apply:
        if tgt_path == os.path.abspath(TGT_DEFAULT):
            os.makedirs(ARCHIVE, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            bak = os.path.join(ARCHIVE, f"interview_合并前_{stamp}.db")
            shutil.copy2(tgt_path, bak)
            print("已备份:", bak)
        else:
            print(f"[试跑] 目标为副本: {tgt_path}（不另备份）")

    for label, src in SOURCES:
        if not os.path.exists(src):
            print(f"[{label}] 源不存在: {src}")
            continue
        copy_source(tgt, src, label, args.apply)

    if args.apply:
        ok = verify(tgt)
        fts_smoke(tgt)
        print("\n[apply] integrity:", tgt.execute("PRAGMA integrity_check").fetchone()[0])
        print("[apply] 全部一致:" , ok)
    else:
        print("\n[dry-run] 未修改数据。加 --apply 执行。")
    tgt.close()


if __name__ == "__main__":
    main()

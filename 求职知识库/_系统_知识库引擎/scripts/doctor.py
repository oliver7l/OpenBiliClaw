#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
doctor.py — 求职知识库健康检查
借鉴 second-brain(zhiwehu) 的 doctor.sh：自动检查系统一致性，可 --fix 修复。
检查项:
  C1 题索引引用:    05_面试题索引.csv 的"答案位置"指向的文件是否存在
  C2 岗位目录:      01_岗位表.csv 的"备战目录"在 03_岗位弹药库 下是否存在
  C3 日志岗位对齐:  04_面试日志.csv 的公司是否都登记在 01_岗位表
  C4 数字表完整:    03_真实数字表.csv 每行是否数字/口径/来源齐全
  C5 索引新鲜度:    数据/file_index.db 记录数与实际文件数是否匹配(仅 --full)
用法:
  python3 scripts/doctor.py            # 常规检查
  python3 scripts/doctor.py --fix      # 检查 + 修复可自动修复项(重建索引)
  python3 scripts/doctor.py --full     # 含全库文件数核对(较慢)
"""
import csv, os, sqlite3, subprocess, sys

# 知识库根目录：由脚本位置推导（scripts/ → _系统_知识库引擎 → 根），便携可迁移
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE = os.path.join(ROOT, "_系统_知识库引擎")
DATA = os.path.join(ENGINE, "数据")
JOB_ROOT = os.path.join(ROOT, "03_岗位弹药库")

def read_csv(name):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f))

def main():
    fix = "--fix" in sys.argv
    full = "--full" in sys.argv
    issues = []

    # C1 题索引引用
    rows = read_csv("05_面试题索引.csv")
    for i, r in enumerate(rows, start=2):
        loc = (r.get("答案位置") or "").strip()
        if not loc:
            issues.append(f"C1 第{i}行 [题索引] 答案位置为空: {r.get('题目','')[:30]}")
            continue
        target = loc if os.path.isabs(loc) else os.path.join(ROOT, loc)
        if not os.path.exists(target):
            issues.append(f"C1 第{i}行 [题索引] 答案位置不存在: {loc}")

    # C2 岗位目录
    rows = read_csv("01_岗位表.csv")
    job_names = []
    for i, r in enumerate(rows, start=2):
        name = (r.get("公司") or "").strip()
        if name:
            job_names.append(name)
        d = (r.get("备战目录") or "").strip()
        if not d:
            issues.append(f"C2 第{i}行 [岗位表] 备战目录为空: {name}")
            continue
        target = d if os.path.isabs(d) else os.path.join(ROOT, d)
        if not os.path.isdir(target):
            issues.append(f"C2 第{i}行 [岗位表] 备战目录不存在: {d}")

    # C3 日志岗位对齐
    rows = read_csv("04_面试日志.csv")
    for i, r in enumerate(rows, start=2):
        c = (r.get("公司") or "").strip()
        if c and c not in job_names:
            issues.append(f"C3 第{i}行 [面试日志] 公司未登记在岗位表: {c}")

    # C4 数字表完整
    rows = read_csv("03_真实数字表.csv")
    for i, r in enumerate(rows, start=2):
        if not (r.get("数字") or "").strip():
            issues.append(f"C4 第{i}行 [数字表] 数字为空")
        if not (r.get("口径") or "").strip():
            issues.append(f"C4 第{i}行 [数字表] 口径为空: {r.get('数字','')}")
        if not (r.get("来源") or "").strip():
            issues.append(f"C4 第{i}行 [数字表] 来源为空: {r.get('数字','')}")

    # C5 索引新鲜度 (仅 --full)
    if full:
        db = os.path.join(DATA, "file_index.db")
        db_count = 0
        if os.path.exists(db):
            conn = sqlite3.connect(db)
            try:
                db_count = conn.execute("SELECT COUNT(*) FROM file_index").fetchone()[0]
            except sqlite3.Error:
                pass
            conn.close()
        def _is_junk(fn):
            return fn.startswith("._") or fn == ".DS_Store" or fn.startswith("~$")
        actual = 0
        for layer in ["01_原始资料库", "02_方向知识库", "03_岗位弹药库"]:
            base = os.path.join(ROOT, layer)
            if os.path.isdir(base):
                for root, dirs, files in os.walk(base):
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    actual += sum(1 for fn in files if not _is_junk(fn))
        if db_count != actual:
            issues.append(f"C5 [索引新鲜度] file_index.db={db_count} 实际文件={actual}，需重建索引(--fix)")

    # 报告
    if issues:
        print(f"[FAIL] 发现 {len(issues)} 个问题:")
        for x in issues:
            print("  - " + x)
        if fix:
            print("\n正在修复(--fix)...")
            subprocess.run([sys.executable, os.path.join(ENGINE, "scripts", "build_index.py")], cwd=ENGINE)
            print("已重建全库索引，请重跑 doctor.py 复核。")
        sys.exit(1)
    else:
        print("[PASS] 全部检查通过 ✅")
        print("  C1 题索引引用 OK / C2 岗位目录 OK / C3 日志对齐 OK / C4 数字表 OK")
        if full:
            print("  C5 索引新鲜度 OK")

if __name__ == "__main__":
    main()

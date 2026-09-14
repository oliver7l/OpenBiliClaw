#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工作资料正文导出器：把总库中指定目录的正文导出成单个 md，供加工/阅读。

用法：
  python3 kb_export.py 导出 <目录关键词> <输出文件> [--每篇上限 N] [--只含 关键词]
  python3 kb_export.py 导出 微视 /tmp/ws.md --每篇上限 60000
  python3 kb_export.py 清单 <目录关键词>          # 只看文件清单与字数，不导出
  python3 kb_export.py 读 <文件路径关键词> [字数]  # 打印单篇正文
"""
import os
import re
import sqlite3
import sys

KB = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/求职知识库"
DB = os.path.join(KB, "_系统_知识库引擎/数据/面试资料总库.db")

# 低价值噪声：模型 dump、训练数据、题库、日志
NOISE = re.compile(
    r"(xgboost_model|num_part|df_test|df_train|\.csv$|leetcode|ProblemsList|"
    r"basic-data-structure|maximum-sum|ones-and-zeros|LICENSE|test\.txt|"
    r"\.ipynb_checkpoints|node_modules|/target/|/build/|awesome-material|"
    r"牛客网|简历制作|GitHub入门|Shell脚本攻略|黑客与画家)",
    re.I,
)


def query(kw, exclude_noise=True, only=None, min_chars=200):
    c = sqlite3.connect(DB, timeout=120)
    c.row_factory = sqlite3.Row
    sql = ("SELECT rel_path, char_count, ext, content FROM doc "
           "WHERE status='ok' AND rel_path LIKE ?")
    rows = [r for r in c.execute(sql, ("%" + kw + "%",)).fetchall()]
    if exclude_noise:
        rows = [r for r in rows if not NOISE.search(r["rel_path"])]
    if only:
        rows = [r for r in rows if re.search(only, r["rel_path"], re.I)]
    rows = [r for r in rows if r["char_count"] >= min_chars]
    rows.sort(key=lambda r: -r["char_count"])
    return rows


def cmd_list(kw, only=None):
    rows = query(kw, only=only)
    total = sum(r["char_count"] for r in rows)
    print("匹配 %d 篇，共 %.1f 万字\n" % (len(rows), total / 10000))
    for r in rows:
        print("%8.1f万  %-5s  %s" % (r["char_count"] / 10000, r["ext"],
                                     r["rel_path"].replace("01_原始资料库/工作资料_", "")))
    return rows


def cmd_export(kw, out, per=60000, only=None, cap=4000000):
    rows = query(kw, only=only)
    buf = []
    used = 0
    for r in rows:
        txt = (r["content"] or "").strip()
        if not txt:
            continue
        if len(txt) > per:
            txt = txt[:per] + "\n\n…（原文共 %d 字，此处截断）" % len(txt)
        buf.append("\n\n" + "=" * 78 + "\n【文件】%s\n【字数】%d\n" % (
            r["rel_path"].replace("01_原始资料库/工作资料_", ""), r["char_count"]) + "=" * 78 + "\n\n" + txt)
        used += len(txt)
        if used > cap:
            buf.append("\n\n…（已达导出上限 %d 万字，剩余 %d 篇未导出）" % (cap / 10000, len(rows)))
            break
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("".join(buf))
    print("导出 %s：%.1f 万字（%d 篇）" % (out, used / 10000, len(buf)))


def cmd_read(kw, n=8000):
    rows = query(kw, min_chars=1)
    if not rows:
        print("未找到：", kw)
        return
    r = rows[0]
    print("【%s】%d 字\n%s\n" % (r["rel_path"], r["char_count"], "-" * 60))
    print((r["content"] or "")[:n])


if __name__ == "__main__":
    a = sys.argv
    if len(a) < 3:
        print(__doc__)
        sys.exit(1)
    if a[1] == "清单":
        cmd_list(a[2], a[3] if len(a) > 3 else None)
    elif a[1] == "导出":
        per = 60000
        only = None
        for i, x in enumerate(a):
            if x == "--每篇上限" and i + 1 < len(a):
                per = int(a[i + 1])
            if x == "--只含" and i + 1 < len(a):
                only = a[i + 1]
        cmd_export(a[2], a[3], per=per, only=only)
    elif a[1] == "读":
        cmd_read(a[2], int(a[3]) if len(a) > 3 else 8000)

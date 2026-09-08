#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
求职知识库统一检索命令 kb.py
用法：
  python3 kb.py 查 <关键词>         全文检索（02/03 + 腾讯文档资料/解码文本），输出命中文件+关键行
  python3 kb.py 岗位 <公司>         查看某公司备战包路径与要点
  python3 kb.py 数字 <关键词>       查真实数字（权威表，只出真实口径）
  python3 kb.py 速记 <公司>         一键生成该公司面试速记卡
  python3 kb.py 方向 <方向>         列出某方向全部方法论文档
  python3 kb.py 索引 [关键词]       全库文件索引查询（knowledge.db，可加 --层 01/02/03）
  python3 kb.py 项目 [关键词]       列出全部项目（或按关键词过滤）
  python3 kb.py 全部                查看系统当前登记的全部岗位/项目/数字
  python3 kb.py 记录 <公司> <轮次> <被问要点>   追加一条面试日志
"""
import csv, os, re, sys, glob, sqlite3

# 知识库根目录：由脚本位置推导（scripts/ → _系统_知识库引擎 → 根），便携可迁移
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE = os.path.join(ROOT, "_系统_知识库引擎")
DATA = os.path.join(ENGINE, "数据")

SEARCH_DIRS = [
    os.path.join(ROOT, "02_方向知识库"),
    os.path.join(ROOT, "03_岗位弹药库"),
    os.path.join(ROOT, "01_原始资料库/腾讯文档资料"),
    os.path.join(ROOT, "01_原始资料库/解码文本"),
]
MAX_FILE = 2 * 1024 * 1024  # 只搜 2MB 以内文本
EXTS = (".md", ".txt")

def read_csv(name):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f))

def find_jobs(keyword=None):
    rows = read_csv("01_岗位表.csv")
    if keyword:
        rows = [r for r in rows if keyword in r["公司"] or keyword in r["岗位"] or keyword in r["主打方向"]]
    return rows

def search_text(keyword):
    hits = []
    pat = re.compile(re.escape(keyword), re.IGNORECASE)
    for d in SEARCH_DIRS:
        if not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            for fn in files:
                if not fn.lower().endswith(EXTS):
                    continue
                fp = os.path.join(root, fn)
                try:
                    if os.path.getsize(fp) > MAX_FILE:
                        continue
                    with open(fp, encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                except Exception:
                    continue
                for i, line in enumerate(lines):
                    if pat.search(line):
                        snippet = line.strip()
                        if len(snippet) > 90:
                            snippet = snippet[:90] + "…"
                        rel = fp.replace(ROOT + "/", "")
                        hits.append((rel, i + 1, snippet))
                        break  # 每文件只报第一个命中行
    return hits

def show_jobs(keyword=None):
    rows = find_jobs(keyword)
    if not rows:
        print("未找到匹配岗位")
        return
    print(f"\n{'公司':<12}{'岗位':<24}{'面试时间':<12}{'状态':<6} 主打方向")
    print("-" * 80)
    for r in rows:
        print(f"{r['公司']:<12}{r['岗位']:<24}{r['面试时间']:<12}{r['状态']:<6} {r['主打方向']}")
        print(f"  目录: {r['备战目录']}   简历: {r['简历版本']}")
        if r.get('备注'):
            print(f"  备注: {r['备注']}")

def show_numbers(keyword):
    rows = read_csv("03_真实数字表.csv")
    if keyword:
        rows = [r for r in rows if keyword in r["数字"] or keyword in r["口径"] or keyword in r["公司/项目"] or keyword in r["来源"]]
    if not rows:
        print(f"真实数字表中未找到: {keyword}")
        return
    print(f"\n『真实数字表』命中 {len(rows)} 条 (口径以表格为准,严禁编造):")
    for r in rows:
        print(f"  {r['数字']:<12} {r['口径']:<14} {r['公司/项目']:<30} 来源:{r['来源']}")

def show_projects(keyword=None):
    rows = read_csv("02_项目表.csv")
    if keyword:
        rows = [r for r in rows if keyword in r["项目名"] or keyword in r["公司"] or keyword in r["可讲要点"]]
    if not rows:
        print("未找到匹配项目")
        return
    print(f"\n项目库 ({len(rows)} 个):")
    for r in rows:
        print(f"  · {r['项目名']} [{r['公司']}] 核心数字: {r['核心数字']}")

def gen_card(company):
    jobs = find_jobs(company)
    if not jobs:
        print(f"岗位表中未找到: {company}")
        return
    j = jobs[0]
    print("=" * 70)
    print(f"速记卡 · {j['公司']} · {j['岗位']}  (面试 {j['面试时间']} · {j['状态']})")
    print("=" * 70)
    print(f"\n【一句话定位】主打方向: {j['主打方向']}")
    print(f"备战目录: {j['备战目录']}")
    if j.get('备注'):
        print(f"备注: {j['备注']}")
    print("\n【本项目相关核心数字】")
    # 速记卡应覆盖全部可讲弹药:优先按公司+主打方向匹配,未命中则展示全部
    nums = [r for r in read_csv("03_真实数字表.csv")
            if j['公司'] in r["公司/项目"] or (j['主打方向'] and j['主打方向'] in r["公司/项目"])]
    if not nums:
        nums = read_csv("03_真实数字表.csv")
    seen = set()
    shown = []
    for r in nums:
        k = (r["数字"], r["口径"], r["公司/项目"])
        if k not in seen:
            seen.add(k)
            shown.append(r)
    for r in shown[:15]:
        print(f"  {r['数字']} {r['口径']} ({r['公司/项目']})")
    print("\n【可讲项目】")
    projs = [r for r in read_csv("02_项目表.csv") if j['公司'] in r["公司"]]
    if not projs:
        projs = read_csv("02_项目表.csv")
    for r in projs:
        print(f"  · {r['项目名']} [{r['公司']}]: {r['核心数字']}")
    print("\n【题库入口】")
    qs = [r for r in read_csv("05_面试题索引.csv") if j['公司'] in r["公司"] or r["公司"] == "跨岗位"]
    for r in qs:
        print(f"  · {r['题目']} [{r['方向']}] -> {r['答案位置']}")

def show_direction(d):
    base = os.path.join(ROOT, "02_方向知识库", d)
    if not os.path.isdir(base):
        # 尝试模糊匹配
        cands = [x for x in os.listdir(os.path.join(ROOT, "02_方向知识库")) if d in x]
        if not cands:
            print(f"02_方向知识库下未找到方向: {d}")
            return
        base = os.path.join(ROOT, "02_方向知识库", cands[0])
    print(f"\n方向 [{os.path.basename(base)}] 的文档:")
    for fp in sorted(glob.glob(os.path.join(base, "*.md"))):
        print(f"  · {os.path.basename(fp)}")

def show_index(keyword=None, layer=None):
    db = os.path.join(DATA, "knowledge.db")
    if not os.path.exists(db):
        print("knowledge.db 不存在，请先运行: python3 scripts/build_index.py")
        return
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    sql = "SELECT 路径,层,子层,类型 FROM file_index WHERE 1=1"
    params = []
    if layer:
        sql += " AND 层=?"
        params.append(layer)
    if keyword:
        sql += " AND (文件名 LIKE ? OR 子层 LIKE ? OR 路径 LIKE ?)"
        kw = f"%{keyword}%"
        params += [kw, kw, kw]
    sql += " ORDER BY 层, 子层, 路径 LIMIT 50"
    rows = cur.execute(sql, params).fetchall()
    conn.close()
    if not rows:
        print(f"全库索引中未找到: {keyword or '(空)'}")
        return
    print(f"\n全库索引命中 {len(rows)} 个文件{('（关键词: ' + keyword) if keyword else ''}:")
    for rel, lay, sub, typ in rows:
        print(f"  [{lay[:2]}] {typ:<4} {sub:<22} {rel}")
    if len(rows) >= 50:
        print("  … 仅显示前50条，可用 --层 <01|02|03> 或更精确关键词缩小范围")

def add_log(company, rnd, points):
    p = os.path.join(DATA, "04_面试日志.csv")
    rows = read_csv("04_面试日志.csv")
    n = len(rows) + 1
    row = [str(n), company, rnd, "", points, "待复盘", ""]
    with open(p, "a", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(row)
    print(f"已追加面试日志: {company} / {rnd} / {points}")

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0]
    if cmd == "查" and len(args) >= 2:
        hits = search_text(args[1])
        print(f"\n全文检索『{args[1]}』命中 {len(hits)} 个文件:")
        for rel, ln, snip in hits[:40]:
            print(f"  {rel}:{ln}  {snip}")
        if len(hits) > 40:
            print(f"  … 共 {len(hits)} 个")
    elif cmd == "岗位" and len(args) >= 2:
        show_jobs(args[1])
    elif cmd == "数字" and len(args) >= 2:
        show_numbers(args[1])
    elif cmd == "项目":
        show_projects(args[1] if len(args) >= 2 else None)
    elif cmd == "速记" and len(args) >= 2:
        gen_card(args[1])
    elif cmd == "方向" and len(args) >= 2:
        show_direction(args[1])
    elif cmd == "索引":
        kw = args[1] if len(args) >= 2 and not args[1].startswith("--") else None
        layer = None
        if "--层" in args:
            idx = args.index("--层")
            if idx + 1 < len(args):
                v = args[idx + 1]
                layer = {"01": "01_原始资料库", "02": "02_方向知识库", "03": "03_岗位弹药库"}.get(v, v)
        show_index(kw, layer)
    elif cmd == "全部":
        show_jobs()
        show_projects()
        print(f"\n真实数字表共 {len(read_csv('03_真实数字表.csv'))} 条; 面试日志 {len(read_csv('04_面试日志.csv'))} 条")
    elif cmd == "记录" and len(args) >= 3:
        add_log(args[1], args[2], " ".join(args[3:]))
    else:
        print(__doc__)

if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.exit(0)

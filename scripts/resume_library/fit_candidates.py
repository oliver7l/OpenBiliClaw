#!/usr/bin/env python3
"""Fit 候选单生成器：按岗位生成「简历调整候选」，不直接改简历。

借鉴 FResume（阅读收藏库 #156）的两道确认门：
  第一道门：本脚本产出《Fit 候选单》——只列候选动作（保留/突出/删改）+ 出处，不改任何文件
  第二道门：Aaron 逐条确认后，人工（或会话内）才落到版本库 vN-<标签>-<日期>

设计原则（FResume 三原则）：
  AI/脚本只产候选和推演；用户掌握确认权；系统负责来源可追溯。
  所有素材必须带出处：事实台账（唯一真值源）、interview.db project/number 表。

用法：
  .venv/bin/python scripts/resume_library/fit_candidates.py --app 13            # 按投递 ID
  .venv/bin/python scripts/resume_library/fit_candidates.py --company 字节 --jd <jd文件路径>
输出：notes/Fit候选单/<公司>-<日期>.md（新建，绝不覆盖已有文件）
"""
from __future__ import annotations

import argparse
import datetime
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path("/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw")
RESUME_DB = ROOT / "data" / "resume.db"
INTERVIEW_DB = ROOT / "data" / "interview.db"
FACT_LEDGER = ROOT / "简历库" / "00_事实源" / "童力-事实台账.md"
OUT_DIR = ROOT / "notes" / "Fit候选单"


def load_app(app_id: int | None, company: str | None):
    conn = sqlite3.connect(RESUME_DB)
    conn.row_factory = sqlite3.Row
    try:
        if app_id:
            row = conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM applications WHERE company LIKE ? ORDER BY id DESC LIMIT 1",
                (f"%{company}%",),
            ).fetchone()
        if row is None:
            sys.exit(f"找不到投递记录: app={app_id} company={company}")
        resume_file = None
        if row["resume_file_id"]:
            resume_file = conn.execute(
                "SELECT rel_path, category FROM resume_files WHERE id=?", (row["resume_file_id"],)
            ).fetchone()
        return dict(row), dict(resume_file) if resume_file else None
    finally:
        conn.close()


def load_materials(company: str, role: str):
    """从 interview.db 拉 project/number/concept 素材（全部带 source 出处）。"""
    ic = sqlite3.connect(INTERVIEW_DB)
    ic.row_factory = sqlite3.Row
    try:
        projects = [
            dict(r)
            for r in ic.execute(
                "SELECT name, company, stack, numbers, source, talking_points FROM project"
            )
        ]
        numbers = [
            dict(r)
            for r in ic.execute(
                "SELECT value, metric, company_project, source FROM number ORDER BY ingested_at DESC LIMIT 80"
            )
        ]
        concepts = [
            dict(r)
            for r in ic.execute(
                "SELECT concept, ctype, related FROM concept LIMIT 60"
            )
        ]
    finally:
        ic.close()

    def hits(text: str, kw: str) -> bool:
        return kw in (text or "")

    kws = [w for w in (company or "").split("(")[0].split("·")[:1] if w]
    # 角色分词：广告算法/游戏商业化 等 → 广告、算法、游戏、商业化
    too_generic = {"工程师", "算法工程师", "数据", "方向"}
    for tok in re.split(r"[^\w\u4e00-\u9fff]+", role or ""):
        if len(tok) >= 2 and tok not in too_generic:
            kws.append(tok)
            # 剥尾缀：广告算法→广告、策略建模→策略（项目名/数字表按领域词命中）
            for suf in ("算法", "建模", "策略", "工程", "方向"):
                if tok.endswith(suf) and len(tok) > len(suf) + 1:
                    kws.append(tok[: -len(suf)])
                    break
    kws = [k for k in dict.fromkeys(kws) if k]
    def proj_hit(p, k):
        return any(hits(p[f], k) for f in ("company", "name", "stack", "numbers", "talking_points"))

    related_projects = [p for p in projects if any(proj_hit(p, k) for k in kws if k)]
    related_numbers = [n for n in numbers if any(hits(n["company_project"], k) for k in kws if k)]
    # 数字按项目去重后跟项目对齐；概念全量列出（供 JD 对照选材）
    return related_projects, related_numbers, concepts


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 Fit 候选单（只产候选，不改简历）")
    ap.add_argument("--app", type=int, help="applications.id")
    ap.add_argument("--company", help="公司名（模糊匹配最近一条投递）")
    ap.add_argument("--jd", help="JD 文本文件路径（可选；提供后会列出 JD 关键词对照）")
    ap.add_argument("--notes", help="补充要求（一句话，写进候选单头部）")
    args = ap.parse_args()

    if not args.app and not args.company:
        ap.error("需要 --app 或 --company 其一")

    app, resume_file = load_app(args.app, args.company)
    company, role = app["company"], app["role"] or ""
    projects, numbers, concepts = load_materials(company, role)

    fact_ok = FACT_LEDGER.exists()
    jd_text = ""
    if args.jd:
        jd_path = Path(args.jd)
        if jd_path.exists():
            jd_text = jd_path.read_text(encoding="utf-8", errors="ignore")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    safe_co = "".join(c for c in company if c.isalnum() or c in "（）()·-")[:30]
    out_path = OUT_DIR / f"{safe_co}-{today}.md"
    n = 1
    while out_path.exists():
        n += 1
        out_path = OUT_DIR / f"{safe_co}-{today}-{n}.md"

    L = []
    L.append(f"# Fit 候选单 · {company} · {role or '岗位待定'}")
    L.append("")
    L.append(f"> 生成：{datetime.datetime.now().isoformat(timespec='seconds')}｜投递 ID：#{app['id']}｜状态：{app['stage'] or app['status']}")
    L.append(f"> 事实台账（唯一真值源）：{'✓ ' + str(FACT_LEDGER) if fact_ok else '⚠️ 未找到——先补台账，禁止凭记忆选材'}")
    L.append(f"> 简历版本：{resume_file['rel_path'] if resume_file else '⚠️ 未关联（resume_file_id 为空）——先在版本库定版再 Fit'}")
    if args.notes:
        L.append(f"> 补充要求：{args.notes}")
    L.append("")
    L.append("## 使用规则（两道确认门）")
    L.append("")
    L.append("1. 本单只是**候选**：每条动作必须逐条确认/否决，禁止整单照单全收")
    L.append("2. 确认通过的动作，才允许落到版本库新版本 `vN-<标签>-<日期>`（不复用旧版本号）")
    L.append("3. 所有表述必须能在事实台账中找到出处；找不到 → 回台账补事实，不许先写简历")
    L.append("")

    L.append("## 一、素材清点（自动从库中拉取，全部带出处）")
    L.append("")
    L.append(f"### 相关项目（命中 {len(projects)} 条）")
    L.append("")
    if projects:
        for p in projects:
            L.append(f"- **{p['name']}**（{p['company']}）｜栈：{p['stack'] or '—'}｜数字：{p['numbers'] or '—'}｜出处：{p['source']}")
    else:
        L.append("- （无精确命中——检查 interview.db project 表的公司别名，或人工指定）")
    L.append("")
    L.append(f"### 相关数字（命中 {len(numbers)} 条）")
    L.append("")
    if numbers:
        for x in numbers[:20]:
            L.append(f"- {x['metric']}: **{x['value']}**｜项目：{x['company_project']}｜出处：{x['source']}")
        if len(numbers) > 20:
            L.append(f"- …其余 {len(numbers) - 20} 条见 number 表")
    else:
        L.append("- （无精确命中）")
    L.append("")
    L.append(f"### 概念清单（{len(concepts)} 条，供 JD 对照选材）")
    L.append("")
    seen_c, uniq_concepts = set(), []
    for c in concepts:
        if c["concept"] not in seen_c:
            seen_c.add(c["concept"])
            uniq_concepts.append(c)
    L.append("、".join(c["concept"] for c in uniq_concepts) or "（空）")
    L.append("")

    if jd_text:
        L.append("## 二、JD 原文（附件，仅供对照）")
        L.append("")
        L.append("```")
        L.append(jd_text[:2000])
        L.append("```")
        L.append("")
    else:
        L.append("## 二、JD")
        L.append("")
        L.append("（未提供 JD 文件。补 JD 后重跑本脚本可生成对照区。）")
        L.append("")

    L.append("## 三、候选动作（AI 会话内填写，Aaron 逐条确认）")
    L.append("")
    L.append("| # | 动作类型 | 涉及内容 | 理由 | 出处（台账/number 表） | 确认 |")
    L.append("|---|---|---|---|---|---|")
    L.append("| 1 | 突出 | （待填） | | | ☐ |")
    L.append("| 2 | 删改 | （待填） | | | ☐ |")
    L.append("| 3 | 保留不动 | （待填） | | | ✓ 默认 |")
    L.append("")
    L.append("## 四、确认后动作（第二道门）")
    L.append("")
    L.append("- [ ] 已逐条确认上表")
    L.append("- [ ] 新版本已落版本库：`v__-____版-________`（填版本号/标签/日期）")
    L.append("- [ ] `applications.resume_file_id` 已指向新版本索引（跑 build_resume_index.py 后更新）")
    L.append("")

    out_path.write_text("\n".join(L), encoding="utf-8")
    print(f"Fit 候选单已生成: {out_path}")
    print(f"  项目命中 {len(projects)}｜数字命中 {len(numbers)}｜概念 {len(concepts)}")
    print("  ⚠️ 候选单不改动任何简历文件——确认动作后再落版本库")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""复盘材料包（B2 + B5）：找出最近 2 天完成的面试，聚合复盘素材。

素材来源（全部只读）：
    1. interview.db.interview_reviews —— 面试记录与逐字转录（ai_evaluation 为空 = 未复盘）
    2. diary.db.diary_fragments       —— 近 3 天口述里提到面试的碎片（日记与面试库「两张皮」的桥）
    3. interview.db.interview_questions —— 该公司预测题清单（复盘对照用）

发现素材时，把复盘草稿骨架写到 data/daily_snapshots/review_drafts/ 下，
供早报自动化补全（关键问题 / 亮点 / 弱项 / 行动项）。

用法：
    .venv/bin/python scripts/daily/review_digest.py

输出：复盘材料包文本 + 最后一行 JSON（has_material/items）。
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IV_DB = ROOT / "data" / "interview.db"
DIARY_DB = ROOT / "data" / "diary.db"
RESUME_DB = ROOT / "data" / "resume.db"
DRAFT_DIR = ROOT / "data" / "daily_snapshots" / "review_drafts"

INTERVIEW_KW = re.compile(r"面试|一面|二面|三面|终面|HR|offer|技术面|老板面")
ROUND_NAME = {"first": "一面", "second": "二面", "third": "三面", "final": "终面"}


def load_companies() -> list[str]:
    names: set[str] = set()
    conn = sqlite3.connect(f"file:{IV_DB}?mode=ro", uri=True)
    for (c,) in conn.execute("SELECT DISTINCT company FROM interview_questions WHERE company != ''"):
        names.add(c)
    for (c,) in conn.execute("SELECT DISTINCT company FROM todo WHERE company != ''"):
        names.add(c)
    conn.close()
    if RESUME_DB.exists():
        conn = sqlite3.connect(f"file:{RESUME_DB}?mode=ro", uri=True)
        for (c,) in conn.execute("SELECT DISTINCT company FROM applications WHERE company != ''"):
            names.add(c)
        conn.close()
    return sorted(n for n in names if len(n) >= 2)


def guess_company(text: str, companies: list[str]) -> str:
    hits = [c for c in companies if c in text]
    if hits:
        return max(hits, key=len)
    # 兜底：前 2 字前缀匹配（「万声集团」→「万声音乐/万声科技」）
    for c in companies:
        if len(c) >= 2 and c[:2] in text:
            return c
    return ""


def recent_unreviewed(days_back: int = 2) -> list[dict]:
    since = (date.today() - timedelta(days=days_back)).isoformat()
    conn = sqlite3.connect(f"file:{IV_DB}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT id, company, position, interview_date, round, result, duration_min,"
        " substr(transcript_text,1,4000), ai_evaluation FROM interview_reviews "
        "WHERE interview_date >= ? ORDER BY interview_date",
        (since,),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        if (r[8] or "").strip():
            continue  # 已复盘
        out.append({
            "id": r[0], "company": r[1], "position": r[2], "date": r[3],
            "round": ROUND_NAME.get(r[4] or "", r[4] or ""), "result": r[5] or "",
            "duration": r[6] or 0, "transcript_head": r[7] or "",
        })
    return out


def interview_diary_fragments(days_back: int = 3, companies: list[str] | None = None) -> list[dict]:
    if not DIARY_DB.exists():
        return []
    since = (date.today() - timedelta(days=days_back)).isoformat()
    conn = sqlite3.connect(f"file:{DIARY_DB}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT fragment_date, content FROM diary_fragments WHERE fragment_date >= ? ORDER BY rowid",
        (since,),
    ).fetchall()
    conn.close()
    comps = companies or []
    out = []
    for d, content in rows:
        if content and INTERVIEW_KW.search(content):
            out.append({"date": d, "content": content.strip(), "company": guess_company(content, comps)})
    return out


def company_questions(company: str, limit: int = 10) -> list[str]:
    if not company:
        return []
    conn = sqlite3.connect(f"file:{IV_DB}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT category, question FROM interview_questions WHERE company=? ORDER BY id LIMIT ?",
        (company, limit),
    ).fetchall()
    conn.close()
    return [f"[{c}] {q}" for c, q in rows]


def main() -> None:
    companies = load_companies()
    reviews = recent_unreviewed()
    fragments = interview_diary_fragments(companies=companies)

    if not reviews and not fragments:
        print(json.dumps({"has_material": False, "items": []}, ensure_ascii=False))
        return

    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    items = []

    print("🎯 近两天面试复盘 · 材料包")
    for rv in reviews:
        company = rv["company"]
        qs = company_questions(company)
        draft_name = f"review_{rv['date']}_{company or '未识别公司'}.md"
        draft_path = DRAFT_DIR / draft_name
        body = (
            f"# 面试复盘草稿 · {company} {rv['round']}（{rv['date']}）\n\n"
            f"- 岗位：{rv['position'] or '未知'} | 时长：{rv['duration']} min | 系统结果：{rv['result'] or '待定'}\n"
            f"- 状态：**草稿待补全**（自动化/用户确认后填写下面四节）\n\n"
            f"## 关键问题（面试官问了什么）\n\n（待补全）\n\n"
            f"## 答得好的点\n\n（待补全）\n\n## 答得弱 / 卡壳的点\n\n（待补全）\n\n"
            f"## 行动项（下次面试前要补的）\n\n- [ ] （待补全）\n\n"
            f"---\n\n### 素材一：逐字转录开头\n\n{(rv['transcript_head'] or '（无转录）')[:3000]}\n"
        )
        if qs:
            body += "\n### 素材二：该公司预测题（对照哪些被问到）\n\n" + "\n".join(f"- {q}" for q in qs) + "\n"
        draft_path.write_text(body, encoding="utf-8")

        print(f"\n· {rv['date']} {company} {rv['round']}（{rv['position'] or ''}，未复盘）")
        if qs:
            print(f"  预测题对照（前 {min(len(qs), 5)} 条）：")
            for q in qs[:5]:
                print(f"    - {q[:90]}")
        items.append({"company": company, "date": rv["date"], "draft": str(draft_path)})

    if fragments:
        print("\n💬 近 3 天口述里的面试相关碎片（日记侧素材）：")
        for f in fragments:
            tag = f"（{f['company']}）" if f["company"] else ""
            print(f"  · {f['date']} {f['content'][:100]}{tag}")

    # 只有日记碎片、没有 review 记录的公司：也生成草稿，提醒补建复盘
    covered = {it["company"] for it in items if it["company"]}
    for f in fragments:
        comp = f["company"]
        if not comp or comp in covered:
            continue
        qs = company_questions(comp)
        draft_path = DRAFT_DIR / f"review_{f['date']}_{comp}.md"
        body = (
            f"# 面试复盘草稿 · {comp}（{f['date']}，来源：口述日记）\n\n"
            f"- 状态：**草稿待补全**（面试库中无对应 interview_reviews 记录，确认后补建）\n\n"
            f"## 关键问题（面试官问了什么）\n\n（待补全）\n\n"
            f"## 答得好的点\n\n（待补全）\n\n## 答得弱 / 卡壳的点\n\n（待补全）\n\n"
            f"## 行动项（下次面试前要补的）\n\n- [ ] （待补全）\n\n"
            f"---\n\n### 素材：口述日记碎片\n\n"
            + "\n".join(f"- {x['date']} {x['content']}" for x in fragments if x["company"] == comp)
            + "\n"
        )
        if qs:
            body += "\n### 该公司预测题（对照哪些被问到）\n\n" + "\n".join(f"- {q}" for q in qs[:10]) + "\n"
        draft_path.write_text(body, encoding="utf-8")
        print(f"\n· {f['date']} {comp}（来自口述，面试库无记录）→ 已生成草稿")
        items.append({"company": comp, "date": f["date"], "draft": str(draft_path), "from_diary": True})
        covered.add(comp)

    for it in items:
        if it.get("from_diary"):
            continue  # 口述碎片版草稿已内嵌素材，避免重复追加
        related = [f["content"] for f in fragments if not it["company"] or f["company"] == it["company"]]
        if related:
            p = Path(it["draft"])
            text = p.read_text(encoding="utf-8")
            text += "\n### 素材三：口述日记碎片\n\n" + "\n".join(f"- {c}" for c in related) + "\n"
            p.write_text(text, encoding="utf-8")

    # 草稿已补全（文件里没有「待补全」）的不再算待办
    pending = [it for it in items if "待补全" in Path(it["draft"]).read_text(encoding="utf-8")]
    print("\n" + json.dumps({"has_material": bool(pending), "items": pending,
                              "fragments": len(fragments)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

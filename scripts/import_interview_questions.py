#!/usr/bin/env python3
"""导入面试题库/攻略类 Markdown 到 interview.db 的 interview_questions 表。

适用范围
--------
扫描 求职知识库/03_岗位弹药库/ 下各岗位备战目录，把「题库/速成包/预测/攻略」类
Markdown 文件解析为结构化的面试题，存入库表。每题包含：

    company        公司（从标题行 `# 公司 · ...` 提取）
    position       岗位（从标题行提取）
    category       题类（按 `## 一、项目深挖` 这种二级标题分）
    question       题目正文
    answer         答案/答题要点
    want_to_hear   面试官想听（可选，大宇风格 `**面试官想听：**`）
    idx            题号（用于保持原文顺序）
    source_path    来源文件相对路径
    source_type    文件类型（题库/速成包/攻略）

解析规则（兼容多种 Q 编号格式）
-----------------------------
- 公司/岗位：优先读文件首行 `# XX · YY 预测题库...`；首行无 `·` 则回退文件名前缀
- 题类：`## 一、xxx` / `## x、xxx` / `## 数字. xxx` 二级标题作为题类分组
- 题目：`Q1` / `Q1.` / `Q1：` / `1.` / `### Q1` / `**Q7**` / `Q7:**` 等格式
- 答案：把 `**答案**` / `**答：**` / `**你的答法**` / `**面试官想听：**` 等段落
         提取到对应字段；其余内容并入题目正文

用法
----
    python3 scripts/import_interview_questions.py          # dry-run 预览
    python3 scripts/import_interview_questions.py --apply   # 实际写入
    python3 scripts/import_interview_questions.py --apply --force  # 清空重导
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INTERVIEW_DB = PROJECT_ROOT / "data" / "interview.db"
ARMORY = PROJECT_ROOT / "求职知识库" / "03_岗位弹药库"

# 文件名/目录名里的公司别名 → 规范公司名
COMPANY_ALIASES = {
    "万声": "万声音乐",
    "比亚迪": "比亚迪",
    "拼多多": "拼多多",
    "字节": "字节跳动",
    "乐趣无限": "乐趣无限",
    "大宇": "大宇无限",
    "GoodLuck": "Good Luck Studio",
    "GoodLuckStudio": "Good Luck Studio",
}

# 题库类文件类型识别
TYPE_BY_NAME = {
    "题库": "题库",
    "预测": "题库",
    "速成包": "速成包",
    "攻略": "攻略",
    "备战策略": "攻略",
    "备战包": "速成包",
}

# 预测题库 / 题库 文件（题题带编号，可解析为结构化题目；攻略/速成包为叙述文档不入库）
QA_FILES = [
    "GoodLuckStudio-面试准备/03_速成包/GoodLuckStudio游戏数据分析_预测题库.md",
    "万声科技-面试准备/03_速成包/万声音乐推荐算法_预测题库.md",
    "乐趣无限-面试准备/03_速成包/乐趣无限推荐算法_预测题库.md",
    "大宇无限-广告岗-面试准备/03_速成包/大宇无限技术面_预测题库.md",
    "大宇无限-广告岗-面试准备/03_速成包/大宇无限技术面_补充题库.md",
    "拼多多-面试准备/02_面试备战资料/预测题库与答案_P0-P4.md",
    "比亚迪-面试准备/03_速成包/比亚迪营销岗_预测题库.md",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS interview_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL DEFAULT '',
    position TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    question TEXT NOT NULL,
    answer TEXT DEFAULT '',
    want_to_hear TEXT DEFAULT '',
    idx INTEGER DEFAULT 0,
    source_path TEXT NOT NULL,
    source_type TEXT DEFAULT '题库',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_questions_company ON interview_questions(company);
CREATE INDEX IF NOT EXISTS idx_questions_category ON interview_questions(category);
"""

# 题类二级标题：## 一、xxx / ## 1. xxx / ## 技术面 等
CAT_RE = re.compile(r"^##\s*([一二三四五六七八九十〇０-９0-9]+)[、.．]?\s*(.+?)\s*$")

# 题目起始行：支持 Q# 与 P#-# 编号（Q/P 必需，避免列表项误判）
Q_RE = re.compile(
    r"^#{0,6}\s*[*_]*(?:Q|问题)\s*(\d+)\s*[*_]*[.。:：、\-)]*(.*)$"
    r"|^\s*\**Q(?:\d+\.?|[:：])\s*(.*)$"
    r"|^#{0,6}\s*[*_]*([Pp]\d+[-–]\d+)\s*[*_.]?\s*(.*)$"
)


def parse_company_position(first_line: str, file_name: str) -> tuple[str, str]:
    """从标题行 / 文件名 提取 (company, position)。"""
    title = first_line.lstrip("#").strip()
    company = ""
    position = ""
    # 先尝试标题行 `# X · Y ...`
    if "·" in first_line:
        m = re.match(r"^(.*?)\s*[·｜]\s*(.*)$", title)
        if m:
            company = _norm_company(m.group(1).strip())
            position = m.group(2).strip()
            position = _clean_position(position)
    # company 尚未命中 → 回退文件名前缀（更可靠）
    if not company:
        company = company_from_filename(file_name) or ""
    # position 仍缺 → 回退文件名中「岗」附近词
    if not position:
        position = position_from_filename(file_name)
    return company, position


def company_from_filename(file_name: str) -> str:
    stem = Path(file_name).name
    for alias_key in sorted(COMPANY_ALIASES, key=len, reverse=True):
        if alias_key in stem:
            return COMPANY_ALIASES[alias_key]
    # 目录名兜底（传 name 即可，目录名与文件名有一致）
    return ""


def position_from_filename(file_name: str) -> str:
    """从文件名提取岗位关键词（如 技术面/推荐算法/广告算法/数据科学）。"""
    stem = Path(file_name).name
    m = re.search(r"(推荐算法|广告算法|数据科学|数据分析|算法工程师|营销岗|游戏数据)", stem)
    if m:
        pos = m.group(1)
        return {"推荐算法": "推荐算法工程师",
                "广告算法": "广告算法工程师",
                "数据科学": "数据科学工程师",
                "数据分析": "数据分析师",
                "算法工程师": "算法工程师",
                "营销岗": "营销/算法岗",
                "游戏数据": "游戏数据分析师"}.get(pos, pos)
    return ""


_POS_TYPES = ("推荐算法工程师", "广告算法工程师", "算法工程师", "高团算法工程师",
              "数据科学工程师", "数据分析师", "游戏数据分析师", "搜索算法工程师",
              "大模型算法工程师", "机器学习工程师")

def _clean_position(pos: str) -> str:
    """归一化岗位：优先白名单匹配，其次截断。"""
    pos = pos.strip()
    # 1) 白名单优先
    for p in _POS_TYPES:
        if p in pos:
            return p
    # 2) 有「岗」直接取「xx岗」之前为岗位主词（如 营销岗 → 营销/算法岗）
    m = re.search(r"([\u4e00-\u9fffA-Za-z0-9/]+)岗(?:位)?", pos)
    if m:
        return m.group(1) if "营销" not in m.group(1) else "营销/算法岗"
    # 3) 截断面试类冗余后缀，取剩余干净部分
    for m in ("面试攻略", "逐轮备战策略", "补充题库", "预测题库", "速成包",
              "技术面", "面试", "备战策略", "题库", "攻略", "备战"):
        if m in pos:
            before = pos.split(m)[0].strip()
            if before:
                return _trim_pos(before)
    return _trim_pos(pos)


def _trim_pos(pos: str) -> str:
    pos = re.sub(r"[（(][^（）()]*[）)]", "", pos).strip()
    pos = re.sub(r"（今日头条）", "", pos)
    pos = re.split(r"[：:]\s*|\s*基于\s*", pos)[0].strip()
    pos = re.sub(r"\s+", "", pos)
    pos = pos.strip(" —，。、（）()·")
    return pos


def _norm_company(c: str) -> str:
    c = c.strip()
    for k, v in COMPANY_ALIASES.items():
        if k in c:
            return v
    return c


def _postfix_meta(rel: str, company: str, position: str) -> tuple[str, str]:
    """按文件目录/名称补正公司岗位的边角情况。"""
    pos = position
    if "广告岗" in rel and pos not in ("广告算法工程师",) and "广告算法" not in pos:
        if not pos or "四轮" in pos or pos in ("技术面", "四轮面试", "面试"):
            pos = "广告算法工程师"
    # 字节：今日头条·数据科学 → 数据科学工程师
    if "字节" in company and "数据科学" in pos and "工程师" not in pos:
        pos = "数据科学工程师"
    return company, pos


def detect_type(file_path: str) -> str:
    for key, t in TYPE_BY_NAME.items():
        if key in file_path:
            return t
    return "题库"


def split_qa_blocks(lines: list[str]) -> list[dict]:
    """把解析文件为「题类 + 题目块」结构。"""
    # 先按二级标题分块
    blocks: list[tuple[str, str, list[str]]] = []  # (category, file, lines)
    cur_cat = ""
    cur = []
    for ln in lines:
        cm = CAT_RE.match(ln)
        if cm and not Q_RE.match(ln):
            if cur:
                blocks.append((cur_cat, "", cur))
            # 题类名
            cd = cm.group(2).strip()
            cur_cat = cd
            cur = []
        else:
            cur.append(ln)
    if cur:
        blocks.append((cur_cat, "", cur))

    questions: list[dict] = []
    idx = 0
    for category, _, blk in blocks:
        # 在块内按题目重切
        cur_q = None
        for ln in blk:
            qm = Q_RE.match(ln)
            if qm:
                if cur_q:
                    questions.append(cur_q)
                num = qm.group(1) or qm.group(2) or qm.group(3) or ""
                text = (qm.group(1) and qm.group(2)) and qm.group(2).strip() or \
                       ((qm.group(3) and qm.group(4)) and (qm.group(3)+" "+qm.group(4)).strip() or ln)
                # 取题目正文行
                body = _extract_question_body(ln, qm)
                cur_q = {"category": category, "qnum": num, "qbody": body, "lines": []}
            else:
                if cur_q:
                    cur_q["lines"].append(ln)
        if cur_q:
            questions.append(cur_q)

    # 组装字段
    result = []
    for q in questions:
        body_lines, answer_parts, want_lines = _classify_para(q["lines"])
        body = "\n".join(body_lines).strip()
        if q["qbody"]:
            body = (q["qbody"] + "\n" + "\n".join(body_lines)).strip() if body else q["qbody"]
        result.append({
            "category": q["category"],
            "question": body or q["qbody"],
            "answer": "\n".join(answer_parts).strip(),
            "want_to_hear": "\n".join(want_lines).strip(),
            "idx": idx,
        })
        idx += 1
    return result


def _extract_question_body(ln: str, qm: re.Match) -> str:
    """从题目行提取题目正文（去掉编号标记）。"""
    joined = ln.lstrip("#").strip("*_ ")
    # 去掉最前面的 Q1:/P0-1 等编号
    joined = re.sub(r"^(?:Q|问题)\s*\d+\s*[.。:：、\-)]*\s*", "", joined, count=1)
    joined = re.sub(r"^[Pp]\d+[-–]\d+\s*[.。:：、\-)]*\s*", "", joined, count=1)
    joined = re.sub(r"^\d+\s*[.。:：、\-)]\s*", "", joined, count=1)
    return joined.strip()


def _classify_para(lines: list[str]) -> tuple[list[str], list[str], list[str]]:
    """把题目下的段落分流：正文 / 答案 / 面试官想听。"""
    body: list[str] = []
    answer: list[str] = []
    want: list[str] = []
    cur = body
    for ln in lines:
        s = ln.strip()
        # 去除行首一个引用/列表前缀（`> ` / `- ` / `* `），保留 `**` 加粗标记
        s_noq = re.sub(r"^(?:>\s*|-{1,2}\s*|\*\s+)", "", s)
        if re.match(r"^\*\**\s*(?:面试官想听|想听)[:：]", s_noq) or "面试官想听" in s_noq[:20]:
            cur = want
        elif re.search(r"^[*]*\s*(?:答|答案|我的答法|你的答法|答题要点|答题策略|答案要点|答案框架|参考话术|考察点|追问防守|追问预案|你的答案框架)[*:：]?", s_noq) or \
             "考察点" in s_noq[:10] or "参考话术" in s_noq[:10] or "答案要点" in s_noq[:10] \
             or "答题策略" in s_noq[:10] or "答案框架" in s_noq[:10] or "你的答案框架" in s_noq[:12] \
             or "追问预案" in s_noq[:12] or "追问防守" in s_noq[:12]:
            cur = answer
        cur.append(ln.rstrip())
    return body, answer, want


def build_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(INTERVIEW_DB), timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def main() -> int:
    apply = "--apply" in sys.argv
    force = "--force" in sys.argv

    conn = build_conn()
    try:
        now = __import__("datetime").datetime.now().isoformat()

        if force:
            conn.execute("DELETE FROM interview_questions")
            conn.commit()
            print("已清空 interview_questions（--force）")

        existing_sources = {
            r["source_path"] for r in conn.execute(
                "SELECT DISTINCT source_path FROM interview_questions"
            ).fetchall()
        }

        total = 0
        files_done = 0
        for rel in QA_FILES:
            path = ARMORY / rel
            if not path.exists():
                print(f"  [跳过] 文件不存在: {rel}")
                continue
            if rel in existing_sources and not force:
                print(f"  [跳过] 已导入: {rel}")
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                print(f"  [跳过] 编码异常: {rel}")
                continue
            lines = text.splitlines()
            first_line = lines[0] if lines else ""
            company, position = parse_company_position(first_line, path.name)
            company, position = _postfix_meta(rel, company, position)
            ftype = detect_type(rel)

            qas = split_qa_blocks(lines)
            if not qas:
                print(f"  [无题] {rel} (公司={company},岗位={position}) — 未解析到题目")
                continue

            for q in qas:
                question = q["question"].strip()
                if not question:
                    continue
                total += 1
                if apply:
                    conn.execute(
                        """INSERT INTO interview_questions
                           (company, position, category, question, answer,
                            want_to_hear, idx, source_path, source_type, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (company, position, q["category"], question,
                         q["answer"], q["want_to_hear"], q["idx"], rel, ftype, now),
                    )
            if apply:
                conn.commit()
            print(f"  {'已导入' if apply else '[预览]'} {rel} → {len([q for q in qas if q['question'].strip()])} 题"
                  f"（公司={company}, 岗位={position}）")
            files_done += 1

        if apply:
            conn.commit()
            print(f"\n共导入 {total} 题 / {files_done} 文件 → {INTERVIEW_DB}")
        else:
            print(f"\n[DRY-RUN] 将导入约 {total} 题 / {files_done} 文件。"
                  f"加 --apply 实际写入，加 --force 清空重导。")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
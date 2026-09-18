"""Data Agent · schema 目录（知识库层）。

扫描核心 SQLite 库 → 生成目录 JSON：表/列/行数 + 业务注释。
对应面试弹药 §1 第 2 点 Schema linking：不是把几百张表全塞 prompt，
而是「表/字段元数据 + 业务注释」检索增强。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
CATALOG_PATH = Path(__file__).resolve().parent / "data_agent_catalog.json"

# 进 Agent 视野的核心库（业务库，不含 embedding_cache/rag 等技术库）
CORE_DBS = ["content.db", "diary.db", "interview.db", "resume.db", "pool.db", "knowledge.db"]

# 表级黑名单：FTS 影子表/内部表
SKIP_SUFFIX = ("_fts", "_fts_data", "_fts_idx", "_fts_docsize", "_fts_config", "_data", "_idx", "_docsize", "_config")

# 业务注释（人工沉淀——这就是「口径/语义知识」的最小形态）
# 结构: db.table → {"desc": 表用途, "cols": {col: 注释}}
ANNOTATIONS: dict[str, dict] = {
    "content.db.articles": {
        "desc": "阅读收藏库主表：收藏的文章/视频，一行一篇（含小红书/V2EX/知乎/B站/公众号）",
        "cols": {
            "source_type": "来源平台：xiaohongshu/v2ex/zhihu/bilibili/youtube/wechat 等",
            "status": "阅读状态：unread 未读 / read 已读（口径：收藏后读完= status='read'）",
            "content_text": "正文全文（空=正文还没补抓成功）",
            "body_fetch_attempts": "正文补抓尝试次数",
            "reading_percent": "阅读进度百分比 0~100",
            "favorited": "1=标星",
            "tags": "JSON 数组字符串，如 '[\"Agent\",\"推荐\"]'",
            "published_at": "原始发布时间（TEXT，可为空）",
            "created_at": "入库时间（UTC）",
        },
    },
    "content.db.fetch_log": {
        "desc": "采集线抓取流水日志：每次 fetch 一行，体检/成功率分析的源表",
        "cols": {
            "ts": "发生时间 TEXT（ISO 格式，UTC）",
            "platform": "目标平台：v2ex/zhihu/xhs/generic 等",
            "channel": "通道名：v2ex-mindback/v2ex-scrapling/agentlimb-text 等（降级链）",
            "ok": "1=成功 0=失败（口径：成功率 = SUM(ok)*100/COUNT(*))",
            "error_kind": "失败类别：parse/cf_challenge/timeout/proxy 等；成功时为空",
            "latency_ms": "耗时毫秒",
        },
    },
    "diary.db.diary_fragments": {
        "desc": "日记碎片：随手记录的想法/事件/观察，一行一条",
        "cols": {
            "fragment_date": "归属日期 YYYY-MM-DD（本地日期）",
            "mood": "情绪：hopeful/reflective/thoughtful/tired 等",
            "source": "来源：im=聊天随手说 / manual / voice",
            "tags": "JSON 数组字符串",
        },
    },
    "resume.db.applications": {
        "desc": "投递台账：一公司一岗一行，求职漏斗的源表",
        "cols": {
            "company": "公司名",
            "role": "岗位名",
            "status": "状态：active/已终止/offer 等",
            "stage": "阶段：投递/一面/二面/HR面/已终止",
            "interview_at": "下次面试时间 TEXT",
            "resume_ver": "自由文本简历版本（旧字段，逐渐被 resume_file_id 替代）",
            "resume_file_id": "关联 resume_files.id（sha256 级文件索引）",
        },
    },
    "interview.db.todo": {
        "desc": "面试/跟进待办：kind=interview 是面试，followup 是投递跟进",
        "cols": {
            "status": "pending 待办 / done 完成",
            "kind": "类型：interview/followup",
            "due_date": "截止/面试日期 YYYY-MM-DD",
            "company": "关联公司",
        },
    },
    "interview.db.question": {
        "desc": "刷题题库：面试题/知识点，topic 是题目文本",
        "cols": {
            "topic": "题目内容",
            "direction": "方向：推荐/广告/NLP/Agent 等",
            "company": "关联公司（可空）",
            "answer_loc": "答案位置/出处",
        },
    },
    "interview.db.interview_questions": {
        "desc": "面试实录中被问到的题（复盘来源）",
        "cols": {},
    },
    "pool.db.content_cache": {
        "desc": "推荐内容池缓存：抓来的候选内容",
        "cols": {},
    },
    "knowledge.db.knowledge_cards": {
        "desc": "知识卡片库",
        "cols": {},
    },
}


def _is_skip(name: str) -> bool:
    return any(name.endswith(s) for s in SKIP_SUFFIX) or name.startswith("sqlite_")


def build_catalog() -> dict:
    catalog: dict = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "dbs": {}}
    for db in CORE_DBS:
        path = DATA_DIR / db
        if not path.exists():
            continue
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        tables = {}
        for (tname,) in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ):
            if _is_skip(tname):
                continue
            cols = []
            for r in con.execute(f"PRAGMA table_info({tname})"):
                cols.append({"name": r["name"], "type": r["type"] or "TEXT"})
            try:
                (nrows,) = con.execute(f"SELECT COUNT(*) FROM {tname}").fetchone()
            except sqlite3.Error:
                nrows = 0
            ann = ANNOTATIONS.get(f"{db}.{tname}", {})
            tables[tname] = {
                "rows": nrows,
                "desc": ann.get("desc", ""),
                "columns": [
                    {"name": c["name"], "type": c["type"], "note": ann.get("cols", {}).get(c["name"], "")}
                    for c in cols
                ],
            }
        con.close()
        catalog["dbs"][db] = {"tables": tables}
    CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
    return catalog


def load_catalog() -> dict:
    if not CATALOG_PATH.exists():
        return build_catalog()
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def render_catalog_context(catalog: dict, max_tables_per_db: int = 12) -> str:
    """渲染成给 LLM 的紧凑 schema 文本。

    有业务注释（desc）的表永远保留——它们是核心表，不能按行数排序被挤掉。
    """
    lines = []
    for db, d in catalog["dbs"].items():
        tables = d["tables"]
        annotated = {k: v for k, v in tables.items() if v.get("desc")}
        rest = {k: v for k, v in tables.items() if not v.get("desc")}
        kept = list(annotated.items()) + sorted(rest.items(), key=lambda kv: -kv[1]["rows"])[:max_tables_per_db]
        lines.append(f"## 库 {db}")
        for tname, t in kept:
            head = f"- {tname}({t['rows']}行)" + (f" —— {t['desc']}" if t["desc"] else "")
            lines.append(head)
            col_parts = []
            for c in t["columns"]:
                part = f"{c['name']} {c['type']}"
                if c["note"]:
                    part += f"[{c['note']}]"
                col_parts.append(part)
            lines.append("  列: " + "; ".join(col_parts))
    return "\n".join(lines)


if __name__ == "__main__":
    cat = build_catalog()
    n_tables = sum(len(d["tables"]) for d in cat["dbs"].values())
    print(f"catalog: {len(cat['dbs'])} 库 {n_tables} 表 → {CATALOG_PATH.name}")

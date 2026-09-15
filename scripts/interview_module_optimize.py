#!/usr/bin/env python3
"""面试模块优化 · 迁移 001：面试时间结构化 + 阶段归一化。

背景（2026-09-15 诊断）
--------------------
`resume.db.applications` 存在三个结构性缺陷：

1. ``interview_at`` 是自由文本，形态混乱：
       '2026-09-17 19:00 视频面试'
       '2026-09-15 15:00 二面+HR面(一次性走完)'
       '2026-09-04 HR面'
       '2026-08下旬~09初'
       '未约面'
   → 无法可靠排序、无法做临期提醒。
2. ``status`` 把「状态」和「时间/细节」揉在一起：
       '已确认参加(9/17周四 19:00 视频面)'
   → 改一次期要同步改多处，必然漂移。
3. 18 行里 status 有 14 种取值，无枚举
   → ``/schedule`` 的 ``status in ("待面","进行中")`` 判断永远命中不了，
     ``is_upcoming`` 恒为 False（真实 bug）。

改法（非破坏性）
----------------
只 **新增** 三列，原 ``interview_at`` / ``status`` 原样保留，向后兼容：

- ``interview_start_at`` TEXT  ISO 前缀时间 ``YYYY-MM-DD[ HH:MM]``，可排序可比较
- ``round_note``       TEXT  场次备注（"HR面"/"二面+HR面(一次性走完)"/"视频面试"）
- ``stage``            TEXT  归一化阶段枚举（见 ``STAGE_ENUM``）

无法解析的时间（如 '2026-08下旬~09初'、'未约面'）留空，原值仍在
``interview_at`` 里，不丢信息。

幂等：可反复运行。已手工填过新列的行不会被覆盖（除非 ``--force``）。

用法
----
    .venv/bin/python scripts/interview_module_optimize.py            # 演练
    .venv/bin/python scripts/interview_module_optimize.py --apply    # 落库
    .venv/bin/python scripts/interview_module_optimize.py --apply --force
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "resume.db"

# ── 归一化阶段枚举 ───────────────────────────────────────────
# 只保留「流程推进」语义，时间/细节一律不进这个字段。
STAGE_ENUM = (
    "候选",      # 候选池，未投递或待决策
    "已投递",    # 已投，静默中
    "待面",      # 已约面/已确认参加，尚未开面
    "面试中",    # 至少面过一轮，流程未结束（含通过待下一轮、暂缓、等待结果）
    "谈薪中",    # 进入 offer/薪酬沟通
    "已结束",    # 流程终结且非主动放弃（挂掉/输给他人）
    "已终止",    # 主动放弃
)

# 显式覆盖表：公司+关键词 → stage（优先于通用规则）
EXPLICIT_STAGE: dict[str, str] = {
    "字节": "面试中",
    "大宇无限": "面试中",
    "比亚迪": "谈薪中",
    "乐趣无限": "已终止",
    "万声音乐": "面试中",
    "GoodLuckStudio": "面试中",
    "万联易达": "面试中",
    "深圳灵动": "待面",
    "拼多多": "面试中",
    "待确认": "待面",
    "HungryStudio": "待面",
    "去哪儿网": "候选",
    "58同城": "候选",
    "远趣科技": "候选",
    "兴趣岛": "候选",
    "某知名互联网上市公司(猎头匿名)": "候选",
}

# 通用兜底规则（按优先级顺序匹配 status 文本）
# ⚠️ 顺序有语义：终态（已终止/已结束）必须排在「谈薪」之前。
#    反例：'已结束(输给内转,HR留门:新增HC可直接推进谈薪)' —— 这里的"谈薪"
#    是条件句而非当前状态，若让"谈薪"先命中会被误判为「谈薪中」。
STAGE_RULES: tuple[tuple[str, str], ...] = (
    ("已终止", "已终止"),
    ("主动放弃", "已终止"),
    ("已结束", "已结束"),
    ("谈薪", "谈薪中"),
    ("offer", "谈薪中"),
    ("待面", "待面"),
    ("约面", "待面"),
    ("已确认参加", "待面"),
    ("待决策", "候选"),
    ("未投递", "候选"),
    ("已投递", "已投递"),
    ("已结束", "已结束"),
    ("已面", "面试中"),
    ("一面通过", "面试中"),
    ("进行中", "面试中"),
    ("暂缓", "面试中"),
)

# 时间解析：'2026-09-17 19:00 视频面试' → ('2026-09-17 19:00', '视频面试')
TIME_RE = re.compile(
    r"^\s*(?P<date>\d{4}-\d{2}-\d{2})"
    r"(?:\s+(?P<time>\d{1,2}:\d{2}))?"
    r"(?P<rest>.*)$"
)

NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("interview_start_at", "TEXT"),
    ("round_note", "TEXT"),
    ("stage", "TEXT"),
)


def ensure_columns(conn: sqlite3.Connection) -> list[str]:
    """新增缺失列，返回实际新增的列名。"""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(applications)")}
    added: list[str] = []
    for col, typ in NEW_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE applications ADD COLUMN {col} {typ}")
            added.append(col)
    return added


def parse_interview_at(raw: str) -> tuple[str, str]:
    """把自由文本时间拆成 (interview_start_at, round_note)。

    解析不了时返回 ("", "")，原值保留在 interview_at 列，不丢信息。
    """
    if not raw:
        return "", ""
    m = TIME_RE.match(raw)
    if not m:
        # 如 '2026-08下旬~09初'、'未约面' —— 不是可解析的时间点
        return "", ""
    date_part = m.group("date")
    time_part = m.group("time")
    rest = (m.group("rest") or "").strip()
    # 去掉 rest 里可能残留的分隔空白/破折号
    rest = rest.strip(" -–—·")
    start = f"{date_part} {time_part}" if time_part else date_part
    return start, rest


def normalize_stage(company: str, status: str) -> str:
    """status 自由文本 → stage 枚举。"""
    text = f"{company}{status}"
    if company in EXPLICIT_STAGE:
        # 显式表命中，但 status 里若出现更强的终止/谈薪信号，以 status 为准
        base = EXPLICIT_STAGE[company]
        # 终态信号优先于显式表基线（同 STAGE_RULES 的顺序语义）
        for kw, st in (
            ("已终止", "已终止"),
            ("主动放弃", "已终止"),
            ("已结束", "已结束"),
            ("谈薪", "谈薪中"),
            ("offer", "谈薪中"),
        ):
            if kw in status:
                return st
        return base
    for kw, stage in STAGE_RULES:
        if kw in text:
            return stage
    return "已投递"


def main() -> int:
    ap = argparse.ArgumentParser(description="面试模块迁移 001：时间结构化 + 阶段归一化")
    ap.add_argument("--apply", action="store_true", help="真正写库（默认只演练）")
    ap.add_argument("--force", action="store_true", help="覆盖已填过的非空新列")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"❌ 找不到数据库：{DB_PATH}")
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    added = ensure_columns(conn)
    print(f"=== 新增列：{added or '（已存在，无需新增）'} ===")

    rows = conn.execute(
        "SELECT id, company, role, interview_at, status, "
        "interview_start_at, round_note, stage FROM applications ORDER BY id"
    ).fetchall()

    plan: list[tuple[int, str, str, str, str]] = []  # (id, company, start, note, stage)
    for r in rows:
        start, note = parse_interview_at(r["interview_at"] or "")
        stage = normalize_stage(r["company"] or "", r["status"] or "")
        # 幂等：已填过且非空 → 保留（除非 --force）
        if not args.force:
            if r["interview_start_at"]:
                start = r["interview_start_at"]
            if r["round_note"]:
                note = r["round_note"]
            if r["stage"]:
                stage = r["stage"]
        plan.append((r["id"], r["company"] or "", start, note, stage))

    print(f"\n{'公司':<28} {'stage':<8} {'interview_start_at':<18} round_note")
    print("-" * 92)
    for _id, comp, start, note, stage in plan:
        print(f"{comp:<28} {stage:<8} {(start or '—'):<18} {note}")

    unknown = sorted({s for *_, s in plan} - set(STAGE_ENUM))
    if unknown:
        print(f"\n⚠️ 出现未登记的 stage 取值：{unknown}")
        print(f"   合法枚举：{STAGE_ENUM}")

    if not args.apply:
        conn.rollback()
        conn.close()
        print("\n（演练模式，未写库。加 --apply 落库）")
        return 0

    for _id, _comp, start, note, stage in plan:
        conn.execute(
            "UPDATE applications SET interview_start_at=?, round_note=?, stage=? WHERE id=?",
            (start, note, stage, _id),
        )
    conn.commit()

    print(f"\n✅ 已更新 {len(plan)} 行")
    print("\n=== 校验：按 interview_start_at 排序 ===")
    for r in conn.execute(
        "SELECT company, interview_start_at, stage FROM applications "
        "WHERE interview_start_at IS NOT NULL AND interview_start_at != '' "
        "ORDER BY interview_start_at"
    ):
        print(f"  {r['interview_start_at']:<18} {r['company']:<14} {r['stage']}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""面试模块优化 · 迁移 002：面试时间去双写（applications 为唯一真值）。

背景（2026-09-15 诊断）
--------------------
面试时间同时写在两个地方，各改各的：

* ``resume.db.applications.interview_start_at``（结构化列，迁移 001 加的）
* ``interview.db.todo.due_date``（提醒日期）

真实事故：深圳灵动改期后，applications 写 ``2026-09-17 19:00``，而待办
``due_date`` 还停在 ``2026-09-16`` —— 排期视图和待办列表对不上。

改法
----
不是「禁止双写」（待办列表本来就需要一个提醒日期），而是**明确派生方向**：

1. ``todo`` 新增 ``kind`` 列：
     ``interview``  面试时间类待办 —— ``due_date`` **由面试时间派生**，改期自动同步
     ``followup``   跟进类待办     —— 自己的截止日（如「谈薪 R3 电话提级别 9/18」），不随改期动
2. 改期唯一写入口：``PATCH /api/interview/study/schedule/{id}/time``
   —— 写 ``applications.interview_start_at``，并派生 ``interview_at`` 与
   该公司 ``kind='interview'`` 待办的 ``due_date``。
3. ``GET /schedule`` 返回 ``time_conflicts``：两边不一致时报警（说明有人
   绕开写入口直接改了一边）。

本脚本做三件事（幂等，可反复运行）
----------------------------------
1. 给 ``interview.db.todo`` 补 ``kind`` 列（缺则加，默认 ``followup``）；
2. **标注**：识别现存的面试时间类待办 → ``kind='interview'``
   判定 = 标题含 ``HH:MM`` 时间 + 该公司有面试时间 + ``due_date`` 与面试日期一致；
3. **对齐**：``kind='interview'`` 且 pending 的待办，``due_date`` 以
   ``applications.interview_start_at`` 为准修正。

用法
----
    .venv/bin/python scripts/interview_time_decouple.py            # 演练（只读）
    .venv/bin/python scripts/interview_time_decouple.py --apply    # 落库（先备份）
"""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESUME_DB = PROJECT_ROOT / "data" / "resume.db"
INTERVIEW_DB = PROJECT_ROOT / "data" / "interview.db"
BACKUP_DIR = PROJECT_ROOT / ".bak"

# 标题里的钟点时间，如「深圳灵动 面试 19:00」「HungryStudio 面试 16:00」
_CLOCK_RE = re.compile(r"\d{1,2}:\d{2}")
_ISO_DAY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _backup(apply: bool) -> Path | None:
    if not apply:
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"interview_time_decouple_{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    for db in (RESUME_DB, INTERVIEW_DB):
        if db.exists():
            shutil.copy2(db, dest / db.name)
    return dest


def _ensure_kind_column(conn: sqlite3.Connection) -> bool:
    """补 kind 列。返回是否新增。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(todo)")}
    if "kind" in cols:
        return False
    conn.execute("ALTER TABLE todo ADD COLUMN kind TEXT DEFAULT 'followup'")
    conn.commit()
    return True


def _load_interview_days(resume_conn: sqlite3.Connection) -> dict[str, set[str]]:
    """company -> {面试日期 YYYY-MM-DD}（来自 applications.interview_start_at）。"""
    cols = {r[1] for r in resume_conn.execute("PRAGMA table_info(applications)")}
    if "interview_start_at" not in cols:
        return {}
    out: dict[str, set[str]] = {}
    for r in resume_conn.execute(
        "SELECT company, interview_start_at FROM applications"
    ):
        start = (r[1] or "").strip()
        if not start:
            continue
        m = _ISO_DAY_RE.match(start)
        if not m:
            continue
        out.setdefault(r[0] or "", set()).add(m.group(1))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="面试时间去双写迁移（迁移 002）")
    ap.add_argument("--apply", action="store_true", help="落库（默认演练，只读）")
    args = ap.parse_args()

    if not RESUME_DB.exists() or not INTERVIEW_DB.exists():
        print(f"缺少数据库：{RESUME_DB} / {INTERVIEW_DB}", file=sys.stderr)
        return 1

    resume_conn = sqlite3.connect(str(RESUME_DB), timeout=30.0)
    resume_conn.row_factory = sqlite3.Row
    todo_conn = sqlite3.connect(str(INTERVIEW_DB), timeout=30.0)
    todo_conn.row_factory = sqlite3.Row

    try:
        days_by_company = _load_interview_days(resume_conn)
        # dry-run 下不建列：缺列时把所有待办的 kind 当作默认值处理
        has_kind = "kind" in {r[1] for r in todo_conn.execute("PRAGMA table_info(todo)")}
        added_col = (not has_kind) if not args.apply else _ensure_kind_column(todo_conn)

        sel = ("SELECT id, title, company, due_date, status, kind FROM todo"
               if has_kind else
               "SELECT id, title, company, due_date, status, 'followup' AS kind FROM todo")
        rows = todo_conn.execute(sel + " ORDER BY id").fetchall()

        to_mark: list[tuple[int, str, str]] = []   # (id, title, company)
        to_fix: list[tuple[int, str, str, str, str]] = []  # (id, title, company, old, new)
        for r in rows:
            company = (r["company"] or "").strip()
            days = days_by_company.get(company)
            if not days:
                continue
            due = (r["due_date"] or "").strip()
            # 标注：标题带钟点时间 + due_date 命中该公司某个面试日期
            is_interview_title = bool(_CLOCK_RE.search(r["title"] or ""))
            if is_interview_title and due in days:
                if (r["kind"] or "followup") != "interview":
                    to_mark.append((r["id"], r["title"], company))
                continue  # 日期一致，无需修正
            # 对齐：已标 interview（或本次会标）但日期与真值不一致
            will_be_interview = (r["kind"] or "followup") == "interview" or (
                is_interview_title and len(days) == 1
            )
            if will_be_interview and due and due not in days and r["status"] == "pending":
                # 该公司只有一个面试时间 → 明确真值，可安全对齐
                target = sorted(days)[0]
                to_fix.append((r["id"], r["title"], company, due, target))

        print("=== 面试时间去双写（迁移 002）===")
        print(f"模式：{'APPLY（落库）' if args.apply else 'DRY-RUN（只读）'}")
        print(f"kind 列：{'将新增' if added_col else '已存在'}")
        print(f"面试时间类待办待标注：{len(to_mark)} 条")
        for tid, title, company in to_mark:
            print(f"  #{tid} [{company}] {title[:40]} → kind=interview")
        print(f"due_date 需对齐（以 applications 为准）：{len(to_fix)} 条")
        for tid, title, company, old, new in to_fix:
            print(f"  #{tid} [{company}] {title[:40]}: {old} → {new}")

        if not args.apply:
            print("\n（演练结束，未改动任何数据。加 --apply 落库）")
            return 0

        backup = _backup(apply=True)
        if backup:
            print(f"\n已备份到：{backup}")

        for tid, _title, _company in to_mark:
            todo_conn.execute("UPDATE todo SET kind='interview' WHERE id=?", (tid,))
        for tid, _title, _company, _old, new in to_fix:
            todo_conn.execute("UPDATE todo SET due_date=?, kind='interview' WHERE id=?", (new, tid))
        todo_conn.commit()

        marked = len(to_mark)
        fixed = len(to_fix)
        print(f"\n完成：标注 {marked} 条、对齐 {fixed} 条")

        remain = todo_conn.execute(
            "SELECT COUNT(*) FROM todo WHERE kind='interview' AND status='pending'"
        ).fetchone()[0]
        print(f"当前面试时间类待办（pending）：{remain} 条")
        return 0
    finally:
        resume_conn.close()
        todo_conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""投递进展日报（A3）：对比昨日快照，报出投递状态的变化。

只读 resume.db，快照写到 data/daily_snapshots/applications_YYYY-MM-DD.json。

用法：
    .venv/bin/python scripts/daily/application_digest.py           # 对比最近一次快照并写今日快照
    .venv/bin/python scripts/daily/application_digest.py --dry-run # 只看 diff，不写快照

输出：变化清单（新增/状态变更/面试时间变更/消失）+ 当前盘面汇总 + 最后一行 JSON。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESUME_DB = ROOT / "data" / "resume.db"
SNAP_DIR = ROOT / "data" / "daily_snapshots"

FIELDS = ("company", "role", "status", "stage", "interview_at", "note", "round_note", "direction")
ACTIVE_STAGES = ("候选", "已投递", "待面", "面试中", "进行中")


def read_applications() -> list[dict]:
    conn = sqlite3.connect(f"file:{RESUME_DB}?mode=ro", uri=True)
    cols = ", ".join(FIELDS)
    rows = conn.execute(f"SELECT id, {cols} FROM applications ORDER BY id").fetchall()
    conn.close()
    out = []
    for r in rows:
        item = dict(zip(("id",) + FIELDS, r, strict=True))
        item = {k: ("" if v is None else str(v)) for k, v in item.items()}
        out.append(item)
    return out


def key_of(item: dict) -> str:
    return f"{item['company']}｜{item['role']}"


def latest_snapshot(today: str) -> Path | None:
    """优先取「今天之前」的最近快照；没有则退回今天的快照（同日多次运行看日内变化）。"""
    files = sorted(SNAP_DIR.glob("applications_*.json"))
    older = [f for f in files if f.stem.replace("applications_", "") < today]
    if older:
        return older[-1]
    same_day = [f for f in files if f.stem == f"applications_{today}"]
    return same_day[-1] if same_day else None


def load_snapshot(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def is_active(item: dict) -> bool:
    blob = f"{item.get('status','')}{item.get('stage','')}"
    return any(s in blob for s in ACTIVE_STAGES) and "已结束" not in blob and "已终止" not in blob


def create_followup_todos(added: list[dict], changed: list[tuple[dict, list[tuple[str, str, str]]]]) -> list[str]:
    """状态变化的投递 → 自动在 interview.db.todo 建跟进待办（幂等：同名 pending 去重）。"""
    if not added and not changed:
        return []
    iv = ROOT / "data" / "interview.db"
    conn = sqlite3.connect(str(iv), timeout=20)
    created = []
    from datetime import date as _date, datetime as _dt, timedelta as _td

    due = (_date.today() + _td(days=2)).isoformat()
    now = _dt.now().strftime("%Y-%m-%d %H:%M")
    today = _date.today().isoformat()

    def insert(title: str, detail: str, company: str, priority: str) -> None:
        dup = conn.execute(
            "SELECT COUNT(*) FROM todo WHERE title=? AND status='pending'", (title,)
        ).fetchone()[0]
        if dup:
            return
        conn.execute(
            "INSERT INTO todo(title, detail, company, due_date, priority, status, created_at, done_at, kind)"
            " VALUES(?,?,?,?,?,'pending',?,'','followup')",
            (title, detail, company, due, priority, now),
        )
        created.append(title)

    for i in added:
        insert(
            f"{i['company']}：新投递跟进（自动）",
            f"{today} 新增投递：{i['company']} · {i['role']} | 状态 {i['status']}。建议 2 天后查看进展；若已约面，把面试时间同步回 resume.db.applications.interview_at。",
            i["company"], "中",
        )
    for item, diffs in changed:
        fields = {f for f, _, _ in diffs}
        prio = "高" if "interview_at" in fields else "中"
        detail_lines = [f"{today} 检测到状态变化：{item['company']} · {item['role']}"]
        for f, old, new in diffs:
            label = {"status": "状态", "stage": "阶段", "interview_at": "面试时间", "round_note": "轮次备注"}.get(f, f)
            detail_lines.append(f"  {label}：{old or '(空)'} → {new or '(空)'}")
        if "interview_at" in fields:
            detail_lines.append("→ 面试时间有变：确认时间/链接/联系人，并准备该岗位弹药。")
        else:
            detail_lines.append("→ 建议判断是否需要跟进（问结果/推进流程），跟进后手动更新 resume.db。")
        insert(f"{item['company']}：跟进投递状态变化（自动）", "\n".join(detail_lines), item["company"], prio)

    conn.commit()
    conn.close()
    return created


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-todo", action="store_true", help="不自动创建跟进待办")
    args = parser.parse_args()

    today = date.today().isoformat()
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    current = read_applications()
    prev_path = latest_snapshot(today)
    prev = load_snapshot(prev_path) if prev_path else []

    cur_map = {key_of(i): i for i in current}
    prev_map = {key_of(i): i for i in prev}

    added = [cur_map[k] for k in cur_map if k not in prev_map]
    removed = [prev_map[k] for k in prev_map if k not in cur_map]
    changed = []
    for k, item in cur_map.items():
        old = prev_map.get(k)
        if not old:
            continue
        diffs = [
            (f, old.get(f, ""), item.get(f, ""))
            for f in ("status", "stage", "interview_at", "round_note")
            if (old.get(f, "") or "") != (item.get(f, "") or "")
        ]
        if diffs:
            changed.append((item, diffs))

    print(f"📮 投递进展日报 · {today}")
    if prev_path:
        print(f"（对比基准：{prev_path.stem.replace('applications_', '')}，共 {len(prev)} 条 → 现在 {len(current)} 条）")
    else:
        print("（首次运行，没有历史快照，只输出当前盘面）")

    if not prev:
        pass
    elif not (added or removed or changed):
        print("\n😐 今日无变化（无新增投递、无状态变更）。")
    else:
        if added:
            print(f"\n🆕 新增 {len(added)} 条：")
            for i in added:
                print(f"  · {i['company']} · {i['role']} | {i['status']}")
        if changed:
            print(f"\n🔄 状态变化 {len(changed)} 条：")
            for item, diffs in changed:
                print(f"  · {item['company']} · {item['role']}")
                for field, old, new in diffs:
                    label = {"status": "状态", "stage": "阶段", "interview_at": "面试时间", "round_note": "轮次备注"}[field]
                    print(f"      {label}：{old or '(空)'} → {new or '(空)'}")
        if removed:
            print(f"\n🗑 记录消失 {len(removed)} 条：")
            for i in removed:
                print(f"  · {i['company']} · {i['role']}（原状态：{i['status']}）")

    active = [i for i in current if is_active(i)]
    print(f"\n📊 当前在推进 {len(active)} 条 / 总计 {len(current)} 条：")
    for i in sorted(active, key=lambda x: x.get("interview_at", "") or "zzz"):
        when = f" | {i['interview_at']}" if i.get("interview_at") else ""
        print(f"  · {i['company']} · {i['role']}{when} —— {i['status']}")

    if not args.dry_run:
        (SNAP_DIR / f"applications_{today}.json").write_text(
            json.dumps(current, ensure_ascii=False, indent=1)
        )

    created_todos: list[str] = []
    if not args.dry_run and not args.no_todo and (added or changed):
        try:
            created_todos = create_followup_todos(added, changed)
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ 自动建跟进待办失败：{exc}")
    if created_todos:
        print(f"\n✅ 已自动创建 {len(created_todos)} 条跟进待办（2 天后到期）：")
        for t in created_todos:
            print(f"  · {t}")

    print("\n" + json.dumps({
        "date": today,
        "total": len(current),
        "active": len(active),
        "added": len(added),
        "changed": len(changed),
        "removed": len(removed),
        "has_changes": bool(added or changed or removed),
        "todos_created": created_todos,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

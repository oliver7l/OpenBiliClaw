#!/usr/bin/env python
"""把「最终定稿」Markdown 单向同步进 travel.db。

用法::

    # 预演：只打印 diff 摘要，一个字节都不写（默认）
    .venv/bin/python scripts/travel/build_travel_db.py

    # 落库
    .venv/bin/python scripts/travel/build_travel_db.py --apply

    # 只同步某几张表 / 额外同步默认不同步的表
    .venv/bin/python scripts/travel/build_travel_db.py --only hotels
    .venv/bin/python scripts/travel/build_travel_db.py --include checklist --dry-run

设计要点（改动前务必读 ``docs/modules/travel.md`` §同步范围）:

* **默认 dry-run**：必须显式 ``--apply`` 才写库。这里刻意不套用 Postel 的
  「发送时保守」，而是反过来——**不确定就别动**。
* **md 是内容真值源**：md 里有的字段，db 一律跟随 md。
* **db 保留自己的状态**：``trip_checklist.owner`` / ``.done``、``trips.status``、
  ``trip_members.id_card`` 这些 md 里根本不存在的列**永不写入**。
* **只补缺**：``trip_days.description`` 这类人工精炼文案只在原值为空时才补，
  想要强制覆盖用 ``--force-description``。
* **陈旧行不自动删**：db 里有而 md 里没有的主键会被统计成「陈旧」报出来，
  只有显式 ``--prune-stale`` 才删除。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from openbiliclaw.travel.md_parser import (  # noqa: E402
    DEFAULT_SECTIONS,
    TravelMarkdownError,
    parse_document,
)

DEFAULT_DB = PROJECT_ROOT / "10_旅游" / "travel.db"
DEFAULT_DATA_DIR = PROJECT_ROOT / "10_旅游"

OPTIONAL_SECTIONS = ("expenses", "checklist", "trip")


@dataclass(frozen=True)
class TablePlan:
    """一张表的同步策略。"""

    table: str
    #: 幂等键（不含 trip_id，执行时自动补在最前）
    key: tuple[str, ...]
    #: 这些列只在 db 原值为空时才写（保护人工精炼/补充的内容）
    fill_if_blank: tuple[str, ...] = ()
    #: True → 已存在的键完全不动（用于带状态的表）
    insert_only: bool = False
    hint: str = ""


def _plans() -> dict[str, TablePlan]:
    return {
        "days": TablePlan(
            table="trip_days",
            key=("day_number",),
            fill_if_blank=("description",),
            hint="date/title 始终跟随 md；description 仅补空。",
        ),
        "flights": TablePlan(
            table="trip_flights",
            key=("flight_type", "flight_no", "passengers"),
            hint="同一航班不同乘机人分组是两行，必须都在键里。",
        ),
        "hotels": TablePlan(
            table="trip_hotels",
            # ⚠️ 刻意不用 (day_number, hotel_name)：db 现有行被人工加了「酒店」后缀
            # （亚朵S→亚朵S酒店、维也纳→维也纳酒店），带名字做键会把 7 晚全判成新行，
            # apply 一遍等于原地复制一份。一行一晚用 day_number 就够；若 md 出现
            # 同一晚两行（分房），plan_diff 会先把重复键报出来而不是静默互相覆盖。
            key=("day_number",),
            hint="一行一晚；同一晚出现两次会被判为重复键并报错。",
        ),
        "members": TablePlan(table="trip_members", key=("name",), hint="id_card 不在 md 里，不写。"),
        "expenses": TablePlan(
            table="trip_expenses",
            key=("category", "item"),
            hint="⚠️ db 现有 (category,item) 不唯一（CZ2312 两笔同名），慎用。",
        ),
        "checklist": TablePlan(
            table="trip_checklist",
            key=("category", "item"),
            insert_only=True,
            hint="owner/done 只存在于 db，所以只对这张表「只插不改」。",
        ),
    }


def _print(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# 库读写
# ---------------------------------------------------------------------------


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]  # noqa: S608 - 表名来自内部常量


def _load_existing(conn: sqlite3.Connection, plan: TablePlan, trip_id: int) -> dict[tuple[str, ...], dict[str, Any]]:
    """读出现有行，按幂等键索引。"""
    rows = conn.execute(
        f"SELECT * FROM {plan.table} WHERE trip_id = ? ORDER BY id",  # noqa: S608
        (trip_id,),
    ).fetchall()
    out: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        values = dict(row)
        out[tuple(str(values.get(col) or "") for col in plan.key)] = values
    return out


def _flatten(value: Any) -> Any:
    """比较用的归一化：空串 / None 一视同仁，数字一律 float。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return str(value)


def plan_diff(
    plan: TablePlan, derived: list[dict[str, Any]], existing: dict[tuple[str, ...], dict[str, Any]], columns: list[str]
) -> dict[str, Any]:
    """算出这张表的 新增 / 更新 / 未变 / 陈旧。

    只比较**真的会写**的列：受保护的列压根不进比较集合，所以「没变化」
    不会因为 owner 这种 md 里没有的字段而被误判成有 diff。
    """
    to_create: list[dict[str, Any]] = []
    to_update: list[dict[str, Any]] = []
    unchanged = 0

    _assert_unique_keys(plan, derived)

    for row in derived:
        key = tuple(str(row.get(col) or "") for col in plan.key)
        current = existing.get(key)
        writable = _writable_values(plan, row, current, columns)
        if current is None:
            to_create.append({"key": key, "values": writable})
            continue
        if plan.insert_only:
            unchanged += 1
            continue
        changed = [c for c, v in writable.items() if _flatten(current.get(c)) != _flatten(v)]
        if changed:
            to_update.append({"key": key, "id": current["id"], "values": writable, "changed": changed})
        else:
            unchanged += 1

    derived_keys = {tuple(str(r.get(c) or "") for c in plan.key) for r in derived}
    stale = [{"key": k, "id": v["id"]} for k, v in existing.items() if k not in derived_keys]
    renames = _guess_renames(plan.key, derived_keys, [item["key"] for item in stale])

    return {
        "table": plan.table,
        "create": to_create,
        "update": to_update,
        "unchanged": unchanged,
        "stale": stale,
        "renames": renames,
        "hint": plan.hint,
    }


def _guess_renames(
    key_columns: tuple[str, ...], derived_keys: set[tuple[str, ...]], stale_keys: list[tuple[str, ...]]
) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """找出「只差最后一列」的新键 ↔ 陈旧键配对。

    典型场景：db 里乘机人写成简称 ``刘霞,姐夫,田佳禾,岳母``，md 里是全称
    ``刘霞霞,田海军,田嘉和,师保花`` —— 键不同意味着插入新行 + 留下一行陈旧数据。
    这种情况**不自动合并**（也可能是真的两笔），但要单独喊出来让人决定。
    """
    if len(key_columns) < 2:
        return []
    pairs: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for new_key in sorted(derived_keys):
        for old_key in stale_keys:
            if new_key[:-1] == old_key[:-1] and new_key != old_key:
                pairs.append((new_key, old_key))
    return pairs


def _writable_values(
    plan: TablePlan,
    row: dict[str, Any],
    current: dict[str, Any] | None,
    columns: list[str],
) -> dict[str, Any]:
    """挑出本次**允许写入**的列。

    * 不在 db schema 里的键跳过（解析器和库表版本错开时不炸）；
    * key 列不重复写；
    * ``fill_if_blank`` 的列只在原值为空时出现 —— 这是保护人工文案的关键开关。
    """
    out: dict[str, Any] = {}
    for column, value in row.items():
        if column in plan.key or column == "trip_id":
            continue
        if column not in columns:
            continue
        if plan.insert_only and current is not None:
            continue
        if column in plan.fill_if_blank and current is not None and _flatten(current.get(column)) is not None:
            continue
        out[column] = value
    return out


def _assert_unique_keys(plan: TablePlan, derived: list[dict[str, Any]]) -> None:
    """幂等键在 md 内部必须唯一，否则后一行会静默覆盖前一行。

    这条守卫是为了 hotels 只用 ``day_number`` 做键而加的：万一哪天 md 里出现
    「同一晚两间房」，宁可中止也不要悄悄丢一行。
    """
    counts: dict[tuple[str, ...], int] = {}
    for row in derived:
        key = tuple(str(row.get(col) or "") for col in plan.key)
        counts[key] = counts.get(key, 0) + 1
    duplicated = [key for key, n in counts.items() if n > 1]
    if duplicated:
        raise ValueError(
            f"{plan.table}: md 解析出重复幂等键 {list(plan.key)} → {duplicated}；"
            f"已中止，避免互相覆盖"
        )


def apply_diff(
    conn: sqlite3.Connection, plan: TablePlan, diff: dict[str, Any], trip_id: int, *, prune_stale: bool
) -> dict[str, int]:
    """把 diff 落到库里。调用方负责事务。"""
    created = updated = deleted = 0

    for item in diff["create"]:
        values: dict[str, Any] = {"trip_id": trip_id}
        values.update(dict(zip(plan.key, item["key"], strict=False)))
        values.update(item["values"])
        cols = ", ".join(values)
        marks = ", ".join(f":{c}" for c in values)
        conn.execute(f"INSERT INTO {plan.table} ({cols}) VALUES ({marks})", values)  # noqa: S608
        created += 1

    for item in diff["update"]:
        values = dict(item["values"])
        if not values:
            continue
        sets = ", ".join(f"{c} = :{c}" for c in values)
        values["id"] = item["id"]
        conn.execute(f"UPDATE {plan.table} SET {sets} WHERE id = :id", values)  # noqa: S608
        updated += 1

    if prune_stale and not plan.insert_only:
        for item in diff["stale"]:
            conn.execute(f"DELETE FROM {plan.table} WHERE id = ?", (item["id"],))  # noqa: S608
            deleted += 1

    return {"create": created, "update": updated, "delete": deleted}


# ---------------------------------------------------------------------------
# trip 主记录
# ---------------------------------------------------------------------------


def _resolve_trip_id(conn: sqlite3.Connection, trip_id: int | None) -> int:
    """确定写到哪个行程。

    ⚠️ 刻意**不更新**已存在的 trips 行：``destination`` / ``status`` / 富文本
    ``notes`` 都是 md 里没有的人工沉淀，覆盖它们等于倒退。trips 也不会自动新建 ——
    行程是用户资产，脚本不该替他造一个。
    """
    if trip_id is not None:
        row = conn.execute("SELECT id FROM trips WHERE id = ?", (trip_id,)).fetchone()
        if row is None:
            raise SystemExit(f"!! trips 里没有 id={trip_id} 的记录")
        return int(row["id"])

    row = conn.execute("SELECT id, title FROM trips ORDER BY id LIMIT 1").fetchone()
    if row is None:
        raise SystemExit("!! trips 表为空：请先在桌面端新建行程，再显式 --trip-id 指定")
    return int(row["id"])


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _locate_markdown(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.exists():
            raise SystemExit(f"!! 找不到 Markdown：{path}")
        return path

    candidates = sorted(p for p in DEFAULT_DATA_DIR.glob("*.md") if "定稿" in p.name)
    if not candidates:
        raise SystemExit(
            f"!! {DEFAULT_DATA_DIR} 下没有名字含「定稿」的 Markdown，请用 --md 指定"
        )
    return candidates[0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build_travel_db",
        description="把「最终定稿」Markdown 单向同步进 travel.db（幂等，默认 dry-run）",
    )
    parser.add_argument("--md", default=None, help="源 Markdown（默认取 data/travel 下名字含「定稿」的第一个）")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="目标 travel.db")
    parser.add_argument("--trip-id", type=int, default=None, help="写到哪个行程（默认取 trips 里 id 最小的）")
    parser.add_argument(
        "--mode",
        choices=("dry-run", "apply"),
        default="dry-run",
        help="dry-run=只打 diff 不写库（默认）；apply=真正落库",
    )
    parser.add_argument(
        "--only",
        action="append",
        choices=sorted(set(DEFAULT_SECTIONS) | set(OPTIONAL_SECTIONS)),
        help="只同步这几张表，可重复",
    )
    parser.add_argument(
        "--include",
        action="append",
        choices=sorted(OPTIONAL_SECTIONS),
        help=f"在默认范围之外额外同步的表（默认范围：{list(DEFAULT_SECTIONS)}）",
    )
    parser.add_argument("--prune-stale", action="store_true", help="删除 db 里有而 md 里没有的行")
    parser.add_argument("--force-description", action="store_true", help="连已有的人工 description 也用 md 摘要覆盖")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出统计结果")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    sections: list[str] = list(args.only) if args.only else list(DEFAULT_SECTIONS)
    for extra in args.include or []:
        if extra not in sections:
            sections.append(extra)
    sections = [s for s in sections if s != "trip"]

    md_path = _locate_markdown(args.md)
    db_path = Path(args.db).expanduser()
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    if not db_path.exists():
        raise SystemExit(f"!! 找不到数据库：{db_path}")

    try:
        document = parse_document(md_path.read_text(encoding="utf-8"), sections)
    except TravelMarkdownError as exc:
        _print(f"!! 解析失败：{exc}")
        return 2

    plans = _plans()
    if args.force_description:
        base = plans["days"]
        plans["days"] = TablePlan(base.table, base.key, fill_if_blank=(), hint=base.hint)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    applied: dict[str, dict[str, int]] = {}
    diffs: list[dict[str, Any]] = []
    active: list[str] = []
    try:
        trip_id = _resolve_trip_id(conn, args.trip_id)
        for name in sections:
            plan = plans[name]
            if not _table_exists(conn, plan.table):
                _print(f"!! 缺少表 {plan.table}，跳过")
                continue
            existing = _load_existing(conn, plan, trip_id)
            diffs.append(plan_diff(plan, document[name], existing, _columns(conn, plan.table)))
            active.append(name)

        if args.mode == "apply":
            with conn:
                for name in active:
                    plan = plans[name]
                    diff = next(d for d in diffs if d["table"] == plan.table)
                    applied[plan.table] = apply_diff(conn, plan, diff, trip_id, prune_stale=args.prune_stale)
    except ValueError as exc:
        _print(f"!! {exc}")
        return 2
    finally:
        conn.close()

    _render(md_path, db_path, trip_id, document, diffs, applied, args.mode, plans, active, args.json)
    return 0


def _render(
    md_path: Path,
    db_path: Path,
    trip_id: int,
    document: dict[str, Any],
    diffs: list[dict[str, Any]],
    applied: dict[str, dict[str, int]],
    mode: str,
    plans: dict[str, TablePlan],
    sections: list[str],
    as_json: bool,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "md": str(md_path),
        "db": str(db_path),
        "trip_id": trip_id,
        "mode": mode,
        "tables": {},
    }
    for diff in diffs:
        summary["tables"][diff["table"]] = {
            "create": len(diff["create"]),
            "update": len(diff["update"]),
            "unchanged": diff["unchanged"],
            "stale": len(diff["stale"]),
            "renames": len(diff["renames"]),
            "applied": applied.get(diff["table"], {}),
        }

    if as_json:
        _print(json.dumps(summary, ensure_ascii=False, indent=2))
        return summary

    _print(f"源 md   : {md_path}")
    _print(f"目标库  : {db_path}  (trip_id={trip_id})")
    _print(f"行程天数: {len(document.get('days', []))} 天 / 标题：{document['meta']['title']}")
    _print(f"模式    : {mode}" + ("" if mode == "apply" else "  ← 未写库，加 --apply 才落库"))
    _print("")
    for name in sections:
        plan = plans[name]
        diff = next((d for d in diffs if d["table"] == plan.table), None)
        if diff is None:
            continue
        _print(f"[{plan.table}] 新增 {len(diff['create'])} / 更新 {len(diff['update'])}"
               f" / 未变 {diff['unchanged']} / 陈旧 {len(diff['stale'])}")
        for item in diff["create"][:5]:
            _print(f"   + 新增 {item['key']}")
        for item in diff["update"][:8]:
            _print(f"   ~ 更新 {item['key']} → {', '.join(item['changed'])}")
        for item in diff["stale"][:5]:
            _print(f"   ? 陈旧 {item['key']}（md 里已没有；--prune-stale 可删）")
        for new_key, old_key in diff["renames"][:5]:
            _print(f"   ↔ 疑似改名 {old_key} → {new_key}（同一笔、写法不一致，请人工确认后 --prune-stale）")
        if plan.hint:
            _print(f"   注：{plan.hint}")
        _print("")

    if mode == "dry-run":
        _print("本轮未写库。确认上面的 diff 后再跑一次：")
        _print(f"  .venv/bin/python scripts/travel/build_travel_db.py --apply"
               + (" --prune-stale" if any(d["stale"] for d in diffs) else ""))
    else:
        total = sum(sum(v.values()) for v in applied.values())
        _print(f"✅ 已写库，受影响行数合计 {total}")
    return summary


if __name__ == "__main__":
    raise SystemExit(main())

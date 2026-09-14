"""重置「空转毒化」的聊天会话：analyzed=1 但零产出。

背景（2026-09-15 实测）：
``self_evolution/loop_engine.py`` 每 6 小时调一次 ``analyze_unanalyzed(limit=10)``，
曾经因为没传 ``llm_service``，导致分析全程静默返回空——**但会话照样被标记
analyzed=1**。这些会话此后不再被扫描，产出永久为 0（代码已在 ``5d31f193`` 修复，
不再新增；本脚本处理**存量**）。

判定「被毒化」的口径（必须同时满足）：
- ``chat_sessions.analyzed = 1``
- ``chat_topics`` 中没有该会话的行
- ``chat_insights`` 中没有该会话的行

安全性：
- **默认 dry-run**，必须显式 ``--apply`` 才写库
- apply 前把受影响的 id + 标题 + 原 last_analyzed_at 落到 JSON（可用 ``--revert`` 还原）
- 幂等：重复执行只会对「仍被毒化」的会话生效，已修好的不再动
- 只改 ``analyzed`` / ``last_analyzed_at`` 两列，不动任何消息/话题数据

用法::

    .venv/bin/python scripts/chat_analysis_reset_analyzed.py            # 预览
    .venv/bin/python scripts/chat_analysis_reset_analyzed.py --apply    # 执行
    .venv/bin/python scripts/chat_analysis_reset_analyzed.py --revert <json>
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path("data/chat_analysis.db")
BACKUP_DIR = Path("data/chat_analysis_backups")


def _poisoned_ids(conn: sqlite3.Connection) -> list[tuple[int, str, str | None]]:
    rows = conn.execute(
        """
        SELECT s.id, s.title, s.last_analyzed_at
        FROM chat_sessions s
        WHERE s.analyzed = 1
          AND NOT EXISTS (SELECT 1 FROM chat_topics    t WHERE t.session_id = s.id)
          AND NOT EXISTS (SELECT 1 FROM chat_insights  i WHERE i.session_id = s.id)
        ORDER BY s.id
        """
    ).fetchall()
    return [(int(r[0]), str(r[1]), r[2]) for r in rows]


def _summary(conn: sqlite3.Connection) -> tuple[int, int]:
    one = conn.execute("SELECT COUNT(*) FROM chat_sessions WHERE analyzed = 1").fetchone()[0]
    zero = conn.execute("SELECT COUNT(*) FROM chat_sessions WHERE analyzed = 0").fetchone()[0]
    return int(one), int(zero)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH), help=f"聊天分析库路径（默认 {DB_PATH}）")
    parser.add_argument("--apply", action="store_true", help="真正写库（默认只预览）")
    parser.add_argument("--revert", metavar="JSON", help="按此前导出的 JSON 还原 analyzed=1")
    parser.add_argument(
        "--mark-with-output",
        action="store_true",
        help="补标记：analyzed=0 但**已有** topics/insights 的会话 → 置 1"
        "（避免定时任务重复分析、重复写行）",
    )
    args = parser.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"找不到数据库：{db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(db), timeout=60.0)
    try:
        one_before, zero_before = _summary(conn)
        print(f"库：{db}  （{db.stat().st_size / 1024 / 1024:.0f} MB）")
        print(f"当前：analyzed=1 → {one_before} 个；analyzed=0 → {zero_before} 个")

        if args.revert:
            payload = json.loads(Path(args.revert).read_text(encoding="utf-8"))
            ids = [int(item["id"]) for item in payload["sessions"]]
            print(f"\n还原模式：把 {len(ids)} 个会话恢复为 analyzed=1")
            if not args.apply:
                print("（dry-run，加 --apply 才执行）")
                return 0
            conn.executemany(
                "UPDATE chat_sessions SET analyzed = 1, last_analyzed_at = ? WHERE id = ?",
                [(item.get("last_analyzed_at"), int(item["id"])) for item in payload["sessions"]],
            )
            conn.commit()
            one_after, zero_after = _summary(conn)
            print(f"完成：analyzed=1 → {one_after}；analyzed=0 → {zero_after}")
            return 0

        if args.mark_with_output:
            rows = conn.execute(
                """
                SELECT s.id, s.title
                FROM chat_sessions s
                WHERE s.analyzed = 0
                  AND (EXISTS (SELECT 1 FROM chat_topics   t WHERE t.session_id = s.id)
                    OR EXISTS (SELECT 1 FROM chat_insights i WHERE i.session_id = s.id))
                ORDER BY s.id
                """
            ).fetchall()
            print(f"\n已有产出但未标记的会话：{len(rows)} 个")
            for sid, title in rows[:20]:
                print(f"  id={sid:<6} {str(title)[:36]}")
            if not rows:
                return 0
            if not args.apply:
                print("（dry-run，加 --apply 才执行）")
                return 0
            conn.executemany(
                "UPDATE chat_sessions SET analyzed = 1, last_analyzed_at = ? WHERE id = ?",
                [(datetime.now().isoformat(), int(sid)) for sid, _ in rows],
            )
            conn.commit()
            one_after, zero_after = _summary(conn)
            print(f"已补标记 {len(rows)} 个。analyzed=1 → {one_after}；analyzed=0 → {zero_after}")
            return 0

        poisoned = _poisoned_ids(conn)
        print(f"\n被毒化（已分析但零产出）的会话：{len(poisoned)} 个")
        for sid, title, ts in poisoned[:15]:
            print(f"  id={sid:<6} {ts or '—':<26} {title[:36]}")
        if len(poisoned) > 15:
            print(f"  … 另有 {len(poisoned) - 15} 个")

        if not poisoned:
            print("\n没有需要重置的会话。")
            return 0

        if not args.apply:
            print("\n[dry-run] 未做任何改动。确认后加 --apply。")
            return 0

        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = BACKUP_DIR / f"reset-analyzed-{stamp}.json"
        backup.write_text(
            json.dumps(
                {
                    "db": str(db),
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "reason": "重置空转毒化会话（analyzed=1 但零 topics/insights）",
                    "sessions": [
                        {"id": sid, "title": title, "last_analyzed_at": ts}
                        for sid, title, ts in poisoned
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n已导出可回滚清单：{backup}")

        conn.executemany(
            "UPDATE chat_sessions SET analyzed = 0, last_analyzed_at = NULL WHERE id = ?",
            [(sid,) for sid, _, _ in poisoned],
        )
        conn.commit()

        one_after, zero_after = _summary(conn)
        print(f"已重置 {len(poisoned)} 个会话。")
        print(f"现在：analyzed=1 → {one_after} 个（应只剩真正有产出的）；analyzed=0 → {zero_after} 个")
        print(f"回滚：.venv/bin/python {Path(__file__).name} --revert {backup} --apply")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

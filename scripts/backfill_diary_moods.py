#!/usr/bin/env python3
"""把日记情绪分析结果回填到 ``diary_entries``，并重建用户画像。

背景
----
``EmotionAnalyzer`` 只把效价/唤醒写进 ``diary_emotion_analyses``，从不回写
``diary_entries.mood`` / ``mood_score``；而画像（``SelfEvolutionService``）和复盘
链路消费的是后者。结果是存量 925 篇日记的 ``emotional_baseline`` 恒为
``{"unknown": 925}``，情绪维度完全失效。

本脚本做两件事，且**幂等可重复执行**：

1. 用已有分析结果回填 ``diary_entries.mood`` / ``mood_score``；
2. 重建指定日期的用户画像，让 ``emotional_baseline`` 读到真实分布。

用法
----
```bash
.venv/bin/python scripts/backfill_diary_moods.py                    # 回填 + 重建画像
.venv/bin/python scripts/backfill_diary_moods.py --force            # 连已有 mood 标注一起覆盖
.venv/bin/python scripts/backfill_diary_moods.py --no-rebuild       # 只回填
.venv/bin/python scripts/backfill_diary_moods.py --db data/diary.db --date 2026-09-11
```
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def _mood_distribution(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT mood, COUNT(*) AS n FROM diary_entries GROUP BY mood").fetchall()
    finally:
        conn.close()
    return {row["mood"]: row["n"] for row in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="回填日记情绪并重建画像（幂等）")
    parser.add_argument("--db", default="data/diary.db", help="日记库路径，默认 data/diary.db")
    parser.add_argument(
        "--date",
        default=None,
        help="画像日期（YYYY-MM-DD），默认今天；画像只统计该日期及之前的日记",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="覆盖已有 mood 标注（默认只回填 mood='unknown' 的条目）",
    )
    parser.add_argument("--no-rebuild", action="store_true", help="只回填，不重建画像")
    args = parser.parse_args()

    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print(f"✗ 找不到日记库: {db_path}", file=sys.stderr)
        return 1

    from openbiliclaw.diary.emotion import EmotionAnalyzer
    from openbiliclaw.diary.self_evolution import SelfEvolutionService
    from openbiliclaw.diary.store import DiaryStore

    store = DiaryStore(db_path=db_path)
    analyzer = EmotionAnalyzer(store)

    print(f"日记库: {db_path}")
    print(f"回填前 mood 分布: {_mood_distribution(db_path)}")

    updated = analyzer.backfill_entry_moods(only_unknown=not args.force)
    print(f"✓ 回填 {updated} 条 diary_entries.mood / mood_score")
    print(f"回填后 mood 分布: {_mood_distribution(db_path)}")

    if args.no_rebuild:
        return 0

    target_date = args.date or datetime.now().strftime("%Y-%m-%d")
    service = SelfEvolutionService(store)
    profile = service.update_user_profile(target_date)
    baseline = profile.emotional_baseline or {}
    print(f"✓ 已重建画像 profile_date={profile.profile_date} 条目数={profile.total_entries_analyzed}")
    print("  emotional_baseline:")
    for key in (
        "average_mood",
        "volatility",
        "positive_ratio",
        "negative_ratio",
        "neutral_ratio",
        "dominant_mood",
        "source",
    ):
        print(f"    {key}: {baseline.get(key)}")
    print(f"    mood_distribution: {baseline.get('mood_distribution')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

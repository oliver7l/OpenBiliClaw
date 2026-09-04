#!/usr/bin/env python3
"""Backfill reading-library signals into the live preference profile.

E2 (real-time feedback loop): the user's reading library — ``read_archive``
(finished long-reads) and ``articles`` marked ``finished`` / ``favorited`` —
is the highest-quality interest evidence the system has, yet only the bare
title of these items ever reached the preference analyzer (tags lived in
event metadata that the LLM prompt never saw). This script:

1. loads recently finished / favorited items (title + tags + source),
2. folds the tags into a natural-language context (same shape as the
   ``article_finished`` event E2a produces in-app),
3. runs the same ``PreferenceAnalyzer.analyze_events`` pipeline the soul
   engine uses, merging extracted interests into the *live* preference and
   onion profile — so subsequent ``serve()`` calls rank by what the user
   is actually reading.

The write-back mirrors ``soul.layer_updaters._update_interest`` exactly
(flat preference layer + ``populate_from_flat_preference`` + soul layer
persist + profile-file sync), so the change is visible to the recommendation
engine immediately. Any analyzer failure aborts before writing.

Usage:
    .venv/bin/python scripts/backfill_reading_to_profile.py [--limit 50]
    .venv/bin/python scripts/backfill_reading_to_profile.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

logger = logging.getLogger("backfill_reading_to_profile")

_EVENT_TYPE = "article_finished"
_SIGNAL_STRENGTH = 0.8


def _parse_tags(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(t).strip() for t in value if str(t).strip()]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(t).strip() for t in parsed if str(t).strip()]
        except json.JSONDecodeError:
            pass
        return [t.strip() for t in value.replace("，", ",").split(",") if t.strip()]
    return []


def _tags_suffix(tags: list[str], *, max_tags: int = 8) -> str:
    if not tags:
        return ""
    return "标签:" + ",".join(tags[:max_tags])


def _item_to_event(row: dict[str, Any]) -> dict[str, object]:
    """Convert one reading item to an article_finished-shaped event."""
    from openbiliclaw.sources.event_format import format_event_context

    title = str(row.get("title") or "").strip()
    author = str(row.get("author") or "").strip()
    tags = _parse_tags(row.get("tags"))
    context = format_event_context(
        event_type=_EVENT_TYPE,
        source_platform=str(row.get("source_type") or row.get("source_name") or "阅读库"),
        title=title,
        author=author,
        extra=_tags_suffix(tags),
    )
    metadata: dict[str, object] = {
        "source_type": str(row.get("source_type") or ""),
        "source_name": str(row.get("source_name") or ""),
        "author": author,
        "tags": row.get("tags") or "[]",
        "signal_strength": _SIGNAL_STRENGTH,
    }
    if row.get("id") is not None:
        metadata["article_id"] = row["id"]
    return {
        "event_type": _EVENT_TYPE,
        "title": title,
        "url": str(row.get("url") or ""),
        "context": context,
        "metadata": metadata,
        "created_at": str(row.get("created_at") or "") or None,
    }


def _load_reading_events(database: Any, limit: int) -> list[dict[str, object]]:
    """Collect finished/favorited reading items as events (newest first)."""
    events: list[dict[str, object]] = []
    seen_urls: set[str] = set()

    # 1) read_archive — fully finished long-reads.
    try:
        archive_rows = database.get_recent_readarchive(limit=limit)
    except Exception:
        logger.exception("get_recent_readarchive failed")
        archive_rows = []
    for row in archive_rows:
        ev = _item_to_event(dict(row))
        url = str(ev.get("url") or "").strip()
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        events.append(ev)

    # 2) articles marked finished or favorited (tags-carrying reading items).
    try:
        article_rows = database.get_recent_articles(limit=limit * 3)
    except Exception:
        logger.exception("get_recent_articles failed")
        article_rows = []
    for row in article_rows:
        status = str(row.get("status") or "").strip()
        favorited = row.get("favorited") in (1, True, "1", "true")
        if status != "finished" and not favorited:
            continue
        ev = _item_to_event(dict(row))
        url = str(ev.get("url") or "").strip()
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        events.append(ev)

    return events


def _diff_preferences(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Build a human-readable changelog between two preference dicts."""
    changes: list[str] = []

    def _interest_map(pref: dict[str, Any]) -> dict[str, float]:
        result: dict[str, float] = {}
        for item in pref.get("interests") or []:
            if isinstance(item, dict) and str(item.get("name") or "").strip():
                try:
                    result[str(item["name"]).strip()] = float(item.get("weight") or 0.0)
                except (TypeError, ValueError):
                    result[str(item["name"]).strip()] = 0.0
        return result

    old_i, new_i = _interest_map(before), _interest_map(after)
    for name in new_i:
        if name not in old_i:
            changes.append(f"新增兴趣: {name} ({new_i[name]:.2f})")
        elif abs(new_i[name] - old_i.get(name, 0.0)) > 0.15:
            changes.append(
                f"兴趣权重变化: {name} {old_i[name]:.2f} → {new_i[name]:.2f}"
            )

    def _topic_set(pref: dict[str, Any]) -> set[str]:
        return {
            str(t).strip()
            for t in (pref.get("disliked_topics") or [])
            if str(t).strip()
        }

    for topic in sorted(_topic_set(after) - _topic_set(before)):
        changes.append(f"新增讨厌: {topic}")
    return changes


async def run(database: Any, memory: Any, *, limit: int, dry_run: bool) -> int:
    from openbiliclaw.llm.registry import build_llm_registry
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer
    from openbiliclaw.soul.profile import OnionProfile

    events = _load_reading_events(database, limit=limit)
    print(f"[reading→profile] 读取已读条目 {len(events)} 条（limit={limit}）")
    if not events:
        print("[reading→profile] 无已读条目，无事可做。")
        return 0

    if dry_run:
        print("[reading→profile] --dry-run：以下事件将被送入偏好分析（最多显示 8 条）:")
        for ev in events[:8]:
            print(f"  - {ev['title']} | {ev['context']}")
        return 0

    cfg = __import__("openbiliclaw.config", fromlist=["load_config"]).load_config()
    registry = build_llm_registry(cfg)
    from openbiliclaw.llm.service import LLMService

    llm_service = LLMService(registry=registry, memory=memory, concurrency=1)
    analyzer = PreferenceAnalyzer(llm_service)

    preference_layer = memory.get_layer("preference")
    existing_preference = dict(preference_layer.data)
    print(
        f"[reading→profile] 现有画像: {len(existing_preference.get('interests') or [])} 个兴趣"
    )

    try:
        updated = await analyzer.analyze_events(
            events=events,
            existing_preference=existing_preference,
            event_chunk_size=20,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[reading→profile] 偏好分析失败（{exc!r}），未写回画像。")
        return 2
    if not isinstance(updated, dict):
        print("[reading→profile] 分析结果异常（非 dict），未写回。")
        return 2
    if not isinstance(updated.get("interests"), list):
        print("[reading→profile] 分析结果缺少 interests 列表，未写回。")
        return 2

    changes = _diff_preferences(existing_preference, updated)
    if not changes:
        print("[reading→profile] 分析完成，画像无实质变化（新增/权重变化 0 项）。")
    else:
        print(f"[reading→profile] 画像变化 {len(changes)} 项：")
        for change in changes:
            print(f"  + {change}")

    # Write-back — mirror soul.layer_updaters._update_interest.
    preference_layer.data.clear()
    preference_layer.data.update(updated)
    preference_layer.save()

    soul_layer = memory.get_layer("soul")
    profile = OnionProfile.from_dict(dict(soul_layer.data))
    profile.populate_from_flat_preference(updated)
    soul_layer.data.clear()
    soul_layer.data.update(profile.to_dict())
    soul_layer.save()
    memory.sync_profile_files(profile)

    print("[reading→profile] 已写回 preference + onion profile，推荐引擎下次 serve 生效。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill reading-library signals into the preference profile."
    )
    parser.add_argument("--limit", type=int, default=50, help="每个数据源取最近 N 条（默认 50）")
    parser.add_argument(
        "--dry-run", action="store_true", help="只展示将送入分析的事件，不写回画像"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    from openbiliclaw.config import load_config
    from openbiliclaw.memory.manager import MemoryManager

    cfg = load_config()
    data_dir = Path(cfg.data_path)
    memory = MemoryManager(data_dir)
    memory.initialize()
    database = memory._database  # noqa: SLF001 — same shared handle used by the app

    return asyncio.run(run(database, memory, limit=args.limit, dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())

"""CLI 外部任务「入队 → 收集 → 落库」helper（知乎 / 抖音）。

从上帝文件 ``cli/__init__.py`` 抽出（P4 第十刀，2026-09-14）。

内容：知乎 / 抖音的搜索与发现任务三段式封装
（``_enqueue_*`` 入队 → ``_collect_*`` 等待完成取回候选），
以及把收集到的原始事件去重写回 memory 的落库 helper
（``_event_memory_key`` / ``_load_existing_event_keys`` / ``_write_events_to_memory``）。

本模块**不含 typer 命令**，故无需 ``register()``；所有符号由主文件顶层
导入后在 cli 命名空间 re-export。本模块顶层**不得** import
``openbiliclaw.cli``（避免循环依赖）。

patch 语义保留：被 ``tests/cli`` patch 到 **cli 命名空间** 的符号
（``_get_runtime_database`` / ``_build_memory_manager`` /
``_enqueue_xhs_bootstrap_task`` / ``_collect_xhs_bootstrap_events`` /
``console``）一律在**函数体内**经 ``from openbiliclaw import cli as _cli``
动态取属性——顶层 from-import 会绑定旧值并破坏
``monkeypatch.setattr(cli_module, ...)``。
``_kick_task_dispatcher`` 只被 patch 到 ``runtime.init_flow`` 命名空间
（供 init_flow 自身使用），本模块顶层直接导入即可。
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from openbiliclaw.runtime.init_flow import _kick_task_dispatcher


def _import_xhs_bootstrap_events() -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Backwards-compatible single-shot wrapper used by tests.

    For the live ``init`` flow we use the split enqueue/collect API
    above so xhs data collection runs in parallel with B站 fetches
    instead of serialising for a fixed wait. This wrapper preserves
    the old test contract.
    """

    from openbiliclaw import cli as _cli  # noqa: E402

    task_id = _cli._enqueue_xhs_bootstrap_task()
    events, counts, _status = _cli._collect_xhs_bootstrap_events(task_id)
    return events, counts


def _event_memory_key(event: dict[str, Any]) -> tuple[str, str, str, str, str]:
    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    source = str(metadata.get("source_platform") or "").strip()
    event_type = str(event.get("event_type") or event.get("type") or "").strip()
    url = str(event.get("url") or "").strip()
    content_id = str(metadata.get("content_id") or "").strip()
    import_source = str(metadata.get("import_source") or "").strip()
    title = str(event.get("title") or "").strip()
    identity = content_id or url or title
    return source, event_type, identity, import_source, url


def _load_existing_event_keys(memory: Any, *, limit: int) -> set[tuple[str, str, str, str, str]]:
    query_events = getattr(memory, "query_events", None)
    if not callable(query_events):
        return set()
    try:
        rows = query_events(limit=limit)
    except Exception:
        return set()

    import json as _json

    keys: set[tuple[str, str, str, str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        event = dict(row)
        metadata = event.get("metadata")
        if isinstance(metadata, str):
            try:
                parsed = _json.loads(metadata)
                event["metadata"] = parsed if isinstance(parsed, dict) else {}
            except _json.JSONDecodeError:
                event["metadata"] = {}
        keys.add(_event_memory_key(event))
    return keys


def _write_events_to_memory(events: list[dict[str, Any]], *, source: str = "") -> tuple[int, int]:
    """Persist collected source events to memory with a lightweight duplicate guard."""

    from openbiliclaw import cli as _cli  # noqa: E402

    if not events:
        return 0, 0

    memory = _cli._build_memory_manager()
    existing_keys = _load_existing_event_keys(memory, limit=max(10_000, len(events) * 4))
    batch_keys: set[tuple[str, str, str, str, str]] = set()
    fresh: list[dict[str, Any]] = []
    for event in events:
        key = _event_memory_key(event)
        if key in existing_keys or key in batch_keys:
            continue
        if source:
            metadata = event.get("metadata")
            if isinstance(metadata, dict):
                metadata.setdefault("source_platform", source)
        batch_keys.add(key)
        fresh.append(event)

    async def _propagate() -> None:
        for event in fresh:
            await memory.propagate_event(event)

    asyncio.run(_propagate())
    return len(fresh), len(events) - len(fresh)


def _enqueue_zhihu_search_task(
    keywords: tuple[str, ...],
    *,
    max_items_per_keyword: int = 20,
) -> str | None:
    """Enqueue a Zhihu plugin search task for the browser extension."""

    from openbiliclaw import cli as _cli  # noqa: E402
    from openbiliclaw.config import load_config
    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    normalized_keywords: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        value = str(keyword).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized_keywords.append(value)
    if not normalized_keywords:
        _cli.console.print("  [yellow]知乎搜索任务未入队: 关键词为空。[/yellow]")
        return None

    try:
        database = _cli._get_runtime_database()
    except Exception as exc:
        _cli.console.print(f"  [yellow]知乎搜索任务未入队: 数据库不可用: {exc}[/yellow]")
        return None
    if not hasattr(database, "conn"):
        return None

    try:
        cfg = load_config()
        budget = int(getattr(getattr(cfg.sources, "zhihu", None), "daily_search_budget", 0))
    except Exception:
        budget = 0

    try:
        queue = ZhihuTaskQueue(database)
        task_id = queue.enqueue_with_id(
            "search",
            {
                "keywords": normalized_keywords,
                "max_items_per_keyword": max(1, int(max_items_per_keyword)),
            },
            daily_budget=budget,
        )
    except Exception as exc:
        _cli.console.print(f"  [yellow]知乎搜索任务未入队: {exc}[/yellow]")
        return None
    if not task_id:
        _cli.console.print("  [yellow]知乎搜索任务未入队: 今日任务预算已用完。[/yellow]")
        return None
    _kick_task_dispatcher("zhihu")
    return task_id


def _collect_zhihu_search_results(
    task_id: str | None,
    *,
    max_wait_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    """Wait for a plugin search task and return raw Zhihu candidates."""

    import json
    import time

    from openbiliclaw import cli as _cli  # noqa: E402
    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    if not task_id:
        return [], {}, "skipped"

    try:
        database = _cli._get_runtime_database()
    except Exception:
        return [], {}, "skipped"
    if not hasattr(database, "conn"):
        return [], {}, "skipped"

    queue = ZhihuTaskQueue(database)
    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    task: dict[str, Any] | None = None
    while True:
        task = queue.get(task_id)
        status = str((task or {}).get("status", "")).strip()
        if status in {"completed", "failed"}:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)

    if not task:
        return [], {}, "timeout"
    if task.get("status") == "failed":
        try:
            result = json.loads(str(task.get("result_json") or "{}"))
        except json.JSONDecodeError:
            result = {}
        debug = result.get("debug", {}) if isinstance(result, dict) else {}
        error = str(result.get("error", "") if isinstance(result, dict) else "")
        if error == "zhihu_login_required" or (isinstance(debug, dict) and debug.get("login_required") is True):
            return [], {}, "login_required"
        return [], {}, "failed"
    if task.get("status") != "completed":
        if str(task.get("status", "")).strip() in {"pending", "in_progress"}:
            with suppress(Exception):
                queue.fail(
                    task_id,
                    error="extension_result_timeout",
                    debug={
                        "wait_seconds": max_wait_seconds,
                        "last_status": str(task.get("status", "")),
                    },
                )
        return [], {}, "timeout"

    try:
        result = json.loads(str(task.get("result_json") or "{}"))
    except json.JSONDecodeError:
        return [], {}, "failed"

    items = [v for v in result.get("items", []) if isinstance(v, dict)]
    raw_counts = result.get("scope_counts", {})
    count = len(items)
    if isinstance(raw_counts, dict):
        with suppress(Exception):
            count = int(raw_counts.get("zhihu_search", count) or count)
    status_label = "ok" if items else "empty"
    return items, {"zhihu_search": count}, status_label


def _enqueue_zhihu_discovery_task(
    task_type: str,
    payload: dict[str, object],
    *,
    daily_budget_key: str,
) -> str | None:
    """Enqueue a non-search Zhihu plugin discovery task."""

    from openbiliclaw import cli as _cli  # noqa: E402
    from openbiliclaw.config import load_config
    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    try:
        database = _cli._get_runtime_database()
    except Exception as exc:
        _cli.console.print(f"  [yellow]知乎 {task_type} 任务未入队: 数据库不可用: {exc}[/yellow]")
        return None
    if not hasattr(database, "conn"):
        return None

    try:
        cfg = load_config()
        budget = int(getattr(getattr(cfg.sources, "zhihu", None), daily_budget_key, 0))
    except Exception:
        budget = 0

    try:
        queue = ZhihuTaskQueue(database)
        task_id = queue.enqueue_with_id(task_type, payload, daily_budget=budget)
    except Exception as exc:
        _cli.console.print(f"  [yellow]知乎 {task_type} 任务未入队: {exc}[/yellow]")
        return None
    if not task_id:
        _cli.console.print(f"  [yellow]知乎 {task_type} 任务未入队: 今日任务预算已用完。[/yellow]")
        return None
    _kick_task_dispatcher("zhihu")
    return task_id


def _collect_zhihu_discovery_results(
    task_id: str | None,
    *,
    max_wait_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    """Wait for a plugin Zhihu discovery task and return raw candidates."""

    import json
    import time

    from openbiliclaw import cli as _cli  # noqa: E402
    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    if not task_id:
        return [], {}, "skipped"
    try:
        database = _cli._get_runtime_database()
    except Exception:
        return [], {}, "skipped"
    if not hasattr(database, "conn"):
        return [], {}, "skipped"

    queue = ZhihuTaskQueue(database)
    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    task: dict[str, Any] | None = None
    while True:
        task = queue.get(task_id)
        status = str((task or {}).get("status", "")).strip()
        if status in {"completed", "failed"}:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)

    if not task:
        return [], {}, "timeout"
    if task.get("status") == "failed":
        try:
            result = json.loads(str(task.get("result_json") or "{}"))
        except json.JSONDecodeError:
            result = {}
        debug = result.get("debug", {}) if isinstance(result, dict) else {}
        error = str(result.get("error", "") if isinstance(result, dict) else "")
        if error == "zhihu_login_required" or (isinstance(debug, dict) and debug.get("login_required") is True):
            return [], {}, "login_required"
        return [], {}, "failed"
    if task.get("status") != "completed":
        if str(task.get("status", "")).strip() in {"pending", "in_progress"}:
            with suppress(Exception):
                queue.fail(
                    task_id,
                    error="extension_result_timeout",
                    debug={
                        "wait_seconds": max_wait_seconds,
                        "last_status": str(task.get("status", "")),
                    },
                )
        return [], {}, "timeout"

    try:
        result = json.loads(str(task.get("result_json") or "{}"))
    except json.JSONDecodeError:
        return [], {}, "failed"
    items = [v for v in result.get("items", []) if isinstance(v, dict)]
    raw_counts = result.get("scope_counts", {})
    scope_counts = {str(k): int(v) for k, v in raw_counts.items()} if isinstance(raw_counts, dict) else {}
    return items, scope_counts, "ok" if items else "empty"


def _enqueue_zhihu_discovery_candidates(items: list[dict[str, Any]]) -> tuple[int, list[Any]]:
    """Convert Zhihu search result rows and enqueue them into discovery_candidates."""

    from obc_discovery.candidate_pool import discovered_content_to_candidate_write

    from openbiliclaw import cli as _cli  # noqa: E402
    from openbiliclaw.sources.zhihu_tasks import zhihu_discovery_items_to_contents

    contents = zhihu_discovery_items_to_contents(items)
    if not contents:
        return 0, []
    database = _cli._get_runtime_database()
    writes = [discovered_content_to_candidate_write(item, source_context=item.source_strategy) for item in contents]
    enqueued = int(database.enqueue_discovery_candidates(writes))
    return enqueued, contents


def _enqueue_dy_search_task(
    keywords: tuple[str, ...],
    *,
    max_items_per_keyword: int = 20,
) -> str | None:
    """Enqueue a Douyin plugin search task for the browser extension."""

    from openbiliclaw import cli as _cli  # noqa: E402
    from openbiliclaw.sources.dy_tasks import DyTaskQueue

    normalized_keywords = []
    seen: set[str] = set()
    for keyword in keywords:
        value = str(keyword).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized_keywords.append(value)
    if not normalized_keywords:
        _cli.console.print("  [yellow]抖音搜索任务未入队: 关键词为空。[/yellow]")
        return None

    try:
        database = _cli._get_runtime_database()
    except Exception as exc:
        _cli.console.print(f"  [yellow]抖音搜索任务未入队: 数据库不可用: {exc}[/yellow]")
        return None
    if not hasattr(database, "conn"):
        return None

    try:
        queue = DyTaskQueue(database)
        task_id = queue.enqueue_with_id(
            "search",
            {
                "keywords": normalized_keywords,
                "max_items_per_keyword": max(1, int(max_items_per_keyword)),
            },
            daily_budget=20,
        )
    except Exception as exc:
        _cli.console.print(f"  [yellow]抖音搜索任务未入队: {exc}[/yellow]")
        return None
    if not task_id:
        _cli.console.print("  [yellow]抖音搜索任务未入队: 今日任务预算已用完。[/yellow]")
        return None
    _kick_task_dispatcher("dy")
    return task_id


def _collect_dy_search_results(
    task_id: str | None,
    *,
    max_wait_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    """Wait for a plugin search task and return raw Douyin video candidates."""

    import json
    import time

    from openbiliclaw import cli as _cli  # noqa: E402
    from openbiliclaw.sources.dy_tasks import DyTaskQueue

    if not task_id:
        return [], {}, "skipped"

    try:
        database = _cli._get_runtime_database()
    except Exception:
        return [], {}, "skipped"
    if not hasattr(database, "conn"):
        return [], {}, "skipped"

    queue = DyTaskQueue(database)
    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    task: dict[str, Any] | None = None
    while True:
        task = queue.get(task_id)
        status = str((task or {}).get("status", "")).strip()
        if status in {"completed", "failed"}:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)

    if not task:
        return [], {}, "timeout"
    if task.get("status") == "failed":
        return [], {}, "failed"
    if task.get("status") != "completed":
        return [], {}, "timeout"

    try:
        result = json.loads(str(task.get("result_json") or "{}"))
    except json.JSONDecodeError:
        return [], {}, "failed"

    videos = [v for v in result.get("videos", []) if isinstance(v, dict)]
    raw_counts = result.get("scope_counts", {})
    count = len(videos)
    if isinstance(raw_counts, dict):
        with suppress(Exception):
            count = int(raw_counts.get("dy_search", count) or count)
    status_label = "ok" if videos else "empty"
    return videos, {"dy_search": count}, status_label

"""Guided init 共享管线 — K6 从 cli 收口（refactor-plan 阶段 3）。

``run_guided_init`` 是 CLI 与 API 共用的初始化流水线，原本内嵌在
``cli/__init__.py``（8.8k 行），导致 api→cli 反向依赖。本模块承接：

- ``run_guided_init`` + ``InitResult`` / ``GuidedInitError``
- 拉取 / bootstrap 任务排队 / 事件转换 / source share 等纯编排 helper
- 进度输出用 Rich console（编排层的进度报告，CLI 与 API 共用同一实现）

``cli`` 对仍被命令层使用的 29 个名字做 re-import，既有调用方不受影响；
``api`` 改为直接依赖本模块（api→runtime 方向，合法）。
"""


import asyncio
import os
import re
import sys
from collections.abc import Callable, Coroutine, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import typer
from rich.console import Console

from openbiliclaw.soul.preference_analyzer import DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE

# ── 从 cli/__init__.py 迁入（K6）──────────────────────────────

console = Console()
_RUNTIME_COMPONENTS: dict[str, Any] = {}
_INIT_POOL_TARGET_COUNT = 15
_INIT_BILIBILI_HISTORY_LIMIT = 500
_INIT_BILIBILI_FAVORITE_LIMIT = 500
_INIT_BILIBILI_FOLLOW_LIMIT = 100
_INIT_X_LIKES_LIMIT = 200
_INIT_X_BOOKMARKS_LIMIT = 200
_INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE = 300
_DEFAULT_XHS_BOOTSTRAP_WAIT_SECONDS = 180.0
_DEFAULT_DY_BOOTSTRAP_WAIT_SECONDS = 180.0
_DEFAULT_YT_BOOTSTRAP_WAIT_SECONDS = 240.0
_DEFAULT_ZHIHU_BOOTSTRAP_WAIT_SECONDS = 180.0
_DEFAULT_XHS_BOOTSTRAP_DEDUPE_HOURS = 6.0
_DEFAULT_DY_BOOTSTRAP_DEDUPE_HOURS = 6.0
_DEFAULT_YT_BOOTSTRAP_DEDUPE_HOURS = 6.0
_DEFAULT_ZHIHU_BOOTSTRAP_DEDUPE_HOURS = 6.0
def _print_section_title(title: str) -> None:
    """Render a consistent section title."""
    console.print(f"[bold cyan]{title}[/bold cyan]")
async def _run_with_progress(
    coro: Any,
    *,
    label: str,
    eta_seconds: int,
    tick_seconds: int = 20,
) -> Any:
    """Run a coroutine while printing periodic progress updates.

    Init's LLM-heavy phases (analyze_events, build_initial_profile,
    discover) each take 1-5 minutes of mostly-silent waiting on
    deepseek thinking. Without a heartbeat the user can't tell
    whether the process is alive or stuck. This helper prints one
    "started, ETA Xs" line, ticks every ``tick_seconds`` with
    elapsed/ETA while the work runs, and prints a final completion
    line with actual wall time.
    """
    import time as _time
    from contextlib import suppress as _suppress

    console.print(f"  [dim]→ {label}（预计 ~{eta_seconds}s）[/dim]")
    start = _time.monotonic()

    async def _ticker() -> None:
        while True:
            await asyncio.sleep(tick_seconds)
            elapsed = int(_time.monotonic() - start)
            remaining = max(0, eta_seconds - elapsed)
            console.print(f"  [dim]· {label}: 已用 {elapsed}s / 预计还需 ~{remaining}s[/dim]")

    ticker_task = asyncio.create_task(_ticker())
    try:
        result = await coro
    finally:
        ticker_task.cancel()
        with _suppress(asyncio.CancelledError, BaseException):
            await ticker_task
    elapsed = int(_time.monotonic() - start)
    console.print(f"  [green]✓[/green] {label} 用时 {elapsed}s")
    return result
def _get_runtime_database() -> Any:
    """Build or return the shared runtime database instance."""
    cached = _RUNTIME_COMPONENTS.get("database")
    if cached is not None:
        return cached

    from openbiliclaw.config import load_config
    from openbiliclaw.storage.database import Database

    config = load_config()
    database = Database(config.data_path / "openbiliclaw.db")
    database.initialize()
    _RUNTIME_COMPONENTS["database"] = database
    return database
def _history_item_to_event(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Bilibili history item into a unified event-layer payload.

    Routes through ``build_event()`` (v0.3.22+) so the resulting dict
    has the same shape as Xiaohongshu / future-source events, with a
    natural-language ``context`` the LLM analyzer can consume directly.
    """
    from openbiliclaw.sources.event_format import SOURCE_BILIBILI, build_event

    history_meta = item.get("history", {})
    if not isinstance(history_meta, dict):
        history_meta = {}
    bvid = str(history_meta.get("bvid", "")).strip()
    title = str(item.get("title", "")).strip()
    author = str(item.get("author_name", item.get("author", ""))).strip()
    view_at = history_meta.get("view_at", item.get("view_at", ""))
    return build_event(
        event_type="view",
        source_platform=SOURCE_BILIBILI,
        title=title,
        url=f"https://www.bilibili.com/video/{bvid}" if bvid else "",
        author=author,
        metadata={
            "bvid": bvid,
            "view_at": view_at,
        },
    )
def _x_tweet_to_event(tweet: dict[str, Any], *, event_type: str) -> dict[str, Any] | None:
    """Normalize a twitter-cli ``tweet_to_dict`` into a unified preference event.

    Mirrors ``_history_item_to_event``: routes through ``build_event()`` so X
    likes / bookmarks share the same event shape as B站 favorites and feed the
    soul analyzer identically. ``event_type`` is ``"like"`` (X likes) or
    ``"favorite"`` (X bookmarks) — both are explicit-positive signals. Returns
    ``None`` for tombstones (no ``id``). The canonical URL matches the discovery
    side (``x_normalize``): ``https://x.com/<handle>/status/<id>``.
    """
    from openbiliclaw.sources.event_format import SOURCE_TWITTER, build_event

    tweet_id = str(tweet.get("id", "") or "").strip()
    if not tweet_id:
        return None
    raw_author = tweet.get("author")
    author = raw_author if isinstance(raw_author, dict) else {}
    screen_name = str(author.get("screenName", "") or "").strip()
    author_name = f"@{screen_name}" if screen_name else str(author.get("name", "") or "").strip()
    handle = screen_name or "i"  # x.com/i/status/<id> resolves without a handle
    text = str(tweet.get("articleText") or tweet.get("text") or "").strip()
    first_line = text.splitlines()[0] if text else ""
    title = first_line[:140]
    verb = "点赞" if event_type == "like" else "收藏"
    if title and author_name:
        context = f"在 X {verb}了 {author_name} 的推文:{title}"
    elif title:
        context = f"在 X {verb}了一条推文:{title}"
    else:
        context = f"在 X {verb}了一条推文"
    return build_event(
        event_type=event_type,
        source_platform=SOURCE_TWITTER,
        title=title,
        url=f"https://x.com/{handle}/status/{tweet_id}",
        author=author_name,
        context=context,
        metadata={
            "tweet_id": tweet_id,
            "screen_name": screen_name,
            "body_text": text,
        },
    )
def _is_interactive_terminal() -> bool:
    """Return whether the current process is attached to an interactive TTY."""
    return sys.stdin.isatty() and sys.stdout.isatty()
def _build_draft_profile_for_discover(memory: Any) -> Any:
    """Build a preference-only ``OnionProfile`` so discover can start
    in parallel with ``build_initial_profile`` (P3).

    The full profile builder runs an LLM synthesis call over history +
    preference + awareness + insights to produce
    ``personality_portrait``, ``deep_needs``, ``core_traits`` etc. —
    fields that *colour* discover's evaluation prompt but aren't
    load-bearing for relevance scoring (interests + style +
    favorite_up_users carry the signal). Letting discover use a
    preference-only draft while the real profile builds in the
    background overlaps two phases that previously serialised.
    """
    from openbiliclaw.soul.profile import OnionProfile

    preference_layer = memory.get_layer("preference").data
    draft = OnionProfile()
    draft.populate_from_flat_preference(preference_layer)
    return draft
def _xhs_bootstrap_dedupe_hours() -> float:
    raw = os.environ.get(
        "OPENBILICLAW_XHS_BOOTSTRAP_DEDUPE_HOURS",
        str(_DEFAULT_XHS_BOOTSTRAP_DEDUPE_HOURS),
    )
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_XHS_BOOTSTRAP_DEDUPE_HOURS
def _dy_bootstrap_dedupe_hours() -> float:
    raw = os.environ.get(
        "OPENBILICLAW_DY_BOOTSTRAP_DEDUPE_HOURS",
        str(_DEFAULT_DY_BOOTSTRAP_DEDUPE_HOURS),
    )
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_DY_BOOTSTRAP_DEDUPE_HOURS
def _yt_bootstrap_dedupe_hours() -> float:
    raw = os.environ.get(
        "OPENBILICLAW_YT_BOOTSTRAP_DEDUPE_HOURS",
        str(_DEFAULT_YT_BOOTSTRAP_DEDUPE_HOURS),
    )
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_YT_BOOTSTRAP_DEDUPE_HOURS
def _zhihu_bootstrap_dedupe_hours() -> float:
    raw = os.environ.get(
        "OPENBILICLAW_ZHIHU_BOOTSTRAP_DEDUPE_HOURS",
        str(_DEFAULT_ZHIHU_BOOTSTRAP_DEDUPE_HOURS),
    )
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_ZHIHU_BOOTSTRAP_DEDUPE_HOURS
def _enqueue_xhs_bootstrap_task(*, force: bool = False, kick: bool = True) -> str | None:
    """Fire-and-forget enqueue of the bootstrap_profile task.

    Returns the task_id if enqueue succeeded, ``None`` otherwise (DB
    unavailable, daily budget exhausted, etc.). Doesn't wait — the
    extension picks the task off the queue and runs it in parallel
    with the rest of init.

    Defaults: ``max_scroll_rounds=15`` and ``max_items_per_scope=300``.
    Both can be overridden via env vars
    ``OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS`` and
    ``OPENBILICLAW_XHS_BOOTSTRAP_MAX_ITEMS``.
    """
    from openbiliclaw.sources.xhs_tasks import XhsTaskQueue

    try:
        database = _get_runtime_database()
    except Exception as exc:
        console.print(f"  [yellow]小红书初始化信号未导入: 数据库不可用: {exc}[/yellow]")
        return None
    if not hasattr(database, "conn"):
        return None

    scroll_rounds = int(os.environ.get("OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS", "15"))
    max_items = int(
        os.environ.get(
            "OPENBILICLAW_XHS_BOOTSTRAP_MAX_ITEMS",
            str(_INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE),
        )
    )
    task_id: str | None = None

    try:
        queue = XhsTaskQueue(database)
        dedupe_hours = _xhs_bootstrap_dedupe_hours()
        find_recent = getattr(queue, "find_recent_task", None)
        if not force and dedupe_hours > 0 and callable(find_recent):
            recent = find_recent(
                "bootstrap_profile",
                recent_hours=dedupe_hours,
                statuses=("pending", "in_progress", "completed", "failed"),
            )
            if recent is not None:
                task_id = str(recent.get("id", "")).strip()
                if task_id:
                    status = str(recent.get("status", "unknown"))
                    console.print(
                        "  [dim]复用最近的小红书 bootstrap 任务"
                        f"({status})；需要重新拉取可用 `openbiliclaw fetch-xhs --force`。[/dim]"
                    )
                    return task_id
        task_id = queue.enqueue_with_id(
            "bootstrap_profile",
            {
                "scopes": ["saved", "liked", "xhs_history"],
                "max_items_per_scope": max(1, max_items),
                "max_scroll_rounds": max(0, scroll_rounds),
            },
            daily_budget=10,
        )
    except Exception as exc:
        console.print(f"  [yellow]小红书初始化信号未导入: {exc}[/yellow]")
        return None
    if not task_id:
        console.print("  [yellow]小红书初始化信号未导入: 今日任务预算已用完。[/yellow]")
        return None
    # Wake the extension dispatcher immediately via the runtime-stream
    # WebSocket instead of waiting up to 60s for the next chrome.alarms
    # tick. The kick is best-effort — if the daemon's API isn't running
    # the existing alarm-based poll still picks up the task on next fire.
    # ``kick=False`` lets the guided-init pipeline register task ownership
    # with the coordinator *before* waking the extension (avoids a
    # register-after-kick race where an owned result is treated as foreign).
    if kick:
        _kick_task_dispatcher("xhs")
    return task_id
def _kick_task_dispatcher(source: str) -> None:
    """Fire-and-forget POST to the daemon's task-kick endpoint.

    The daemon broadcasts ``<source>_task_available`` over the
    runtime-stream WebSocket, which the extension's service-worker
    handles by triggering an immediate poll on the matching dispatcher.
    Failures are silent: if the daemon isn't running the existing
    chrome.alarms 60s poll fallback still picks the task up.
    """
    if source not in {"xhs", "dy", "yt", "zhihu"}:
        return
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:8420/api/sources/{source}/kick"
    req = urllib.request.Request(url, method="POST", data=b"")
    # Short timeout — kick is best-effort. Daemon-not-running /
    # network blip / connection-refused all degrade silently to the
    # 60s alarm fallback.
    with suppress(urllib.error.URLError, TimeoutError, OSError):
        urllib.request.urlopen(req, timeout=1.0).close()
def _collect_xhs_bootstrap_events(
    task_id: str | None,
    *,
    max_wait_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    """Wait for and harvest a previously-enqueued bootstrap_profile task.

    Returns ``(events, scope_counts, status_label)`` where
    ``status_label`` is one of:
      - ``"ok"``         — task completed with notes
      - ``"empty"``      — task completed but extension returned 0 notes
      - ``"timeout"``    — wait window expired, task still pending / in-progress
      - ``"failed"``     — extension or backend reported error
      - ``"skipped"``    — no task_id (DB unavailable / budget exhausted)

    The wait deadline starts NOW; callers that enqueued the task earlier
    in the init flow benefit from the parallel-execution head start.
    """
    import json
    import time

    from openbiliclaw.sources.xhs_tasks import (
        XhsTaskQueue,
        xhs_bootstrap_notes_to_events,
    )

    if not task_id:
        return [], {}, "skipped"

    if max_wait_seconds is None:
        max_wait_seconds = float(
            os.environ.get(
                "OPENBILICLAW_XHS_BOOTSTRAP_WAIT_SECONDS",
                str(_DEFAULT_XHS_BOOTSTRAP_WAIT_SECONDS),
            )
        )

    try:
        database = _get_runtime_database()
    except Exception:
        return [], {}, "skipped"
    if not hasattr(database, "conn"):
        return [], {}, "skipped"

    queue = XhsTaskQueue(database)
    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    poll_interval = 0.5
    task: dict[str, Any] | None = None
    while True:
        task = queue.get(task_id)
        status = str((task or {}).get("status", "")).strip()
        if status in {"completed", "failed"}:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_interval)

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
    notes = [note for note in result.get("notes", []) if isinstance(note, dict)]
    events = xhs_bootstrap_notes_to_events(notes)
    raw_counts = result.get("scope_counts", {})
    scope_counts = {"saved": 0, "liked": 0, "xhs_history": 0}
    if isinstance(raw_counts, dict):
        for key in scope_counts:
            with suppress(Exception):
                scope_counts[key] = int(raw_counts.get(key, 0) or 0)
    if not any(scope_counts.values()):
        for event in events:
            metadata = event.get("metadata", {})
            if not isinstance(metadata, dict):
                continue
            source = str(metadata.get("import_source", ""))
            for key in scope_counts:
                if source == f"xhs_bootstrap_{key}":
                    scope_counts[key] += 1
    status_label = "ok" if events else "empty"
    return events, scope_counts, status_label
def _enqueue_dy_bootstrap_task(*, kick: bool = True) -> str | None:
    """Fire-and-forget enqueue of the Douyin bootstrap_profile task.

    Mirror of ``_enqueue_xhs_bootstrap_task`` for the Douyin pipeline.
    No code shared between the two — separate ``DyTaskQueue`` table,
    separate env vars, separate user-visible messages. Soul-engine
    consumes the resulting events through the unified
    ``event_format.build_event`` contract, so the cross-source
    analysis remains uniform downstream.

    Defaults: ``max_scroll_rounds=15`` and ``max_items_per_scope=300``.
    Both can be overridden via env vars
    ``OPENBILICLAW_DY_BOOTSTRAP_SCROLL_ROUNDS`` and
    ``OPENBILICLAW_DY_BOOTSTRAP_MAX_ITEMS``.
    """
    from openbiliclaw.sources.dy_tasks import DyTaskQueue

    try:
        database = _get_runtime_database()
    except Exception as exc:
        console.print(f"  [yellow]抖音初始化信号未导入: 数据库不可用: {exc}[/yellow]")
        return None
    if not hasattr(database, "conn"):
        return None

    scroll_rounds = int(os.environ.get("OPENBILICLAW_DY_BOOTSTRAP_SCROLL_ROUNDS", "15"))
    max_items = int(
        os.environ.get(
            "OPENBILICLAW_DY_BOOTSTRAP_MAX_ITEMS",
            str(_INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE),
        )
    )
    task_id: str | None = None

    try:
        queue = DyTaskQueue(database)
        dedupe_hours = _dy_bootstrap_dedupe_hours()
        find_recent = getattr(queue, "find_recent_task", None)
        if dedupe_hours > 0 and callable(find_recent):
            recent = find_recent(
                "bootstrap_profile",
                recent_hours=dedupe_hours,
                statuses=("pending", "in_progress", "completed", "failed"),
            )
            if recent is not None:
                task_id = str(recent.get("id", "")).strip()
                if task_id:
                    status = str(recent.get("status", "unknown"))
                    console.print(
                        "  [dim]复用最近的抖音 bootstrap 任务"
                        f"({status})；需要重新拉取可设 "
                        "OPENBILICLAW_DY_BOOTSTRAP_DEDUPE_HOURS=0。[/dim]"
                    )
                    return task_id
        task_id = queue.enqueue_with_id(
            "bootstrap_profile",
            {
                "scopes": ["dy_post", "dy_collect", "dy_like", "dy_follow"],
                "max_items_per_scope": max(1, max_items),
                "max_scroll_rounds": max(0, scroll_rounds),
            },
            daily_budget=10,
        )
    except Exception as exc:
        console.print(f"  [yellow]抖音初始化信号未导入: {exc}[/yellow]")
        return None
    if not task_id:
        console.print("  [yellow]抖音初始化信号未导入: 今日任务预算已用完。[/yellow]")
        return None
    if kick:
        _kick_task_dispatcher("dy")
    return task_id
def _collect_dy_bootstrap_events(
    task_id: str | None,
    *,
    max_wait_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    """Wait for and harvest a previously-enqueued Douyin bootstrap task.

    Returns ``(events, scope_counts, status_label)`` where
    ``status_label`` is one of:
      - ``"ok"``         — task completed with videos
      - ``"empty"``      — task completed but extension returned 0 videos
        (typical when the user is not logged in to douyin.com — the
        soft anti-bot returns HTTP 200 + empty body, see design-doc
        Risk #7)
      - ``"timeout"``    — wait window expired, task still pending
      - ``"failed"``     — extension or backend reported error
      - ``"skipped"``    — no task_id (DB unavailable / budget exhausted)
    """
    import json
    import time

    from openbiliclaw.sources.dy_tasks import (
        DyTaskQueue,
        dy_bootstrap_videos_to_events,
    )

    if not task_id:
        return [], {}, "skipped"

    if max_wait_seconds is None:
        max_wait_seconds = float(
            os.environ.get(
                "OPENBILICLAW_DY_BOOTSTRAP_WAIT_SECONDS",
                str(_DEFAULT_DY_BOOTSTRAP_WAIT_SECONDS),
            )
        )

    try:
        database = _get_runtime_database()
    except Exception:
        return [], {}, "skipped"
    if not hasattr(database, "conn"):
        return [], {}, "skipped"

    queue = DyTaskQueue(database)
    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    poll_interval = 0.5
    task: dict[str, Any] | None = None
    while True:
        task = queue.get(task_id)
        status = str((task or {}).get("status", "")).strip()
        if status in {"completed", "failed"}:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_interval)

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
    events = dy_bootstrap_videos_to_events(videos)
    raw_counts = result.get("scope_counts", {})
    scope_counts = {"dy_post": 0, "dy_collect": 0, "dy_like": 0, "dy_follow": 0}
    if isinstance(raw_counts, dict):
        for key in scope_counts:
            with suppress(Exception):
                scope_counts[key] = int(raw_counts.get(key, 0) or 0)
    if not any(scope_counts.values()):
        # Fall back to per-event count: dy_bootstrap_videos_to_events
        # tags each event's metadata.import_source as
        # "dy_bootstrap_<scope_short>" (post / collect / like / follow).
        for event in events:
            metadata = event.get("metadata", {})
            if not isinstance(metadata, dict):
                continue
            source = str(metadata.get("import_source", ""))
            for key in scope_counts:
                short = key.removeprefix("dy_") if key.startswith("dy_") else key
                if source == f"dy_bootstrap_{short}":
                    scope_counts[key] += 1
    status_label = "ok" if events else "empty"
    return events, scope_counts, status_label
def _enqueue_yt_bootstrap_task(*, kick: bool = True) -> str | None:
    """Enqueue a YouTube bootstrap_profile task for the browser extension.

    Defaults: ``max_scroll_rounds=10`` and ``max_items_per_scope=300``.
    Both can be overridden via env vars
    ``OPENBILICLAW_YT_BOOTSTRAP_SCROLL_ROUNDS`` and
    ``OPENBILICLAW_YT_BOOTSTRAP_MAX_ITEMS``.
    """
    from openbiliclaw.sources.yt_tasks import YtTaskQueue

    try:
        database = _get_runtime_database()
    except Exception as exc:
        console.print(f"  [yellow]YouTube 初始化信号未导入: 数据库不可用: {exc}[/yellow]")
        return None
    if not hasattr(database, "conn"):
        return None

    scroll_rounds = int(os.environ.get("OPENBILICLAW_YT_BOOTSTRAP_SCROLL_ROUNDS", "10"))
    max_items = int(
        os.environ.get(
            "OPENBILICLAW_YT_BOOTSTRAP_MAX_ITEMS",
            str(_INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE),
        )
    )
    task_id: str | None = None

    try:
        queue = YtTaskQueue(database)
        dedupe_hours = _yt_bootstrap_dedupe_hours()
        find_recent = getattr(queue, "find_recent_task", None)
        if dedupe_hours > 0 and callable(find_recent):
            recent = find_recent(
                "bootstrap_profile",
                recent_hours=dedupe_hours,
                statuses=("pending", "in_progress", "completed", "failed"),
            )
            if recent is not None:
                task_id = str(recent.get("id", "")).strip()
                if task_id:
                    status = str(recent.get("status", "unknown"))
                    console.print(
                        "  [dim]复用最近的 YouTube bootstrap 任务"
                        f"({status})；需要重新拉取可设 "
                        "OPENBILICLAW_YT_BOOTSTRAP_DEDUPE_HOURS=0。[/dim]"
                    )
                    return task_id
        task_id = queue.enqueue_with_id(
            "bootstrap_profile",
            {
                "scopes": ["yt_history", "yt_subscriptions", "yt_likes"],
                "max_items_per_scope": max(1, max_items),
                "max_scroll_rounds": max(0, scroll_rounds),
            },
            daily_budget=10,
        )
    except Exception as exc:
        console.print(f"  [yellow]YouTube 初始化信号未导入: {exc}[/yellow]")
        return None
    if not task_id:
        console.print("  [yellow]YouTube 初始化信号未导入: 今日任务预算已用完。[/yellow]")
        return None
    if kick:
        _kick_task_dispatcher("yt")
    return task_id
def _collect_yt_bootstrap_events(
    task_id: str | None,
    *,
    max_wait_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    """Wait for and harvest a previously-enqueued YouTube bootstrap task.

    Returns ``(events, scope_counts, status_label)`` where
    ``status_label`` is one of ``"ok"``, ``"empty"``, ``"timeout"``,
    ``"failed"``, or ``"skipped"``.
    """
    import json
    import time

    from openbiliclaw.sources.yt_tasks import (
        YtTaskQueue,
        yt_bootstrap_items_to_events,
    )

    if not task_id:
        return [], {}, "skipped"

    if max_wait_seconds is None:
        max_wait_seconds = float(
            os.environ.get(
                "OPENBILICLAW_YT_BOOTSTRAP_WAIT_SECONDS",
                str(_DEFAULT_YT_BOOTSTRAP_WAIT_SECONDS),
            )
        )

    try:
        database = _get_runtime_database()
    except Exception:
        return [], {}, "skipped"
    if not hasattr(database, "conn"):
        return [], {}, "skipped"

    queue = YtTaskQueue(database)
    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    poll_interval = 0.5
    task: dict[str, Any] | None = None
    while True:
        task = queue.get(task_id)
        status = str((task or {}).get("status", "")).strip()
        if status in {"completed", "failed"}:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_interval)

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

    items = [v for v in result.get("items", []) if isinstance(v, dict)]
    events = yt_bootstrap_items_to_events(items)
    raw_counts = result.get("scope_counts", {})
    scope_counts: dict[str, int] = {"yt_history": 0, "yt_subscriptions": 0, "yt_likes": 0}
    if isinstance(raw_counts, dict):
        for key in scope_counts:
            with suppress(Exception):
                scope_counts[key] = int(raw_counts.get(key, 0) or 0)
    if not any(scope_counts.values()):
        for event in events:
            metadata = event.get("metadata", {})
            if not isinstance(metadata, dict):
                continue
            source = str(metadata.get("import_source", ""))
            for key in scope_counts:
                short = key.removeprefix("yt_") if key.startswith("yt_") else key
                if source == f"yt_bootstrap_{short}":
                    scope_counts[key] += 1
    status_label = "ok" if events else "empty"
    return events, scope_counts, status_label
def _enqueue_zhihu_bootstrap_task(
    *,
    profile_slug: str = "",
    kick: bool = True,
    profile_update: bool = False,
) -> str | None:
    """Enqueue a Zhihu bootstrap_events task for the browser extension.

    The extension executes same-origin Zhihu session fetches in the logged-in
    browser. This command is fetch-only; it does not trigger profile generation.
    """
    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    try:
        database = _get_runtime_database()
    except Exception as exc:
        console.print(f"  [yellow]知乎事件未拉取: 数据库不可用: {exc}[/yellow]")
        return None
    if not hasattr(database, "conn"):
        return None

    max_items = int(
        os.environ.get(
            "OPENBILICLAW_ZHIHU_BOOTSTRAP_MAX_ITEMS",
            str(_INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE),
        )
    )
    max_collections = int(os.environ.get("OPENBILICLAW_ZHIHU_BOOTSTRAP_MAX_COLLECTIONS", "20"))
    task_id: str | None = None

    try:
        queue = ZhihuTaskQueue(database)
        dedupe_hours = _zhihu_bootstrap_dedupe_hours()
        find_recent = getattr(queue, "find_recent_task", None)
        if dedupe_hours > 0 and callable(find_recent):
            recent = find_recent(
                "bootstrap_events",
                recent_hours=dedupe_hours,
                statuses=("pending", "in_progress", "completed", "failed"),
            )
            if recent is not None:
                task_id = str(recent.get("id", "")).strip()
                if task_id:
                    status = str(recent.get("status", "unknown"))
                    console.print(
                        "  [dim]复用最近的知乎 bootstrap 任务"
                        f"({status})；需要重新拉取可设 "
                        "OPENBILICLAW_ZHIHU_BOOTSTRAP_DEDUPE_HOURS=0。[/dim]"
                    )
                    return task_id

        scopes = ["zhihu_read_history", "zhihu_collection", "zhihu_activity"]
        if not profile_slug.strip():
            console.print(
                "  [dim]未传 --profile-slug，扩展会尝试从知乎登录态识别当前用户；"
                "识别失败时只返回浏览记录和收藏夹。[/dim]"
            )
        task_id = queue.enqueue_with_id(
            "bootstrap_events",
            {
                "scopes": scopes,
                "profile_slug": profile_slug.strip(),
                "max_items_per_scope": max(1, max_items),
                "max_collections": max(1, max_collections),
                "profile_update": bool(profile_update),
            },
            daily_budget=10,
        )
    except Exception as exc:
        console.print(f"  [yellow]知乎事件未拉取: {exc}[/yellow]")
        return None
    if not task_id:
        console.print("  [yellow]知乎事件未拉取: 今日任务预算已用完。[/yellow]")
        return None
    if kick:
        _kick_task_dispatcher("zhihu")
    return task_id
def _collect_zhihu_bootstrap_events(
    task_id: str | None,
    *,
    max_wait_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    """Wait for and harvest a previously-enqueued Zhihu bootstrap task."""
    import json
    import time

    from openbiliclaw.sources.zhihu_tasks import (
        ZhihuTaskQueue,
        zhihu_bootstrap_items_to_events,
    )

    empty_counts = {
        "zhihu_read_history": 0,
        "zhihu_collection": 0,
        "zhihu_activity_like": 0,
        "zhihu_activity_favorite": 0,
    }
    if not task_id:
        return [], empty_counts, "skipped"

    if max_wait_seconds is None:
        max_wait_seconds = float(
            os.environ.get(
                "OPENBILICLAW_ZHIHU_BOOTSTRAP_WAIT_SECONDS",
                str(_DEFAULT_ZHIHU_BOOTSTRAP_WAIT_SECONDS),
            )
        )

    try:
        database = _get_runtime_database()
    except Exception:
        return [], empty_counts, "skipped"
    if not hasattr(database, "conn"):
        return [], empty_counts, "skipped"

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
        return [], empty_counts, "timeout"
    if task.get("status") == "failed":
        try:
            result = json.loads(str(task.get("result_json") or "{}"))
        except json.JSONDecodeError:
            result = {}
        debug = result.get("debug", {}) if isinstance(result, dict) else {}
        error = str(result.get("error", "") if isinstance(result, dict) else "")
        if error == "zhihu_login_required" or (
            isinstance(debug, dict) and debug.get("login_required") is True
        ):
            return [], empty_counts, "login_required"
        return [], empty_counts, "failed"
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
        return [], empty_counts, "timeout"

    try:
        result = json.loads(str(task.get("result_json") or "{}"))
    except json.JSONDecodeError:
        return [], empty_counts, "failed"

    items = [v for v in result.get("items", []) if isinstance(v, dict)]
    events = zhihu_bootstrap_items_to_events(items)
    scope_counts = dict(empty_counts)
    raw_counts = result.get("scope_counts", {})
    if isinstance(raw_counts, dict):
        for key in scope_counts:
            with suppress(Exception):
                scope_counts[key] = int(raw_counts.get(key, 0) or 0)
    if not any(scope_counts.values()):
        for event in events:
            event_type = str(event.get("event_type", ""))
            metadata = event.get("metadata", {})
            if not isinstance(metadata, dict):
                continue
            source = str(metadata.get("import_source", ""))
            if source == "zhihu_bootstrap_read_history":
                scope_counts["zhihu_read_history"] += 1
            elif source == "zhihu_bootstrap_collection":
                scope_counts["zhihu_collection"] += 1
            elif event_type == "like":
                scope_counts["zhihu_activity_like"] += 1
            elif event_type == "favorite":
                scope_counts["zhihu_activity_favorite"] += 1
    status_label = "ok" if events else "empty"
    return events, scope_counts, status_label
def _dy_events_to_history_items(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Douyin bootstrap events into profile-builder history rows.

    Mirror of ``_xhs_events_to_history_items`` — preserves the
    natural-language ``context`` and tags ``source_platform=douyin``
    so cross-source analysis remains uniform.
    """
    rows: list[dict[str, Any]] = []
    for event in events:
        metadata = event.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        rows.append(
            {
                "title": str(event.get("title", "")).strip(),
                "url": str(event.get("url", "")).strip(),
                "author": str(metadata.get("author", "")).strip(),
                "event_type": str(event.get("event_type", "")).strip(),
                "context": str(event.get("context", "")).strip(),
                "metadata": metadata,
                "source_platform": "douyin",
            }
        )
    return [row for row in rows if row.get("title") or row.get("url")]
def _xhs_events_to_history_items(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert XHS bootstrap events into profile-builder history rows.

    Preserves the natural-language ``context`` field from the source
    event so downstream consumers that opt into context-aware
    summarisation can use it. Profile_builder's current
    ``_summarize_history`` doesn't read ``context``, but keeping it
    intact means the data flows uniformly across sources without
    blocking future analyzer enhancements.
    """
    rows: list[dict[str, Any]] = []
    for event in events:
        metadata = event.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        rows.append(
            {
                "title": str(event.get("title", "")).strip(),
                "url": str(event.get("url", "")).strip(),
                "author": str(metadata.get("author", "")).strip(),
                "event_type": str(event.get("event_type", "")).strip(),
                # v0.3.22+: preserve natural-language context so the
                # history list carries the same single-source-of-truth
                # description as the underlying event.
                "context": str(event.get("context", "")).strip(),
                "metadata": metadata,
                "source_platform": "xiaohongshu",
            }
        )
    return [row for row in rows if row.get("title") or row.get("url")]
def _yt_events_to_history_items(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert YouTube bootstrap events into profile-builder history rows.

    Mirror of ``_xhs_events_to_history_items`` — preserves natural-language
    ``context`` and tags ``source_platform=youtube`` for cross-source analysis.
    """
    rows: list[dict[str, Any]] = []
    for event in events:
        metadata = event.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        rows.append(
            {
                "title": str(event.get("title", "")).strip(),
                "url": str(event.get("url", "")).strip(),
                "author": str(metadata.get("author", "")).strip(),
                "event_type": str(event.get("event_type", "")).strip(),
                "context": str(event.get("context", "")).strip(),
                "metadata": metadata,
                "source_platform": "youtube",
            }
        )
    return [row for row in rows if row.get("title") or row.get("url")]
def _x_events_to_history_items(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert X (Twitter) init events into profile-builder history rows.

    Mirror of ``_xhs_events_to_history_items`` — preserves natural-language
    ``context`` and tags ``source_platform=twitter``. Keeps the profile
    builder fed when X is the only (or one of few) selected init sources.
    """
    rows: list[dict[str, Any]] = []
    for event in events:
        metadata = event.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        rows.append(
            {
                "title": str(event.get("title", "")).strip(),
                "url": str(event.get("url", "")).strip(),
                "author": str(metadata.get("author", "")).strip(),
                "event_type": str(event.get("event_type", "")).strip(),
                "context": str(event.get("context", "")).strip(),
                "metadata": metadata,
                "source_platform": "twitter",
            }
        )
    return [row for row in rows if row.get("title") or row.get("url")]
def _zhihu_events_to_history_items(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Zhihu bootstrap events into profile-builder history rows."""
    rows: list[dict[str, Any]] = []
    for event in events:
        metadata = event.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        rows.append(
            {
                "title": str(event.get("title", "")).strip(),
                "url": str(event.get("url", "")).strip(),
                "author": str(metadata.get("author", "")).strip(),
                "event_type": str(event.get("event_type", "")).strip(),
                "context": str(event.get("context", "")).strip(),
                "metadata": metadata,
                "source_platform": "zhihu",
            }
        )
    return [row for row in rows if row.get("title") or row.get("url")]
def _select_init_source_shares(
    event_counts: Mapping[str, int],
    *,
    enabled_sources: Mapping[str, bool],
    configured_shares: Mapping[str, int],
) -> dict[str, int]:
    """Return source shares selected during interactive init."""
    from openbiliclaw.runtime.source_policy import (
        SOURCE_ORDER,
        suggest_pool_source_shares,
    )

    configured = _merge_source_shares(configured_shares, {})
    suggestion = suggest_pool_source_shares(
        event_counts,
        enabled_sources=enabled_sources,
        configured_shares=configured,
    )
    if not _is_interactive_terminal():
        return configured

    enabled_order = [source for source in SOURCE_ORDER if enabled_sources.get(source, False)]
    console.print()
    console.print("[bold]平台发现比例[/bold]")
    console.print(
        "[dim]根据本次初始化采集到的各平台事件量，推荐后台发现池比例："
        f"{_format_source_shares(suggestion)}。[/dim]"
    )
    if typer.confirm("使用这个比例?", default=True):
        return _merge_source_shares(configured, suggestion)

    raw = typer.prompt(
        "手动输入比例",
        default=",".join(f"{source}={configured.get(source, 1)}" for source in enabled_order),
    ).strip()
    parsed = _parse_source_share_input(raw, enabled_order=enabled_order)
    if not parsed:
        console.print("[yellow]比例输入无效，保留原配置。[/yellow]")
        return configured
    return _merge_source_shares(configured, parsed)
def _maybe_update_init_source_shares(event_counts: Mapping[str, int]) -> None:
    """Ask the user to accept/update source shares after init event collection."""
    try:
        from openbiliclaw.config import load_config, save_config
        from openbiliclaw.runtime.source_policy import source_enabled_map

        cfg = load_config()
        enabled_sources = source_enabled_map(cfg)
        selected = _select_init_source_shares(
            event_counts,
            enabled_sources=enabled_sources,
            configured_shares=cfg.scheduler.pool_source_shares,
        )
        if selected != cfg.scheduler.pool_source_shares:
            cfg.scheduler.pool_source_shares = selected
            save_config(cfg)
    except Exception:
        return
def _merge_source_shares(
    configured_shares: Mapping[str, int],
    updates: Mapping[str, int],
) -> dict[str, int]:
    from openbiliclaw.runtime.source_policy import DEFAULT_POOL_SOURCE_SHARES, SOURCE_ORDER

    merged = dict(DEFAULT_POOL_SOURCE_SHARES)
    for source in SOURCE_ORDER:
        if source in configured_shares:
            try:
                share = int(configured_shares[source])
            except (TypeError, ValueError):
                continue
            if share > 0:
                merged[source] = share
    for source, raw_share in updates.items():
        if source not in SOURCE_ORDER:
            continue
        try:
            share = int(raw_share)
        except (TypeError, ValueError):
            continue
        if share > 0:
            merged[source] = share
    return {source: merged[source] for source in SOURCE_ORDER if source in merged}
def _parse_source_share_input(raw: str, *, enabled_order: list[str]) -> dict[str, int]:
    if not raw.strip():
        return {}

    parsed: dict[str, int] = {}
    if "=" in raw:
        for part in re.split(r"[,，\s]+", raw.strip()):
            if not part or "=" not in part:
                continue
            key, value = part.split("=", 1)
            source = key.strip().lower()
            if source not in enabled_order:
                continue
            try:
                share = int(value)
            except ValueError:
                continue
            if share > 0:
                parsed[source] = share
        return parsed

    values = [item for item in re.split(r"[:：,，\s]+", raw.strip()) if item]
    for source, value in zip(enabled_order, values, strict=False):
        try:
            share = int(value)
        except ValueError:
            continue
        if share > 0:
            parsed[source] = share
    return parsed
def _format_source_shares(shares: Mapping[str, int]) -> str:
    labels = {
        "bilibili": "B站",
        "xiaohongshu": "小红书",
        "douyin": "抖音",
        "youtube": "YouTube",
    }
    return ", ".join(f"{labels.get(source, source)}={share}" for source, share in shares.items())


@dataclass
class InitResult:
    """Outcome of :func:`run_guided_init`, consumed by the CLI summary
    and (gui-init) the API init endpoint.
    """

    history: list[dict[str, Any]]
    favorites_data: list[dict[str, Any]]
    following_data: list[dict[str, Any]]
    events: list[dict[str, Any]]
    bilibili_event_count: int
    xhs_events: list[dict[str, Any]]
    xhs_scope_counts: dict[str, Any]
    xhs_status: str
    dy_events: list[dict[str, Any]]
    dy_scope_counts: dict[str, Any]
    dy_status: str
    yt_events: list[dict[str, Any]]
    yt_scope_counts: dict[str, Any]
    yt_status: str
    zhihu_events: list[dict[str, Any]]
    zhihu_scope_counts: dict[str, Any]
    zhihu_status: str
    profile_data: Any
    discovered_count: int
    discovery_error: bool
    discover_exc: BaseException | None


class GuidedInitError(Exception):
    """Hard failure raised inside :func:`run_guided_init`.

    ``reason`` is a stable machine code (``empty_history`` /
    ``profile_failed``) the API maps onto ``InitCoordinator.fail`` and
    the CLI maps onto a status panel + non-zero exit.
    """

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        self.message = message
        super().__init__(message)
async def _fetch_bilibili_init_data(
    client: Any,
    *,
    history_limit: int = _INIT_BILIBILI_HISTORY_LIMIT,
    favorite_limit: int,
    follow_limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch B站 history / favorites / following in one event loop.

    Extracted from the old ``init`` closure so the CLI and the API
    guided-init paths share a single B站 fetch (gui-init spec §1).
    Favorites/following limits are resolved by the caller; history uses
    ``_INIT_BILIBILI_HISTORY_LIMIT`` unless a caller passes an override.
    """
    hist = await client.get_user_history(max_items=history_limit)

    favs: list[dict[str, Any]] = []
    try:
        fav_folders = (
            await client.get_all_favorites(
                max_folders=200,
                max_items_per_folder=max(1, favorite_limit),
                max_total_items=favorite_limit,
            )
            if favorite_limit > 0
            else []
        )
        for folder in fav_folders:
            folder_title = folder.folder.title if hasattr(folder, "folder") else "未知"
            for item in folder.items if hasattr(folder, "items") else []:
                if len(favs) >= favorite_limit:
                    break
                upper = item.get("upper", {}) if isinstance(item, dict) else {}
                if not isinstance(upper, dict):
                    upper = {}
                favs.append(
                    {
                        "title": item.get("title", "") if isinstance(item, dict) else str(item),
                        "upper": str(upper.get("name", "")).strip(),
                        "folder": folder_title,
                    }
                )
            if len(favs) >= favorite_limit:
                break
    except Exception as exc:
        console.print(f"  [yellow]收藏夹拉取失败: {exc}[/yellow]")

    follows: list[dict[str, Any]] = []
    try:
        page = 1
        page_size = 50
        while len(follows) < follow_limit:
            page_users = await client.get_following(page=page, page_size=page_size)
            if not page_users:
                break
            for user in page_users:
                if len(follows) >= follow_limit:
                    break
                follows.append(
                    {
                        "name": getattr(user, "uname", str(user)),
                        "sign": getattr(user, "sign", ""),
                    }
                )
            if len(page_users) < page_size:
                break
            page += 1
    except Exception as exc:
        console.print(f"  [yellow]关注列表拉取失败: {exc}[/yellow]")

    return hist, favs, follows
async def _fetch_x_init_data(
    *,
    likes_limit: int,
    bookmarks_limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch the user's own X likes + bookmarks for init preference backfill.

    X is server-side cookie replay (no extension bootstrap task), so — like
    B站 — we fetch directly here. Resolves the synced ``x.com`` cookie via the
    same path the discovery producer uses; if it's absent (user enabled X but
    hasn't logged in / the extension hasn't synced yet) we skip cleanly. All
    fetches are best-effort: a missing / expired cookie or a rate-limit must
    never hard-fail ``init``. Returns ``(likes, bookmarks)`` as
    ``tweet_to_dict`` dicts.
    """
    from openbiliclaw.config import load_config

    cfg = load_config()
    x_cfg = getattr(getattr(cfg, "sources", None), "twitter", None)
    cookie_env = str(getattr(x_cfg, "cookie_env", "OPENBILICLAW_X_COOKIE"))

    from openbiliclaw.sources.x_auth import resolve_x_cookie

    cookie = resolve_x_cookie(data_dir=cfg.data_path, cookie_env=cookie_env)
    if not cookie:
        console.print(
            "  [dim]X 未同步 cookie,跳过点赞/收藏历史回填"
            "(登录 x.com 后扩展会自动同步,下次 init 生效)。[/dim]"
        )
        return [], []

    from openbiliclaw.sources.x_client import XClient

    x_client = XClient(cookie=cookie)
    likes: list[dict[str, Any]] = []
    bookmarks: list[dict[str, Any]] = []
    if likes_limit > 0:
        try:
            likes = await x_client.likes(limit=likes_limit)
        except Exception as exc:
            console.print(f"  [yellow]X 点赞拉取失败: {exc}[/yellow]")
    if bookmarks_limit > 0:
        try:
            bookmarks = await x_client.bookmarks(limit=bookmarks_limit)
        except Exception as exc:
            console.print(f"  [yellow]X 收藏拉取失败: {exc}[/yellow]")
    return likes, bookmarks
async def run_guided_init(
    *,
    client: Any,
    memory: Any,
    soul_engine: Any,
    favorite_limit: int,
    follow_limit: int,
    history_limit: int = _INIT_BILIBILI_HISTORY_LIMIT,
    include_bili: bool = True,
    include_xhs: bool,
    include_dy: bool,
    include_yt: bool,
    include_x: bool = False,
    include_zhihu: bool = False,
    target_pool_count: int,
    discover_backfill: Callable[..., Coroutine[Any, Any, int]],
    coordinator: Any = None,
    run_id: str | None = None,
) -> InitResult:
    """Shared async init pipeline (gui-init spec §1).

    Runs the four init stages in one event loop so neither the CLI
    (``asyncio.run(run_guided_init(...))``) nor the API (``await
    run_guided_init(...)`` on the server loop) nests event loops:

      1. fetch B站 + collect cross-platform bootstrap signals → propagate
      2. analyze preferences
      3/4. build soul profile ‖ backfill discovery pool (parallel)

    Bilibili is optional like every other source (``include_bili``); at
    least one selected source must yield signals or stage 1 raises
    ``GuidedInitError("empty_signals")``. ``client`` may be ``None`` when
    ``include_bili`` is False.

    ``discover_backfill`` is the one genuinely path-specific step: the CLI
    injects :func:`_run_init_discovery_backfill_async` (one-shot engine);
    the API injects ``controller.run_init_backfill`` (holds the refresh
    lock). When ``coordinator``/``run_id`` are supplied, stage transitions
    and enqueued bootstrap task ids are reported for live GUI progress;
    run lifecycle (mark_running / complete / fail) stays with the caller.
    """

    async def _stage_started(n: int) -> None:
        if coordinator is not None and run_id is not None:
            await coordinator.stage_started(run_id, n)

    async def _stage_done(n: int, *, status: str = "ok", reason: str | None = None) -> None:
        if coordinator is not None and run_id is not None:
            await coordinator.stage_done(run_id, n, status=status, reason=reason)

    def _register_task(task_id: str | None) -> None:
        if coordinator is not None and run_id is not None and task_id:
            coordinator.register_enqueued_task(run_id, task_id)

    async def _enqueue_register_kick(
        enqueue_fn: Callable[..., str | None], source: str
    ) -> str | None:
        """Enqueue a bootstrap task off-loop, then wake the extension.

        On the API path (coordinator set) the dispatcher kick is deferred until
        AFTER the task id is registered as init-owned, so a fast extension can't
        post the result before ownership is recorded (which would make the
        task-result handler treat init's own data as foreign and skip memory
        propagation). The CLI path keeps the helper's built-in kick and has no
        ownership to register.
        """
        if coordinator is not None:
            task_id = await asyncio.to_thread(lambda: enqueue_fn(kick=False))
            _register_task(task_id)
            if task_id:
                await asyncio.to_thread(_kick_task_dispatcher, source)
            return task_id
        return await asyncio.to_thread(enqueue_fn)

    # Enqueue the XHS bootstrap task FIRST so the browser extension can
    # run it in parallel with the slow B站 history/favs/follows fetches
    # below (~10–30s). XHS is HTTP-only on B站's side so there's no
    # browser-tab focus conflict; Douyin/YouTube are enqueued LATER,
    # serialised, to avoid two active-tab focus grabs racing.
    xhs_task_id = (
        (await _enqueue_register_kick(_enqueue_xhs_bootstrap_task, "xhs")) if include_xhs else None
    )
    if xhs_task_id:
        console.print("  [dim]已请求扩展拉小红书收藏 / 点赞（后台并行,不阻塞 B 站拉取）。[/dim]")

    # ── Stage 1: fetch + cross-platform bootstrap collect → propagate ──
    await _stage_started(1)
    _print_section_title("1/4 拉取数据")
    history: list[dict[str, Any]] = []
    favorites_data: list[dict[str, Any]] = []
    following_data: list[dict[str, Any]] = []
    if include_bili:
        history, favorites_data, following_data = await _fetch_bilibili_init_data(
            client,
            history_limit=history_limit,
            favorite_limit=favorite_limit,
            follow_limit=follow_limit,
        )
        if not history:
            raise GuidedInitError("empty_history", "当前无法从 B 站历史中生成初始画像。")
        console.print(
            f"  浏览历史 [green]{len(history)}[/green] 条"
            f" / 收藏 [green]{len(favorites_data)}[/green] 个"
            f" / 关注 [green]{len(following_data)}[/green] 人"
        )
    else:
        console.print("  [dim]未选择 B 站来源,跳过 B 站历史 / 收藏 / 关注拉取。[/dim]")

    # Bootstrap collectors poll a DB task queue with a blocking sleep —
    # run them in a worker thread (Database is check_same_thread=False) so
    # the API event loop isn't frozen for the collect window. CLI output /
    # ordering is unchanged (it's sequential here regardless).
    xhs_events, xhs_scope_counts, xhs_status = await asyncio.to_thread(
        _collect_xhs_bootstrap_events, xhs_task_id
    )
    if xhs_status == "ok":
        console.print(
            "  小红书 "
            f"收藏 [green]{xhs_scope_counts.get('saved', 0)}[/green] 个"
            f" / 点赞 [green]{xhs_scope_counts.get('liked', 0)}[/green] 个"
            f" / 浏览记录 [green]{xhs_scope_counts.get('xhs_history', 0)}[/green] 个"
        )
    elif xhs_status == "empty":
        console.print(
            "  [yellow]小红书任务跑通但 0 条 notes —— "
            "可能未登录小红书 / 个人主页没有公开收藏 / 页面 state 漂移。[/yellow]"
        )
    elif xhs_status == "timeout":
        console.print(
            "  [dim]小红书初始化信号未导入：扩展未连接或任务仍在后台跑。"
            "可设 OPENBILICLAW_XHS_BOOTSTRAP_WAIT_SECONDS=180 延长等待。[/dim]"
        )
    elif xhs_status == "failed":
        console.print("  [yellow]小红书任务失败 —— 检查扩展日志,或重试 init。[/yellow]")

    # Now (XHS done) enqueue Douyin. Serialised so the two browser-
    # focus-grabbing dispatchers don't race for the same active tab.
    dy_task_id = (
        (await _enqueue_register_kick(_enqueue_dy_bootstrap_task, "dy")) if include_dy else None
    )
    if dy_task_id:
        console.print(
            "  [dim]已请求扩展拉抖音发布 / 收藏 / 点赞 / 关注"
            "(开始抢一次浏览器焦点,~60-90 秒)。[/dim]"
        )
    dy_events, dy_scope_counts, dy_status = await asyncio.to_thread(
        _collect_dy_bootstrap_events, dy_task_id
    )
    if dy_status == "ok":
        console.print(
            "  抖音 "
            f"发布 [green]{dy_scope_counts.get('dy_post', 0)}[/green] 条"
            f" / 收藏 [green]{dy_scope_counts.get('dy_collect', 0)}[/green] 个"
            f" / 点赞 [green]{dy_scope_counts.get('dy_like', 0)}[/green] 个"
            f" / 关注 [green]{dy_scope_counts.get('dy_follow', 0)}[/green] 人"
        )
    elif dy_status == "empty":
        console.print(
            "  [yellow]抖音任务跑通但 0 条 videos —— "
            "未登录抖音(常见,抖音对未登录返回 200+空 body),或个人主页隐私设置阻拦。[/yellow]"
        )
    elif dy_status == "timeout":
        console.print(
            "  [dim]抖音初始化信号未导入:扩展未连接或任务仍在后台跑。"
            "可设 OPENBILICLAW_DY_BOOTSTRAP_WAIT_SECONDS=180 延长等待。[/dim]"
        )
    elif dy_status == "failed":
        console.print("  [yellow]抖音任务失败 —— 检查扩展日志,或重试 init。[/yellow]")

    # YouTube is enqueued AFTER Douyin completes — same serialisation
    # rationale as XHS→Douyin: each dispatcher opens a foreground tab and
    # grabs focus; running two at once causes tab-focus races.
    yt_task_id = (
        (await _enqueue_register_kick(_enqueue_yt_bootstrap_task, "yt")) if include_yt else None
    )
    if yt_task_id:
        console.print(
            "  [dim]已请求扩展拉 YouTube 观看历史 / 订阅 / 点赞"
            "(开始抢一次浏览器焦点,~30-90 秒)。[/dim]"
        )
    yt_events, yt_scope_counts, yt_status = await asyncio.to_thread(
        _collect_yt_bootstrap_events, yt_task_id
    )
    if yt_status == "ok":
        console.print(
            "  YouTube "
            f"观看历史 [green]{yt_scope_counts.get('yt_history', 0)}[/green] 条"
            f" / 订阅 [green]{yt_scope_counts.get('yt_subscriptions', 0)}[/green] 个"
            f" / 点赞 [green]{yt_scope_counts.get('yt_likes', 0)}[/green] 个"
        )
    elif yt_status == "empty":
        console.print(
            "  [yellow]YouTube 任务跑通但 0 条记录 —— 未登录 YouTube 或页面内容为空。[/yellow]"
        )
    elif yt_status == "timeout":
        console.print(
            "  [dim]YouTube 初始化信号未导入:扩展未连接或任务仍在后台跑。"
            "可设 OPENBILICLAW_YT_BOOTSTRAP_WAIT_SECONDS=300 延长等待。[/dim]"
        )
    elif yt_status == "failed":
        console.print("  [yellow]YouTube 任务失败 —— 检查扩展日志,或重试 init。[/yellow]")

    # Zhihu is also plugin-backed and uses the browser's logged-in zhihu.com
    # session. Keep it serial with the other tab-driving sources.
    zhihu_task_id = (
        (await _enqueue_register_kick(_enqueue_zhihu_bootstrap_task, "zhihu"))
        if include_zhihu
        else None
    )
    if zhihu_task_id:
        console.print(
            "  [dim]已请求扩展拉知乎浏览 / 收藏 / 点赞(使用当前浏览器登录态,~30-90 秒)。[/dim]"
        )
    zhihu_events, zhihu_scope_counts, zhihu_status = await asyncio.to_thread(
        _collect_zhihu_bootstrap_events, zhihu_task_id
    )
    if zhihu_status == "ok":
        zhihu_activity_favorites = int(zhihu_scope_counts.get("zhihu_activity_favorite", 0))
        zhihu_favorites = (
            int(zhihu_scope_counts.get("zhihu_collection", 0)) + zhihu_activity_favorites
        )
        console.print(
            "  知乎 "
            f"浏览 [green]{zhihu_scope_counts.get('zhihu_read_history', 0)}[/green] 条"
            f" / 收藏 [green]{zhihu_favorites}[/green] 条"
            f" / 点赞 [green]{zhihu_scope_counts.get('zhihu_activity_like', 0)}[/green] 条"
        )
    elif zhihu_status == "empty":
        console.print(
            "  [yellow]知乎任务跑通但 0 条记录 —— 可能未登录知乎，或页面数据为空。[/yellow]"
        )
    elif zhihu_status == "login_required":
        console.print("  [yellow]知乎需要登录 —— 请先在当前浏览器登录知乎后重试 init。[/yellow]")
    elif zhihu_status == "timeout":
        console.print(
            "  [dim]知乎初始化信号未导入:扩展未连接或任务仍在后台跑。"
            "可设 OPENBILICLAW_ZHIHU_BOOTSTRAP_WAIT_SECONDS=180 延长等待。[/dim]"
        )
    elif zhihu_status == "failed":
        console.print("  [yellow]知乎任务失败 —— 检查扩展日志,或重试 init。[/yellow]")

    # X (Twitter): server-side cookie replay (no extension bootstrap task), so —
    # like B站 — fetch the user's own likes + bookmarks directly here. Skips
    # cleanly when X is disabled or the cookie isn't synced yet.
    x_likes_data: list[dict[str, Any]] = []
    x_bookmarks_data: list[dict[str, Any]] = []
    if include_x:
        x_likes_data, x_bookmarks_data = await _fetch_x_init_data(
            likes_limit=_INIT_X_LIKES_LIMIT,
            bookmarks_limit=_INIT_X_BOOKMARKS_LIMIT,
        )
        if x_likes_data or x_bookmarks_data:
            console.print(
                f"  X 点赞 [green]{len(x_likes_data)}[/green] 条"
                f" / 收藏 [green]{len(x_bookmarks_data)}[/green] 条"
            )

    # Build events from all data sources via the unified event_format
    # builder so B站 / 小红书 / future-source events share one shape.
    from openbiliclaw.sources.event_format import SOURCE_BILIBILI, build_event

    events = [_history_item_to_event(item) for item in history]
    for fav in favorites_data:
        folder = str(fav.get("folder", "")).strip()
        upper = str(fav.get("upper", "")).strip()
        events.append(
            build_event(
                event_type="favorite",
                source_platform=SOURCE_BILIBILI,
                title=str(fav.get("title", "")),
                author=upper,
                metadata={
                    "folder": folder,
                    "upper": upper,
                },
            )
        )
    for user in following_data:
        sign = str(user.get("sign", "")).strip()
        name = str(user.get("name", ""))
        events.append(
            build_event(
                event_type="follow",
                source_platform=SOURCE_BILIBILI,
                title=name,
                author=name,
                context=(
                    f"在 B 站关注了《{name}》,签名:{sign}" if sign else f"在 B 站关注了《{name}》"
                ),
                metadata={
                    "up_name": name,
                    "sign": sign,
                },
            )
        )
    bilibili_event_count = len(events)
    # X likes/bookmarks are direct-fetched here (no extension task handler to
    # propagate them), so — like B站 — they must be persisted in this run.
    # Appended before the events_to_persist snapshot below; the cross-platform
    # (xhs/dy/yt) extends happen after the snapshot since those are persisted by
    # their task-result handler instead.
    x_likes_events = [
        ev for tw in x_likes_data if (ev := _x_tweet_to_event(tw, event_type="like")) is not None
    ]
    x_bookmark_events = [
        ev
        for tw in x_bookmarks_data
        if (ev := _x_tweet_to_event(tw, event_type="favorite")) is not None
    ]
    events.extend(x_likes_events)
    events.extend(x_bookmark_events)
    x_event_count = len(x_likes_events) + len(x_bookmark_events)
    # Persist B站 + X events to memory here. Cross-platform (xhs/dy/yt) events
    # are propagated by the task-result handler — which, during init, only
    # propagates init-OWNED results and reuses its bootstrap-key dedupe (so a
    # force re-init within the task-reuse window doesn't double-insert). They
    # still feed *this* run's analyze/profile via the collected ``events`` list
    # below; memory persistence is owned by the handler on both CLI and API
    # paths (gui-init review §5e).
    events_to_persist = list(events)
    events_to_persist.extend(zhihu_events)
    events.extend(xhs_events)
    events.extend(dy_events)
    events.extend(yt_events)
    events.extend(zhihu_events)
    # With bilibili now optional, the floor is "at least one selected source
    # produced signals" — an all-empty run can't build a meaningful profile.
    if not events:
        raise GuidedInitError(
            "empty_signals",
            "所选数据来源没有拉到任何行为信号，无法生成初始画像。"
            "请确认对应平台已在浏览器登录（或扩展已连接）后重试 init。",
        )
    # Source-share tuning does an unlocked load_config/save_config. That's
    # fine for the CLI (single-process, no live runtime), but on the API path
    # it would mutate config.toml outside _CONFIG_SAVE_LOCK / rebuild_from_config
    # and race a live backend — so only the CLI (coordinator is None) does it
    # (gui-init review §5e). The API keeps default shares for the first run.
    if coordinator is None:
        _maybe_update_init_source_shares(
            {
                "bilibili": bilibili_event_count,
                "xiaohongshu": len(xhs_events),
                "douyin": len(dy_events),
                "youtube": len(yt_events),
                "twitter": x_event_count,
                "zhihu": len(zhihu_events),
            }
        )
    for event in events_to_persist:
        await memory.propagate_event(event)
    await _stage_done(1)

    # ── Stage 2: analyze preferences ──
    await _stage_started(2)
    _print_section_title("2/4 分析偏好")
    console.print(f"  总信号量: [green]{len(events)}[/green] 条事件")
    # Chunk the event list so bootstrap does bounded batch processing
    # instead of serialising one max-thinking call over hundreds of events.
    await _run_with_progress(
        soul_engine.analyze_events(
            events,
            event_chunk_size=DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE,
        ),
        label="分析偏好（分片批处理）",
        eta_seconds=180,
    )
    await _stage_done(2)

    # ── Stage 3 + 4: build profile ‖ discovery backfill (parallel) ──
    await _stage_started(3)
    await _stage_started(4)
    _print_section_title("3/4 生成画像 + 4/4 发现内容(并发)")
    combined_history: list[dict[str, Any]] = list(history)
    if favorites_data:
        combined_history.append(
            {
                "title": "[收藏夹汇总]",
                "_favorites": favorites_data,
                "_favorites_summary": f"共 {len(favorites_data)} 个收藏，"
                + "涵盖: "
                + ", ".join(
                    set(f.get("folder", "") for f in favorites_data[:100] if f.get("folder"))
                ),
            }
        )
    if following_data:
        combined_history.append(
            {
                "title": "[关注列表汇总]",
                "_following": following_data,
                "_following_summary": f"共关注 {len(following_data)} 人，"
                + "包括: "
                + ", ".join(f["name"] for f in following_data[:100]),
            }
        )
    if xhs_events:
        combined_history.extend(_xhs_events_to_history_items(xhs_events))
    if dy_events:
        combined_history.extend(_dy_events_to_history_items(dy_events))
    if yt_events:
        combined_history.extend(_yt_events_to_history_items(yt_events))
    if zhihu_events:
        combined_history.extend(_zhihu_events_to_history_items(zhihu_events))
    # X likes/bookmarks previously only fed the analyze stage; feeding the
    # profile builder too keeps cross-source flow uniform AND guarantees a
    # non-empty profile input when X is the only selected source.
    if x_likes_events or x_bookmark_events:
        combined_history.extend(_x_events_to_history_items(x_likes_events + x_bookmark_events))

    # Discover starts on a preference-only draft so trending / search /
    # related_chain / explore can score candidates while the LLM
    # synthesizes the rich personality_portrait / deep_needs fields.
    draft_profile = _build_draft_profile_for_discover(memory)

    profile_task = asyncio.create_task(
        _run_with_progress(
            soul_engine.build_initial_profile(combined_history),
            label="生成画像(单次 LLM 综合分析)",
            eta_seconds=70,
        )
    )
    discover_task = asyncio.create_task(
        discover_backfill(
            draft_profile,
            target_pool_count=target_pool_count,
            label_suffix=" — 用 P2 草稿画像并发预热",
        )
    )
    profile_data: Any = None
    discovered_count = 0
    discover_exc: BaseException | None = None
    try:
        # Profile is load-bearing. CancelledError is deliberately NOT caught —
        # it propagates (and the finally tears down the sibling) so the wrapper
        # records `cancelled`, never `completed`.
        try:
            profile_data = await profile_task
        except Exception as exc:
            raise GuidedInitError(
                "profile_failed",
                "画像生成阶段出错。可稍后手动重试 `openbiliclaw init`。",
            ) from exc
        await _stage_done(3)

        # Discover is best-effort: a normal failure leaves a partial pool the
        # user can still start with. Cancellation propagates (not caught).
        try:
            discovered_count = await discover_task
        except Exception as exc:
            discovered_count = 0
            discover_exc = exc
        await _stage_done(
            4,
            status="warning" if discover_exc is not None else "ok",
            reason="discovery_partial" if discover_exc is not None else None,
        )
    finally:
        # Guarantee neither parallel task outlives this scope on ANY exit path —
        # including a CancelledError raised at an await *between* the stages
        # (e.g. _stage_done(3)'s event publish). An orphaned run_init_backfill
        # would otherwise keep holding _refresh_lock. Cancel then drain both.
        for _parallel_task in (profile_task, discover_task):
            if not _parallel_task.done():
                _parallel_task.cancel()
        for _parallel_task in (profile_task, discover_task):
            with suppress(BaseException):
                await _parallel_task

    return InitResult(
        history=history,
        favorites_data=favorites_data,
        following_data=following_data,
        events=events,
        bilibili_event_count=bilibili_event_count,
        xhs_events=xhs_events,
        xhs_scope_counts=xhs_scope_counts,
        xhs_status=xhs_status,
        dy_events=dy_events,
        dy_scope_counts=dy_scope_counts,
        dy_status=dy_status,
        yt_events=yt_events,
        yt_scope_counts=yt_scope_counts,
        yt_status=yt_status,
        zhihu_events=zhihu_events,
        zhihu_scope_counts=zhihu_scope_counts,
        zhihu_status=zhihu_status,
        profile_data=profile_data,
        discovered_count=discovered_count,
        discovery_error=discover_exc is not None,
        discover_exc=discover_exc,
    )

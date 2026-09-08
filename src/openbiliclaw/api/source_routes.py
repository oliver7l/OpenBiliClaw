"""Source management API routes（从 app.py 提取）。

包含源配方管理、XHS/Bilibili/Twitter/Douyin/YouTube 扩展调度端点、
E2E 测试系统、源健康检查、自动启动配置。
通过 ``register_source_routes(app, ctx, _CONFIG_SAVE_LOCK)`` 注册。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
import uuid
from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from openbiliclaw.api.models import (
    AutostartApplyIn,
    AutostartStatusOut,
    ExtensionE2EAction,
    ExtensionE2EActionReportOut,
    ExtensionE2EActionStatus,
    ExtensionE2EEventMatchOut,
    ExtensionE2EPlatform,
    ExtensionE2EPlatformReportOut,
    ExtensionE2EResultIn,
    ExtensionE2ERunIn,
    ExtensionE2ERunOut,
    ExtensionE2ERunStatus,
    SourceCredentialItem,
    SourcesCredentialsResponse,
    LLMUsageSummary,
    ObservabilityResponse,
    PoolPipelineStats,
    SourcesStatusResponse,
    SourceStatusItem,
    XStatusResponse,
)
from openbiliclaw.api.utils import (
    coerce_e2e_event_rows as _coerce_e2e_event_rows,
)
from openbiliclaw.api.utils import (
    event_row_id as _event_row_id,
)
from openbiliclaw.api.utils import (
    event_row_metadata as _event_row_metadata,
)
from openbiliclaw.api.utils import (
    infer_source_platform_from_url as _infer_source_platform_from_url,
)
from openbiliclaw.api.utils import (
    normalize_source_platform as _normalize_source_platform,
)
from openbiliclaw.config import (
    _default_config_path as _cfg_path,
)
from openbiliclaw.config import (
    load_config as _load,
)
from openbiliclaw.config import (
    save_config as _save,
)
from openbiliclaw.runtime.keyword_fetch import (
    mark_keyword_terminal_from_xhs_task,
    source_keyword_id_from_xhs_task,
)
from openbiliclaw.sources.x_auth import resolve_x_cookie

if TYPE_CHECKING:
    from collections.abc import Callable

    from openbiliclaw.api.runtime_context import RuntimeContext

logger = logging.getLogger(__name__)


_E2E_STATE_CHANGING_ACTIONS = frozenset({"like", "favorite", "follow", "repost", "bookmark"})
_E2E_DEFAULT_SAFE_ACTIONS: tuple[ExtensionE2EAction, ...] = (
    "snapshot",
    "scroll",
    "click",
    "share",
)
_E2E_ACTION_EVENT_TYPES: dict[ExtensionE2EAction, frozenset[str]] = {
    "snapshot": frozenset({"snapshot"}),
    "scroll": frozenset({"scroll"}),
    "click": frozenset({"click"}),
    "share": frozenset({"click"}),
    "like": frozenset({"like", "favorite"}),
    "favorite": frozenset({"favorite", "bookmark"}),
    "follow": frozenset({"follow"}),
    "repost": frozenset({"share", "repost"}),
    "bookmark": frozenset({"bookmark", "favorite"}),
}


@dataclass
class _ExtensionE2ERunState:
    run_id: str
    token: str
    started_at: float
    after_event_id: int
    expected_actions: dict[ExtensionE2EPlatform, list[ExtensionE2EAction]]
    event: asyncio.Event
    extension_result: ExtensionE2EResultIn | None = None
    error: str = ""


def _extension_e2e_actions_for_request(
    payload: ExtensionE2ERunIn,
) -> dict[ExtensionE2EPlatform, list[ExtensionE2EAction]]:
    actions_by_platform: dict[ExtensionE2EPlatform, list[ExtensionE2EAction]] = {}
    seen_platforms: set[ExtensionE2EPlatform] = set()
    for platform in payload.platforms:
        if platform in seen_platforms:
            continue
        seen_platforms.add(platform)
        requested_actions = (
            payload.actions[platform]
            if platform in payload.actions
            else list(_E2E_DEFAULT_SAFE_ACTIONS)
        )
        deduped: list[ExtensionE2EAction] = []
        seen_actions: set[ExtensionE2EAction] = set()
        for action in requested_actions:
            if action in seen_actions:
                continue
            seen_actions.add(action)
            deduped.append(action)
        actions_by_platform[platform] = deduped
    return actions_by_platform


def _latest_e2e_event_id(ctx: Any) -> int:
    database = getattr(ctx, "database", None)
    conn = getattr(database, "conn", None)
    if conn is not None:
        try:
            row = conn.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM events").fetchone()
            if row is not None:
                try:
                    return int(row["max_id"])
                except Exception:
                    return int(row[0])
        except Exception:
            pass

    memory_manager = getattr(ctx, "memory_manager", None)
    query_events = getattr(memory_manager, "query_events", None)
    if callable(query_events):
        try:
            rows = _coerce_e2e_event_rows(query_events(limit=1))
        except Exception:
            rows = []
        if rows:
            return _event_row_id(rows[-1]) or 0
    return 0


def _query_e2e_events(ctx: Any, *, after_event_id: int, limit: int = 1000) -> list[dict[str, Any]]:
    memory_manager = getattr(ctx, "memory_manager", None)
    query_events = getattr(memory_manager, "query_events", None)
    if callable(query_events):
        try:
            return _coerce_e2e_event_rows(
                query_events(after_event_id=after_event_id, limit=limit),
                after_event_id=after_event_id,
            )
        except TypeError:
            try:
                return _coerce_e2e_event_rows(
                    query_events(limit=limit),
                    after_event_id=after_event_id,
                )
            except Exception:
                return []
        except Exception:
            return []

    database = getattr(ctx, "database", None)
    query_events = getattr(database, "query_events", None)
    if callable(query_events):
        try:
            return _coerce_e2e_event_rows(
                query_events(after_event_id=after_event_id, limit=limit),
                after_event_id=after_event_id,
            )
        except Exception:
            return []
    return []


def _match_e2e_event(
    events: list[dict[str, Any]],
    *,
    platform: ExtensionE2EPlatform,
    action: ExtensionE2EAction,
    used_event_ids: set[int],
) -> dict[str, object] | None:
    accepted_event_types = _E2E_ACTION_EVENT_TYPES.get(action, frozenset())
    if not accepted_event_types:
        return None

    for row in sorted(events, key=lambda item: _event_row_id(item) or 0):
        event_id = _event_row_id(row)
        if event_id is None or event_id in used_event_ids:
            continue
        event_type = str(row.get("event_type") or row.get("type") or "").strip()
        if event_type not in accepted_event_types:
            continue
        metadata = _event_row_metadata(row)
        source_platform = _normalize_source_platform(
            metadata.get("source_platform")
            or row.get("source_platform")
            or _infer_source_platform_from_url(row.get("url", ""))
        )
        if source_platform != platform:
            continue
        used_event_ids.add(event_id)
        return {
            "event_id": event_id,
            "event_type": event_type,
            "url": str(row.get("url", "") or ""),
            "title": str(row.get("title", "") or ""),
        }
    return None


def _build_extension_e2e_report(
    state: _ExtensionE2ERunState,
    events: list[dict[str, Any]],
    *,
    timed_out: bool,
    timeout_seconds: int,
) -> ExtensionE2ERunOut:
    result = state.extension_result
    action_results: dict[
        tuple[ExtensionE2EPlatform, ExtensionE2EAction], tuple[ExtensionE2EActionStatus, str]
    ] = {}
    platform_details: dict[ExtensionE2EPlatform, str] = {}
    if result is not None:
        for platform_result in result.platforms:
            platform_details[platform_result.platform] = platform_result.detail
            for action_result in platform_result.actions:
                action_results[(platform_result.platform, action_result.action)] = (
                    action_result.status,
                    action_result.detail,
                )

    used_event_ids: set[int] = set()
    reports: list[ExtensionE2EPlatformReportOut] = []
    total_actions = 0
    complete_actions = 0
    partial_actions = 0
    default_status: ExtensionE2EActionStatus = "skipped" if timed_out else "failed"
    default_detail = "extension result timed out" if timed_out else "extension result missing"

    for platform, actions in state.expected_actions.items():
        action_reports: list[ExtensionE2EActionReportOut] = []
        for action in actions:
            total_actions += 1
            action_status, detail = action_results.get(
                (platform, action),
                (default_status, default_detail),
            )
            match = _match_e2e_event(
                events,
                platform=platform,
                action=action,
                used_event_ids=used_event_ids,
            )
            backend_event = (
                ExtensionE2EEventMatchOut(
                    event_id=cast("int", match["event_id"]),
                    event_type=str(match["event_type"]),
                    url=str(match["url"]),
                    title=str(match["title"]),
                )
                if match is not None
                else None
            )
            extension_executed = action_status == "ok"
            backend_matched = backend_event is not None
            if extension_executed and backend_matched:
                complete_actions += 1
            elif extension_executed or backend_matched:
                partial_actions += 1
            action_reports.append(
                ExtensionE2EActionReportOut(
                    action=action,
                    extension_status=action_status,
                    extension_executed=extension_executed,
                    extension_detail=detail,
                    backend_event_matched=backend_matched,
                    backend_event=backend_event,
                )
            )
        reports.append(
            ExtensionE2EPlatformReportOut(
                platform=platform,
                actions=action_reports,
                detail=platform_details.get(platform, ""),
            )
        )

    error = state.error or (result.error if result is not None else "")
    if timed_out:
        run_status: ExtensionE2ERunStatus = "timeout"
        error = error or "extension e2e result timed out"
    elif error and complete_actions == 0 and partial_actions == 0:
        run_status = "failed"
    elif total_actions == complete_actions:
        run_status = "ok"
    elif complete_actions > 0 or partial_actions > 0:
        run_status = "partial"
    else:
        run_status = "failed"

    return ExtensionE2ERunOut(
        run_id=state.run_id,
        status=run_status,
        platforms=reports,
        error=error,
        timeout_seconds=timeout_seconds,
    )


def register_source_routes(
    app: FastAPI,
    ctx: RuntimeContext,
    _CONFIG_SAVE_LOCK: asyncio.Lock,
    *,
    get_auth_gate: Any,
    init_active_now: Any,
    ingest_profile_update_events: Any,
    snapshot_config_file: Any,
    restore_config_snapshot: Any,
) -> dict[str, Any]:
    _get_auth_gate = get_auth_gate
    _init_active_now = init_active_now
    _ingest_profile_update_events = ingest_profile_update_events
    _snapshot_config_file = snapshot_config_file
    _restore_config_snapshot = restore_config_snapshot
    config = _load()

    # ── 从 create_app 搬入的辅助函数 ─────────────────────

    def _init_owns_task(task_id: str) -> bool:
        """Whether ``task_id`` is a bootstrap task enqueued by the active init
        run (so its task-result is init's own data, not a stale/steady-state
        completion). Defensive — never raises.
        """
        coord = getattr(ctx, "init_coordinator", None)
        if coord is None or not task_id:
            return False
        try:
            return bool(coord.is_owned_bootstrap_task(str(task_id)))
        except Exception:
            return False

    def _init_owned_ids_filter() -> set[str] | None:
        """``next-task`` filter: during an active init, restrict the dispatcher
        to init-owned bootstrap task ids (so a stale pending task can't be
        claimed and starve the run's collectors); None = no restriction.
        """
        if not _init_active_now():
            return None
        coord = getattr(ctx, "init_coordinator", None)
        if coord is None:
            return None
        try:
            return set(coord.owned_task_ids())
        except Exception:
            return None

    def _load_source_bootstrap_state() -> dict[str, object]:
        from openbiliclaw.sources.bootstrap_state import (
            default_source_bootstrap_state,
            normalize_source_bootstrap_state,
        )

        load_state = getattr(ctx.memory_manager, "load_source_bootstrap_state", None)
        if not callable(load_state):
            return default_source_bootstrap_state()
        with suppress(Exception):
            return normalize_source_bootstrap_state(load_state())
        return default_source_bootstrap_state()

    def _save_source_bootstrap_state(state: dict[str, object]) -> None:
        from openbiliclaw.sources.bootstrap_state import normalize_source_bootstrap_state

        save_state = getattr(ctx.memory_manager, "save_source_bootstrap_state", None)
        if not callable(save_state):
            return
        with suppress(Exception):
            save_state(normalize_source_bootstrap_state(state))

    def _filter_new_source_bootstrap_items(
        source: str,
        items: list[dict[str, Any]],
        key_func: Callable[[dict[str, Any]], str],
    ) -> tuple[list[dict[str, Any]], dict[int, str]]:
        """Filter bootstrap items that already propagated from an older task."""
        from openbiliclaw.sources.bootstrap_state import (
            as_string_list,
            source_bootstrap_state_key,
        )

        state = _load_source_bootstrap_state()
        state_key = source_bootstrap_state_key(source)
        seen = set(as_string_list(state.get(state_key, [])))
        batch_seen: set[str] = set()
        fresh: list[dict[str, Any]] = []
        fresh_keys_by_index: dict[int, str] = {}
        for item in items:
            key = key_func(item)
            if not key or key in seen or key in batch_seen:
                continue
            batch_seen.add(key)
            fresh_keys_by_index[len(fresh)] = key
            fresh.append(item)
        return fresh, fresh_keys_by_index

    def _mark_source_bootstrap_keys(source: str, keys: list[str]) -> None:
        """Persist bootstrap keys that already entered the source event path."""
        if not keys:
            return
        from datetime import UTC, datetime

        from openbiliclaw.sources.bootstrap_state import (
            as_string_list,
            source_bootstrap_state_key,
        )

        state = _load_source_bootstrap_state()
        state_key = source_bootstrap_state_key(source)
        merged = as_string_list(state.get(state_key, []))
        seen = set(merged)
        for key in keys:
            normalized = str(key).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(normalized)
        state[state_key] = merged
        state["last_source_bootstrap_sync_at"] = datetime.now(UTC).isoformat()
        _save_source_bootstrap_state(state)

    async def _drain_discovery_candidates_once() -> None:
        """Best-effort drain for newly enqueued source candidates."""
        drain = getattr(ctx.runtime_controller, "drain_discovery_candidates_once", None)
        if not callable(drain):
            return
        try:
            await drain(batch_size=30)
        except Exception:
            logger.exception("Background discovery candidate drain failed")

    # ── Source recipe management endpoints ──────────────────────────

    @app.get("/api/sources")
    def list_sources() -> dict[str, Any]:
        """Return all source recipes."""
        recipes = ctx.database.get_all_recipes()
        return {"items": recipes}

    @app.post("/api/sources", status_code=201)
    def create_source(payload: dict[str, Any]) -> dict[str, Any]:
        """Create a new source recipe."""
        import uuid

        recipe_id = payload.get("id") or str(uuid.uuid4())
        source_type = payload.get("source_type", "")
        name = payload.get("name", "")
        strategy = payload.get("strategy", "")
        if not source_type or not name or not strategy:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=422,
                detail="source_type, name, and strategy are required",
            )
        recipe = {
            "id": recipe_id,
            "source_type": source_type,
            "name": name,
            "strategy": strategy,
            "config": payload.get("config", {}),
            "target_share": payload.get("target_share", 4),
            "enabled": payload.get("enabled", True),
            "created_by": payload.get("created_by", "user"),
        }
        ctx.database.save_source_recipe(recipe)
        return {"ok": True, "recipe": recipe}

    @app.put("/api/sources/{recipe_id}")
    def update_source(recipe_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Update fields of an existing source recipe."""
        updated = ctx.database.update_recipe(recipe_id, **payload)
        if not updated:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Recipe not found")
        return {"ok": True, "id": recipe_id}

    @app.delete("/api/sources/{recipe_id}")
    def delete_source(recipe_id: str) -> dict[str, Any]:
        """Delete a source recipe (system recipes cannot be deleted)."""
        # Check if it's a system recipe
        all_recipes = ctx.database.get_all_recipes()
        target = next((r for r in all_recipes if r["id"] == recipe_id), None)
        if target and target.get("created_by") == "system":
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="System recipes cannot be deleted")
        deleted = ctx.database.delete_recipe(recipe_id)
        if not deleted:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Recipe not found")
        return {"ok": True, "id": recipe_id}

    # ── XHS observed URL ingestion endpoint ─────────────────────────

    xhs_max_urls_per_batch = 50
    xhs_url_prefix = "https://www.xiaohongshu.com/"

    def _discovery_candidate_pending_cap() -> int:
        from openbiliclaw.discovery.candidate_pool import discovery_candidate_pending_cap

        scheduler = getattr(config, "scheduler", None)
        target = int(getattr(scheduler, "pool_target_count", 300) or 300)
        return discovery_candidate_pending_cap(target)

    def _intish(value: Any) -> int:
        if isinstance(value, bool):
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _cache_bili_search_videos(
        database: Any,
        videos: list[dict[str, Any]],
        *,
        query: str = "",
        source_keyword_id: int | None = None,
    ) -> int:
        """Enqueue extension-collected Bilibili search videos for evaluation."""
        from openbiliclaw.discovery.candidate_pool import discovered_content_to_candidate_write
        from openbiliclaw.discovery.engine import DiscoveredContent

        enqueue = getattr(database, "enqueue_discovery_candidates", None)
        if not callable(enqueue):
            return 0
        writes = []
        for video in videos:
            bvid = str(video.get("bvid") or video.get("content_id") or "").strip()
            if not bvid:
                continue
            title = str(video.get("title") or "").strip()
            if not title:
                continue
            up_name = str(
                video.get("up_name") or video.get("author_name") or video.get("author") or ""
            ).strip()
            content_url = str(video.get("content_url") or video.get("url") or "").strip()
            if not content_url:
                content_url = f"https://www.bilibili.com/video/{bvid}"
            tags_raw = video.get("tags")
            tags = (
                [str(item).strip() for item in tags_raw if str(item).strip()]
                if isinstance(tags_raw, list)
                else []
            )
            item = DiscoveredContent(
                bvid=bvid,
                title=title,
                up_name=up_name,
                up_mid=_intish(video.get("up_mid") or video.get("mid")),
                cover_url=str(video.get("cover_url") or video.get("pic") or "").strip(),
                duration=_intish(video.get("duration")),
                view_count=_intish(video.get("view_count") or video.get("play")),
                like_count=_intish(video.get("like_count") or video.get("likes")),
                favorite_count=_intish(
                    video.get("favorite_count") or video.get("favorites") or video.get("favorite")
                ),
                danmaku_count=_intish(
                    video.get("danmaku_count") or video.get("danmaku") or video.get("video_review")
                ),
                comment_count=_intish(
                    video.get("comment_count") or video.get("reply") or video.get("review")
                ),
                share_count=_intish(video.get("share_count") or video.get("share")),
                tags=tags,
                description=str(video.get("description") or video.get("desc") or "").strip(),
                source_strategy="bili-extension-search",
                content_id=bvid,
                content_url=content_url,
                source_platform="bilibili",
                author_name=up_name,
                score_threshold=0.60,
                source_keyword_id=source_keyword_id,
            )
            writes.append(
                discovered_content_to_candidate_write(
                    item,
                    source_context="bili-extension-search",
                    raw_payload={
                        "bvid": bvid,
                        "query": query,
                        "url": content_url,
                        "admission_policy": "observed",
                        "score_threshold": 0.60,
                    },
                )
            )
        if not writes:
            return 0
        try:
            return int(enqueue(writes, max_pending_per_source=_discovery_candidate_pending_cap()))
        except TypeError:
            return int(enqueue(writes))

    def _mark_bili_task_keyword_terminal(payload_json: str | None, *, success: bool) -> None:
        from openbiliclaw.sources.bili_tasks import source_keyword_id_from_bili_task

        keyword_id = source_keyword_id_from_bili_task(payload_json)
        if keyword_id is None:
            return
        method = "mark_keyword_used" if success else "mark_keyword_failed"
        mark = getattr(ctx.database, method, None)
        if not callable(mark):
            return
        with suppress(Exception):
            mark(keyword_id)

    def _pick_best_xhs_url(database: Any, note_id: str, incoming: str) -> str:
        """Return the most share-worthy URL for a xhs note.

        xhs search-result pages don't render ``xsec_token`` into ``<a href>``
        (React SPA keeps the token in props, not DOM), but explore-feed
        cards do. When the same note arrives both ways, prefer the URL
        that carries a token — without it, outbound links can silently
        dead-end at an xhs login wall.

        Order of preference:
        1. ``incoming`` URL if it already has ``xsec_token=``
        2. Any prior ``xhs_observed_urls`` row for this note with a token
        3. Existing ``content_cache.content_url`` if it has a token
        4. Fall back to ``incoming`` (bare URL — still works for the
           logged-in user on the xhs domain, just not guaranteed for
           share/outbound traffic)
        """
        if "xsec_token=" in incoming:
            return incoming
        try:
            row = database.conn.execute(
                "SELECT url FROM xhs_observed_urls "
                "WHERE url LIKE ? AND url LIKE '%xsec_token=%' "
                "ORDER BY observed_at DESC LIMIT 1",
                (f"%/{note_id}?%",),
            ).fetchone()
            if row and row["url"]:
                return str(row["url"])
        except Exception:
            pass
        try:
            row = database.conn.execute(
                "SELECT content_url FROM content_cache WHERE bvid=?",
                (note_id,),
            ).fetchone()
            if row and isinstance(row["content_url"], str) and "xsec_token=" in row["content_url"]:
                return str(row["content_url"])
        except Exception:
            pass
        try:
            row = database.conn.execute(
                "SELECT content_url FROM discovery_candidates "
                "WHERE source_platform='xiaohongshu' AND content_id=? "
                "  AND content_url LIKE '%xsec_token=%' "
                "ORDER BY last_seen_at DESC LIMIT 1",
                (note_id,),
            ).fetchone()
            if row and row["content_url"]:
                return str(row["content_url"])
        except Exception:
            pass
        return incoming

    def _backfill_xhs_tokens(database: Any, urls: list[str]) -> int:
        """Upgrade cached xhs rows whose content_url lacks xsec_token.

        The extension often observes the same note twice — once from a
        search result page (no token in ``<a href>``) and once from an
        explore-feed card (token present). When a tokenized URL arrives
        later, rewrite the previously-cached bare URL so share links
        don't dead-end at xhs's login wall.
        """
        updated = 0
        for url in urls:
            if "xsec_token=" not in url:
                continue
            try:
                path = urlparse(url).path.strip("/")
                note_id = path.rsplit("/", 1)[-1] if path else ""
            except Exception:
                continue
            if not note_id:
                continue
            try:
                cursor = database.conn.execute(
                    "UPDATE content_cache SET content_url=? "
                    "WHERE bvid=? AND source_platform='xiaohongshu' "
                    "AND (content_url = '' OR content_url NOT LIKE '%xsec_token=%')",
                    (url, note_id),
                )
                updated += cursor.rowcount or 0
            except Exception:
                pass
            try:
                cursor = database.conn.execute(
                    "UPDATE discovery_candidates "
                    "SET content_url=?, last_seen_at=CURRENT_TIMESTAMP "
                    "WHERE source_platform='xiaohongshu' AND content_id=? "
                    "AND (content_url = '' OR content_url NOT LIKE '%xsec_token=%')",
                    (url, note_id),
                )
                updated += cursor.rowcount or 0
            except Exception:
                continue
        # Commit unconditionally: a bare UPDATE (even with zero matched
        # rows) opens a write transaction in sqlite3's default isolation
        # mode and holds a RESERVED lock until commit. Skipping the commit
        # when `updated == 0` leaked the transaction, blocking other
        # connections' writers (e.g. the thread-local connection used by
        # tests / threadpool requests) behind a stale lock.
        if database.conn.in_transaction:
            with suppress(Exception):
                database.conn.commit()
        return updated

    # ── XHS self-author filter (v0.3.48+) ────────────────────────────
    #
    # XHS search / explore / saved-author paths all happily return the
    # logged-in user's own published notes. Without filtering, the
    # recommendation pool fills with content the user posted themselves
    # ("自己发的笔记被推回给自己" — observed in 2026-05-05 logs as
    # 屎屎/三花/etc. cat photos polluting the popup). The extension
    # bootstrap captures self user_id + nickname from XHS state and
    # sends it back via ``debug.xhs_bootstrap.steps[*].self_info``.
    # Backend persists in ``discovery_runtime_state["xhs_self_info"]``
    # and consults it on every ingest path.

    def _normalize_self_info(raw: Any) -> dict[str, str] | None:
        """Validate + normalize a self_info-shaped dict.

        Returns ``{"user_id": ..., "nickname": ...}`` if either field is
        non-empty, otherwise ``None``.
        """
        if not isinstance(raw, dict):
            return None
        user_id = str(raw.get("user_id", "") or "").strip()
        nickname = str(raw.get("nickname", "") or "").strip()
        if not user_id and not nickname:
            return None
        return {"user_id": user_id, "nickname": nickname}

    def _extract_self_info_from_payload(payload: Any) -> dict[str, str] | None:
        """Pull self_info from any XHS ingest payload.

        v0.3.57+: extension v0.3.10 sends self_info at the **payload top
        level** for every ingest path (passive ``observed-urls``, search /
        creator ``task-result``, bootstrap_profile ``task-result``). The
        legacy bootstrap-only nested location
        ``debug.xhs_bootstrap.steps[*].self_info`` (v0.3.48 / extension
        v0.3.9) is kept as fallback for older extensions.
        """
        if not isinstance(payload, dict):
            return None
        # 1) New top-level location.
        info = _normalize_self_info(payload.get("self_info"))
        if info is not None:
            return info
        # 2) Legacy bootstrap-debug nested location.
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            return None
        bootstrap = debug.get("xhs_bootstrap")
        if not isinstance(bootstrap, dict):
            return None
        steps = bootstrap.get("steps")
        if not isinstance(steps, list):
            return None
        for step in steps:
            if not isinstance(step, dict):
                continue
            info = _normalize_self_info(step.get("self_info"))
            if info is not None:
                return info
        return None

    def _persist_xhs_self_info(self_info: dict[str, str]) -> None:
        """Save self info into discovery_runtime_state if not already there."""
        memory_manager = getattr(ctx.runtime_controller, "memory_manager", None)
        if memory_manager is None:
            return
        try:
            state = memory_manager.load_discovery_runtime_state()
            existing = state.get("xhs_self_info")
            # Idempotent: only write when content changes (avoid sqlite churn).
            if isinstance(existing, dict) and existing == self_info:
                return
            update_state = getattr(memory_manager, "update_discovery_runtime_state", None)
            if callable(update_state):
                update_state(
                    lambda runtime_state: runtime_state.update({"xhs_self_info": self_info})
                )
            else:
                state["xhs_self_info"] = self_info
                memory_manager.save_discovery_runtime_state(state)
            logger.info(
                "xhs self_info persisted: user_id=%s nickname=%r",
                self_info.get("user_id", ""),
                self_info.get("nickname", ""),
            )
            # Immediately purge any self-authored rows that slipped into
            # the pool before this self_info was known.
            suppressed = _purge_self_authored_pool_items(ctx.database, self_info)
            if suppressed:
                logger.info(
                    "xhs self_info purge: suppressed %d self-authored pool item(s) (nickname=%r)",
                    suppressed,
                    self_info.get("nickname", ""),
                )
        except Exception:
            logger.exception("Failed to persist xhs self_info")

    def _load_xhs_self_info() -> dict[str, str]:
        """Load self info from runtime state (returns empty dict on miss)."""
        memory_manager = getattr(ctx.runtime_controller, "memory_manager", None)
        if memory_manager is None:
            return {}
        try:
            state = memory_manager.load_discovery_runtime_state()
            existing = state.get("xhs_self_info")
            if isinstance(existing, dict):
                return {
                    "user_id": str(existing.get("user_id", "") or ""),
                    "nickname": str(existing.get("nickname", "") or ""),
                }
        except Exception:
            logger.exception("Failed to load xhs self_info")
        return {}

    def _is_self_authored_note(note: dict[str, Any], self_info: dict[str, str]) -> bool:
        """Check whether a note's author matches the logged-in user.

        Both user_id and nickname can match — XHS sometimes only ships
        nickname in note metadata (no author user_id), other times both.
        Treat the match as case-insensitive on the trimmed values.
        """
        if not self_info:
            return False
        nickname = self_info.get("nickname", "").strip().lower()
        user_id = self_info.get("user_id", "").strip().lower()
        author = str(note.get("author", "") or "").strip().lower()
        if author and nickname and author == nickname:
            return True
        author_id = str(note.get("author_id", "") or "").strip().lower()
        return bool(author_id and user_id and author_id == user_id)

    def _purge_self_authored_pool_items(
        database: Any,
        self_info: dict[str, str],
    ) -> int:
        """Mark every pool row authored by ``self_info.nickname`` as suppressed.

        v0.3.57+: cleans up content_cache rows that entered before the
        per-path self_info filter was wired in. Idempotent — already-
        suppressed rows are not flipped further. Returns the number of
        rows actually changed in this call.

        ``up_name`` is the column populated by ``_cache_xhs_notes`` from
        the note's ``author`` field, so the comparison mirrors the
        runtime filter exactly.
        """
        if not self_info or not hasattr(database, "conn"):
            return 0
        nickname = (self_info.get("nickname") or "").strip()
        if not nickname:
            return 0
        try:
            cursor = database.conn.execute(
                "UPDATE content_cache "
                "SET pool_status = 'suppressed' "
                "WHERE source_platform = 'xiaohongshu' "
                "  AND COALESCE(pool_status, 'fresh') = 'fresh' "
                "  AND ("
                "    LOWER(COALESCE(up_name, '')) = LOWER(?)"
                "    OR LOWER(COALESCE(author_name, '')) = LOWER(?)"
                "  )",
                (nickname, nickname),
            )
            database.conn.commit()
            return int(cursor.rowcount or 0)
        except Exception:
            logger.exception("Failed to purge self-authored xhs pool items")
            return 0

    def _cache_xhs_notes(
        database: Any,
        notes: list[dict[str, Any]],
        page_type: str,
        self_info: dict[str, str] | None = None,
        *,
        source_keyword_id: int | None = None,
    ) -> int:
        """Enqueue xhs note metadata from the extension into discovery_candidates.

        ``self_info`` (v0.3.48+) lets the caller pass the just-extracted
        login fingerprint from the same request — avoids a round-trip
        through ``discovery_runtime_state`` and works against test
        stubs that haven't implemented the runtime-state API.  When
        ``None``, falls back to the persisted state.

        ``source_keyword_id`` (P1.8) is the ``discovery_keywords.id`` carried on
        the originating xhs *search* task payload. XHS is truly async, so the id
        cannot be stamped at search time — it rides the task and is threaded onto
        each ingested candidate here so admission can backfill the keyword's
        yield. ``None`` for passive / observed / non-planner ingests.
        """
        from openbiliclaw.discovery.candidate_pool import discovered_content_to_candidate_write
        from openbiliclaw.discovery.engine import DiscoveredContent

        enqueue = getattr(database, "enqueue_discovery_candidates", None)
        if not callable(enqueue):
            return 0
        if self_info is None:
            self_info = _load_xhs_self_info()
        writes = []
        skipped_self = 0
        for note in notes:
            if _is_self_authored_note(note, self_info):
                skipped_self += 1
                continue
            url = note.get("url", "")
            if not isinstance(url, str) or not url.startswith(xhs_url_prefix):
                continue
            # Extract note ID from URL path
            try:
                path = urlparse(url).path.strip("/")
                note_id = path.rsplit("/", 1)[-1] if path else ""
            except Exception:
                note_id = ""
            if not note_id:
                continue

            title = str(note.get("title", "") or "").strip()
            if not title:
                continue  # Skip notes with empty title — they produce blank recommendation cards
            author = str(note.get("author", "") or "").strip()
            cover_url = str(note.get("cover_url", "") or "").strip()
            best_url = _pick_best_xhs_url(database, note_id, url)

            item = DiscoveredContent(
                bvid=note_id,
                title=title,
                up_name=author,
                cover_url=cover_url,
                view_count=_intish(note.get("view_count") or note.get("views")),
                like_count=_intish(note.get("like_count") or note.get("likes")),
                collect_count=_intish(
                    note.get("collect_count")
                    or note.get("favorite_count")
                    or note.get("favorites")
                    or note.get("collects")
                ),
                comment_count=_intish(note.get("comment_count") or note.get("comments")),
                share_count=_intish(note.get("share_count") or note.get("shares")),
                description=str(
                    note.get("description") or note.get("desc") or note.get("text") or ""
                ),
                source_strategy=f"xhs-extension-{page_type}",
                content_id=note_id,
                content_url=best_url,
                source_platform="xiaohongshu",
                author_name=author,
                source_keyword_id=source_keyword_id,
            )
            writes.append(
                discovered_content_to_candidate_write(
                    item,
                    source_context=page_type,
                    raw_payload={
                        "note_id": note_id,
                        "url": best_url,
                        "page_type": page_type,
                        "title": title,
                        "author": author,
                        "cover_url": cover_url,
                        "admission_policy": "observed",
                    },
                )
            )
        if skipped_self > 0:
            logger.info(
                "xhs ingest filter: dropped %d self-authored note(s) (%s)",
                skipped_self,
                page_type,
            )
        if not writes:
            return 0
        try:
            return int(enqueue(writes, max_pending_per_source=_discovery_candidate_pending_cap()))
        except TypeError:
            return int(enqueue(writes))

    @app.post("/api/sources/xhs/observed-urls")
    async def ingest_xhs_observed_urls(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept xhs note URLs + optional metadata the extension collected.

        Body: ``{ "urls": [...], "notes": [{url, title, author, cover_url}], "page_type": "..." }``

        When ``notes`` is present, metadata is normalized into
        ``discovery_candidates``.  The shared discovery-candidate drain then
        evaluates and admits accepted notes through the same path as other
        platforms.
        """
        from fastapi import HTTPException

        urls_raw: list[str] = payload.get("urls", [])
        notes_raw: list[dict[str, Any]] = payload.get("notes", [])
        page_type: str = payload.get("page_type", "other")

        if not urls_raw and not notes_raw:
            raise HTTPException(status_code=422, detail="urls or notes must be non-empty")
        if len(urls_raw) > xhs_max_urls_per_batch:
            raise HTTPException(
                status_code=422,
                detail=f"Too many URLs (max {xhs_max_urls_per_batch})",
            )

        # v0.3.57+: passive collector (extension v0.3.10) piggybacks
        # self_info on every observed-urls request. Persist on first
        # arrival so subsequent requests without self_info still filter
        # via the loaded state.
        self_info_now = _extract_self_info_from_payload(payload)
        if self_info_now:
            _persist_xhs_self_info(self_info_now)
        self_info_for_filter = self_info_now or _load_xhs_self_info()

        # Filter to valid xhs note URLs
        valid_urls = [
            u
            for u in urls_raw
            if isinstance(u, str) and u.startswith(xhs_url_prefix) and "/explore/" in u
        ]

        # Store bare URLs for tracking
        if valid_urls:
            ctx.database.save_xhs_observed_urls(valid_urls, page_type)
            _backfill_xhs_tokens(ctx.database, valid_urls)

        # Store rich notes into the shared pending evaluation pool.
        enqueued = 0
        if notes_raw:
            enqueued = _cache_xhs_notes(
                ctx.database,
                notes_raw,
                page_type,
                self_info=self_info_for_filter or None,
            )
            if enqueued:
                asyncio.create_task(_drain_discovery_candidates_once())

        return {
            "ok": True,
            "accepted": len(valid_urls),
            "enqueued": enqueued,
        }

    @app.post("/api/sources/xhs/tokens")
    def ingest_xhs_tokens(payload: dict[str, Any]) -> dict[str, Any]:
        """Ingest ``(note_id, xsec_token)`` pairs harvested by the MAIN-
        world fetch sniffer inside ``dist/main/xhs-token-sniffer.js``.

        We rebuild the full tokenized URL from each pair and feed it
        through ``_backfill_xhs_tokens`` so previously-cached bare URLs
        (the typical search-page-sourced ones) get upgraded in place.
        Without this, clicking an xhs recommendation trips xhs's 300031
        access-denied gating because the stored URL lacks xsec_token.
        """
        raw = payload.get("pairs", [])
        if not isinstance(raw, list) or not raw:
            return {"ok": True, "upgraded": 0}
        urls: list[str] = []
        for pair in raw:
            if not isinstance(pair, dict):
                continue
            note_id = str(pair.get("note_id", "") or "").strip()
            token = str(pair.get("xsec_token", "") or "").strip()
            # Guard against the noise the sniffer's deep-walk can surface
            # — e.g. 24-hex ids that aren't notes. The backfill UPDATE is
            # narrow (bvid match), so the worst case of a false id is a
            # no-op, but the token must at least be non-empty.
            if not note_id or not token:
                continue
            urls.append(f"{xhs_url_prefix}explore/{note_id}?xsec_token={token}")
        upgraded = _backfill_xhs_tokens(ctx.database, urls)
        return {"ok": True, "upgraded": upgraded}

    # ── Bilibili extension search fallback endpoints ────────────────

    from openbiliclaw.sources.bili_tasks import (
        BiliTaskQueue,
        source_keyword_id_from_bili_task,
    )

    _bili_task_queue: BiliTaskQueue | None = None
    if hasattr(ctx.database, "conn"):
        _bili_task_queue = BiliTaskQueue(ctx.database)

    @app.get("/api/sources/bili/next-task")
    def bili_next_task(response: Any = None) -> Any:
        """Claim and return the oldest runnable Bilibili extension task."""
        if _bili_task_queue is None:
            return Response(status_code=204)
        task = _bili_task_queue.next_pending()
        if task is None:
            return Response(status_code=204)

        import json as _json

        payload = _json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            **payload,
        }

    @app.post("/api/sources/bili/task-result")
    async def bili_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept Bilibili extension search results and enqueue candidates."""
        task_id = str(payload.get("task_id", "") or "").strip()
        status = str(payload.get("status", "") or "").strip()
        videos = [video for video in payload.get("videos", []) if isinstance(video, dict)]
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = None

        if not task_id:
            from fastapi import HTTPException

            raise HTTPException(status_code=422, detail="task_id is required")

        if _bili_task_queue is None:
            return {"ok": True, "enqueued": 0}

        task = _bili_task_queue.get(task_id)
        task_payload_json = str(task.get("payload_json") or "") if task else ""

        if status in {"partial", "ok", "empty"}:
            is_final = status in {"ok", "empty"}
            added_videos = _bili_task_queue.merge_result(
                task_id,
                videos=videos if videos else None,
                debug=debug,
                complete=is_final,
            )
            if is_final:
                _mark_bili_task_keyword_terminal(task_payload_json, success=status == "ok")
            task_payload: dict[str, Any] = {}
            if task_payload_json:
                with suppress(Exception):
                    parsed = json.loads(task_payload_json)
                    if isinstance(parsed, dict):
                        task_payload = parsed
            query = str(task_payload.get("query") or task_payload.get("keyword") or "").strip()
            source_keyword_id = source_keyword_id_from_bili_task(task_payload_json)
            enqueued = 0
            if added_videos:
                enqueued = _cache_bili_search_videos(
                    ctx.database,
                    added_videos,
                    query=query,
                    source_keyword_id=source_keyword_id,
                )
                if enqueued:
                    asyncio.create_task(_drain_discovery_candidates_once())
            return {"ok": True, "enqueued": enqueued}

        _bili_task_queue.fail(task_id, error=str(payload.get("error", "") or ""), debug=debug)
        _mark_bili_task_keyword_terminal(task_payload_json, success=False)
        return {"ok": True, "enqueued": 0}

    @app.post("/api/sources/bili/kick")
    async def bili_task_kick() -> dict[str, Any]:
        """Broadcast `bili_task_available` over runtime-stream."""
        publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
        if callable(publish):
            with suppress(Exception):
                await publish({"type": "bili_task_available", "source": "task_kick"})
        return {"ok": True}

    # ── XHS task queue endpoints (extension dispatcher) ──────────────

    from openbiliclaw.sources.xhs_tasks import (
        XhsCreatorStore,
        XhsTaskQueue,
        xhs_bootstrap_note_key,
        xhs_bootstrap_notes_to_events,
    )

    # Guard: only initialise when ctx.database is a real Database (has .conn).
    # Tests that pass database=object() as a stub won't trigger table creation.
    _xhs_task_queue: XhsTaskQueue | None = None
    _xhs_creator_store: XhsCreatorStore | None = None
    if hasattr(ctx.database, "conn"):
        _xhs_task_queue = XhsTaskQueue(ctx.database)
        _xhs_creator_store = XhsCreatorStore(ctx.database)

    @app.get("/api/sources/xhs/next-task")
    def xhs_next_task(response: Any = None) -> Any:
        """Claim and return the oldest runnable xhs task, or 204 if none."""
        # 204 No Content responses MUST NOT carry a body (RFC 7230).
        # JSONResponse(204, None) serialises None to "null" (4 bytes),
        # then GZipMiddleware (minimum_size=0) wraps it into ~20 bytes
        # of gzip stream while Content-Length stays at 4, which trips
        # h11's strict "Too much data for declared Content-Length"
        # check on every poll. Use a body-less Response instead.
        if _xhs_task_queue is None:
            return Response(status_code=204)
        task = _xhs_task_queue.next_pending(only_ids=_init_owned_ids_filter())
        if task is None:
            return Response(status_code=204)

        import json as _json

        payload = _json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            **payload,
        }

    @app.post("/api/sources/xhs/task-result")
    async def xhs_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept a task result from the extension dispatcher."""
        task_id = payload.get("task_id", "")
        status = payload.get("status", "")
        urls = payload.get("urls", [])
        notes = [note for note in payload.get("notes", []) if isinstance(note, dict)]
        scope_counts = payload.get("scope_counts")
        if not isinstance(scope_counts, dict):
            scope_counts = None
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = None

        if not task_id:
            from fastapi import HTTPException

            raise HTTPException(status_code=422, detail="task_id is required")

        if _xhs_task_queue is None:
            return {"ok": True}

        task = _xhs_task_queue.get(task_id)
        task_type = str(task.get("type", "")).strip() if task else ""

        if status in {"partial", "ok"} or (status == "empty" and task_type == "bootstrap_profile"):
            is_final = status == "ok" or (status == "empty" and task_type == "bootstrap_profile")
            added_notes = _xhs_task_queue.merge_result(
                task_id,
                urls=urls,
                notes=notes if notes else None,
                scope_counts=scope_counts,
                debug=debug,
                complete=is_final,
            )
            # Unified keyword planner lifecycle (P1.7): XHS is truly async, so a
            # claimed search word stays ``executing`` until this terminal
            # callback. On a final ``ok`` mark its ``source_keyword_id`` word
            # ``used`` (a ``partial`` is not terminal → leave it ``executing``).
            if is_final and task is not None:
                mark_keyword_terminal_from_xhs_task(
                    ctx.database, task.get("payload_json"), success=True
                )
            # v0.3.48+: piggyback self_info from bootstrap debug payload.
            # v0.3.57+: also accept self_info at the payload top level for
            # search / creator / passive paths via extension v0.3.10.
            # Persist immediately so future requests can also consult it,
            # AND use the just-extracted value in this request's
            # downstream filters (skip a state round-trip that some
            # in-process test stubs don't implement).
            self_info_from_request = _extract_self_info_from_payload(payload)
            if self_info_from_request:
                _persist_xhs_self_info(self_info_from_request)
            self_info_now = self_info_from_request or _load_xhs_self_info()
            # gui-init D1: the result is always persisted above (merge_result)
            # so init's own collector can read it. During an active init:
            #  - skip ALL live discovery-pool writes (stage 4 owns the pool);
            #  - skip profile propagation for tasks NOT owned by this run (stale
            #    / steady-state completions), but DO propagate init-OWNED
            #    bootstrap results through the normal deduped path so the source
            #    signals land in memory exactly once (handles force re-init).
            # Computed BEFORE the URL/token backfill (_backfill_xhs_tokens writes
            # content_cache + discovery_candidates).
            _init_busy = _init_active_now()
            _skip_profile = _init_busy and not _init_owns_task(task_id)
            # Store discovered URLs + metadata
            valid_urls = [u for u in urls if isinstance(u, str) and u.startswith(xhs_url_prefix)]
            if valid_urls and not _init_busy:
                ctx.database.save_xhs_observed_urls(valid_urls, "task")
                _backfill_xhs_tokens(ctx.database, valid_urls)
            if added_notes and not _init_busy:
                # P1.8: a planner-driven xhs *search* task carries its
                # ``source_keyword_id`` on the payload → thread it onto the
                # ingested candidates so admission backfills the keyword's yield.
                # Passive / non-search tasks have no id → plain None.
                task_source_keyword_id = (
                    source_keyword_id_from_xhs_task(task.get("payload_json"))
                    if task is not None
                    else None
                )
                enqueued = _cache_xhs_notes(
                    ctx.database,
                    added_notes,
                    "task",
                    self_info_now,
                    source_keyword_id=task_source_keyword_id,
                )
                if enqueued:
                    asyncio.create_task(_drain_discovery_candidates_once())
            if task_type == "bootstrap_profile" and added_notes and not _skip_profile:
                fresh_notes, note_keys_by_index = _filter_new_source_bootstrap_items(
                    "xhs",
                    added_notes,
                    xhs_bootstrap_note_key,
                )
                # Filter self-authored notes from event propagation —
                # otherwise the user's own posts get treated as their
                # own "favorite/like" signals and warp the soul profile.
                propagated = 0
                skipped_self = 0
                profile_events: list[dict[str, Any]] = []
                propagated_keys: list[str] = []
                for index, note in enumerate(fresh_notes):
                    if _is_self_authored_note(note, self_info_now):
                        skipped_self += 1
                        continue
                    for event in xhs_bootstrap_notes_to_events([note]):
                        await ctx.memory_manager.propagate_event(event)
                        profile_events.append(event)
                        key = note_keys_by_index.get(index, "")
                        if key:
                            propagated_keys.append(key)
                        propagated += 1
                # During init, skip the incremental profile-update pipeline even
                # for owned results — run_guided_init's explicit analyze/build
                # owns the profile this run, and a force re-init has an existing
                # profile that _ingest would otherwise mutate concurrently
                # (gui-init review). Memory propagation above still records the
                # signals; the pipeline resumes for steady-state after init.
                if not _init_busy:
                    await _ingest_profile_update_events(profile_events)
                _mark_source_bootstrap_keys("xhs", propagated_keys)
                if skipped_self > 0:
                    logger.info(
                        "xhs bootstrap propagate: dropped %d self-authored note(s) (%d propagated)",
                        skipped_self,
                        propagated,
                    )
        else:
            _xhs_task_queue.fail(task_id, error=payload.get("error", ""), debug=debug)
            # Unified keyword planner lifecycle (P1.7): the async search failed →
            # mark its ``source_keyword_id`` word ``failed`` (retry via attempts).
            if task is not None:
                mark_keyword_terminal_from_xhs_task(
                    ctx.database, task.get("payload_json"), success=False
                )

        return {"ok": True}

    @app.get("/api/sources/xhs/creators")
    def xhs_list_creators() -> dict[str, Any]:
        """List all xhs creator subscriptions."""
        if _xhs_creator_store is None:
            return {"items": []}
        return {"items": _xhs_creator_store.list_all()}

    @app.post("/api/sources/xhs/creators", status_code=201)
    def xhs_add_creator(payload: dict[str, Any]) -> dict[str, Any]:
        """Add an xhs creator subscription."""
        from fastapi import HTTPException

        creator_id = payload.get("creator_id", "")
        creator_url = payload.get("creator_url", "")
        display_name = payload.get("display_name", "")

        if not creator_id or not creator_url:
            raise HTTPException(
                status_code=422,
                detail="creator_id and creator_url are required",
            )

        if _xhs_creator_store is None:
            raise HTTPException(status_code=503, detail="xhs not configured")
        _xhs_creator_store.add(creator_id, creator_url, display_name)
        return {"ok": True}

    @app.delete("/api/sources/xhs/creators/{sub_id}")
    def xhs_delete_creator(sub_id: int) -> dict[str, Any]:
        """Delete an xhs creator subscription."""
        from fastapi import HTTPException

        if _xhs_creator_store is None:
            raise HTTPException(status_code=503, detail="xhs not configured")
        deleted = _xhs_creator_store.delete(sub_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Subscription not found")
        return {"ok": True}

    # ── X (Twitter) account subscriptions ──────────────────────────
    # No extension round-trip: the X producer fetches each subscription
    # server-side via XCreatorStrategy. This block only owns the
    # x_creator_subscriptions table + CRUD (mirrors the XHS creators above).

    from openbiliclaw.sources.x_tasks import XCreatorStore, normalize_handle

    _x_creator_store: XCreatorStore | None = None
    if hasattr(ctx.database, "conn"):
        _x_creator_store = XCreatorStore(ctx.database)

    @app.get("/api/sources/x/creators")
    def x_list_creators() -> dict[str, Any]:
        """List all X account subscriptions."""
        if _x_creator_store is None:
            return {"items": []}
        return {"items": _x_creator_store.list_all()}

    @app.post("/api/sources/x/creators", status_code=201)
    def x_add_creator(payload: dict[str, Any]) -> dict[str, Any]:
        """Add an X account subscription (idempotent; leading @ stripped)."""
        from fastapi import HTTPException

        handle = normalize_handle(str(payload.get("handle", "")))
        if not handle:
            raise HTTPException(status_code=422, detail="handle is required")

        if _x_creator_store is None:
            raise HTTPException(status_code=503, detail="x not configured")
        _x_creator_store.add(handle)
        return {"ok": True}

    @app.delete("/api/sources/x/creators/{sub_id}")
    def x_delete_creator(sub_id: int) -> dict[str, Any]:
        """Delete an X account subscription."""
        from fastapi import HTTPException

        if _x_creator_store is None:
            raise HTTPException(status_code=503, detail="x not configured")
        deleted = _x_creator_store.delete(sub_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Subscription not found")
        return {"ok": True}

    # ── X (Twitter) source health (spec §7) ────────────────────────
    # Surfaces the persisted health state machine so the settings UI can
    # show login / rate-limit / block status (rendered in Task 12).

    @app.get("/api/sources/x/status", response_model=XStatusResponse)
    def x_source_status() -> XStatusResponse:
        """Return the current X source health (ok / cookie / rate-limit / block)."""
        if not hasattr(ctx.database, "conn"):
            return XStatusResponse()
        from openbiliclaw.storage.x_health import XSourceHealthStore

        health = XSourceHealthStore(ctx.database).get()
        return XStatusResponse(
            state=str(health.get("state", "ok")),
            consecutive_failures=int(health.get("consecutive_failures", 0)),
            feed_paused=bool(health.get("feed_paused", False)),
            cooldown_until=str(health.get("cooldown_until", "")),
            detail=str(health.get("detail", "")),
            updated_at=str(health.get("updated_at", "")),
        )

    # Window for treating synced 小红书 access tokens as fresh. xsec_tokens
    # die well within a day, so /api/sources/status only reports "ready" when
    # token activity happened inside this window — older-only rows degrade to
    # the yellow "stale" state instead of staying green forever.
    _xhs_token_fresh_hours = 24

    # Human-readable detail for each X (twitter) health state, reused by the
    # unified /api/sources/status chip below.
    _x_state_detail = {
        "ok": "X 来源正常，cookie 有效。",
        "missing_cookie": "未检测到登录 —— 在浏览器登录 x.com，插件会自动同步 cookie。",
        "expired_cookie": "cookie 已过期 —— 请重新登录 x.com。",
        "rate_limited": "被限流，正在退避冷却中，稍后会自动重试。",
        "blocked": "请求被拒绝 (403) —— 账号可能受限或需要重新验证。",
    }

    @app.get("/api/sources/status", response_model=SourcesStatusResponse)
    def sources_status() -> SourcesStatusResponse:
        """Unified per-source login / cookie readiness for the settings pages.

        Local-only: each source's state is derived from config cookie fields,
        the Douyin cookie file/env, the count of token-bearing 小红书 cache
        rows, and the X health store — no outbound platform requests. See
        :class:`SourcesStatusResponse`. ``ready`` means a credential is present
        and structurally valid (not live-validated); only X reports a真正
        live-validated ``ok``.
        """
        from openbiliclaw.config import load_config

        cfg = load_config()
        srcs = cfg.sources

        # ── Bilibili: cookie present with the core login fields ──
        # config.toml is the mirror, data/bilibili_cookie.json is the runtime
        # store (CLI QR login writes only the file) — check both, like the
        # douyin/x branches resolve env + file.
        bili_cookie = str(getattr(cfg.bilibili, "cookie", "") or "")
        if not bili_cookie.strip():
            with suppress(Exception):
                from openbiliclaw.bilibili.auth import AuthManager

                bili_cookie = AuthManager(data_dir=cfg.data_path).load_cookie()
        bili_enabled = bool(getattr(srcs.bilibili, "enabled", True))
        has_fields = sum(
            1 for f in ("SESSDATA", "bili_jct", "DedeUserID") if f"{f}=" in bili_cookie
        )
        if getattr(cfg.bilibili, "auth_method", "cookie") == "none":
            bilibili = SourceStatusItem(
                enabled=bili_enabled,
                state="no_auth",
                detail="未启用 B 站登录（auth_method=none）。",
                logged_in=True,
            )
        elif has_fields >= 3:
            bilibili = SourceStatusItem(
                enabled=bili_enabled,
                state="ready",
                detail="Cookie 就绪（含 SESSDATA / bili_jct / DedeUserID）。",
                logged_in=True,
            )
        elif bili_cookie.strip():
            bilibili = SourceStatusItem(
                enabled=bili_enabled,
                state="partial",
                detail="Cookie 已配置，但缺少部分登录字段，可能未完整登录。",
            )
        else:
            bilibili = SourceStatusItem(
                enabled=bili_enabled,
                state="missing",
                detail="未配置 Cookie —— 在浏览器登录 bilibili.com，插件会自动同步。",
            )

        # ── 小红书: token-bearing cache rows = extension is syncing tokens ──
        # A bare COUNT(*) is sticky: one token row from weeks ago keeps the
        # status green forever after the extension stops syncing, while the
        # stored tokens are long dead (xhs 300031 access-denied). Gate "ready"
        # on recent activity instead — a token-bearing cache row discovered,
        # or a candidate token-backfilled (``_backfill_xhs_tokens`` refreshes
        # ``last_seen_at`` without touching ``discovered_at``), inside the
        # freshness window. Old-only rows degrade to ``stale``.
        xhs_enabled = bool(getattr(srcs.xiaohongshu, "enabled", False))
        xhs_tokens = 0
        xhs_fresh = 0
        if hasattr(ctx.database, "conn"):
            window = f"-{_xhs_token_fresh_hours} hours"
            try:
                row = ctx.database.conn.execute(
                    "SELECT COUNT(*), "
                    "COALESCE(SUM(discovered_at >= datetime('now', ?)), 0) "
                    "FROM content_cache "
                    "WHERE source_platform = 'xiaohongshu' "
                    "AND content_url LIKE '%xsec_token=%'",
                    (window,),
                ).fetchone()
                xhs_tokens = int(row[0]) if row else 0
                xhs_fresh = int(row[1]) if row else 0
            except Exception:  # pragma: no cover - defensive
                xhs_tokens = 0
                xhs_fresh = 0
            if xhs_tokens and not xhs_fresh:
                try:
                    row = ctx.database.conn.execute(
                        "SELECT COUNT(*) FROM discovery_candidates "
                        "WHERE source_platform = 'xiaohongshu' "
                        "AND content_url LIKE '%xsec_token=%' "
                        "AND last_seen_at >= datetime('now', ?)",
                        (window,),
                    ).fetchone()
                    xhs_fresh = int(row[0]) if row else 0
                except Exception:  # pragma: no cover - defensive
                    xhs_fresh = 0
        if xhs_fresh > 0:
            xiaohongshu = SourceStatusItem(
                enabled=xhs_enabled,
                state="ready",
                detail=(
                    f"访问令牌已同步（最近 {_xhs_token_fresh_hours} 小时内 {xhs_fresh} 条，"
                    f"共 {xhs_tokens} 条带 xsec_token 的缓存内容）。"
                ),
                logged_in=True,
            )
        elif xhs_tokens > 0:
            xiaohongshu = SourceStatusItem(
                enabled=xhs_enabled,
                state="stale",
                detail=(
                    f"令牌可能已失效 —— 超过 {_xhs_token_fresh_hours} 小时未同步新令牌"
                    f"（存量 {xhs_tokens} 条）。在浏览器逛逛小红书即可自动刷新。"
                ),
            )
        else:
            xiaohongshu = SourceStatusItem(
                enabled=xhs_enabled,
                state="missing",
                detail="未检测到访问令牌 —— 在浏览器登录小红书后插件会自动同步。",
            )

        # ── 抖音: cookie resolvable from env / data/douyin_cookie.json ──
        dy_enabled = bool(getattr(srcs.douyin, "enabled", False))
        dy_cookie = ""
        try:
            from openbiliclaw.sources.douyin_auth import resolve_douyin_cookie

            dy_cookie = resolve_douyin_cookie(
                data_dir=cfg.data_path,
                cookie_env=getattr(srcs.douyin, "cookie_env", "OPENBILICLAW_DOUYIN_COOKIE"),
            )
        except Exception:  # pragma: no cover - defensive
            dy_cookie = ""
        if dy_cookie.strip():
            douyin = SourceStatusItem(
                enabled=dy_enabled, state="ready", detail="Cookie 就绪。", logged_in=True
            )
        else:
            douyin = SourceStatusItem(
                enabled=dy_enabled,
                state="missing",
                detail="未配置 Cookie —— 设置环境变量，或登录抖音后由插件同步。",
            )

        # ── YouTube: public, no login required ──
        youtube = SourceStatusItem(
            enabled=bool(getattr(srcs.youtube, "enabled", False)),
            state="no_auth",
            detail="公开源 · 无需登录。",
            logged_in=True,
        )

        # ── X (Twitter): reuse the live health store ──
        tw_enabled = bool(getattr(srcs.twitter, "enabled", False))
        tw_state, tw_feed_paused = "missing_cookie", False
        if hasattr(ctx.database, "conn"):
            from openbiliclaw.storage.x_health import XSourceHealthStore

            h = XSourceHealthStore(ctx.database).get()
            tw_state = str(h.get("state", "ok"))
            tw_feed_paused = bool(h.get("feed_paused", False))
        # The health row defaults to ``ok`` before any fetch has run, so an
        # ``ok`` without an actual cookie would falsely report a logged-in
        # source — gate it on the resolved credential, like the douyin branch.
        if tw_state == "ok":
            tw_cookie = ""
            with suppress(Exception):
                tw_cookie = resolve_x_cookie(
                    data_dir=cfg.data_path,
                    cookie_env=getattr(srcs.twitter, "cookie_env", "OPENBILICLAW_X_COOKIE"),
                )
            if not tw_cookie.strip():
                tw_state = "missing_cookie"
        tw_detail = _x_state_detail.get(tw_state, f"X 来源状态：{tw_state}。")
        if tw_feed_paused:
            tw_detail += " For-You 因连续失败已自动暂停。"
        twitter = SourceStatusItem(
            enabled=tw_enabled,
            state=tw_state,
            detail=tw_detail,
            logged_in=tw_state == "ok",
            feed_paused=tw_feed_paused,
        )

        zh_cfg = getattr(srcs, "zhihu", None)
        zh_enabled = bool(getattr(zh_cfg, "enabled", False))
        zhihu = SourceStatusItem(
            enabled=zh_enabled,
            state="unverified",
            detail=(
                "浏览器插件登录态源 · 尚未看到知乎任务结果，"
                "保存后可运行 init 或 discover 验证登录态。"
            ),
            logged_in=False,
        )
        if hasattr(ctx.database, "conn"):
            try:
                row = ctx.database.conn.execute(
                    """
                        SELECT type, status, result_json, created_at, completed_at
                        FROM zhihu_tasks
                        WHERE status IN ('pending', 'in_progress', 'completed', 'failed')
                        ORDER BY COALESCE(completed_at, created_at) DESC, created_at DESC
                        LIMIT 1
                        """
                ).fetchone()
            except Exception:
                row = None
            if row is not None:
                task_type = str(row["type"] if hasattr(row, "keys") else row[0])
                status = str(row["status"] if hasattr(row, "keys") else row[1])
                result_json = row["result_json"] if hasattr(row, "keys") else row[2]
                payload: dict[str, Any] = {}
                with suppress(Exception):
                    parsed = json.loads(str(result_json or "{}"))
                    if isinstance(parsed, dict):
                        payload = parsed
                items = payload.get("items")
                item_count = len(items) if isinstance(items, list) else 0
                error_code = str(payload.get("error", "") or "").strip()
                debug = payload.get("debug")
                login_required = error_code == "zhihu_login_required" or (
                    isinstance(debug, dict) and bool(debug.get("login_required"))
                )
                if status == "completed":
                    zhihu = SourceStatusItem(
                        enabled=zh_enabled,
                        state="ready",
                        detail=f"最近任务完成（{task_type}，{item_count} 条）。",
                        logged_in=True,
                    )
                elif login_required:
                    zhihu = SourceStatusItem(
                        enabled=zh_enabled,
                        state="missing",
                        detail="最近知乎任务提示需要登录知乎。请在当前浏览器登录知乎后重试。",
                        logged_in=False,
                    )
                elif status == "failed":
                    suffix = f"：{error_code}" if error_code else ""
                    zhihu = SourceStatusItem(
                        enabled=zh_enabled,
                        state="partial",
                        detail=f"最近知乎任务失败{suffix}。可在浏览器登录态正常后重试。",
                        logged_in=False,
                    )
                elif status in {"pending", "in_progress"}:
                    zhihu = SourceStatusItem(
                        enabled=zh_enabled,
                        state="unverified",
                        detail=f"知乎任务正在等待插件执行（{task_type} / {status}）。",
                        logged_in=False,
                    )

        return SourcesStatusResponse(
            bilibili=bilibili,
            xiaohongshu=xiaohongshu,
            douyin=douyin,
            youtube=youtube,
            twitter=twitter,
            zhihu=zhihu,
        )


    @app.get("/api/observability", response_model=ObservabilityResponse)
    async def observability() -> ObservabilityResponse:
        """Aggregate observability data for the dashboard page."""
        db = getattr(ctx, "database", None)
        if db is None:
            return ObservabilityResponse(
                pipeline=PoolPipelineStats(
                    total_items=0,
                    fresh=0,
                    shown=0,
                    stale=0,
                    suppressed=0,
                    feedbacked=0,
                    pending=0,
                    discovery_candidates_pending=0,
                    discovery_candidates_evaluated=0,
                    items_with_quality_score=0,
                    items_without_quality_score=0,
                ),
                platforms=[],
                score_distribution=[],
                topic_groups=[],
                llm_usage=LLMUsageSummary(),
                discovery_candidates=[],
            )

        def _query() -> ObservabilityResponse:
            # ── 1. Master content_cache aggregate (single query replaces 9+ separate queries) ──
            master = db.conn.execute("""
                SELECT
                  COUNT(*) AS total,
                  AVG(CASE WHEN quality_score > 0.0 THEN quality_score END) AS avg_score,
                  SUM(CASE WHEN quality_score > 0.0 THEN 1 ELSE 0 END) AS scored_count,
                  SUM(CASE WHEN pool_status = 'fresh' THEN 1 ELSE 0 END) AS fresh,
                  SUM(CASE WHEN pool_status = 'shown' THEN 1 ELSE 0 END) AS shown,
                  SUM(CASE WHEN pool_status = 'stale' THEN 1 ELSE 0 END) AS stale,
                  SUM(CASE WHEN pool_status = 'suppressed' THEN 1 ELSE 0 END) AS suppressed,
                  SUM(CASE WHEN pool_status = 'feedbacked' THEN 1 ELSE 0 END) AS feedbacked,
                  SUM(CASE WHEN pool_status = 'pending' THEN 1 ELSE 0 END) AS pending,
                  SUM(CASE WHEN COALESCE(pool_expression, '') != '' THEN 1 ELSE 0 END) AS with_expr,
                  SUM(CASE WHEN COALESCE(pool_expression, '') = '' THEN 1 ELSE 0 END) AS without_expr,
                  SUM(CASE WHEN topic_group != '' AND topic_group IS NOT NULL THEN 1 ELSE 0 END) AS with_topic,
                  SUM(CASE WHEN delight_score > 0.0 THEN 1 ELSE 0 END) AS delight_candidates,
                  SUM(CASE WHEN delight_notified = 1 THEN 1 ELSE 0 END) AS delight_notified,
                  SUM(CASE WHEN last_scored_at IS NOT NULL THEN 1 ELSE 0 END) AS candidates_accepted,
                  SUM(CASE WHEN quality_score <= 0.0 THEN 1 ELSE 0 END) AS bucket_0,
                  SUM(CASE WHEN quality_score > 0.0 AND quality_score <= 0.2 THEN 1 ELSE 0 END) AS bucket_02,
                  SUM(CASE WHEN quality_score > 0.2 AND quality_score <= 0.4 THEN 1 ELSE 0 END) AS bucket_04,
                  SUM(CASE WHEN quality_score > 0.4 AND quality_score <= 0.6 THEN 1 ELSE 0 END) AS bucket_06,
                  SUM(CASE WHEN quality_score > 0.6 AND quality_score <= 0.8 THEN 1 ELSE 0 END) AS bucket_08,
                  SUM(CASE WHEN quality_score > 0.8 AND quality_score <= 1.0 THEN 1 ELSE 0 END) AS bucket_10
                FROM content_cache
            """).fetchone()
            m_total = int(master["total"]) if master else 0
            m_avg = (
                float(master["avg_score"]) if master and master["avg_score"] is not None else 0.0
            )
            m_scored = int(master["scored_count"]) if master else 0

            pipeline = PoolPipelineStats(
                total_items=m_total,
                fresh=int(master["fresh"]) if master else 0,
                shown=int(master["shown"]) if master else 0,
                stale=int(master["stale"]) if master else 0,
                suppressed=int(master["suppressed"]) if master else 0,
                feedbacked=int(master["feedbacked"]) if master else 0,
                pending=int(master["pending"]) if master else 0,
                discovery_candidates_pending=0,
                discovery_candidates_evaluated=0,
                items_with_quality_score=m_scored,
                items_without_quality_score=m_total - m_scored,
                avg_quality_score=round(m_avg, 4),
            )

            # ── 2. Per-platform breakdown ──
            plat_rows = db.conn.execute("""
                SELECT source_platform, pool_status, COUNT(*) AS c
                FROM content_cache
                GROUP BY source_platform, pool_status
                ORDER BY source_platform
            """).fetchall()
            plat_map: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            for r in plat_rows:
                plat = str(r["source_platform"]) or "unknown"
                status = str(r["pool_status"]) or "unknown"
                plat_map[plat][status] += int(r["c"])
            platforms = [
                PlatformPoolStats(
                    platform=plat,
                    total=sum(s.values()),
                    fresh=s.get("fresh", 0),
                    shown=s.get("shown", 0),
                    stale=s.get("stale", 0),
                    suppressed=s.get("suppressed", 0),
                    feedbacked=s.get("feedbacked", 0),
                    pending=s.get("pending", 0),
                )
                for plat, s in sorted(plat_map.items())
            ]

            # ── 3. Score distribution (from master) ──
            score_buckets = [
                ("0 (未评分)", "bucket_0"),
                ("0~0.2", "bucket_02"),
                ("0.2~0.4", "bucket_04"),
                ("0.4~0.6", "bucket_06"),
                ("0.6~0.8", "bucket_08"),
                ("0.8~1.0", "bucket_10"),
            ]
            score_dist = [
                ScoreDistribution(bucket=b, count=int(master[col]) if master else 0)
                for b, col in score_buckets
            ]

            # ── 4. Topic groups ──
            topic_rows = db.conn.execute("""
                SELECT topic_group, COUNT(*) AS c
                FROM content_cache
                WHERE topic_group != '' AND topic_group IS NOT NULL
                GROUP BY topic_group ORDER BY c DESC LIMIT 30
            """).fetchall()
            topic_groups = [
                TopicGroupStats(topic=str(r["topic_group"]), count=int(r["c"])) for r in topic_rows
            ]

            # ── 5. Discovery candidates ──
            disc_rows = db.conn.execute("""
                SELECT status, COUNT(*) AS c
                FROM discovery_candidates GROUP BY status ORDER BY c DESC
            """).fetchall()
            disc_stats = [
                DiscoveryCandidateStats(status=str(r["status"]), count=int(r["c"]))
                for r in disc_rows
            ]

            # Discovery candidates pipeline counts (from disc_rows)
            disc_pending = 0
            disc_evaluated = 0
            for r in disc_rows:
                st = str(r["status"])
                if st in ("pending_eval", "evaluating"):
                    disc_pending += int(r["c"])
                elif st == "evaluated":
                    disc_evaluated += int(r["c"])
            pipeline.discovery_candidates_pending = disc_pending
            pipeline.discovery_candidates_evaluated = disc_evaluated

            # ── 6. LLM usage (combined: by_caller covers all 7d, derive today from it) ──
            caller_rows = db.conn.execute("""
                SELECT caller, COUNT(*) AS calls,
                       COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny,
                       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                       SUM(CASE WHEN timestamp >= datetime('now', 'start of day', 'localtime') THEN 1 ELSE 0 END) AS today_calls,
                       SUM(CASE WHEN timestamp >= datetime('now', 'start of day', 'localtime') THEN COALESCE(estimated_cost_cny, 0) ELSE 0 END) AS today_cost
                FROM llm_usage
                WHERE timestamp >= datetime('now', '-7 day', 'localtime')
                GROUP BY caller ORDER BY cost_cny DESC LIMIT 20
            """).fetchall()
            by_caller: list[dict[str, object]] = []
            total_7d_calls = 0
            total_7d_cost = 0.0
            total_today_calls = 0
            total_today_cost = 0.0
            for r in caller_rows:
                calls = int(r["calls"])
                cost = float(r["cost_cny"])
                t_calls = int(r["today_calls"])
                t_cost = float(r["today_cost"])
                total_7d_calls += calls
                total_7d_cost += cost
                total_today_calls += t_calls
                total_today_cost += t_cost
                by_caller.append(
                    {
                        "caller": str(r["caller"] or "unknown"),
                        "calls": calls,
                        "cost_cny": round(cost, 4),
                        "prompt_tokens": int(r["prompt_tokens"]),
                        "completion_tokens": int(r["completion_tokens"]),
                    }
                )
            llm_usage = LLMUsageSummary(
                today_calls=total_today_calls,
                today_cost_cny=round(total_today_cost, 4),
                total_calls_7d=total_7d_calls,
                total_cost_7d=round(total_7d_cost, 4),
                by_caller=by_caller,
            )

            # ── 7. Runtime snapshot ──
            runtime: dict[str, object] = {}
            get_runtime_status = getattr(ctx.runtime_controller, "get_runtime_status", None)
            if callable(get_runtime_status):
                with suppress(Exception):
                    st = get_runtime_status()
                    if isinstance(st, dict):
                        for k in (
                            "last_refresh_at",
                            "last_discovered_count",
                            "last_replenished_count",
                            "pool_available_count",
                            "pool_raw_count",
                            "pool_target_count",
                            "recommendation_count",
                            "recent_pool_topics",
                            "pending_delight_count",
                            "last_delight_notification_at",
                            "manual_refresh_state",
                            "pending_signal_events",
                        ):
                            if k in st:
                                runtime[k] = st[k]

            # ── 8. Keywords stats ──
            kw_rows = db.conn.execute("""
                SELECT platform, status, COUNT(*) AS c
                FROM discovery_keywords GROUP BY platform, status ORDER BY platform, status
            """).fetchall()
            keywords = [
                {
                    "platform": str(r["platform"] or "unknown"),
                    "status": str(r["status"] or "unknown"),
                    "count": int(r["c"]),
                }
                for r in kw_rows
            ]

            # ── 9. Eval stats (combined single query) ──
            eval_row = db.conn.execute("""
                SELECT COUNT(*) AS total, COALESCE(SUM(eval_attempts), 0) AS attempts
                FROM discovery_candidates
            """).fetchone()
            eval_total_c = int(eval_row["total"]) if eval_row else 0
            eval_stats: dict[str, object] = {
                "total_candidates": eval_total_c,
                "total_eval_attempts": int(eval_row["attempts"]) if eval_row else 0,
                "candidates_accepted": int(master["candidates_accepted"]) if master else 0,
                "acceptance_rate": round(
                    int(master["candidates_accepted"]) / max(eval_total_c, 1) * 100, 1
                )
                if eval_total_c
                else 0,
            }

            # ── 10. Event stats (combined single query) ──
            event_rows = db.conn.execute("""
                SELECT event_type, source_platform, inferred_satisfaction, COUNT(*) AS c
                FROM events
                GROUP BY event_type, source_platform, inferred_satisfaction
            """).fetchall()
            event_types: dict[str, int] = defaultdict(int)
            event_platforms: dict[str, int] = defaultdict(int)
            sat_map: dict[str, int] = defaultdict(int)
            for r in event_rows:
                et = str(r["event_type"] or "unknown")
                sp = str(r["source_platform"]) or None
                sat = str(r["inferred_satisfaction"]) or None
                c = int(r["c"])
                event_types[et] += c
                if sp:
                    event_platforms[sp] += c
                if sat:
                    sat_map[sat] += c
            event_total = sum(event_types.values())
            event_stats = {
                "total_events": event_total,
                "by_type": dict(event_types),
                "by_platform": dict(event_platforms),
            }
            satisfaction_distribution = [
                {"satisfaction": k, "count": v}
                for k, v in sorted(sat_map.items(), key=lambda x: -x[1])
            ]

            # ── 11. Feedback stats ──
            fb_rows = db.conn.execute("""
                SELECT feedback_type, COUNT(*) AS c FROM content_cache
                WHERE feedback_type != '' AND feedback_type IS NOT NULL
                GROUP BY feedback_type ORDER BY c DESC
            """).fetchall()
            fb_types = {str(r["feedback_type"]): int(r["c"]) for r in fb_rows}
            feedback_stats = {"total_feedback": sum(fb_types.values()), "by_type": fb_types}

            # ── 12. Expression / Delight (from master) ──
            expression_coverage = {
                "with_expression": int(master["with_expr"]) if master else 0,
                "without_expression": int(master["without_expr"]) if master else 0,
                "with_topic_group": int(master["with_topic"]) if master else 0,
                "with_quality_score": m_scored,
            }
            delight_stats = {
                "delight_candidates": int(master["delight_candidates"]) if master else 0,
                "delight_notified": int(master["delight_notified"]) if master else 0,
                "pending_delight": runtime.get("pending_delight_count", 0),
                "last_delight_notification": runtime.get("last_delight_notification_at", ""),
            }

            # ── 13. Soul profile (pre-fetched) ──
            soul_profile = _soul_profile_cache

            # ── 14. Scheduler loop health ──
            scheduler_loops: list[dict[str, object]] = []
            try:
                controller = getattr(ctx, "runtime_controller", None)
                if controller is not None and hasattr(controller, "get_loop_health"):
                    scheduler_loops = controller.get_loop_health()
            except Exception:
                pass

            # ── 15. Auth sources ──
            auth_sources: list[dict[str, object]] = []
            try:
                ss_resp = sources_status()
                if isinstance(ss_resp, SourcesStatusResponse):
                    for s in cast("Any", ss_resp).sources:
                        auth_sources.append(
                            {
                                "platform": s.platform,
                                "label": s.label,
                                "status": s.status,
                                "last_ok_at": s.last_ok_at or "",
                                "error": s.error or "",
                                "cookie_age_hours": s.cookie_age_hours,
                            }
                        )
            except Exception:
                pass

            # ── 16. Style distribution ──
            style_rows = db.conn.execute("""
                SELECT style_key, COUNT(*) AS c FROM content_cache
                WHERE style_key != '' AND style_key IS NOT NULL
                GROUP BY style_key ORDER BY c DESC LIMIT 20
            """).fetchall()
            style_distribution = [
                {"style": str(r["style_key"]), "count": int(r["c"])} for r in style_rows
            ]

            # ── 17. Suppressed (quota-managed) qualification breakdown ──
            # One GROUP BY over source_platform × qualification bucket so the
            # dashboard can explain WHY items sit outside the rotation pool:
            # qualified = passed admission line + full precompute + linkable
            # (could re-enter if reactivated), below_threshold = LLM scored
            # them below the 0.60 admission line, unevaluated = no score yet.
            suppressed_rows = db.conn.execute("""
                SELECT
                    source_platform AS platform,
                    SUM(CASE WHEN COALESCE(relevance_score, 0.0) >= 0.60
                              AND COALESCE(pool_expression, '') != ''
                              AND COALESCE(pool_topic_label, '') != ''
                              AND COALESCE(style_key, '') != ''
                              AND COALESCE(topic_group, '') != ''
                              AND COALESCE(content_url, '') != ''
                        THEN 1 ELSE 0 END) AS qualified,
                    SUM(CASE WHEN COALESCE(relevance_score, 0.0) > 0
                              AND COALESCE(relevance_score, 0.0) < 0.60
                        THEN 1 ELSE 0 END) AS below_threshold,
                    SUM(CASE WHEN COALESCE(relevance_score, 0.0) = 0
                        THEN 1 ELSE 0 END) AS unevaluated,
                    COUNT(*) AS total
                FROM content_cache
                WHERE pool_status = 'suppressed'
                GROUP BY source_platform
                ORDER BY total DESC
            """).fetchall()
            suppressed_breakdown = [
                {
                    "platform": str(r["platform"] or "unknown"),
                    "qualified": int(r["qualified"]),
                    "below_threshold": int(r["below_threshold"]),
                    "unevaluated": int(r["unevaluated"]),
                    "total": int(r["total"]),
                }
                for r in suppressed_rows
            ]

            return ObservabilityResponse(
                pipeline=pipeline,
                platforms=platforms,
                score_distribution=score_dist,
                topic_groups=topic_groups,
                llm_usage=llm_usage,
                discovery_candidates=disc_stats,
                runtime=runtime,
                keywords=keywords,
                eval_stats=eval_stats,
                event_stats=event_stats,
                feedback_stats=feedback_stats,
                expression_coverage=expression_coverage,
                delight_stats=delight_stats,
                soul_profile=soul_profile,
                scheduler_loops=scheduler_loops,
                auth_sources=auth_sources,
                style_distribution=style_distribution,
                satisfaction_distribution=satisfaction_distribution,
                suppressed_breakdown=suppressed_breakdown,
            )

        # Pre-fetch soul profile in async context (not inside thread pool executor)
        _soul_profile_cache: dict[str, object] = {}
        try:
            soul_engine = getattr(ctx, "soul_engine", None)
            if soul_engine is not None and hasattr(soul_engine, "get_profile"):
                profile = await soul_engine.get_profile()
                if profile is not None:
                    interest_tags = (
                        len(getattr(profile.interest, "likes", []))
                        if hasattr(profile, "interest")
                        else 0
                    )
                    awareness_count = len(getattr(profile, "recent_awareness", []))
                    insights_count = len(getattr(profile, "active_insights", []))
                    portrait = getattr(profile, "personality_portrait", "")[:100]
                    _soul_profile_cache = {
                        "interest_tags_count": interest_tags,
                        "awareness_notes_count": awareness_count,
                        "insight_hypotheses_count": insights_count,
                        "personality_traits": portrait,
                    }
        except Exception:
            _soul_profile_cache = {"error": "profile unavailable"}

        return await asyncio.get_running_loop().run_in_executor(None, _query)

    def _mask_source_credential(value: str, *, reveal: bool) -> str:
        if reveal or not value:
            return value
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}{'*' * max(4, len(value) - 8)}{value[-4:]}"

    def _xhs_token_from_url(url: str) -> str:
        match = re.search(r"(?:[?&])xsec_token=([^&#]+)", str(url or ""))
        return match.group(1) if match else ""

    def _latest_xhs_token() -> str:
        if not hasattr(ctx.database, "conn"):
            return ""
        queries = (
            """
                SELECT content_url
                FROM discovery_candidates
                WHERE source_platform = 'xiaohongshu'
                  AND content_url LIKE '%xsec_token=%'
                ORDER BY last_seen_at DESC, id DESC
                LIMIT 1
                """,
            """
                SELECT content_url
                FROM content_cache
                WHERE source_platform = 'xiaohongshu'
                  AND content_url LIKE '%xsec_token=%'
                ORDER BY discovered_at DESC, bvid DESC
                LIMIT 1
                """,
        )
        for sql in queries:
            with suppress(Exception):
                row = ctx.database.conn.execute(sql).fetchone()
                if row:
                    url = row["content_url"] if hasattr(row, "keys") else row[0]
                    token = _xhs_token_from_url(str(url))
                    if token:
                        return token
        return ""

    @app.get("/api/sources/credentials", response_model=SourcesCredentialsResponse)
    def sources_credentials(reveal_keys: bool = False) -> SourcesCredentialsResponse:
        """Return current local Cookie / token snapshots for source settings pages."""
        from openbiliclaw.bilibili.auth import resolve_runtime_cookie
        from openbiliclaw.config import load_config
        from openbiliclaw.sources.douyin_auth import resolve_douyin_cookie

        cfg = load_config()
        srcs = cfg.sources

        bili_cookie = resolve_runtime_cookie(
            data_dir=cfg.data_path,
            configured_cookie=str(getattr(cfg.bilibili, "cookie", "") or ""),
        )
        dy_cookie = resolve_douyin_cookie(
            data_dir=cfg.data_path,
            cookie_env=getattr(srcs.douyin, "cookie_env", "OPENBILICLAW_DOUYIN_COOKIE"),
        )
        tw_cookie = resolve_x_cookie(
            data_dir=cfg.data_path,
            cookie_env=getattr(srcs.twitter, "cookie_env", "OPENBILICLAW_X_COOKIE"),
        )
        xhs_token = _latest_xhs_token()

        def item(label: str, value: str, detail: str) -> SourceCredentialItem:
            return SourceCredentialItem(
                label=label,
                value=_mask_source_credential(value, reveal=reveal_keys),
                available=bool(value.strip()),
                detail=detail,
            )

        return SourcesCredentialsResponse(
            bilibili=item("Cookie", bili_cookie, "B 站当前 resolved Cookie。"),
            xiaohongshu=item(
                "xsec_token",
                xhs_token,
                "小红书不保存整站 Cookie；这里展示最近同步内容 URL 中的 xsec_token。",
            ),
            douyin=item("Cookie", dy_cookie, "抖音当前 resolved Cookie。"),
            youtube=SourceCredentialItem(
                label="Cookie",
                available=False,
                detail="YouTube 当前按公开源接入，后端不保存 Cookie。",
            ),
            twitter=item("Cookie", tw_cookie, "X 当前 resolved Cookie。"),
            zhihu=SourceCredentialItem(
                label="Cookie",
                available=False,
                detail="知乎登录态保存在浏览器站点 / 插件上下文中，后端不保存可展示 Cookie。",
            ),
        )

    # ── Douyin task queue endpoints (extension dispatcher) ──────────
    # Independent from the XHS block above by design — see
    # docs/plans/2026-05-06-douyin-bootstrap-import-design.md
    # §"Module Isolation from XHS". Different table (dy_tasks),
    # different queue class, different fail isolation.

    from openbiliclaw.sources.dy_tasks import (
        DyTaskQueue,
        dy_bootstrap_video_key,
        dy_bootstrap_videos_to_events,
    )

    _dy_task_queue: DyTaskQueue | None = None
    if hasattr(ctx.database, "conn"):
        _dy_task_queue = DyTaskQueue(ctx.database)

    @app.get("/api/sources/dy/next-task")
    def dy_next_task(response: Any = None) -> Any:
        """Return the oldest pending dy task, or 204 if none."""
        if _dy_task_queue is None:
            return Response(status_code=204)
        task = _dy_task_queue.next_pending(only_ids=_init_owned_ids_filter())
        if task is None:
            return Response(status_code=204)

        import json as _json

        payload = _json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            **payload,
        }

    @app.post("/api/sources/dy/task-result")
    async def dy_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept a Douyin task result from the extension dispatcher.

        Status semantics mirror XHS (``ok`` = final, ``partial`` = keep
        pending, ``failed`` = mark failed) but the result schema uses
        ``videos`` instead of ``notes`` and propagation goes through
        ``dy_bootstrap_videos_to_events``. No self-author filtering yet
        (Douyin has its own posts in ``dy_post`` scope which we treat as
        a weak ``view`` signal — they're meant to count as input).
        """
        task_id = payload.get("task_id", "")
        status = payload.get("status", "")
        videos = [v for v in payload.get("videos", []) if isinstance(v, dict)]
        # TEMP DEBUG: surface incoming partial debug field for the dy
        # bootstrap e2e probe (will be reverted before release).
        logger.info(
            "[dy-debug] task_result IN: status=%s videos=%d debug=%s",
            status,
            len(videos),
            payload.get("debug"),
        )
        scope_counts = payload.get("scope_counts")
        if not isinstance(scope_counts, dict):
            scope_counts = None
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = None

        if not task_id:
            from fastapi import HTTPException

            raise HTTPException(status_code=422, detail="task_id is required")

        if _dy_task_queue is None:
            return {"ok": True}

        task = _dy_task_queue.get(task_id)
        task_type = str(task.get("type", "")).strip() if task else ""

        if status in {"partial", "ok"} or (status == "empty" and task_type == "bootstrap_profile"):
            is_final = status == "ok" or (status == "empty" and task_type == "bootstrap_profile")
            added_videos = _dy_task_queue.merge_result(
                task_id,
                videos=videos if videos else None,
                scope_counts=scope_counts,
                debug=debug,
                complete=is_final,
            )
            # gui-init D1: persist the result (above) for init's own collector;
            # during init skip profile propagation for non-owned results, but
            # propagate init-OWNED bootstrap results through the deduped path.
            _init_busy = _init_active_now()
            _skip_profile = _init_busy and not _init_owns_task(task_id)
            if task_type == "bootstrap_profile" and added_videos and not _skip_profile:
                fresh_videos, video_keys_by_index = _filter_new_source_bootstrap_items(
                    "dy",
                    added_videos,
                    dy_bootstrap_video_key,
                )
                profile_events: list[dict[str, Any]] = []
                propagated_keys: list[str] = []
                for index, video in enumerate(fresh_videos):
                    for event in dy_bootstrap_videos_to_events([video]):
                        await ctx.memory_manager.propagate_event(event)
                        profile_events.append(event)
                        key = video_keys_by_index.get(index, "")
                        if key:
                            propagated_keys.append(key)
                # Skip the incremental pipeline during init (see xhs handler).
                if not _init_busy:
                    await _ingest_profile_update_events(profile_events)
                _mark_source_bootstrap_keys("dy", propagated_keys)
        else:
            _dy_task_queue.fail(task_id, error=payload.get("error", ""), debug=debug)

        return {"ok": True}

    # ── Wake-up kick endpoints ──────────────────────────────────────
    #
    # The extension's task dispatchers normally poll on a 60s
    # chrome.alarms timer. That's fine for the steady state but
    # introduces a 0–60s wait between CLI enqueue and extension pickup,
    # which racing init's 30s collect window is the actual reason init
    # sometimes prints "扩展未连接或任务仍在后台跑". These endpoints let
    # the CLI broadcast a wake-up event over the existing
    # /api/runtime-stream WebSocket so the dispatcher polls immediately
    # instead of waiting for the next alarm. The 60s alarm stays as
    # fallback for the WS-down case.

    # TEMP DEBUG: extension-side log relay. Lets the service-worker
    # dispatcher POST debug events here so they end up in the daemon
    # log alongside backend-side activity. Will be reverted before
    # release.
    @app.post("/api/sources/_debug/log")
    async def ext_debug_log(payload: dict[str, Any]) -> dict[str, Any]:
        source = str(payload.get("source", "?"))[:8]
        event = str(payload.get("event", "?"))[:80]
        data = payload.get("data")
        logger.warning("[ext-debug] [%s] %s data=%s", source, event, data)
        return {"ok": True}

    @app.post("/api/sources/xhs/kick")
    async def xhs_task_kick() -> dict[str, Any]:
        """Broadcast `xhs_task_available` so any subscribed extension
        service-worker triggers an immediate poll. Idempotent and best
        effort — failures here never affect task state.
        """
        publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
        if callable(publish):
            with suppress(Exception):
                await publish({"type": "xhs_task_available", "source": "task_kick"})
        return {"ok": True}

    @app.post("/api/sources/dy/kick")
    async def dy_task_kick() -> dict[str, Any]:
        """Broadcast `dy_task_available` over runtime-stream. See
        xhs_task_kick docstring for rationale.
        """
        publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
        if callable(publish):
            with suppress(Exception):
                await publish({"type": "dy_task_available", "source": "task_kick"})
        return {"ok": True}

    # ── YouTube bootstrap endpoints ────────────────────────────────
    from openbiliclaw.sources.yt_tasks import (
        YtTaskQueue,
        yt_bootstrap_item_key,
        yt_bootstrap_items_to_events,
    )
    from openbiliclaw.sources.zhihu_tasks import (
        ZhihuTaskQueue,
        zhihu_bootstrap_item_key,
        zhihu_bootstrap_items_to_events,
    )

    _zhihu_task_queue: ZhihuTaskQueue | None = None
    db_conn = getattr(ctx.database, "conn", None)
    if hasattr(db_conn, "executescript"):
        _zhihu_task_queue = ZhihuTaskQueue(ctx.database)

    @app.get("/api/sources/zhihu/next-task")
    def zhihu_next_task(response: Any = None) -> Any:
        """Return the oldest pending Zhihu task, or 204 if none."""
        if _zhihu_task_queue is None:
            return Response(status_code=204)
        task = _zhihu_task_queue.next_pending(only_ids=_init_owned_ids_filter())
        if task is None:
            return Response(status_code=204)

        import json as _json

        payload = _json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            **payload,
        }

    @app.post("/api/sources/zhihu/task-result")
    async def zhihu_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept a Zhihu task result from the extension dispatcher.

        Plain ``fetch-zhihu`` smoke tasks only record the task payload. Tasks
        explicitly marked ``profile_update`` also propagate bootstrap events to
        memory and, once a profile exists, into the incremental profile-update
        pipeline.
        """
        task_id = str(payload.get("task_id", "") or "").strip()
        status = str(payload.get("status", "") or "").strip()
        items = [v for v in payload.get("items", []) if isinstance(v, dict)]
        scope_counts = payload.get("scope_counts")
        if not isinstance(scope_counts, dict):
            scope_counts = None
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = None

        if not task_id:
            from fastapi import HTTPException

            raise HTTPException(status_code=422, detail="task_id is required")

        if _zhihu_task_queue is None:
            return {"ok": True}

        task = _zhihu_task_queue.get(task_id)
        task_type = str(task.get("type", "")).strip() if task else ""
        task_payload: dict[str, Any] = {}
        if task and task.get("payload_json"):
            with suppress(Exception):
                parsed_payload = json.loads(str(task.get("payload_json") or "{}"))
                if isinstance(parsed_payload, dict):
                    task_payload = parsed_payload
        profile_update = bool(task_payload.get("profile_update"))

        if status in {"partial", "ok"} or status == "empty":
            is_final = status in {"ok", "empty"}
            added_items = _zhihu_task_queue.merge_result(
                task_id,
                items=items if items else None,
                scope_counts=scope_counts,
                debug=debug,
                complete=is_final,
            )
            _init_busy = _init_active_now()
            _skip_profile = _init_busy and not _init_owns_task(task_id)
            if (
                task_type == "bootstrap_events"
                and profile_update
                and added_items
                and not _skip_profile
            ):
                fresh_items, item_keys_by_index = _filter_new_source_bootstrap_items(
                    "zhihu",
                    added_items,
                    zhihu_bootstrap_item_key,
                )
                profile_events: list[dict[str, Any]] = []
                propagated_keys: list[str] = []
                for index, item in enumerate(fresh_items):
                    for event in zhihu_bootstrap_items_to_events([item]):
                        await ctx.memory_manager.propagate_event(event)
                        profile_events.append(event)
                        key = item_keys_by_index.get(index, "")
                        if key:
                            propagated_keys.append(key)
                if not _init_busy:
                    await _ingest_profile_update_events(profile_events)
                _mark_source_bootstrap_keys("zhihu", propagated_keys)
        else:
            _zhihu_task_queue.fail(task_id, error=str(payload.get("error", "") or ""), debug=debug)

        return {"ok": True}

    @app.post("/api/sources/zhihu/kick")
    async def zhihu_task_kick() -> dict[str, Any]:
        """Broadcast `zhihu_task_available` over runtime-stream."""
        publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
        if callable(publish):
            with suppress(Exception):
                await publish({"type": "zhihu_task_available", "source": "task_kick"})
        return {"ok": True}

    _yt_task_queue: YtTaskQueue | None = None
    if hasattr(ctx.database, "conn"):
        _yt_task_queue = YtTaskQueue(ctx.database)

    @app.get("/api/sources/yt/next-task")
    def yt_next_task(response: Any = None) -> Any:
        """Return the oldest pending YouTube task, or 204 if none."""
        if _yt_task_queue is None:
            return Response(status_code=204)
        task = _yt_task_queue.next_pending(only_ids=_init_owned_ids_filter())
        if task is None:
            return Response(status_code=204)

        import json as _json

        payload = _json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            **payload,
        }

    @app.post("/api/sources/yt/task-result")
    async def yt_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept a YouTube task result from the extension dispatcher."""
        task_id = payload.get("task_id", "")
        status = payload.get("status", "")
        items = [v for v in payload.get("items", []) if isinstance(v, dict)]
        scope_counts = payload.get("scope_counts")
        if not isinstance(scope_counts, dict):
            scope_counts = None
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = None

        if not task_id:
            from fastapi import HTTPException

            raise HTTPException(status_code=422, detail="task_id is required")

        if _yt_task_queue is None:
            return {"ok": True}

        task = _yt_task_queue.get(task_id)
        task_type = str(task.get("type", "")).strip() if task else ""

        if status in {"partial", "ok"} or (status == "empty" and task_type == "bootstrap_profile"):
            is_final = status == "ok" or (status == "empty" and task_type == "bootstrap_profile")
            added_items = _yt_task_queue.merge_result(
                task_id,
                items=items if items else None,
                scope_counts=scope_counts,
                debug=debug,
                complete=is_final,
            )
            # gui-init D1: persist the result (above) for init's own collector;
            # during init skip profile propagation for non-owned results, but
            # propagate init-OWNED bootstrap results through the deduped path.
            _init_busy = _init_active_now()
            _skip_profile = _init_busy and not _init_owns_task(task_id)
            if task_type == "bootstrap_profile" and added_items and not _skip_profile:
                fresh_items, item_keys_by_index = _filter_new_source_bootstrap_items(
                    "yt",
                    added_items,
                    yt_bootstrap_item_key,
                )
                profile_events: list[dict[str, Any]] = []
                propagated_keys: list[str] = []
                for index, item in enumerate(fresh_items):
                    for event in yt_bootstrap_items_to_events([item]):
                        await ctx.memory_manager.propagate_event(event)
                        profile_events.append(event)
                        key = item_keys_by_index.get(index, "")
                        if key:
                            propagated_keys.append(key)
                # Skip the incremental pipeline during init (see xhs handler).
                if not _init_busy:
                    await _ingest_profile_update_events(profile_events)
                _mark_source_bootstrap_keys("yt", propagated_keys)
        else:
            _yt_task_queue.fail(task_id, error=payload.get("error", ""), debug=debug)

        return {"ok": True}

    @app.post("/api/sources/yt/kick")
    async def yt_task_kick() -> dict[str, Any]:
        """Broadcast `yt_task_available` over runtime-stream."""
        publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
        if callable(publish):
            with suppress(Exception):
                await publish({"type": "yt_task_available", "source": "task_kick"})
        return {"ok": True}

    @app.post("/api/extension/e2e/run", response_model=ExtensionE2ERunOut)
    async def extension_e2e_run(
        request: Request,
        payload: ExtensionE2ERunIn,
    ) -> ExtensionE2ERunOut:
        """Local-only control plane for extension E2E simulation runs."""
        if not _get_auth_gate().is_trusted_local(request):
            raise HTTPException(status_code=403, detail="local_only")

        registry = cast("dict[str, _ExtensionE2ERunState]", app.state.extension_e2e_runs)
        if registry:
            raise HTTPException(status_code=409, detail="e2e_run_in_progress")

        expected_actions = _extension_e2e_actions_for_request(payload)
        if not payload.allow_state_changing:
            blocked_actions = sorted(
                {
                    action
                    for actions in expected_actions.values()
                    for action in actions
                    if action in _E2E_STATE_CHANGING_ACTIONS
                }
            )
            if blocked_actions:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "allow_state_changing must be true for actions: "
                        + ", ".join(blocked_actions)
                    ),
                )

        run_id = f"e2e-{uuid.uuid4().hex}"
        token = secrets.token_urlsafe(32)
        after_event_id = _latest_e2e_event_id(ctx)
        state = _ExtensionE2ERunState(
            run_id=run_id,
            token=token,
            started_at=time.time(),
            after_event_id=after_event_id,
            expected_actions=expected_actions,
            event=asyncio.Event(),
        )
        registry[run_id] = state
        timed_out = False

        try:
            publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
            if not callable(publish):
                state.error = "extension_runtime_unavailable"
            else:
                delivered = await publish(
                    {
                        "type": "extension_e2e_run",
                        "source": "api",
                        "run_id": run_id,
                        "token": token,
                        "platforms": list(expected_actions.keys()),
                        "actions": {
                            platform: list(actions)
                            for platform, actions in expected_actions.items()
                        },
                        "allow_state_changing": payload.allow_state_changing,
                        "timeout_seconds": payload.timeout_seconds,
                    }
                )
                if delivered is False:
                    state.error = "extension_runtime_unavailable"

            if not state.error:
                try:
                    await asyncio.wait_for(state.event.wait(), timeout=payload.timeout_seconds)
                except TimeoutError:
                    timed_out = True

            events = _query_e2e_events(ctx, after_event_id=after_event_id)
            return _build_extension_e2e_report(
                state,
                events,
                timed_out=timed_out,
                timeout_seconds=payload.timeout_seconds,
            )
        finally:
            registry.pop(run_id, None)

    @app.post("/api/extension/e2e/result")
    async def extension_e2e_result(
        request: Request,
        payload: ExtensionE2EResultIn,
    ) -> dict[str, object]:
        """Accept a signed callback from the extension E2E runner."""
        if not _get_auth_gate().is_trusted_local(request):
            raise HTTPException(status_code=403, detail="local_only")

        registry = cast("dict[str, _ExtensionE2ERunState]", app.state.extension_e2e_runs)
        state = registry.get(payload.run_id)
        if state is None:
            raise HTTPException(status_code=404, detail="unknown run_id")
        if not secrets.compare_digest(state.token, payload.token):
            raise HTTPException(status_code=403, detail="bad token")

        state.extension_result = payload
        state.event.set()
        return {"ok": True, "run_id": payload.run_id}

    @app.post("/api/extension/reload")
    async def extension_reload() -> dict[str, Any]:
        """Dev-only: broadcast `extension_reload` so the connected
        service-worker calls chrome.runtime.reload() — picks up the
        latest /dist bundle without the user clicking the reload icon
        in chrome://extensions.

        Best-effort — silent when no event-hub is wired.
        """
        publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
        if callable(publish):
            with suppress(Exception):
                await publish({"type": "extension_reload", "source": "dev"})
        return {"ok": True}

    def _autostart_status_out(
        request: Request,
        cfg: Any,
        *,
        reason_override: str | None = None,
        detail_override: str | None = None,
    ) -> AutostartStatusOut:
        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart.guards import (
            active_env_managed_inputs,
            autostart_shadowed,
        )
        from openbiliclaw.runtime.ollama_supervisor import (
            effective_ollama_endpoint,
            is_loopback,
            ollama_required,
        )

        state = autostart.status()
        managed_env = active_env_managed_inputs(cfg)
        shadowed = autostart_shadowed(cfg.autostart.enabled)
        trusted_local = _get_auth_gate().is_trusted_local(request)
        requires_ollama = ollama_required(cfg)

        reason = "none"
        if not state.supported:
            reason = state.reason
        elif not trusted_local:
            reason = "local_only"
        elif managed_env:
            reason = "env_managed"
        elif shadowed:
            reason = "shadowed"
        if reason_override is not None:
            reason = reason_override

        detail = ""
        if not state.supported:
            detail = "当前运行环境不支持注册开机自启动。"
        elif not trusted_local:
            detail = "仅本机可信请求可以修改开机自启动。"
        elif managed_env:
            detail = "检测到环境变量配置，自启动登录会话可能缺失：" + ", ".join(managed_env)
        elif shadowed:
            detail = "config.local.toml 覆盖了 [autostart].enabled，config.toml 修改不会生效。"
        elif cfg.autostart.enabled and not state.registered:
            detail = "开机自启动配置已开启，但系统自启动项缺失。"
        elif cfg.autostart.enabled:
            detail = "开机自启动已开启。"
        else:
            detail = "尚未开启开机自启动。"
        if detail_override is not None:
            detail = detail_override

        if requires_ollama:
            endpoint = effective_ollama_endpoint(cfg)
            if not is_loopback(endpoint):
                detail = (detail + " " if detail else "") + "Ollama 端点是远端地址，需自行管理。"

        return AutostartStatusOut(
            supported=state.supported,
            enabled=cfg.autostart.enabled,
            registered=state.registered,
            can_manage=trusted_local and state.supported and not managed_env and not shadowed,
            platform=state.platform,
            mechanism=state.mechanism,
            manage_ollama=cfg.autostart.manage_ollama,
            ollama_required=requires_ollama,
            reason=reason,
            detail=detail,
        )

    @app.get("/api/autostart-status", response_model=AutostartStatusOut)
    def autostart_status(request: Request) -> AutostartStatusOut:
        from openbiliclaw.config import load_config

        cfg = load_config()
        return _autostart_status_out(request, cfg)

    @app.post("/api/autostart/apply", response_model=AutostartStatusOut)
    async def autostart_apply(
        payload: AutostartApplyIn, request: Request
    ) -> AutostartStatusOut | JSONResponse:
        from openbiliclaw.config import load_config as _load
        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart.guards import active_env_managed_inputs

        cfg = _load()
        if not _get_auth_gate().is_trusted_local(request):
            body = _autostart_status_out(
                request,
                cfg,
                reason_override="local_only",
                detail_override="仅本机可信请求可以修改开机自启动。",
            )
            return JSONResponse(status_code=403, content=body.model_dump(mode="json"))

        current = autostart.status()
        if not current.supported:
            body = _autostart_status_out(
                request,
                cfg,
                reason_override=current.reason,
                detail_override="当前运行环境不支持注册开机自启动。",
            )
            return JSONResponse(status_code=409, content=body.model_dump(mode="json"))

        managed = active_env_managed_inputs(cfg)
        if payload.enabled and managed:
            body = _autostart_status_out(
                request,
                cfg,
                reason_override="env_managed",
                detail_override="检测到环境变量配置，自启动登录会话可能缺失：" + ", ".join(managed),
            )
            return JSONResponse(status_code=409, content=body.model_dump(mode="json"))

        async with _CONFIG_SAVE_LOCK:
            config_path = _cfg_path()
            config_existed = config_path.exists()
            backup_path = _snapshot_config_file(config_path)

            def _rollback_cfg() -> None:
                if backup_path is not None:
                    with suppress(Exception):
                        _restore_config_snapshot(backup_path, config_path)
                elif not config_existed:
                    with suppress(Exception):
                        config_path.unlink(missing_ok=True)

            cfg = _load()
            was_registered = autostart.status().registered

            if payload.enabled:
                cfg.autostart.enabled = True
                try:
                    _save(cfg, autostart_authoritative=True)
                except Exception:
                    _rollback_cfg()
                    logger.warning("autostart: enable save_config failed", exc_info=True)
                    body = _autostart_status_out(
                        request,
                        _load(),
                        reason_override="unavailable",
                        detail_override="保存配置失败，开机自启动未修改。",
                    )
                    return JSONResponse(status_code=503, content=body.model_dump(mode="json"))

                effective = _load()
                if effective.autostart.enabled is not True:
                    _rollback_cfg()
                    body = _autostart_status_out(
                        request,
                        _load(),
                        reason_override="shadowed",
                        detail_override=(
                            "config.local.toml 覆盖了 [autostart].enabled，"
                            "config.toml 修改不会生效。"
                        ),
                    )
                    return JSONResponse(status_code=409, content=body.model_dump(mode="json"))

                try:
                    autostart.register(effective)
                except Exception:
                    _rollback_cfg()
                    logger.warning("autostart: OS registration failed", exc_info=True)
                    body = _autostart_status_out(
                        request,
                        _load(),
                        reason_override="registration_failed",
                        detail_override="系统自启动项注册失败，配置已回滚。",
                    )
                    return JSONResponse(status_code=409, content=body.model_dump(mode="json"))
                return _autostart_status_out(request, _load())

            try:
                autostart.unregister()
            except Exception:
                logger.warning("autostart: OS unregister failed", exc_info=True)
                body = _autostart_status_out(
                    request,
                    cfg,
                    reason_override="unregister_failed",
                    detail_override="系统自启动项移除失败，配置未修改。",
                )
                return JSONResponse(status_code=409, content=body.model_dump(mode="json"))

            cfg.autostart.enabled = False
            try:
                _save(cfg, autostart_authoritative=True)
            except Exception:
                if was_registered:
                    with suppress(Exception):
                        cfg.autostart.enabled = True
                        autostart.register(cfg)
                _rollback_cfg()
                logger.warning("autostart: disable save_config failed", exc_info=True)
                body = _autostart_status_out(
                    request,
                    _load(),
                    reason_override="unavailable",
                    detail_override="保存配置失败，系统自启动项已尝试恢复。",
                )
                return JSONResponse(status_code=503, content=body.model_dump(mode="json"))

            effective = _load()
            if effective.autostart.enabled is not False:
                if was_registered:
                    with suppress(Exception):
                        cfg.autostart.enabled = True
                        autostart.register(cfg)
                _rollback_cfg()
                body = _autostart_status_out(
                    request,
                    _load(),
                    reason_override="shadowed",
                    detail_override=(
                        "config.local.toml 覆盖了 [autostart].enabled，config.toml 修改不会生效。"
                    ),
                )
                return JSONResponse(status_code=409, content=body.model_dump(mode="json"))

            return _autostart_status_out(request, _load())

    return {
        "sources_status": sources_status,
        "_pick_best_xhs_url": _pick_best_xhs_url,
        "_load_xhs_self_info": _load_xhs_self_info,
        "_purge_self_authored_pool_items": _purge_self_authored_pool_items,
    }

"""ContinuousRefreshController 共享常量与签名探测工具。

从 ``runtime/refresh.py`` 拆出的模块级常量与函数；refresh 核心、各 mixin
统一从本模块导入，避免 mixin → refresh 反向依赖。
"""

from __future__ import annotations

import inspect
from typing import Any

_MAX_DISCOVERY_BACKFILL_PER_REFRESH = 60
_DEFAULT_CANDIDATE_EVAL_BATCH_SIZE = 45
# How often the cover-image disk cache is pruned of consumed + unsaved covers.
# The bulk one-shot prune runs at API startup; this is the steady-state sweep.
_IMAGE_CACHE_CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60
# Discovery-time cover prefetch: cache covers while their CDN token is still fresh
# (XHS signed URLs expire fast). Runs often, scans recent discoveries newest-first,
# and is bounded per tick so it never floods a CDN.
_COVER_PREFETCH_INTERVAL_SECONDS = 60
_COVER_PREFETCH_RECENT_HOURS = 12
_COVER_PREFETCH_SCAN = 300
_COVER_PREFETCH_MAX_FETCH = 40
_DEFAULT_PLATFORM_SOURCE_SHARES: dict[str, int] = {
    "bilibili": 5,
}
_PLATFORM_SOURCE_ORDER = ("bilibili", "xiaohongshu", "douyin", "youtube", "twitter", "zhihu")
_BILIBILI_DISCOVERY_SOURCES = ("search", "related_chain", "trending", "explore")
_PROBE_CHALLENGE_MODES = {"lateral", "bridge", "wildcard"}



def _call_accepts_limit(fn: Any) -> bool:
    """Return whether a producer callable accepts a ``limit=`` keyword."""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    return "limit" in signature.parameters or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )


def _call_accepts_strategy_limits(fn: Any) -> bool:
    """Return whether a discovery callable accepts ``strategy_limits=``."""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    return "strategy_limits" in signature.parameters or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )


def _call_accepts_pool_snapshot(fn: Any) -> bool:
    """Return whether a discovery callable accepts ``pool_snapshot=``."""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    return "pool_snapshot" in signature.parameters or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )


def _call_accepts_keywords(fn: Any) -> bool:
    """Return whether a discovery callable accepts a ``keywords=`` keyword.

    Used for the direct-engine B站 search fallback path so the unified keyword
    planner's injected words are only forwarded to engines/stubs that declare
    the kwarg — stubs without it stay byte-compatible (flag-off / tests).
    """
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    return "keywords" in signature.parameters or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )


def _call_accepts_keyword_ids(fn: Any) -> bool:
    """Return whether a discovery callable accepts a ``keyword_ids=`` keyword.

    P1.8 parallel of :func:`_call_accepts_keywords` for the direct-engine B站
    search fallback so the keyword→id provenance map is only forwarded to
    engines that declare it; stubs without it stay byte-compatible.
    """
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    return "keyword_ids" in signature.parameters or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )


def _string_state_map(value: object) -> dict[str, str]:
    """Normalize a JSON object field into a string-to-string map."""
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


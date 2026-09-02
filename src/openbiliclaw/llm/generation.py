"""Robust structured-JSON generation on top of ``LLMService``.

Folds the scattered "call → tolerant-parse → hand-rolled fallback" pattern at
the core generation call sites (delight reason, discovery strategies, batch
copy, keyword planning) into one place that:

* disables thinking by default for structured tasks (``reasoning_effort=""``)
  so a small ``max_tokens`` budget is not eaten by reasoning tokens — the root
  cause behind the recurring "产出 0 条" incidents;
* validates the parsed result against a contract ``item_predicate``
  (reusing :mod:`openbiliclaw.llm.json_utils`) so partial/garbage output fails
  loudly instead of silently;
* on empty content or an apparent ``length`` truncation, escalates once
  (force ``reasoning_effort=""`` and double ``max_tokens``) before giving up;
* makes every final failure *visible* (WARN log with salvaged context) rather
  than an anonymous ``return []`` — callers get ``None`` and decide their own
  deterministic fallback.

Pure orchestration; no new dependencies.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, TypeVar

from openbiliclaw.llm.json_utils import (
    JSONDictPredicate,
    JSONObject,
    extract_llm_json_list,
    extract_llm_json_object,
    format_parse_failure,
)
from openbiliclaw.llm.service import LLMResponseContentError, is_llm_rate_limit_error

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from openbiliclaw.llm.base import LLMResponse

logger = logging.getLogger(__name__)

_MAX_TOKENS_CEILING = 16384
_TRUNC_TOKEN_RATIO = 0.95
_RATE_LIMIT_BACKOFF_SECONDS = 2.0

_T = TypeVar("_T")


def _completion_tokens(resp: LLMResponse) -> int:
    usage = getattr(resp, "usage", None)
    if isinstance(usage, dict):
        for key in ("completion_tokens", "output_tokens", "output_token_count"):
            val = usage.get(key)
            if isinstance(val, int):
                return val
    return -1


def _looks_truncated(resp: LLMResponse | None, max_tokens: int, content: str) -> bool:
    """Heuristics for 'the model hit its output ceiling' (reasoning-token blowout)."""
    if not content or not content.strip():
        return True
    if resp is None or max_tokens <= 0:
        return False
    produced = _completion_tokens(resp)
    if produced >= _TRUNC_TOKEN_RATIO * max_tokens:
        return True
    # A hard JSON truncation usually leaves an unbalanced snippet; treat an
    # unterminated array/object as a truncation signal.
    stripped = content.strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        if stripped.count(opener) > stripped.count(closer):
            return True
    return False


async def _call_once(
    service: Any,
    *,
    system_instruction: str,
    user_input: str,
    history: list[dict[str, str]] | None,
    temperature: float,
    max_tokens: int,
    caller: str,
    reasoning_effort: str | None,
    inject_core_memory: bool,
) -> tuple[LLMResponse | None, str, str | None]:
    """Return ``(resp, content, error)``; never raises."""
    try:
        resp = await service.complete_structured_task(
            system_instruction=system_instruction,
            user_input=user_input,
            history=history,
            temperature=temperature,
            max_tokens=max_tokens,
            caller=caller,
            reasoning_effort=reasoning_effort,
            inject_core_memory=inject_core_memory,
        )
    except LLMResponseContentError as exc:
        return None, "", f"empty-content:{exc}"
    except Exception as exc:  # noqa: BLE001 - classify then let caller retry/fallback
        if is_llm_rate_limit_error(exc):
            return None, "", "rate-limit"
        return None, "", f"provider-error:{exc}"
    content = getattr(resp, "content", "") or ""
    return resp, content, None


def _escalate(max_tokens: int) -> int:
    return min(_MAX_TOKENS_CEILING, max(max_tokens * 2, max_tokens + 512))


async def generate_structured(
    service: Any,
    *,
    system_instruction: str,
    user_input: str,
    parse: Callable[[str], _T | None],
    label: str = "structured",
    history: list[dict[str, str]] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    caller: str = "",
    reasoning_effort: str | None = "",
    inject_core_memory: bool = False,
    max_attempts: int = 2,
) -> _T | None:
    """Generate text, run ``parse`` over it, and harden the outcome.

    ``parse`` maps raw content to a result or ``None`` (treated as a miss).
    This is the reusable core behind :func:`generate_json_list` /
    :func:`generate_json_object`, and is also useful directly when a caller
    already has a dedicated, tested parser. Returns the parsed result or
    ``None``; never raises — every final failure is logged at WARN so a silent
    "0 条" becomes observable.
    """
    attempt = 0
    budget = max_tokens
    effort = reasoning_effort
    last_error = ""
    last_content = ""
    while attempt < max(1, max_attempts):
        attempt += 1
        resp, content, err = await _call_once(
            service,
            system_instruction=system_instruction,
            user_input=user_input,
            history=history,
            temperature=temperature,
            max_tokens=budget,
            caller=caller,
            reasoning_effort=effort,
            inject_core_memory=inject_core_memory,
        )
        if err == "rate-limit":
            last_error = err
            if attempt < max_attempts:
                await _sleep(_RATE_LIMIT_BACKOFF_SECONDS)
                continue
            break
        if err:
            last_error = err
            # Empty content is very likely a reasoning-token blowout → escalate once.
            if err.startswith("empty-content") and budget < _MAX_TOKENS_CEILING:
                budget = _escalate(budget)
                effort = ""
                continue
            continue

        parsed = parse(content)
        if parsed is not None:
            if attempt > 1:
                logger.info("LLM %s recovered on attempt %d (caller=%s)", label, attempt, caller)
            return parsed

        last_content = content
        if _looks_truncated(resp, budget, content):
            last_error = "truncated"
            if budget < _MAX_TOKENS_CEILING:
                budget = _escalate(budget)
                effort = ""  # disable thinking to free the bumped budget for output
                continue
            last_error = "truncated-at-ceiling"
            break
        last_error = "parse-failed"
        break  # well-formed but non-conforming output won't improve by retrying

    logger.warning(
        "LLM %s produced no valid output after %d attempt(s) (caller=%s, reason=%s)",
        label,
        attempt,
        caller,
        last_error,
    )
    if last_content:
        logger.debug("%s", format_parse_failure(last_content, ValueError(last_error), label=label))
    return None


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


async def generate_json_list(
    service: Any,
    *,
    system_instruction: str,
    user_input: str,
    history: list[dict[str, str]] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    caller: str = "",
    reasoning_effort: str | None = "",
    inject_core_memory: bool = False,
    item_predicate: JSONDictPredicate | None = None,
    wrapper_keys: Sequence[str] = (),
    allow_singleton: bool = False,
    max_attempts: int = 2,
    label: str = "json-list",
) -> list[JSONObject] | None:
    """Generate and contract-validate a JSON object list, with truncation recovery.

    Returns the parsed list, or ``None`` when all attempts fail (already logged
    at WARN). ``reasoning_effort=""`` by default so structured tasks don't burn
    ``max_tokens`` on chain-of-thought.
    """

    def _parse(content: str) -> list[JSONObject] | None:
        return extract_llm_json_list(
            content,
            wrapper_keys=tuple(wrapper_keys),
            allow_singleton=allow_singleton,
            item_predicate=item_predicate,
        )

    return await generate_structured(
        service,
        system_instruction=system_instruction,
        user_input=user_input,
        parse=_parse,
        label=label,
        history=history,
        temperature=temperature,
        max_tokens=max_tokens,
        caller=caller,
        reasoning_effort=reasoning_effort,
        inject_core_memory=inject_core_memory,
        max_attempts=max_attempts,
    )


async def generate_json_object(
    service: Any,
    *,
    system_instruction: str,
    user_input: str,
    history: list[dict[str, str]] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    caller: str = "",
    reasoning_effort: str | None = "",
    inject_core_memory: bool = False,
    item_predicate: JSONDictPredicate | None = None,
    wrapper_keys: Sequence[str] = (),
    max_attempts: int = 2,
    label: str = "json-object",
) -> JSONObject | None:
    """Generate and contract-validate a single JSON object, with truncation recovery."""

    def _parse(content: str) -> JSONObject | None:
        return extract_llm_json_object(
            content,
            wrapper_keys=tuple(wrapper_keys),
            item_predicate=item_predicate,
        )

    return await generate_structured(
        service,
        system_instruction=system_instruction,
        user_input=user_input,
        parse=_parse,
        label=label,
        history=history,
        temperature=temperature,
        max_tokens=max_tokens,
        caller=caller,
        reasoning_effort=reasoning_effort,
        inject_core_memory=inject_core_memory,
        max_attempts=max_attempts,
    )

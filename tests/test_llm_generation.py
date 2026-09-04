"""Tests for the robust structured-JSON generation wrapper.

A fake LLM service drives the failure modes the wrapper exists to handle:
empty content (reasoning-token blowout), length truncation, rate limiting,
well-formed-but-non-conforming output, and hard parse failures.
"""

from __future__ import annotations

from typing import Any

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.llm.generation import generate_json_list, generate_json_object
from openbiliclaw.llm.service import LLMRateLimitError, LLMResponseContentError


class _FakeLLM:
    def __init__(self, seq: list[Any]) -> None:
        self._seq = list(seq)
        self.calls: list[dict[str, Any]] = []

    async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        nxt = self._seq.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt


_HAS_BVID = lambda d: "bvid" in d  # noqa: E731


@pytest.mark.asyncio
async def test_happy_path_returns_parsed_list() -> None:
    llm = _FakeLLM([LLMResponse(content='[{"bvid": "x", "score": 1}]')])
    out = await generate_json_list(
        llm,
        system_instruction="s",
        user_input="u",
        caller="t",
        item_predicate=_HAS_BVID,
    )
    assert out == [{"bvid": "x", "score": 1}]
    assert len(llm.calls) == 1
    # default disables thinking for structured tasks
    assert llm.calls[0]["reasoning_effort"] == ""


@pytest.mark.asyncio
async def test_empty_content_escalates_and_recovers() -> None:
    llm = _FakeLLM(
        [LLMResponseContentError("empty"), LLMResponse(content='{"items": [{"bvid": "y"}]}')]
    )
    out = await generate_json_list(
        llm,
        system_instruction="s",
        user_input="u",
        caller="t",
        max_tokens=512,
        wrapper_keys=("items",),
        item_predicate=_HAS_BVID,
    )
    assert out == [{"bvid": "y"}]
    # second attempt bumped the token budget
    assert llm.calls[1]["max_tokens"] > 512


@pytest.mark.asyncio
async def test_truncated_output_recovers_on_retry() -> None:
    llm = _FakeLLM(
        [
            LLMResponse(content='[{"bvid": "a"', usage={"completion_tokens": 512}),  # truncated
            LLMResponse(content='[{"bvid": "a"}]'),
        ]
    )
    out = await generate_json_list(
        llm,
        system_instruction="s",
        user_input="u",
        caller="t",
        max_tokens=512,
        item_predicate=_HAS_BVID,
    )
    assert out == [{"bvid": "a"}]
    assert llm.calls[1]["max_tokens"] > 512  # escalated


@pytest.mark.asyncio
async def test_unparseable_output_returns_none_without_spinning() -> None:
    llm = _FakeLLM([LLMResponse(content="not json at all"), LLMResponse(content="never reached")])
    out = await generate_json_list(llm, system_instruction="s", user_input="u", caller="t")
    assert out is None
    assert len(llm.calls) == 1  # not truncated → no pointless retry


@pytest.mark.asyncio
async def test_contract_predicate_rejects_wellformed_garbage() -> None:
    llm = _FakeLLM([LLMResponse(content='[{"score": 1}]'), LLMResponse(content='[{"score": 2}]')])
    out = await generate_json_list(
        llm, system_instruction="s", user_input="u", caller="t", item_predicate=_HAS_BVID
    )
    assert out is None
    assert len(llm.calls) == 1  # well-formed but non-conforming → don't retry


@pytest.mark.asyncio
async def test_rate_limit_retries_after_backoff() -> None:
    llm = _FakeLLM(
        [LLMRateLimitError("429 too many requests"), LLMResponse(content='{"bvid": "z"}')]
    )
    out = await generate_json_object(
        llm, system_instruction="s", user_input="u", caller="t", item_predicate=_HAS_BVID
    )
    assert out == {"bvid": "z"}
    assert len(llm.calls) == 2

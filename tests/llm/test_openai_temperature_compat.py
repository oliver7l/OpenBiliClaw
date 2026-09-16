"""temperature 兼容重试测试（移植自上游 aa7c1bed）。

部分 OpenAI 兼容后端会拒绝 temperature 参数——被拒时按报错语义重试一次，
而不是把 400 抛给用户。直接伪造 `_request_with_retry`，零网络。
"""

from __future__ import annotations

from typing import Any

import pytest
from obc_llm.base import LLMProviderError
from obc_llm.openai_provider import OpenAIProvider

from .test_llm_providers import _openai_response


def _provider_with_script(
    monkeypatch: pytest.MonkeyPatch,
    script: list[Any],
) -> tuple[OpenAIProvider, list[dict[str, Any]]]:
    """`script` 依次决定每次 `_request_with_retry` 的结果（Exception 则抛出）。"""
    provider = OpenAIProvider(api_key="test-key")
    calls: list[dict[str, Any]] = []

    async def fake_request(**kwargs: Any) -> Any:
        calls.append(dict(kwargs))
        outcome = script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)
    return provider, calls


class TestTemperatureCompat:
    @pytest.mark.asyncio
    async def test_unsupported_temperature_drops_param_and_retries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider, calls = _provider_with_script(
            monkeypatch,
            [
                LLMProviderError("temperature is unsupported by this model"),
                _openai_response("ok"),
            ],
        )

        response = await provider.complete([{"role": "user", "content": "hi"}], temperature=0.3)

        assert response.content == "ok"
        assert len(calls) == 2
        assert "temperature" in calls[0]
        assert "temperature" not in calls[1]  # 重试时已剔除

    @pytest.mark.asyncio
    async def test_only_one_allowed_sets_temperature_to_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider, calls = _provider_with_script(
            monkeypatch,
            [
                LLMProviderError("invalid temperature: only 1 is allowed"),
                _openai_response("ok"),
            ],
        )

        response = await provider.complete([{"role": "user", "content": "hi"}], temperature=0.7)

        assert response.content == "ok"
        assert calls[1]["temperature"] == 1  # 不是剔除，而是改成 1

    @pytest.mark.asyncio
    async def test_unrelated_errors_are_not_retried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider, calls = _provider_with_script(
            monkeypatch,
            [LLMProviderError("rate limit exceeded, slow down")],
        )

        with pytest.raises(LLMProviderError):
            await provider.complete([{"role": "user", "content": "hi"}])

        assert len(calls) == 1  # 与 temperature 无关 → 不重试

    @pytest.mark.asyncio
    async def test_not_allowed_message_is_recognized(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider, calls = _provider_with_script(
            monkeypatch,
            [
                LLMProviderError("parameter temperature is not allowed here"),
                _openai_response("recovered"),
            ],
        )

        response = await provider.complete([{"role": "user", "content": "hi"}])

        assert response.content == "recovered"
        assert "temperature" not in calls[1]

    @pytest.mark.asyncio
    async def test_error_without_temperature_keyword_passes_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """报错里没提 temperature → 兼容层不掺和。"""
        provider, calls = _provider_with_script(
            monkeypatch,
            [LLMProviderError("context length exceeded")],
        )

        with pytest.raises(LLMProviderError, match="context length"):
            await provider.complete([{"role": "user", "content": "hi"}])

        assert len(calls) == 1

"""LLM 流式补全测试（provider / registry / dialogue 三层，打字机链路）。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from obc_llm.base import LLMFallbackError, LLMProviderError, LLMRegistry, LLMResponse
from obc_llm.openai_provider import OpenAIProvider


def _chunks(*texts: str) -> Any:
    """把若干文本段包成 openai 流式 chunk 形状。"""
    return [
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=t), finish_reason=None)]
        )
        for t in texts
    ]


class _FakeAsyncStream:
    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> Any:
        async def _gen() -> Any:
            for c in self._chunks:
                yield c

        return _gen()


class TestProviderCompleteStream:
    def test_yields_deltas_in_order(self) -> None:
        provider = OpenAIProvider(api_key="k")

        async def fake_open_once(**kwargs: Any) -> Any:
            return _FakeAsyncStream(list(_chunks("你", "好", "呀")))

        provider._open_stream_once = fake_open_once  # type: ignore[method-assign]

        async def run() -> list[str]:
            return [d async for d in provider.complete_stream([{"role": "user", "content": "hi"}])]

        deltas = asyncio.run(run())
        assert deltas == ["你", "好", "呀"]

    def test_temperature_rejected_adapts_and_retries(self) -> None:
        """流式路径同样吃 temperature 兼容重试（与补全路径同源）。"""
        provider = OpenAIProvider(api_key="k")
        attempts: list[dict[str, Any]] = []

        async def fake_open_once(**kwargs: Any) -> Any:
            attempts.append(dict(kwargs))
            if len(attempts) == 1:
                raise LLMProviderError("invalid temperature: only 1 is allowed")
            return _FakeAsyncStream(list(_chunks("ok")))

        provider._open_stream_once = fake_open_once  # type: ignore[method-assign]

        async def run() -> list[str]:
            return [
                d
                async for d in provider.complete_stream(
                    [{"role": "user", "content": "hi"}], temperature=0.3
                )
            ]

        deltas = asyncio.run(run())
        assert deltas == ["ok"]
        assert attempts[0]["temperature"] == 0.3
        assert attempts[1]["temperature"] == 1  # 与补全路径同款语义

    def test_skips_empty_deltas(self) -> None:
        provider = OpenAIProvider(api_key="k")

        async def fake_open_once(**kwargs: Any) -> Any:
            chunks = list(_chunks("a", "", "b")) + [
                SimpleNamespace(choices=[])  # role-only chunk
            ]
            return _FakeAsyncStream(chunks)

        provider._open_stream_once = fake_open_once  # type: ignore[method-assign]

        async def run() -> list[str]:
            return [d async for d in provider.complete_stream([{"role": "user", "content": "hi"}])]

        assert asyncio.run(run()) == ["a", "b"]


class _NoStreamProvider:
    """不支持流式的 provider（如部分原生实现）——registry 应一次性降级。"""

    name = "nostream"

    async def complete(self, messages: Any, **kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content="整段回复",
            model="m",
            provider="nostream",
            usage=None,
            raw=None,
        )


class _StreamProvider:
    name = "streamy"

    def complete_stream(self, messages: Any, **kwargs: Any) -> Any:
        async def gen() -> Any:
            for t in ("第", "一"):
                yield t

        return gen()


def _registry(*providers: Any) -> LLMRegistry:
    registry = LLMRegistry()
    for i, provider in enumerate(providers):
        registry.register(provider, default=(i == 0))
    return registry


class TestRegistryCompleteStream:
    def test_falls_back_to_one_shot_for_provider_without_stream(self) -> None:
        registry = _registry(_NoStreamProvider())

        async def run() -> list[str]:
            return [d async for d in registry.complete_stream([{"role": "user", "content": "hi"}])]

        assert asyncio.run(run()) == ["整段回复"]

    def test_streams_from_capable_provider(self) -> None:
        registry = _registry(_StreamProvider())

        async def run() -> list[str]:
            return [d async for d in registry.complete_stream([{"role": "user", "content": "hi"}])]

        assert asyncio.run(run()) == ["第", "一"]

    def test_falls_back_when_first_provider_fails_before_first_delta(self) -> None:
        """首 delta 之前的失败要尝试下一个 provider（惰性成功语义）。"""

        class _Failing:
            name = "failing"

            def complete_stream(self, messages: Any, **kwargs: Any) -> Any:
                async def gen() -> Any:
                    raise LLMProviderError("backend down")
                    yield ""  # pragma: no cover

                return gen()

        registry = _registry(_Failing(), _StreamProvider())
        registry.fallback_provider = "streamy"

        async def run() -> list[str]:
            return [d async for d in registry.complete_stream([{"role": "user", "content": "hi"}])]

        assert asyncio.run(run()) == ["第", "一"]

    def test_all_failed_raises_fallback_error(self) -> None:
        registry = _registry()

        async def run() -> list[str]:
            return [d async for d in registry.complete_stream([{"role": "user", "content": "hi"}])]

        with pytest.raises(LLMFallbackError):
            asyncio.run(run())

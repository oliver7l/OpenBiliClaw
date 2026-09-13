"""模型发现（``POST /api/config/discover-models`` 的纯逻辑层）单元测试。

覆盖 2026-09-13 修复的 F1 残余：setup 向导的「获取模型」按钮此前调用一个
后端不存在的端点（恒 404）。这里锁定各 provider 的枚举协议、错误降级与
「不猜官方域名」两条约束。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from openbiliclaw.llm.model_discovery import (
    REASONING_EFFORT_SUGGESTIONS,
    ModelDiscoveryResult,
    discover_models,
)


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _recording_handler(
    payload: object,
    *,
    status_code: int = 200,
    text: str | None = None,
) -> tuple[Any, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if text is not None:
            return httpx.Response(status_code, text=text)
        return httpx.Response(status_code, json=payload)

    return handler, seen


@pytest.mark.asyncio
async def test_openai_compatible_lists_models_and_sends_bearer() -> None:
    handler, seen = _recording_handler({"data": [{"id": "gpt-b"}, {"id": "gpt-a"}, {"id": "gpt-a"}]})
    async with _client(handler) as client:
        result = await discover_models(
            provider_type="openai_compatible",
            api_key="sk-test",
            base_url="https://gw.example.com/v1",
            client=client,
        )

    assert result.ok is True
    assert result.models == ("gpt-a", "gpt-b")  # 去重 + 排序
    assert result.error == ""
    assert len(seen) == 1
    assert str(seen[0].url) == "https://gw.example.com/v1/models"
    assert seen[0].headers["authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_openai_falls_back_to_official_base_url() -> None:
    handler, seen = _recording_handler({"data": [{"id": "gpt-5-nano"}]})
    async with _client(handler) as client:
        result = await discover_models(provider_type="openai", api_key="sk-test", client=client)

    assert result.ok is True
    assert str(seen[0].url) == "https://api.openai.com/v1/models"


@pytest.mark.asyncio
async def test_deepseek_default_base_url_needs_no_explicit_hint() -> None:
    handler, seen = _recording_handler({"data": [{"id": "deepseek-chat"}]})
    async with _client(handler) as client:
        result = await discover_models(provider_type="DeepSeek", api_key="sk-test", client=client)

    assert result.ok is True
    assert str(seen[0].url) == "https://api.deepseek.com/models"


@pytest.mark.asyncio
async def test_ollama_reads_api_tags_and_strips_v1_shim() -> None:
    handler, seen = _recording_handler({"models": [{"name": "bge-m3:latest"}, {"model": "qwen3:8b"}]})
    async with _client(handler) as client:
        result = await discover_models(
            provider_type="ollama",
            base_url="http://localhost:11434/v1",
            client=client,
        )

    assert result.ok is True
    assert result.models == ("bge-m3:latest", "qwen3:8b")
    assert str(seen[0].url) == "http://localhost:11434/api/tags"


@pytest.mark.asyncio
async def test_claude_uses_anthropic_headers() -> None:
    handler, seen = _recording_handler({"data": [{"id": "claude-sonnet-4-6"}]})
    async with _client(handler) as client:
        result = await discover_models(provider_type="claude", api_key="sk-ant", client=client)

    assert result.ok is True
    assert str(seen[0].url) == "https://api.anthropic.com/v1/models"
    assert seen[0].headers["x-api-key"] == "sk-ant"
    assert seen[0].headers["anthropic-version"] == "2023-06-01"


@pytest.mark.asyncio
async def test_gemini_strips_models_prefix_and_passes_key_param() -> None:
    handler, seen = _recording_handler({"models": [{"name": "models/gemini-2.5-flash"}, {"name": "gemini-2.5-pro"}]})
    async with _client(handler) as client:
        result = await discover_models(provider_type="gemini", api_key="gk-test", client=client)

    assert result.ok is True
    assert result.models == ("gemini-2.5-flash", "gemini-2.5-pro")
    assert str(seen[0].url) == ("https://generativelanguage.googleapis.com/v1beta/models?key=gk-test")


@pytest.mark.asyncio
async def test_empty_catalogue_is_success_not_error() -> None:
    handler, _ = _recording_handler({"data": []})
    async with _client(handler) as client:
        result = await discover_models(
            provider_type="openai_compatible",
            api_key="sk-test",
            base_url="https://gw.example.com/v1",
            client=client,
        )

    assert result.ok is True
    assert result.models == ()


@pytest.mark.asyncio
async def test_openai_compatible_requires_explicit_base_url() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        calls.append(request)
        return httpx.Response(200, json={})

    async with _client(handler) as client:
        result = await discover_models(provider_type="openai_compatible", api_key="sk-test", client=client)

    assert result.ok is False
    assert "Base URL" in result.error
    assert calls == []  # 绝不回退到官方域名发请求


@pytest.mark.asyncio
async def test_codex_oauth_reports_manual_entry_instead_of_guessing() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        calls.append(request)
        return httpx.Response(200, json={})

    async with _client(handler) as client:
        result = await discover_models(
            provider_type="openai",
            api_key="",
            auth_mode="codex_oauth",
            client=client,
        )

    assert result.ok is False
    assert "手填" in result.error
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "payload"),
    [(401, "invalid api key"), (404, "not found"), (500, "boom")],
)
async def test_http_error_surfaces_status_and_body(status_code: int, payload: str) -> None:
    handler, _ = _recording_handler(None, status_code=status_code, text=payload)
    async with _client(handler) as client:
        result = await discover_models(provider_type="openai", api_key="sk-bad", client=client)

    assert result.ok is False
    assert f"HTTP {status_code}" in result.error
    assert payload in result.error


@pytest.mark.asyncio
async def test_non_json_body_is_reported_not_crashed() -> None:
    handler, _ = _recording_handler(None, text="<html>gateway</html>")
    async with _client(handler) as client:
        result = await discover_models(provider_type="openai", api_key="sk-test", client=client)

    assert result.ok is False
    assert "JSON" in result.error


@pytest.mark.asyncio
async def test_transport_failure_is_reported_not_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with _client(handler) as client:
        result = await discover_models(provider_type="ollama", client=client)

    assert result.ok is False
    assert "ConnectError" in result.error


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["", "nope", "unknown-vendor"])
async def test_unknown_provider_is_a_soft_failure(provider: str) -> None:
    result = await discover_models(provider_type=provider, api_key="sk-test")

    assert isinstance(result, ModelDiscoveryResult)
    assert result.ok is False
    assert result.error


@pytest.mark.asyncio
async def test_tolerates_models_key_and_bare_list_shapes() -> None:
    """自建网关对 /models 的返回形状并不统一，两种都要能吃下。"""
    handler, _ = _recording_handler({"models": [{"id": "m1"}, "m2"]})
    async with _client(handler) as client:
        from_dict = await discover_models(
            provider_type="openai_compatible",
            api_key="sk",
            base_url="https://gw.example.com/v1",
            client=client,
        )

    handler2, _ = _recording_handler([{"id": "m3"}, "m4"])
    async with _client(handler2) as client2:
        from_list = await discover_models(
            provider_type="openai_compatible",
            api_key="sk",
            base_url="https://gw.example.com/v1",
            client=client2,
        )

    assert from_dict.models == ("m1", "m2")
    assert from_list.models == ("m3", "m4")


def test_reasoning_effort_suggestions_are_stable() -> None:
    """向导的 datalist 依赖这份本地建议表（协议侧无枚举端点）。"""
    assert REASONING_EFFORT_SUGGESTIONS[0] == "none"
    assert "max" in REASONING_EFFORT_SUGGESTIONS
    assert len(set(REASONING_EFFORT_SUGGESTIONS)) == len(REASONING_EFFORT_SUGGESTIONS)

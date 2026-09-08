"""CN-direct outbound isolation guards.

OpenBiliClaw reaches Bilibili / Douyin / Ollama / image CDNs directly
from the host. The macOS system proxy (127.0.0.1:7890, e.g. Clash) is
restarted often and its downtime took down every outbound request
(observed: trust_env=True httpx clients picked up the proxy env injected
by PM2, and the whole LLM/feed path stalled). The fixes are:

1. every outbound httpx client passes ``trust_env=False`` so it never
   inherits HTTP(S)_PROXY from the environment, and
2. ``openbiliclaw.cli._strip_proxy_env()`` drops the proxy vars inside
   the Python process at startup, covering SDKs that only honour env.

These tests pin that both guards hold. If someone wires a proxy into one
of these clients, or removes the env stripping, this file MUST go red.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from openbiliclaw import cli

# An obviously-bogus proxy that would break every request if inherited.
_GUARD_PROXY = "socks5://127.0.0.1:9999"

_PROXY_ENV_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "FTP_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "ftp_proxy",
    "no_proxy",
)


@pytest.fixture
def _capture_httpx(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture every httpx.AsyncClient/Client construction and its kwargs.

    Lets a test assert on the exact transport options each outbound client
    was built with — without making any real network call.
    """
    captured: list[dict[str, Any]] = []
    orig_async_init = httpx.AsyncClient.__init__
    orig_sync_init = httpx.Client.__init__

    def _recording_async_init(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        captured.append(("async", kwargs))
        orig_async_init(self, *args, **kwargs)

    def _recording_sync_init(self: httpx.Client, *args: Any, **kwargs: Any) -> None:
        captured.append(("sync", kwargs))
        orig_sync_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _recording_async_init)
    monkeypatch.setattr(httpx.Client, "__init__", _recording_sync_init)
    return captured


def _assert_no_proxy_leak(captured: list[dict[str, Any]]) -> None:
    assert captured, "expected at least one httpx client construction"
    for kind, kwargs in captured:
        # Never the guarded proxy, neither as proxy nor proxies.
        assert kwargs.get("proxy") != _GUARD_PROXY, f"{kind} client inherited proxy!"
        assert kwargs.get("proxies") != _GUARD_PROXY, f"{kind} client inherited proxies!"
        # Never trust the environment for proxy discovery.
        assert kwargs.get("trust_env") is False, (
            f"{kind} client left trust_env enabled — it can inherit HTTP(S)_PROXY!"
        )


def test_bilibili_client_never_uses_environment_proxy(
    _capture_httpx: list[dict[str, Any]],
) -> None:
    from openbiliclaw.bilibili.api import BilibiliAPIClient

    BilibiliAPIClient(cookie="SESSDATA=x; bili_jct=y; DedeUserID=1")
    _assert_no_proxy_leak(_capture_httpx)


def test_douyin_direct_client_never_uses_environment_proxy(
    _capture_httpx: list[dict[str, Any]],
) -> None:
    from openbiliclaw.sources.douyin_direct import DouyinDirectClient

    DouyinDirectClient(cookie="sessionid=abc; ttwid=def")
    _assert_no_proxy_leak(_capture_httpx)


def test_openai_provider_client_never_uses_environment_proxy(
    _capture_httpx: list[dict[str, Any]],
) -> None:
    from openbiliclaw.llm.openai_provider import OpenAIProvider

    OpenAIProvider(
        api_key="sk-test",
        model="sensenova-6.8-flash-lite",
        base_url="https://token.sensenova.cn/v1",
        reasoning_effort="none",
    )
    _assert_no_proxy_leak(_capture_httpx)


def test_image_cache_fetch_never_uses_environment_proxy(
    _capture_httpx: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``fetch_cover_bytes`` must build its client with trust_env=False.

    We interrupt the actual network send with a sentinel failure so the
    client construction (inside the ``async with``) is captured but no
    real bytes are fetched.
    """
    import openbiliclaw.runtime.image_cache as ic

    async def _boom(*args: Any, **kwargs: Any) -> httpx.Response:
        raise ic.CoverFetchError(502, "intentional test interrupt")

    monkeypatch.setattr(ic, "_send_with_redirects", _boom)

    async def _run() -> None:
        with pytest.raises(ic.CoverFetchError):
            await ic.fetch_cover_bytes("https://i0.hdslb.com/bfs/archive/test.jpg")

    asyncio.run(_run())
    _assert_no_proxy_leak(_capture_httpx)


def test_updater_tag_check_never_uses_environment_proxy(
    _capture_httpx: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The auto-update GitHub tag check must not inherit a proxy either."""
    from openbiliclaw.runtime.updater import AutoUpdateService

    updater = AutoUpdateService()

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("intentional test interrupt")

    monkeypatch.setattr(httpx.AsyncClient, "get", _boom)

    async def _run() -> None:
        selection = await updater._fetch_latest_candidate_once(channel="stable", verify_tls=True)
        # The method swallows the transport failure into an error selection,
        # but the httpx.AsyncClient was already constructed inside the async
        # with — which is exactly what we captured above.
        assert selection.error_reason, "expected an error selection from the interrupted fetch"

    asyncio.run(_run())
    _assert_no_proxy_leak(_capture_httpx)


def test_strip_proxy_env_removes_all_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_strip_proxy_env`` must remove every case-variant of proxy vars.

    PM2 re-injects proxy env vars that a wrapper ``unset`` cannot always
    reach — this in-process stripping is the last line of defence, so it
    must cover the full variable family.
    """
    for var in _PROXY_ENV_VARS:
        monkeypatch.setenv(var, _GUARD_PROXY)
    cli._strip_proxy_env()
    for var in _PROXY_ENV_VARS:
        assert var not in __import__("os").environ, f"{var} survived stripping!"

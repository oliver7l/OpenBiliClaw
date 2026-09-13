"""Discover the model ids a chat provider endpoint actually exposes.

Backs ``POST /api/config/discover-models`` — the setup wizard's 「获取模型」
button. The wizard asks the endpoint itself instead of shipping a hard-coded
catalogue, because third-party OpenAI-compatible gateways serve arbitrary
deployment names that no static list can know.

The helpers here take plain strings rather than a ``Config`` object on
purpose: the wizard discovers models from credentials the user has *typed*
but not yet saved, so there is no persisted ``[llm.<provider>]`` block to read
from (``PUT /api/config`` remains a separate, validated write).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

#: Providers that speak the OpenAI ``GET /models`` protocol.
_OPENAI_PROTOCOL_PROVIDERS = frozenset({"openai", "deepseek", "openrouter", "openai_compatible", "orcarouter"})

#: Fallbacks used only when the caller supplied no ``base_url``. Mirrors the
#: defaults the registry/providers use when constructing the same provider.
_DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434",
    "claude": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com",
}

#: Providers whose whole point is a user-supplied endpoint — guessing an
#: official host here would query a *different* service than the one the user
#: is configuring, so these require an explicit ``base_url``.
_REQUIRES_EXPLICIT_BASE_URL = frozenset({"openai_compatible", "orcarouter"})

_ANTHROPIC_VERSION = "2023-06-01"

#: Local suggestion list. No provider in scope exposes an effort *enumeration*
#: endpoint, so this is a convenience for the datalist — the field stays free
#: text and an unrecognised value is rejected by the provider, not by us.
REASONING_EFFORT_SUGGESTIONS: tuple[str, ...] = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)

DEFAULT_DISCOVERY_TIMEOUT_SECONDS = 15.0

_MAX_ERROR_BODY_CHARS = 200


@dataclass(frozen=True)
class ModelDiscoveryResult:
    """Outcome of a single model-catalogue request.

    ``ok=True`` with an empty ``models`` tuple is a meaningful state (the
    endpoint answered but advertises nothing); ``ok=False`` always carries a
    human-readable ``error``.
    """

    ok: bool
    models: tuple[str, ...] = ()
    error: str = ""


def _normalize_provider(provider_type: str) -> str:
    return str(provider_type or "").strip().lower()


def _strip_trailing_slash(url: str) -> str:
    return str(url or "").strip().rstrip("/")


def _resolve_base_url(provider: str, base_url: str) -> str:
    explicit = _strip_trailing_slash(base_url)
    if explicit:
        return explicit
    return _DEFAULT_BASE_URLS.get(provider, "")


def _model_name(item: object) -> str:
    """Pull a model id out of one catalogue entry.

    Gateways disagree on both the envelope *and* the key (``id`` / ``name`` /
    ``model``), and entries are sometimes bare strings. Guessing wrong costs
    the user a hand-typed model name, so accept all four shapes.
    """
    if isinstance(item, dict):
        for key in ("id", "name", "model"):
            value = item.get(key)
            if value:
                return str(value).strip()
        return ""
    return str(item or "").strip()


def _dedupe_sorted(values: object) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    cleaned = {_model_name(item) for item in values}
    cleaned.discard("")
    return tuple(sorted(cleaned))


def _extract_openai_models(payload: object) -> tuple[str, ...]:
    """Pull ids out of an OpenAI-style ``{"data": [{"id": ...}]}`` body.

    Also tolerates ``{"models": [...]}`` and a bare list, because
    self-hosted gateways differ on this and a wrong guess costs the user a
    hand-typed model name.
    """
    if isinstance(payload, list):
        return _dedupe_sorted(payload)
    if not isinstance(payload, dict):
        return ()
    data = payload.get("data")
    if isinstance(data, list):
        return _dedupe_sorted(data)
    return _dedupe_sorted(payload.get("models"))


def _extract_ollama_models(payload: object) -> tuple[str, ...]:
    """Pull names out of Ollama's ``GET /api/tags`` body."""
    if not isinstance(payload, dict):
        return ()
    return _dedupe_sorted(payload.get("models"))


def _extract_gemini_models(payload: object) -> tuple[str, ...]:
    """Pull names out of Gemini's ``v1beta/models`` body (strips ``models/``)."""
    if not isinstance(payload, dict):
        return ()
    entries = payload.get("models")
    if not isinstance(entries, list):
        return ()
    names: list[str] = []
    for entry in entries:
        text = _model_name(entry)
        if not text:
            continue
        names.append(text[len("models/") :] if text.startswith("models/") else text)
    return _dedupe_sorted(names)


def _error_detail(response: httpx.Response) -> str:
    text = " ".join(str(getattr(response, "text", "") or "").split())
    if not text:
        return ""
    return f"：{text[:_MAX_ERROR_BODY_CHARS]}"


async def _get(
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, str] | None,
    timeout: float,
    client: httpx.AsyncClient | None,
) -> httpx.Response:
    """Issue the catalogue request, owning a client unless one was injected."""
    if client is not None:
        return await client.get(url, headers=headers, params=params)

    from openbiliclaw.network import httpx_kwargs_for_endpoint

    kwargs = httpx_kwargs_for_endpoint(url)
    async with httpx.AsyncClient(timeout=timeout, **kwargs) as owned:
        return await owned.get(url, headers=headers, params=params)


async def discover_models(
    *,
    provider_type: str,
    api_key: str = "",
    base_url: str = "",
    auth_mode: str = "",
    timeout: float = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
    client: httpx.AsyncClient | None = None,
) -> ModelDiscoveryResult:
    """Query ``provider_type`` for its available model ids.

    Never raises for a remote failure — every error path comes back as
    ``ModelDiscoveryResult(ok=False, error=...)`` so the endpoint can return a
    200 the wizard can render inline (the model field stays hand-editable).
    """
    provider = _normalize_provider(provider_type)
    key = str(api_key or "").strip()
    explicit_base = _strip_trailing_slash(base_url)

    if not provider:
        return ModelDiscoveryResult(False, error="缺少 AI 服务商类型。")
    if provider in _REQUIRES_EXPLICIT_BASE_URL and not explicit_base:
        return ModelDiscoveryResult(False, error="该服务商需要先填写接口地址 Base URL，再获取模型列表。")
    if provider == "openai" and str(auth_mode or "").strip().lower() == "codex_oauth":
        # The Codex OAuth transport is a ChatGPT-subscription channel, not the
        # Platform API — it has no /models catalogue to enumerate.
        return ModelDiscoveryResult(False, error="Codex OAuth 走 ChatGPT 订阅通道，无法枚举模型，请手填模型名。")

    base = _resolve_base_url(provider, base_url)
    if not base:
        return ModelDiscoveryResult(False, error=f"暂不支持为 {provider} 枚举模型，请手填模型名。")

    headers: dict[str, str] = {}
    params: dict[str, str] | None = None
    extractor = _extract_openai_models

    if provider in _OPENAI_PROTOCOL_PROVIDERS:
        url = f"{base}/models"
        if key:
            headers["Authorization"] = f"Bearer {key}"
    elif provider == "ollama":
        # Ollama's OpenAI shim lives under /v1, but /api/tags is rooted at the
        # server; accept either spelling.
        root = base[: -len("/v1")] if base.endswith("/v1") else base
        url = f"{root}/api/tags"
        extractor = _extract_ollama_models
    elif provider == "claude":
        url = f"{base}/v1/models"
        headers["anthropic-version"] = _ANTHROPIC_VERSION
        if key:
            headers["x-api-key"] = key
    elif provider == "gemini":
        url = f"{base}/v1beta/models"
        if key:
            params = {"key": key}
        extractor = _extract_gemini_models
    else:
        return ModelDiscoveryResult(False, error=f"暂不支持为 {provider} 枚举模型，请手填模型名。")

    try:
        response = await _get(
            url,
            headers=headers,
            params=params,
            timeout=timeout,
            client=client,
        )
    except httpx.HTTPError as exc:
        logger.debug("Model discovery failed for %s: %s", provider, exc)
        return ModelDiscoveryResult(False, error=f"无法连接端点（{type(exc).__name__}）：{str(exc)[:120]}")

    if response.status_code >= 400:
        return ModelDiscoveryResult(
            False,
            error=f"端点返回 HTTP {response.status_code}{_error_detail(response)}",
        )

    try:
        payload = response.json()
    except ValueError:
        return ModelDiscoveryResult(False, error="端点返回的不是合法 JSON。")

    return ModelDiscoveryResult(True, models=extractor(payload))

"""LLM registry routing & fallback-chain behaviour.

Pins the properties we depend on after the 2026-09-01 fallback rework:

- ``fallback_provider`` must name a *registered* chat provider; an unknown
  or unregistered name must NOT extend the chain (``_fallback_order``
  returns just the default).
- Providers with an empty ``api_key`` must not be registered at all, so a
  dead key (e.g. the old deepseek one) can never sit in the chain.
- ``openai_compatible`` must read its ``base_url`` from config — this is
  what lets a SenseNova/日日夜夜 key be used through the OpenAI-compatible
  endpoint.
- ``OpenAIProvider`` must forward ``reasoning_effort`` via ``extra_body``
  (``"none"`` disables thinking on SenseNova, which otherwise returns only
  ``reasoning`` and never ``content``).
"""

from __future__ import annotations

from obc_llm.base import LLMRegistry
from obc_llm.openai_provider import OpenAIProvider

from openbiliclaw.config import Config
from openbiliclaw.llm.registry import build_llm_registry


def _sensenova_style_llm_config() -> object:
    """The 2026-09-01 SenseNova arrangement as a synthetic config.

    openai (default) + openai_compatible (fallback), both keyed; deepseek's
    key left empty so it must never register. Built in-memory — the earlier
    versions of these tests read the developer's live ``config.toml``,
    which legitimately changes over time (e.g. default flipped to gemini)
    and made them fail for reasons unrelated to registry behaviour.
    """
    cfg = Config().llm
    cfg.default_provider = "openai"
    cfg.fallback_enabled = True
    cfg.fallback_provider = "openai_compatible"
    cfg.openai.api_key = "sk-test-openai"
    cfg.openai_compatible.api_key = "sk-test-compat"
    cfg.openai_compatible.base_url = "https://token.sensenova.cn/v1"
    cfg.deepseek.api_key = ""
    return cfg


def test_registry_openai_default_with_openai_compatible_fallback() -> None:
    """openai (default) + openai_compatible (fallback) both build from config.

    This is the exact arrangement that restored chat output after the
    SenseNova reasoning/content issue.
    """
    registry = build_llm_registry(_sensenova_style_llm_config())  # type: ignore[arg-type]

    assert "openai" in registry.available_providers
    assert "openai_compatible" in registry.available_providers
    assert registry.default_provider == "openai"
    assert registry.fallback_provider == "openai_compatible"


def test_fallback_order_contains_default_and_fallback() -> None:
    registry = build_llm_registry(_sensenova_style_llm_config())  # type: ignore[arg-type]

    order = registry._fallback_order()
    assert order[0] == "openai"
    assert "openai_compatible" in order
    assert len(order) <= 2  # never walk the whole provider list


def test_dead_provider_with_empty_key_is_not_registered() -> None:
    """A provider whose key is empty must not appear in the registry.

    Pins the deepseek cleanup: an empty key can never sit in the chain and
    therefore can never be picked as a fallback.
    """
    registry = build_llm_registry(_sensenova_style_llm_config())  # type: ignore[arg-type]
    assert "deepseek" not in registry.available_providers


def test_openai_compatible_uses_configured_base_url() -> None:
    """openai_compatible must honour its config base_url (SenseNova endpoint)."""
    from openbiliclaw.llm.registry import _maybe_openai_compatible_provider

    cfg = _sensenova_style_llm_config()
    provider = _maybe_openai_compatible_provider(cfg, overrides={})  # type: ignore[arg-type]
    assert provider is not None
    assert provider.base_url == cfg.openai_compatible.base_url
    assert provider.base_url.startswith("https://")


def test_unknown_fallback_provider_does_not_extend_chain() -> None:
    """An unregistered fallback name must collapse to just the default."""
    registry = build_llm_registry(_sensenova_style_llm_config())  # type: ignore[arg-type]
    registry.fallback_provider = "does-not-exist"

    assert registry._fallback_order() == ["openai"]


def test_reasoning_effort_none_forwards_via_extra_body() -> None:
    """reasoning_effort='none' must land in extra_body verbatim.

    SenseNova defaults to thinking mode (medium) where content is empty
    and only ``reasoning`` is returned; 'none' makes it return ``content``
    — this is the fix that unblocked chat.
    """
    provider = OpenAIProvider(
        api_key="sk-test",
        model="sensenova-6.8-flash-lite",
        base_url="https://token.sensenova.cn/v1",
        reasoning_effort="none",
    )
    assert provider._extra_body() == {"reasoning_effort": "none"}


def test_empty_reasoning_effort_sends_no_extra_body() -> None:
    """Empty reasoning_effort must not inject anything (openai.com safety)."""
    provider = OpenAIProvider(
        api_key="sk-test",
        model="gpt-4o",
        base_url="https://api.openai.com/v1",
        reasoning_effort="",
    )
    assert provider._extra_body() == {}


def test_deepseek_provider_injects_thinking_schema() -> None:
    """DeepSeekProvider uses its own thinking schema, not plain reasoning_effort."""
    from obc_llm.openai_provider import DeepSeekProvider

    provider = DeepSeekProvider(
        api_key="sk-test",
        model="deepseek-chat",
        reasoning_effort="high",
    )
    body = provider._extra_body()
    assert body.get("reasoning_effort") == "high"
    assert body.get("thinking") == {"type": "enabled"}


def test_provider_registration_sets_default_and_chat_flags() -> None:
    """register() must record the first provider as default and expose names."""
    registry = LLMRegistry()
    p = OpenAIProvider(api_key="sk-test", model="m", base_url="https://x/v1")
    registry.register(p, default=True, chat_capable=True)
    assert registry.default_provider == "openai"
    assert registry.available_providers == ["openai"]
    assert registry.is_chat_capable("openai")

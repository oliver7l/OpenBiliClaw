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

from openbiliclaw.config import load_config
from openbiliclaw.llm.base import LLMRegistry
from openbiliclaw.llm.openai_provider import OpenAIProvider
from openbiliclaw.llm.registry import build_llm_registry


def test_registry_with_real_config_has_openai_and_fallback() -> None:
    """The live config.toml must build: openai (default) + openai_compatible.

    This is the exact arrangement that restored chat output after the
    SenseNova reasoning/content issue.
    """
    config = load_config()
    registry = build_llm_registry(config)

    assert "openai" in registry.available_providers
    assert "openai_compatible" in registry.available_providers
    assert registry.default_provider == "openai"
    assert registry.fallback_provider == "openai_compatible"


def test_fallback_order_contains_default_and_fallback() -> None:
    config = load_config()
    registry = build_llm_registry(config)

    order = registry._fallback_order()
    assert order[0] == "openai"
    assert "openai_compatible" in order
    assert len(order) <= 2  # never walk the whole provider list


def test_dead_provider_with_empty_key_is_not_registered() -> None:
    """A provider whose key was cleared must not appear in the registry.

    This pins the deepseek cleanup: the old dead key is gone from
    config.toml, so ``deepseek`` must not be registered and therefore can
    never be picked as a fallback.
    """
    config = load_config()
    registry = build_llm_registry(config)
    assert "deepseek" not in registry.available_providers


def test_openai_compatible_uses_configured_base_url() -> None:
    """openai_compatible must honour its config base_url (SenseNova endpoint)."""
    from openbiliclaw.llm.registry import _maybe_openai_compatible_provider

    config = load_config()
    provider = _maybe_openai_compatible_provider(config, overrides={})
    assert provider is not None
    assert provider.base_url == config.llm.openai_compatible.base_url
    assert config.llm.openai_compatible.base_url.startswith("https://")


def test_unknown_fallback_provider_does_not_extend_chain() -> None:
    """An unregistered fallback name must collapse to just the default."""
    config = load_config()
    registry = build_llm_registry(config)
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
    from openbiliclaw.llm.openai_provider import DeepSeekProvider

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

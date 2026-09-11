"""Service probe routes for OpenBiliClaw config API."""

from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from openbiliclaw.api.models import (
    ConfigServiceProbeIn,
    ConfigServiceProbeResponse,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI


def register_probe_routes(
    app: FastAPI,
    *,
    apply_llm_update: Callable[[Any, object], None],
) -> None:
    """Register config service probe endpoints on the FastAPI app."""

    async def _probe_llm_config(cfg: Any) -> ConfigServiceProbeResponse:
        from openbiliclaw.llm.base import LLM_CONNECTIVITY_PROBE_MAX_TOKENS
        from openbiliclaw.llm.registry import build_llm_registry

        started = time.perf_counter()
        provider = str(getattr(cfg.llm, "default_provider", "") or "").strip().lower()
        model = ""
        try:
            registry = build_llm_registry(cfg.llm)
            provider = provider or str(getattr(registry, "default_provider", "") or "")
            provider_cfg = getattr(cfg.llm, provider, None)
            model = str(getattr(provider_cfg, "model", "") or "").strip()
            if not registry.is_chat_capable(provider):
                return ConfigServiceProbeResponse(
                    ok=False,
                    kind="llm",
                    provider=provider,
                    model=model,
                    error=f"LLM provider {provider!r} is not registered or not chat-capable.",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            timeout_s = min(max(float(getattr(cfg.llm, "timeout", 300) or 300), 10.0), 30.0)
            response = await asyncio.wait_for(
                registry.complete_provider(
                    provider,
                    [
                        {"role": "system", "content": "Reply with only OK."},
                        {"role": "user", "content": "OpenBiliClaw connectivity probe."},
                    ],
                    temperature=0,
                    max_tokens=LLM_CONNECTIVITY_PROBE_MAX_TOKENS,
                    reasoning_effort="",
                    model=model or None,
                ),
                timeout=timeout_s,
            )
            ok = bool(str(getattr(response, "content", "") or "").strip())
            response_model = str(getattr(response, "model", "") or model)
            return ConfigServiceProbeResponse(
                ok=ok,
                kind="llm",
                provider=provider,
                model=response_model,
                message="LLM provider is available." if ok else "",
                error="" if ok else "LLM provider returned an empty response.",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            return ConfigServiceProbeResponse(
                ok=False,
                kind="llm",
                provider=provider,
                model=model,
                error=str(exc),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

    async def _probe_embedding_config(cfg: Any) -> ConfigServiceProbeResponse:
        from openbiliclaw.llm.base import LLMRegistry
        from openbiliclaw.llm.registry import build_embedding_service

        started = time.perf_counter()
        emb_cfg = getattr(getattr(cfg, "llm", None), "embedding", None)
        provider = str(getattr(emb_cfg, "provider", "") or "").strip().lower()
        model = str(getattr(emb_cfg, "model", "") or "").strip()
        if not provider:
            return ConfigServiceProbeResponse(
                ok=False,
                kind="embedding",
                provider="",
                model=model,
                error="Embedding provider is not configured.",
            )
        try:
            service = build_embedding_service(cfg, LLMRegistry())
            if service is None:
                return ConfigServiceProbeResponse(
                    ok=False,
                    kind="embedding",
                    provider=provider,
                    model=model,
                    error="Embedding service could not be built from the submitted config.",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            probe = getattr(service, "probe", None)
            # Legacy/stub embedding service without a live probe —
            # building it successfully is the best signal we have.
            ok = (
                True
                if not callable(probe)
                else bool(await asyncio.wait_for(probe(), timeout=15.0))
            )
            return ConfigServiceProbeResponse(
                ok=ok,
                kind="embedding",
                provider=provider,
                model=model,
                message="Embedding provider is available." if ok else "",
                error="" if ok else "Embedding provider returned no vector.",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            return ConfigServiceProbeResponse(
                ok=False,
                kind="embedding",
                provider=provider,
                model=model,
                error=str(exc),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

    @app.post("/api/config/probe-service", response_model=ConfigServiceProbeResponse)
    async def probe_config_service(payload: ConfigServiceProbeIn) -> ConfigServiceProbeResponse:
        """Probe submitted LLM / embedding settings without saving config.toml."""
        from openbiliclaw.config import load_config

        cfg = deepcopy(load_config())
        update = payload.config if isinstance(payload.config, dict) else {}
        llm_data = update.get("llm")
        if isinstance(llm_data, dict):
            apply_llm_update(cfg, llm_data)
        if payload.kind == "llm":
            return await _probe_llm_config(cfg)
        return await _probe_embedding_config(cfg)

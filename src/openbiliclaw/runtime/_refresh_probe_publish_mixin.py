"""ContinuousRefreshController mixin: 兴趣/回避探针生成与推送。

从 ``runtime/refresh.py`` 拆出的方法组；``ContinuousRefreshController``
继承本 mixin，调用方代码无需修改。
"""

from __future__ import annotations

import logging
from contextlib import suppress
from datetime import timedelta
from typing import Any

from openbiliclaw.runtime._refresh_shared import (
    _PROBE_CHALLENGE_MODES,
    _string_state_map,
)
from openbiliclaw.soul.avoidance_speculator import choose_next_avoidance_candidate
from openbiliclaw.soul.speculator import (
    _normalize_probe_mode,
    build_probe_axis,
    choose_next_probe_candidate,
)

logger = logging.getLogger("openbiliclaw.runtime.refresh")


class ProbePublishMixin:
    """兴趣/回避探针生成与推送。"""

    _PROBE_COOLDOWN_HOURS: Any  # 由 ContinuousRefreshController 提供
    _now: Any  # 由 ContinuousRefreshController 提供
    _publish_event: Any  # 由 ContinuousRefreshController 提供
    _update_discovery_runtime_state: Any  # 由 ContinuousRefreshController 提供
    memory_manager: Any  # 由 ContinuousRefreshController 提供
    soul_engine: Any  # 由 ContinuousRefreshController 提供

    async def _publish_interest_probe_if_available(self) -> bool:
        """Push the top speculative-interest hypothesis via WebSocket.

        Fires an ``interest.probe`` event when the speculator has an active
        hypothesis that the agent should ask the user to confirm.

        De-duplication: each domain is pushed at most once per cooldown
        window (``_PROBE_COOLDOWN_HOURS``).  Already-probed domains are
        tracked in ``discovery_runtime_state["probed_domains"]``.
        """
        speculator = getattr(self.soul_engine, "_speculator", None)
        get_active = getattr(speculator, "get_active_speculations", None)
        if not callable(get_active):
            return False
        specs = [
            spec
            for spec in get_active()
            if str(getattr(spec, "status", "active")).strip().lower() == "active"
        ]
        if not specs:
            return False

        # Load probe history from runtime state
        state = self.memory_manager.load_discovery_runtime_state()
        probed: dict[str, str] = state.get("probed_domains", {})  # type: ignore[assignment]
        probed_axes: dict[str, str] = state.get("probed_axes", {})  # type: ignore[assignment]
        probed_distance_bands: dict[str, str] = state.get("probed_distance_bands", {})  # type: ignore[assignment]
        # Purge expired entries
        now = self._now()
        cutoff = (now - timedelta(hours=self._PROBE_COOLDOWN_HOURS)).isoformat()
        probed = {d: t for d, t in probed.items() if t > cutoff}
        probed_axes = {axis: t for axis, t in probed_axes.items() if t > cutoff}
        probed_distance_bands = {mode: t for mode, t in probed_distance_bands.items() if t > cutoff}

        top = choose_next_probe_candidate(
            specs,
            probed_domains=set(probed),
            probed_axes=set(probed_axes),
            probed_probe_modes=set(probed_distance_bands),
            feedback_history=state.get("probe_feedback_history", []),
        )
        if top is None:
            return False  # All active specs were probed recently

        domain = str(getattr(top, "domain", "")).strip()
        if not domain:
            return False

        probe_mode = _normalize_probe_mode(getattr(top, "probe_mode", ""))
        challenge = probe_mode in _PROBE_CHALLENGE_MODES
        with suppress(Exception):
            challenge = challenge or bool(getattr(top, "challenge", False))
        axis = build_probe_axis(
            experience_mode=getattr(top, "experience_mode", ""),
            entry_load=getattr(top, "entry_load", ""),
        )
        reason = str(getattr(top, "reason", "")).strip()
        specifics = [
            str(getattr(item, "name", "")).strip()
            for item in getattr(top, "specifics", [])
            if str(getattr(item, "name", "")).strip()
        ][:5]
        specific_hint = ""
        if specifics:
            specific_hint = "（比如：" + "、".join(specifics[:3]) + "）"
        question = (
            f"我从你最近的轨迹里嗅到你可能对【{domain}】{specific_hint}感兴趣"
            f"——{reason} 这个方向你自己认不认？"
            if reason
            else f"我感觉你可能对【{domain}】{specific_hint}有潜在兴趣，这个方向你自己认不认？"
        )
        delivered = await self._publish_event(
            {
                "type": "interest.probe",
                "phase": "ready",
                "message": "有一个猜测兴趣方向想确认",
                "domain": domain,
                "category": str(getattr(top, "category", "")),
                "reason": reason,
                "confidence": float(getattr(top, "confidence", 0.0) or 0.0),
                "weight": float(getattr(top, "weight", 0.0) or 0.0),
                "experience_mode": str(getattr(top, "experience_mode", "")),
                "entry_load": str(getattr(top, "entry_load", "")),
                "probe_mode": probe_mode,
                "challenge": challenge,
                "specifics": specifics,
                "question": question,
            }
        )
        if not delivered:
            logger.debug("interest probe skipped: no runtime-stream subscriber")
            return False

        # Record this probe only after it has reached at least one runtime stream.
        delivered_at = now.isoformat()

        def _record_probe(runtime_state: dict[str, object]) -> None:
            latest_probed = _string_state_map(runtime_state.get("probed_domains"))
            latest_probed[domain.lower()] = delivered_at
            runtime_state["probed_domains"] = latest_probed
            latest_axes = _string_state_map(runtime_state.get("probed_axes"))
            if axis:
                latest_axes[axis] = delivered_at
            runtime_state["probed_axes"] = latest_axes
            latest_bands = _string_state_map(runtime_state.get("probed_distance_bands"))
            latest_bands[probe_mode] = delivered_at
            runtime_state["probed_distance_bands"] = latest_bands

        self._update_discovery_runtime_state(_record_probe)
        return True

    async def _publish_avoidance_probe_if_available(self) -> bool:
        """Push the top speculative-avoidance hypothesis via WebSocket."""
        speculator = getattr(self.soul_engine, "_avoidance_speculator", None)
        get_active = getattr(speculator, "get_active_avoidances", None)
        if not callable(get_active):
            return False
        avoidances = [
            avoidance
            for avoidance in get_active()
            if str(getattr(avoidance, "status", "active")).strip().lower() == "active"
        ]
        if not avoidances:
            return False

        state = self.memory_manager.load_discovery_runtime_state()
        probed = _string_state_map(state.get("probed_avoidance_domains"))
        probed_axes = _string_state_map(state.get("probed_avoidance_axes"))
        now = self._now()
        cutoff = (now - timedelta(hours=self._PROBE_COOLDOWN_HOURS)).isoformat()
        probed = {d: t for d, t in probed.items() if t > cutoff}
        probed_axes = {axis: t for axis, t in probed_axes.items() if t > cutoff}

        top = choose_next_avoidance_candidate(
            avoidances,
            probed_domains=set(probed),
            probed_axes=set(probed_axes),
            feedback_history=state.get("avoidance_probe_feedback_history", []),
        )
        if top is None:
            return False

        domain = str(getattr(top, "domain", "")).strip()
        if not domain:
            return False

        axis = build_probe_axis(
            experience_mode=getattr(top, "experience_mode", ""),
            entry_load=getattr(top, "entry_load", ""),
        )
        reason = str(getattr(top, "reason", "")).strip()
        specifics = [
            str(getattr(item, "name", "")).strip()
            for item in getattr(top, "specifics", [])
            if str(getattr(item, "name", "")).strip()
        ][:5]
        specific_hint = ""
        if specifics:
            specific_hint = "（比如：" + "、".join(specifics[:3]) + "）"
        question = (
            f"我猜【{domain}】{specific_hint}可能是你想避开的方向——{reason} 这个判断准不准？"
            if reason
            else f"我感觉【{domain}】{specific_hint}可能不是你想看的方向，这个判断准不准？"
        )
        delivered = await self._publish_event(
            {
                "type": "avoidance.probe",
                "phase": "ready",
                "message": "有一个可能想避开的方向想确认",
                "domain": domain,
                "reason": reason,
                "confidence": float(getattr(top, "confidence", 0.0) or 0.0),
                "weight": float(getattr(top, "weight", 0.0) or 0.0),
                "source_mode": str(getattr(top, "source_mode", "")),
                "source_signal": str(getattr(top, "source_signal", "")),
                "experience_mode": str(getattr(top, "experience_mode", "")),
                "entry_load": str(getattr(top, "entry_load", "")),
                "specifics": specifics,
                "question": question,
            }
        )
        if not delivered:
            logger.debug("avoidance probe skipped: no runtime-stream subscriber")
            return False

        delivered_at = now.isoformat()

        def _record_avoidance_probe(runtime_state: dict[str, object]) -> None:
            latest_probed = _string_state_map(runtime_state.get("probed_avoidance_domains"))
            latest_probed[domain.lower()] = delivered_at
            runtime_state["probed_avoidance_domains"] = latest_probed
            latest_axes = _string_state_map(runtime_state.get("probed_avoidance_axes"))
            if axis:
                latest_axes[axis] = delivered_at
            runtime_state["probed_avoidance_axes"] = latest_axes

        self._update_discovery_runtime_state(_record_avoidance_probe)
        return True

    async def _publish_probe_if_available(self) -> bool:
        """Publish at most one proactive probe, alternating interest and avoidance."""
        state = self.memory_manager.load_discovery_runtime_state()
        last_kind = str(state.get("last_probe_kind", "")).strip().lower()
        order = (
            ("avoidance", self._publish_avoidance_probe_if_available),
            ("interest", self._publish_interest_probe_if_available),
        )
        if last_kind != "interest":
            order = (
                ("interest", self._publish_interest_probe_if_available),
                ("avoidance", self._publish_avoidance_probe_if_available),
            )

        for kind, publish in order:
            delivered = await publish()
            if not delivered:
                continue

            def _record_last_probe_kind(
                runtime_state: dict[str, object],
                *,
                probe_kind: str = kind,
            ) -> None:
                runtime_state["last_probe_kind"] = probe_kind

            self._update_discovery_runtime_state(_record_last_probe_kind)
            return True
        return False

    def _strategy_message(self, strategies: list[str]) -> str:
        if strategies == ["search", "related_chain"]:
            return "先从你刚刚的口味里搜一轮"
        if strategies == ["trending"]:
            return "顺手看看站内热榜里有没有你会吃的"
        if strategies == ["explore"]:
            return "再给你探一点你可能会意外喜欢的"
        return "正在继续给你补候选"

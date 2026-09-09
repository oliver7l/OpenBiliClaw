"""Unified rate-limit / anti-bot guard for personal-content producers.

Provides:
  * Risk signal detection (HTTP 403/429, empty results after prior success,
    captcha/verification keywords in stderr)
  * Exponential backoff with configurable base/max
  * Consecutive-failure circuit breaker (auto-pause after N failures)
  * Persistent state file so backoff survives process restarts
  * Success-resets-all recovery

Usage::

    from openbiliclaw.runtime.rate_limit_guard import RateLimitGuard

    guard = RateLimitGuard("xhs-favorites", state_dir=Path("data/rate_limit"))
    if guard.should_skip():
        logger.info("skipped due to rate-limit cooldown until %s", guard.cooldown_until)

Return:
    ok = fetch_and_insert(...)
    if ok:
        guard.record_success()
    else:
        guard.record_failure(reason="empty_result", detail=stderr_text)

"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Keywords that strongly suggest an anti-bot / rate-limit response
_RISK_KEYWORDS = (
    "403",
    "429",
    "rate limit",
    "rate-limit",
    "too many requests",
    "风控",
    "风险控制",
    "验证",
    "captcha",
    "CAPTCHA",
    "slider",
    "滑动验证",
    "人机验证",
    "登录",
    "login required",
    "unauthorized",
    "forbidden",
    "blocked",
    "ArgusSecurity",
    "Uifid",
    "spam",
    "异常",
    "频繁",
)

# Number of consecutive failures before circuit breaker opens
_DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 3
# Backoff multipliers (hours) for consecutive failures
_DEFAULT_BACKOFF_HOURS = (1, 6, 24, 48, 72)
# Max cooldown duration in hours
_DEFAULT_MAX_COOLDOWN_HOURS = 72


@dataclass
class GuardState:
    """Persistent state for a single guard instance."""

    name: str
    consecutive_failures: int = 0
    last_failure_reason: str = ""
    last_failure_detail: str = ""
    last_failure_time: str = ""
    last_success_time: str = ""
    cooldown_until: str = ""  # ISO timestamp; empty = no cooldown
    circuit_open: bool = False
    total_failures: int = 0
    total_successes: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GuardState:
        # Filter out unknown keys for forward-compatibility
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


class RateLimitGuard:
    """Anti-bot guard with exponential backoff + circuit breaker.

    Parameters
    ----------
    name:
        Unique identifier for this guard (used in state filename).
    state_dir:
        Directory to persist state JSON files.
    backoff_hours:
        Tuple of cooldown hours for consecutive failures (index = failure count - 1).
    max_cooldown_hours:
        Upper bound for cooldown duration.
    circuit_breaker_threshold:
        Number of consecutive failures before circuit opens (auto-pause).

    """

    def __init__(
        self,
        name: str,
        *,
        state_dir: Path,
        backoff_hours: tuple[int, ...] = _DEFAULT_BACKOFF_HOURS,
        max_cooldown_hours: int = _DEFAULT_MAX_COOLDOWN_HOURS,
        circuit_breaker_threshold: int = _DEFAULT_CIRCUIT_BREAKER_THRESHOLD,
    ) -> None:
        self.name = name
        self.state_dir = Path(state_dir)
        self.backoff_hours = backoff_hours
        self.max_cooldown_hours = max_cooldown_hours
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.state_file = self.state_dir / f"ratelimit_{name}.json"
        self._state = self._load_state()

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> GuardState:
        if self.state_file.exists():
            try:
                with open(self.state_file, encoding="utf-8") as f:
                    return GuardState.from_dict(json.load(f))
            except (json.JSONDecodeError, OSError, TypeError) as exc:
                logger.warning("failed to load rate-limit state for %s: %s", self.name, exc)
        return GuardState(name=self.name)

    def _save_state(self) -> None:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self._state.to_dict(), f, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.warning("failed to save rate-limit state for %s: %s", self.name, exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def consecutive_failures(self) -> int:
        return self._state.consecutive_failures

    @property
    def circuit_open(self) -> bool:
        return self._state.circuit_open

    @property
    def cooldown_until(self) -> str:
        return self._state.cooldown_until

    def should_skip(self) -> bool:
        """Return True if the current run should be skipped due to cooldown."""
        if not self._state.cooldown_until:
            return False
        try:
            cooldown_end = datetime.fromisoformat(self._state.cooldown_until)
        except ValueError:
            return False
        now = datetime.now()
        if now < cooldown_end:
            remaining = cooldown_end - now
            hours_left = remaining.total_seconds() / 3600
            logger.info(
                "[%s] rate-limit cooldown active — skipping (%.1f h remaining, %d consecutive failures)",
                self.name,
                hours_left,
                self._state.consecutive_failures,
            )
            return True
        # Cooldown expired — clear it but keep failure count (will reset on success)
        self._state.cooldown_until = ""
        self._save_state()
        return False

    def record_success(self) -> None:
        """Record a successful fetch — reset all failure counters."""
        now = datetime.now().isoformat(timespec="seconds")
        self._state.consecutive_failures = 0
        self._state.cooldown_until = ""
        self._state.circuit_open = False
        self._state.last_success_time = now
        self._state.total_successes += 1
        self._append_history("success", now)
        self._save_state()
        logger.info("[%s] fetch success — rate-limit counters reset", self.name)

    def record_failure(
        self,
        *,
        reason: str = "unknown",
        detail: str = "",
        exit_code: int | None = None,
    ) -> bool:
        """Record a failed fetch. Returns True if circuit breaker just opened.

        Parameters
        ----------
        reason:
            Short machine-readable reason (e.g. "http_403", "empty_result").
        detail:
            Full stderr / response text for risk-keyword detection.
        exit_code:
            CLI exit code if applicable.

        """
        now = datetime.now().isoformat(timespec="seconds")
        self._state.consecutive_failures += 1
        self._state.last_failure_reason = reason
        self._state.last_failure_detail = detail[:500]
        self._state.last_failure_time = now
        self._state.total_failures += 1

        # Detect risk signals
        is_risk_triggered = self._detect_risk(reason, detail, exit_code)

        # Calculate backoff
        failure_idx = min(self._state.consecutive_failures - 1, len(self.backoff_hours) - 1)
        base_hours = self.backoff_hours[failure_idx]
        # If risk keywords detected, use a longer backoff (at least 6h)
        if is_risk_triggered and base_hours < 6:
            base_hours = 6
        cooldown_hours = min(base_hours, self.max_cooldown_hours)

        # Check circuit breaker
        circuit_just_opened = False
        if self._state.consecutive_failures >= self.circuit_breaker_threshold and not self._state.circuit_open:  # noqa: E501
            self._state.circuit_open = True
            circuit_just_opened = True
            cooldown_hours = self.max_cooldown_hours
            logger.error(
                "[%s] CIRCUIT BREAKER OPEN — %d consecutive failures, pausing for %dh",
                self.name,
                self._state.consecutive_failures,
                cooldown_hours,
            )

        # Set cooldown
        cooldown_end = datetime.now() + timedelta(hours=cooldown_hours)
        self._state.cooldown_until = cooldown_end.isoformat(timespec="seconds")

        self._append_history(
            "failure",
            now,
            reason=reason,
            risk_detected=is_risk_triggered,
            cooldown_hours=cooldown_hours,
            circuit_open=self._state.circuit_open,
        )
        self._save_state()

        logger.warning(
            "[%s] fetch failure (#%d, reason=%s, risk=%s) — cooldown %.1fh (until %s)",
            self.name,
            self._state.consecutive_failures,
            reason,
            is_risk_triggered,
            cooldown_hours,
            self._state.cooldown_until,
        )
        return circuit_just_opened

    def reset_circuit(self) -> None:
        """Manually reset the circuit breaker and all counters."""
        self._state.circuit_open = False
        self._state.consecutive_failures = 0
        self._state.cooldown_until = ""
        self._save_state()
        logger.info("[%s] circuit breaker manually reset", self.name)

    def get_status(self) -> dict[str, Any]:
        """Return a human-readable status dict."""
        return {
            "name": self.name,
            "consecutive_failures": self._state.consecutive_failures,
            "total_failures": self._state.total_failures,
            "total_successes": self._state.total_successes,
            "circuit_open": self._state.circuit_open,
            "cooldown_until": self._state.cooldown_until,
            "last_failure_reason": self._state.last_failure_reason,
            "last_failure_time": self._state.last_failure_time,
            "last_success_time": self._state.last_success_time,
            "should_skip_now": self.should_skip(),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _detect_risk(self, reason: str, detail: str, exit_code: int | None) -> bool:
        """Detect anti-bot / rate-limit signals from failure context."""
        # Known risk reasons
        if reason in ("http_403", "http_429", "rate_limited", "blocked", "captcha"):
            return True
        # Exit code patterns (non-zero + specific)
        # CLI tools often use specific non-zero codes for auth failures
        if exit_code is not None and exit_code not in (0, 1) and exit_code in (403, 429, 77, 78):
            return True
        # Keyword scan in detail
        if detail:
            detail_lower = detail.lower()
            for kw in _RISK_KEYWORDS:
                if kw.lower() in detail_lower:
                    return True
        return False

    def _append_history(self, event_type: str, timestamp: str, **extra: Any) -> None:
        """Append a compact history entry (keep last 50)."""
        entry = {"type": event_type, "time": timestamp, **extra}
        self._state.history.append(entry)
        if len(self._state.history) > 50:
            self._state.history = self._state.history[-50:]

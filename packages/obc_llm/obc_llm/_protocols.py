"""Protocol interfaces for decoupling obc-llm from upstream modules.

Main project implements these protocols and injects them into LLMService.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from obc_llm.base import LLMResponse

if TYPE_CHECKING:
    from datetime import datetime


# ---------------------------------------------------------------------------
# Data classes (extracted from openbiliclaw.soul.profile)
# ---------------------------------------------------------------------------


@dataclass
class InterestTag:
    """A weighted interest tag with time decay."""

    name: str
    category: str
    weight: float = 1.0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    source: str = ""


@dataclass
class StylePreference:
    """Content style preferences."""

    preferred_duration: str = ""
    preferred_pace: str = ""
    quality_sensitivity: float = 0.5
    humor_preference: float = 0.5
    depth_preference: float = 0.5


@dataclass
class ContextMode:
    """Contextual usage patterns."""

    weekday_patterns: str = ""
    weekend_patterns: str = ""
    time_of_day_patterns: str = ""
    session_type: str = ""


@dataclass
class PreferenceLayer:
    """Preference Layer — structured preferences extracted from behavior."""

    interests: list[InterestTag] = field(default_factory=list)
    style: StylePreference = field(default_factory=StylePreference)
    context: ContextMode = field(default_factory=ContextMode)
    exploration_openness: float = 0.5
    disliked_topics: list[str] = field(default_factory=list)
    favorite_up_users: list[str] = field(default_factory=list)
    source_platform_mix: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_list(value: object) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _as_str_list(value: object) -> list[str]:
    return [str(v) for v in _as_list(value) if v]


def preference_layer_from_dict(raw_value: object) -> PreferenceLayer:
    """Build a preference layer from persisted JSON data."""
    data = raw_value if isinstance(raw_value, dict) else {}
    raw_mix = data.get("source_platform_mix")
    mix: dict[str, float] = {}
    if isinstance(raw_mix, dict):
        for key, value in raw_mix.items():
            if not isinstance(key, str):
                continue
            mix[key] = _as_float(value, 0.0)
    return PreferenceLayer(
        source_platform_mix=mix,
    )


# ---------------------------------------------------------------------------
# Protocol interfaces
# ---------------------------------------------------------------------------


@runtime_checkable
class ProfileRenderer(Protocol):
    """Renders user profile sections for LLM system prompts."""

    def render_core_memory_prompt(self) -> str:
        """Return core memory block for system prompt injection."""
        ...

    def get_layer(self, layer: str) -> Any:
        """Return a memory layer object (must have .data attribute)."""
        ...

    def get_core_memory(self) -> dict[str, Any]:
        """Return the core memory dict."""
        ...


@runtime_checkable
class ToneProvider(Protocol):
    """Provides tone profile for dialogue rendering."""

    def get_tone_profile(self) -> ToneProfile | None:
        ...


@runtime_checkable
class MemorySummarizer(Protocol):
    """Summarizes memory context for LLM prompts."""

    def summarize(self, max_tokens: int = 512) -> str:
        ...


@runtime_checkable
class UsageRecorder(Protocol):
    """Records LLM usage for cost tracking."""

    def record(self, response: LLMResponse, caller: str = "") -> None:
        ...


@dataclass
class ToneProfile:
    """Value-object for tone profile used in dialogue prompts.

    This is a pure data class extracted from openbiliclaw.soul.tone
    so obc-llm has no dependency on the soul module.
    """

    style: str = "balanced"
    warmth: str = "warm"
    playfulness: str = "low"
    directness: str = "direct"

    def __getitem__(self, key: str) -> str:
        return getattr(self, key, "balanced")

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)


def build_tone_profile(
    profile: Any | None = None,
    preference_summary: dict[str, Any] | None = None,
    recent_feedback: list[Any] | None = None,
) -> ToneProfile:
    """Build a tone profile from optional profile data.

    Simplified version of the original openbiliclaw.soul.tone.build_tone_profile.
    """
    return ToneProfile()

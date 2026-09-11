"""Configuration data classes for SoulEngine.

Extracted from openbiliclaw.config to decouple obc-soul from main config.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SoulConfig:
    """Configuration for the SoulEngine."""

    # Consolidation settings
    consolidation_interval_days: float = 7.0
    consolidation_min_events: int = 50

    # Preference analysis
    preference_analysis_enabled: bool = True
    preference_min_feedback: int = 3

    # Cognition cycle
    cognition_cycle_interval_hours: float = 24.0
    max_new_insights_per_cycle: int = 5

    # Exploration buffer
    exploration_buffer_size: int = 100
    exploration_buffer_refill_trigger: int = 10

    def __init__(
        self,
        consolidation_interval_days: float = 7.0,
        consolidation_min_events: int = 50,
        preference_analysis_enabled: bool = True,
        preference_min_feedback: int = 3,
        cognition_cycle_interval_hours: float = 24.0,
        max_new_insights_per_cycle: int = 5,
        exploration_buffer_size: int = 100,
        exploration_buffer_refill_trigger: int = 10,
    ):
        self.consolidation_interval_days = consolidation_interval_days
        self.consolidation_min_events = consolidation_min_events
        self.preference_analysis_enabled = preference_analysis_enabled
        self.preference_min_feedback = preference_min_feedback
        self.cognition_cycle_interval_hours = cognition_cycle_interval_hours
        self.max_new_insights_per_cycle = max_new_insights_per_cycle
        self.exploration_buffer_size = exploration_buffer_size
        self.exploration_buffer_refill_trigger = exploration_buffer_refill_trigger

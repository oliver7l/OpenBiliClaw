"""Sliding-window Thompson sampling for recommendation-side exploration.

The five-dimension curator score (:class:`~openbiliclaw.recommendation.curator.PoolCurator`)
is fully deterministic: given the same pool and history, the same top-K always
wins. That is exactly what makes a local single-user recommender settle into a
filter bubble — ``topic_fatigue`` suppresses *over*-shown topics but nothing
actively *up*-weights under-explored ones, so an interest the user never happened
to click yet stays invisible forever.

This module adds the missing exploration axis as a contextual bandit over coarse
arms. Each arm is a ``(discovery strategy, topic_group)`` pair — the same two
axes the curator already fatigues on, so the bandit explores precisely where
fatigue has been pushing mass. Arm posteriors are Bernoulli Beta distributions
over a sliding ``window_days`` impression window (SWTS): recent-only counts make
interest drift self-forgetting, and the posterior variance is what drives
exploration, so no hand-tuned ε is needed.

The reward model treats a presentation as a success when the user explicitly
liked/saved/favourited it **or** stayed on it past ``deep_dwell_seconds`` —
which is the same implicit-feedback definition ``Database.get_dwell_scores``
uses, so the two implicit signals cannot disagree.

Design note (why the default term is zero-mean): the exploration adjustment is
``weight * (θ_sample − posterior_mean)``. Its expectation is zero, so it can
never systematically re-rank the pool the way an additive bonus would — it only
injects variance where the posterior is uncertain. Well-observed arms get
≈ nothing; cold arms get real chances. Exploitation (ranking by posterior mean)
is available through ``exploitation_weight`` but defaults to 0.0 so it can be
turned on only after the offline eval says it pays.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = [
    "ArmStats",
    "ThompsonSamplingConfig",
    "SlidingWindowThompsonSampler",
    "arm_key",
    "sampler_from_scoring_config",
]


@dataclass(frozen=True)
class ThompsonSamplingConfig:
    """Knobs for the sliding-window Thompson sampling term.

    ``enabled`` is the master gate and defaults to off so the term can be rolled
    out behind a flag; every other field is inert while it is false.
    """

    enabled: bool = False
    # Impression window. Short enough that a moved-on interest self-forgets,
    # long enough that a normal user accumulates per-arm counts.
    window_days: int = 30
    # Scale of the zero-mean exploration term, i.e. max |Δ| it can contribute.
    # Compared against ``ScoringWeights`` this is a mid-sized axis: strong enough
    # to move a cold arm past a mid-relevance one, too weak to override a
    # dislike penalty.
    exploration_weight: float = 0.15
    # Scale of the posterior-mean term. Defaults to 0.0: the curator's relevance
    # and feedback axes already exploit, so enabling this double-counts.
    exploitation_weight: float = 0.0
    # Jeffreys-style prior. Beta(1, 1) is uniform on [0, 1]: a never-seen arm
    # has maximum posterior variance and therefore gets explored first.
    prior_alpha: float = 1.0
    prior_beta: float = 1.0
    # A view at or past this many seconds counts as a reward (implicit like).
    deep_dwell_seconds: float = 60.0
    # Explicit positive feedback types counted as rewards.
    positive_feedback_types: tuple[str, ...] = ("like", "save", "favorite")


@dataclass(frozen=True)
class ArmStats:
    """Observed impressions and rewards for one arm inside the window."""

    exposures: int = 0
    rewards: int = 0

    @property
    def successes(self) -> int:
        """Reward count, clamped to the exposure count it came from."""
        return min(self.rewards, self.exposures)

    @property
    def failures(self) -> int:
        """Presented-but-not-rewarded impressions."""
        return max(0, self.exposures - self.successes)

    def posterior(self, config: ThompsonSamplingConfig) -> tuple[float, float]:
        """Return the ``(alpha, beta)`` parameters of the arm's Beta posterior."""
        return (
            config.prior_alpha + self.successes,
            config.prior_beta + self.failures,
        )


def arm_key(source_strategy: str, topic_group: str) -> str:
    """Canonical key for a ``(strategy, topic_group)`` arm.

    Empty parts collapse to a shared bucket so unlabeled content still
    accumulates statistics instead of fragmenting into per-item singletons.
    """
    strategy = (source_strategy or "").strip().lower() or "unknown"
    group = (topic_group or "").strip().lower() or "ungrouped"
    return f"{strategy}|{group}"


class SlidingWindowThompsonSampler:
    """Turns windowed impression rows into per-candidate score adjustments.

    Stateless with respect to the database: the caller loads arm rows
    (:meth:`build_arms`), then scores any number of candidates against that
    snapshot (:meth:`adjustment`). One snapshot per serve call keeps the sampled
    batch mutually consistent.
    """

    def __init__(
        self,
        config: ThompsonSamplingConfig,
        *,
        rng: random.Random | None = None,
    ) -> None:
        self._config = config
        # An explicit seed is only for tests; production gets a fresh RNG so
        # consecutive "换一批" calls do not replay the same exploration pattern.
        self._rng = rng or random.Random()

    @property
    def config(self) -> ThompsonSamplingConfig:
        """The active configuration."""
        return self._config

    @property
    def enabled(self) -> bool:
        """Whether the sampler contributes anything to a score."""
        return self._config.enabled

    def build_arms(self, rows: Iterable[Mapping[str, Any]]) -> dict[str, ArmStats]:
        """Aggregate DB impression rows into ``arm_key → ArmStats``.

        Accepted row keys mirror ``Database.get_bandit_impressions``:
        ``source``, ``topic_group``, ``exposures``, ``rewards``. Rows with no
        usable group are folded into the ``ungrouped`` bucket.
        """
        arms: dict[str, ArmStats] = {}
        for row in rows:
            key = arm_key(
                str(row.get("source", "") or ""),
                str(row.get("topic_group", "") or ""),
            )
            exposures = int(row.get("exposures", 0) or 0)
            rewards = int(row.get("rewards", 0) or 0)
            previous = arms.get(key)
            if previous is None:
                arms[key] = ArmStats(exposures=exposures, rewards=rewards)
            else:
                arms[key] = ArmStats(
                    exposures=previous.exposures + exposures,
                    rewards=previous.rewards + rewards,
                )
        return arms

    def posterior_mean(
        self,
        key: str,
        arms: Mapping[str, ArmStats],
    ) -> float:
        """Posterior reward probability of one arm (prior for unseen arms)."""
        alpha, beta = arms.get(key, ArmStats()).posterior(self._config)
        return alpha / (alpha + beta)

    def adjustment(
        self,
        key: str,
        arms: Mapping[str, ArmStats],
    ) -> float:
        """Score adjustment for a candidate on arm ``key``.

        Zero-mean exploration plus optional exploitation. Returns ``0.0`` when
        the sampler is disabled so callers can skip the gate entirely.
        """
        if not self._config.enabled:
            return 0.0

        alpha, beta = arms.get(key, ArmStats()).posterior(self._config)
        mean = alpha / (alpha + beta)
        sample = self._rng.betavariate(alpha, beta)

        delta = self._config.exploration_weight * (sample - mean)
        if self._config.exploitation_weight:
            prior_mean = self._config.prior_alpha / (
                self._config.prior_alpha + self._config.prior_beta
            )
            delta += self._config.exploitation_weight * (mean - prior_mean)
        return delta

    def score(
        self,
        item: Any,
        arms: Mapping[str, ArmStats],
    ) -> float:
        """Adjustment for a candidate exposing ``source_strategy`` / ``topic_group``."""
        if not self._config.enabled:
            return 0.0
        key = arm_key(
            str(getattr(item, "source_strategy", "") or ""),
            str(getattr(item, "topic_group", "") or ""),
        )
        return self.adjustment(key, arms)


def sampler_from_scoring_config(scoring: Any) -> SlidingWindowThompsonSampler:
    """Build a sampler from the ``[recommendation]`` config section.

    Takes the config object structurally (duck-typed on its ``ts_*`` field
    names) so ``recommendation`` never imports ``config`` and the dependency
    stays one-directional. Callers get a sampler back in every case — when the
    section is missing or disabled it simply reports ``enabled == False`` and
    contributes 0.0, which keeps the wiring in one place.
    """
    return SlidingWindowThompsonSampler(
        ThompsonSamplingConfig(
            enabled=bool(getattr(scoring, "thompson_sampling_enabled", False)),
            window_days=int(getattr(scoring, "ts_window_days", 30)),
            exploration_weight=float(getattr(scoring, "ts_exploration_weight", 0.15)),
            exploitation_weight=float(getattr(scoring, "ts_exploitation_weight", 0.0)),
            deep_dwell_seconds=float(getattr(scoring, "ts_deep_dwell_seconds", 60.0)),
        )
    )

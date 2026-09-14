"""Tests for the recommendation-side Thompson sampling exploration axis.

Covers the three layers the feature touches: the pure sampler math
(``recommendation/bandit.py``), the windowed impression query
(``Database.get_bandit_impressions``), and the curator wiring — including the
guarantee that a disabled sampler leaves the five-dimension scores byte-identical.
"""

from __future__ import annotations

import random
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from obc_discovery.engine import DiscoveredContent

from openbiliclaw.config import (
    Config,
    RecommendationScoringConfig,
    _render_config_toml,
    load_config,
    save_config,
)
from openbiliclaw.recommendation.bandit import (
    ArmStats,
    SlidingWindowThompsonSampler,
    ThompsonSamplingConfig,
    arm_key,
    sampler_from_scoring_config,
)
from openbiliclaw.recommendation.curator import PoolCurator, ScoringContext
from openbiliclaw.storage.database import Database


def _make_db() -> Database:
    db = Database(Path(tempfile.mkdtemp()) / "test.db")
    db.initialize()
    return db


def _enabled(**overrides: object) -> ThompsonSamplingConfig:
    return ThompsonSamplingConfig(enabled=True, **overrides)  # type: ignore[arg-type]


def _sampler(config: ThompsonSamplingConfig, *, seed: int = 7) -> SlidingWindowThompsonSampler:
    return SlidingWindowThompsonSampler(config, rng=random.Random(seed))


# ---------------------------------------------------------------------------
# Arm keys
# ---------------------------------------------------------------------------


def test_arm_key_normalizes_case_and_blanks() -> None:
    assert arm_key("Search", "  游戏 ") == "search|游戏"
    assert arm_key("", "") == "unknown|ungrouped"


def test_same_group_same_strategy_share_one_arm() -> None:
    # Two sibling items in one arm must collide, otherwise per-arm counts never
    # accumulate and the bandit degenerates into uniform random noise.
    assert arm_key("explore", "科普") == arm_key("explore", "科普")
    assert arm_key("explore", "科普") != arm_key("trending", "科普")


# ---------------------------------------------------------------------------
# Gating: disabled must be a no-op
# ---------------------------------------------------------------------------


def test_disabled_sampler_contributes_zero() -> None:
    sampler = _sampler(ThompsonSamplingConfig(enabled=False))
    item = DiscoveredContent(bvid="BV1", source_strategy="explore", topic_group="科普")
    assert sampler.enabled is False
    assert sampler.score(item, {}) == 0.0
    assert sampler.adjustment("explore|科普", {}) == 0.0


def test_curator_without_sampler_is_byte_identical() -> None:
    candidates = [
        DiscoveredContent(
            bvid="BV1",
            relevance_score=0.8,
            source_strategy="explore",
            topic_group="科普",
        ),
        DiscoveredContent(
            bvid="BV2",
            relevance_score=0.6,
            source_strategy="search",
            topic_group="游戏",
        ),
    ]
    context = ScoringContext()
    baseline = PoolCurator(_make_db()).score_candidates(candidates, context)
    gated = PoolCurator(
        _make_db(),
        ts_sampler=_sampler(ThompsonSamplingConfig(enabled=False)),
    ).score_candidates(candidates, context)
    assert baseline == gated


# ---------------------------------------------------------------------------
# Exploration math
# ---------------------------------------------------------------------------


def test_cold_arm_gets_bounded_nonzero_exploration() -> None:
    sampler = _sampler(_enabled(exploration_weight=0.15))
    deltas = {sampler.adjustment("explore|科普", {}) for _ in range(200)}
    assert len(deltas) > 1, "a prior-only arm must actually be sampled"
    assert all(-0.15 - 1e-9 <= d <= 0.15 + 1e-9 for d in deltas)


def test_exploration_is_mean_neutral() -> None:
    # The whole point of the (sample − mean) form: over many draws the term adds
    # no systematic bias, so it cannot silently re-rank the pool.
    sampler = _sampler(_enabled(exploration_weight=0.2), seed=1234)
    draws = [sampler.adjustment("search|游戏", {}) for _ in range(4000)]
    assert abs(sum(draws) / len(draws)) < 0.01


def test_saturated_arm_barely_moves() -> None:
    # 400 impressions, zero rewards: posterior variance has collapsed, so this
    # arm gets ≈ nothing — exploration budget goes to arms that lack evidence.
    saturated = {"search|短视频": ArmStats(exposures=400, rewards=0)}
    sampler = _sampler(_enabled(exploration_weight=0.15), seed=99)
    deltas = [sampler.adjustment("search|短视频", saturated) for _ in range(200)]
    assert max(abs(d) for d in deltas) < 0.02


def test_exploration_favours_cold_arm_over_saturated_one() -> None:
    saturated = {"a|热": ArmStats(exposures=400, rewards=0)}
    sampler = _sampler(_enabled(exploration_weight=0.15), seed=5)
    hot = [sampler.adjustment("a|热", saturated) for _ in range(500)]
    cold = [sampler.adjustment("a|冷", saturated) for _ in range(500)]
    assert max(abs(d) for d in cold) > max(abs(d) for d in hot)


def test_exploitation_weight_ranks_high_ctr_arm_above_low_ctr() -> None:
    arms = {
        # 50/60 → posterior mean ≈ 0.82, clearly above the 0.5 prior mean.
        # (30/60 would land exactly on the prior mean and yield a zero shift.)
        "s|好": ArmStats(exposures=60, rewards=50),
        "s|差": ArmStats(exposures=60, rewards=2),
    }
    # Exploration off (weight 0) isolates the exploitation term.
    sampler = _sampler(_enabled(exploration_weight=0.0, exploitation_weight=0.5))
    high = sum(sampler.adjustment("s|好", arms) for _ in range(100))
    low = sum(sampler.adjustment("s|差", arms) for _ in range(100))
    assert high > 0.0 > low


def test_build_arms_merges_duplicate_rows() -> None:
    sampler = _sampler(_enabled())
    arms = sampler.build_arms(
        [
            {"source": "explore", "topic_group": "科普", "exposures": 3, "rewards": 1},
            {"source": "explore", "topic_group": "科普", "exposures": 4, "rewards": 2},
        ]
    )
    assert arms["explore|科普"] == ArmStats(exposures=7, rewards=3)


def test_arm_stats_clamps_rewards_over_exposures() -> None:
    # A reward join can double-count if the same bvid has both a like and a deep
    # view row; the posterior must never go negative on failures.
    stats = ArmStats(exposures=2, rewards=5)
    assert stats.successes == 2
    assert stats.failures == 0


def test_posterior_uses_jeffreys_prior_for_unseen_arm() -> None:
    sampler = _sampler(_enabled(prior_alpha=1.0, prior_beta=1.0))
    assert sampler.posterior_mean("nope|nothing", {}) == 0.5


# ---------------------------------------------------------------------------
# Config factory
# ---------------------------------------------------------------------------


class _FakeScoringConfig:
    def __init__(self, **kwargs: object) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_sampler_from_scoring_config_maps_fields() -> None:
    sampler = sampler_from_scoring_config(
        _FakeScoringConfig(
            thompson_sampling_enabled=True,
            ts_window_days=14,
            ts_exploration_weight=0.3,
            ts_exploitation_weight=0.1,
            ts_deep_dwell_seconds=45.0,
        )
    )
    config = sampler.config
    assert sampler.enabled is True
    assert (config.window_days, config.exploration_weight) == (14, 0.3)
    assert (config.exploitation_weight, config.deep_dwell_seconds) == (0.1, 45.0)


def test_sampler_from_missing_config_is_inert() -> None:
    # Older / partial configs must degrade to "off", not raise at boot.
    assert sampler_from_scoring_config(None).enabled is False
    assert sampler_from_scoring_config(_FakeScoringConfig()).enabled is False


# ---------------------------------------------------------------------------
# Windowed impression query
# ---------------------------------------------------------------------------


def _seed_pool(db: Database) -> None:
    db.cache_content("BV_HOT", source="explore", topic_group="科普", title="a")
    db.cache_content("BV_COLD", source="explore", topic_group="游戏", title="b")
    db.cache_content("BV_OLD", source="explore", topic_group="历史", title="c")


def test_get_bandit_impressions_counts_explicit_and_dwell_rewards() -> None:
    db = _make_db()
    _seed_pool(db)
    hot = db.insert_recommendation("BV_HOT", confidence=0.9, presented=1)
    db.insert_recommendation("BV_HOT", confidence=0.9, presented=1)
    db.insert_recommendation("BV_COLD", confidence=0.8, presented=1)
    db.update_recommendation_feedback(hot, feedback_type="like")
    db.insert_view_history({"bvid": "BV_COLD", "topic_group": "游戏", "dwell_seconds": 120})

    rows = db.get_bandit_impressions(since=datetime.now(UTC) - timedelta(days=30))
    by_group = {str(row["topic_group"]): row for row in rows}

    assert int(by_group["科普"]["exposures"]) == 2
    assert int(by_group["科普"]["rewards"]) == 1
    # Deep dwell alone counts as a reward — same implicit definition as the
    # profile side's get_dwell_scores().
    assert int(by_group["游戏"]["exposures"]) == 1
    assert int(by_group["游戏"]["rewards"]) == 1


def test_get_bandit_impressions_shallow_dwell_is_not_a_reward() -> None:
    db = _make_db()
    _seed_pool(db)
    rec_id = db.insert_recommendation("BV_COLD", confidence=0.8, presented=1)
    db.update_recommendation_feedback(rec_id, feedback_type="dislike")
    db.insert_view_history({"bvid": "BV_COLD", "topic_group": "游戏", "dwell_seconds": 5})

    rows = db.get_bandit_impressions(since=datetime.now(UTC) - timedelta(days=30))
    game = next(row for row in rows if row["topic_group"] == "游戏")
    assert int(game["exposures"]) == 1
    assert int(game["rewards"]) == 0


def test_get_bandit_impressions_respects_window() -> None:
    db = _make_db()
    _seed_pool(db)
    db.insert_recommendation("BV_OLD", confidence=0.7, presented=1)
    db.conn.execute(
        "UPDATE recommendations SET created_at = ? WHERE bvid = 'BV_OLD'",
        ((datetime.now(UTC) - timedelta(days=90)).isoformat(sep=" "),),
    )
    db.conn.commit()

    rows = db.get_bandit_impressions(since=datetime.now(UTC) - timedelta(days=30))
    assert all(row["topic_group"] != "历史" for row in rows)


# ---------------------------------------------------------------------------
# Curator wiring
# ---------------------------------------------------------------------------


def test_curator_loads_arms_only_when_enabled() -> None:
    enabled_db = _make_db()
    _seed_pool(enabled_db)
    enabled_db.insert_recommendation("BV_HOT", confidence=0.9, presented=1)

    enabled = PoolCurator(
        enabled_db,
        ts_sampler=_sampler(_enabled()),
    ).build_context()
    assert enabled.arm_stats, "enabled sampler must hydrate arms from the window"

    disabled = PoolCurator(
        _make_db(),
        ts_sampler=_sampler(ThompsonSamplingConfig(enabled=False)),
    ).build_context()
    assert disabled.arm_stats == {}


def test_curator_degrades_to_no_arms_when_query_fails() -> None:
    class _BrokenDB:
        def get_recent_recommendation_signals(self, *, limit: int = 30) -> list[dict[str, str]]:
            return []

        def get_recent_recommendation_signals_since(self, *, since: datetime) -> list[dict]:
            return []

        def get_feedback_signals(self, *, limit: int = 50) -> list[dict]:
            return []

        def get_bandit_impressions(self, **_: object) -> list[dict]:
            raise sqlite_error()

    def sqlite_error() -> OSError:
        return OSError("no such table: view_history")

    curator = PoolCurator(_BrokenDB(), ts_sampler=_sampler(_enabled()))  # type: ignore[arg-type]
    context = curator.build_context()
    assert context.arm_stats == {}


def test_enabled_sampler_can_reverse_a_close_ranking() -> None:
    """The exploration term is strong enough to actually surface a cold arm."""
    candidates = [
        DiscoveredContent(
            bvid="BV_HOT",
            relevance_score=0.80,
            source_strategy="explore",
            topic_group="科普",
        ),
        DiscoveredContent(
            bvid="BV_COLD",
            relevance_score=0.78,
            source_strategy="explore",
            topic_group="游戏",
        ),
    ]
    arms = {"explore|科普": ArmStats(exposures=120, rewards=60)}
    saturated_first = PoolCurator(
        _make_db(),
        ts_sampler=_sampler(_enabled(exploration_weight=0.15), seed=3),
    ).score_candidates(candidates, ScoringContext(arm_stats=arms))
    deterministic = PoolCurator(_make_db()).score_candidates(
        candidates, ScoringContext(arm_stats=arms)
    )

    assert deterministic["BV_HOT"] > deterministic["BV_COLD"]
    # Re-sampled across seeds, the cold arm must win sometimes — that is the
    # filter-bubble break this axis exists to provide.
    flips = 0
    for seed in range(30):
        scores = PoolCurator(
            _make_db(),
            ts_sampler=_sampler(_enabled(exploration_weight=0.15), seed=seed),
        ).score_candidates(candidates, ScoringContext(arm_stats=arms))
        flips += int(scores["BV_COLD"] > scores["BV_HOT"])
    assert 0 < flips < 30, "cold arm should sometimes, never always, overtake"
    assert saturated_first != deterministic


async def test_async_scoring_path_also_gets_exploration() -> None:
    candidates = [
        DiscoveredContent(
            bvid="BV1",
            relevance_score=0.8,
            source_strategy="explore",
            topic_group="科普",
        )
    ]
    arms = {"explore|科普": ArmStats(exposures=2, rewards=1)}
    # Two samplers with the same seed: the sync and async paths must consume the
    # identical draw stream. Sharing one sampler would compare 1st vs 2nd draw
    # and always diverge.
    sync_scores = PoolCurator(
        _make_db(),
        ts_sampler=_sampler(_enabled(exploration_weight=0.15), seed=11),
    ).score_candidates(candidates, ScoringContext(arm_stats=arms))
    async_scores = await PoolCurator(
        _make_db(),
        ts_sampler=_sampler(_enabled(exploration_weight=0.15), seed=11),
    ).score_candidates_async(candidates, ScoringContext(arm_stats=arms))
    assert sync_scores == async_scores

    baseline = PoolCurator(_make_db()).score_candidates(candidates, ScoringContext(arm_stats=arms))
    off = await PoolCurator(_make_db()).score_candidates_async(
        candidates, ScoringContext(arm_stats=arms)
    )
    assert baseline == off, "disabled sampler must leave async scores untouched too"


# ---------------------------------------------------------------------------
# [recommendation] config section
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_recommendation_section_defaults_to_off() -> None:
    assert Config().recommendation.thompson_sampling_enabled is False
    assert Config().recommendation.ts_exploitation_weight == 0.0


def test_recommendation_section_parses_values(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        "[recommendation]\n"
        "thompson_sampling_enabled = true\n"
        "ts_window_days = 14\n"
        "ts_exploration_weight = 0.4\n"
        "ts_exploitation_weight = 0.1\n"
        "ts_deep_dwell_seconds = 30\n",
    )
    config = load_config(path).recommendation
    assert config == RecommendationScoringConfig(
        thompson_sampling_enabled=True,
        ts_window_days=14,
        ts_exploration_weight=0.4,
        ts_exploitation_weight=0.1,
        ts_deep_dwell_seconds=30.0,
    )


def test_recommendation_section_falls_back_on_out_of_range(tmp_path: Path) -> None:
    # A bad number must not take the whole flag (or the serve path) down with it.
    path = _write_config(
        tmp_path,
        "[recommendation]\n"
        "thompson_sampling_enabled = true\n"
        "ts_window_days = 99999\n"
        "ts_exploration_weight = 12.0\n"
        "ts_deep_dwell_seconds = -5\n",
    )
    config = load_config(path).recommendation
    assert config.thompson_sampling_enabled is True
    assert config.ts_window_days == 30
    assert config.ts_exploration_weight == 0.15
    assert config.ts_deep_dwell_seconds == 60.0


def test_recommendation_section_survives_a_non_table_value(tmp_path: Path) -> None:
    # A hand-edited file can leave `recommendation` as a scalar instead of a
    # table; that must degrade to defaults rather than crash config load.
    path = _write_config(tmp_path, 'recommendation = "oops"\n')
    assert load_config(path).recommendation == RecommendationScoringConfig()


def test_recommendation_section_round_trips_through_save(tmp_path: Path) -> None:
    config = Config()
    config.recommendation = RecommendationScoringConfig(
        thompson_sampling_enabled=True,
        ts_window_days=21,
        ts_exploration_weight=0.25,
    )
    path = tmp_path / "config.toml"
    save_config(config, path)
    assert load_config(path).recommendation == config.recommendation


def test_rendered_toml_advertises_the_new_section() -> None:
    # Existing installs get the new keys on their next settings save, so the
    # section must be in the renderer output and off by default.
    rendered = _render_config_toml(Config())
    assert "[recommendation]" in rendered
    assert "thompson_sampling_enabled = false" in rendered


def test_runtime_wiring_enables_the_sampler_from_config(tmp_path: Path) -> None:
    # Guards the actual integration seam: config file → sampler the curator uses.
    path = _write_config(
        tmp_path,
        "[recommendation]\nthompson_sampling_enabled = true\nts_window_days = 7\n",
    )
    sampler = sampler_from_scoring_config(load_config(path).recommendation)
    assert sampler.enabled is True
    assert sampler.config.window_days == 7

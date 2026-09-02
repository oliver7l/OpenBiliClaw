"""Unit tests for RankAgent scoring core and the shared embedding cache key.

Covers the /api/agent-recommend ranking path: implicit dwell blending,
semantic re-rank using pre-computed content embeddings, the canonical
MMR cache-key parity between the prewarm side and the agent side, and the
session-context label rendering.
"""

from __future__ import annotations

from typing import Any

import pytest

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.llm.embedding import mmr_cache_text
from openbiliclaw.recommendation.agents import RankAgent
from openbiliclaw.recommendation.engine import RecommendationEngine


def _row(bvid: str, *, topic: str = "", qs: float = 0.5, title: str = "") -> dict[str, Any]:
    return {
        "bvid": bvid,
        "title": title,
        "body_text": "",
        "topic_group": topic,
        "up_name": "up",
        "source_platform": "bilibili",
        "quality_score": qs,
    }


# ---------------------------------------------------------------------------
# canonical cache key parity (prewarm side == agent side)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title,description",
    [
        ("深度学习入门", "从零讲透 Transformer 架构"),
        ("", ""),
        ("只有标题", ""),
        ("x" * 500, "y" * 500),
        ("标题", "描述" * 200),  # exercise the 160-char description truncation
    ],
)
def test_mmr_cache_text_matches_engine_canonical(title: str, description: str) -> None:
    content = DiscoveredContent(bvid="b1", title=title, description=description)
    assert mmr_cache_text(title, description) == RecommendationEngine._mmr_embedding_text(content)


# ---------------------------------------------------------------------------
# compute_dwell_beta — confidence in implicit dwell signal
# ---------------------------------------------------------------------------


class _StubDB:
    def __init__(
        self, *, views: int = 0, dwell: dict[str, float] | None = None, raises: bool = False
    ) -> None:
        self._views = views
        self._dwell = dwell or {}
        self._raises = raises
        self.total_days_seen: int | None = None
        self.dwell_days_seen: int | None = None

    def get_total_view_count(self, *, days: int = 30) -> int:
        self.total_days_seen = days
        if self._raises:
            raise RuntimeError("db down")
        return self._views

    def get_dwell_scores(self, *, days: int = 14) -> dict[str, float]:
        self.dwell_days_seen = days
        if self._raises:
            raise RuntimeError("db down")
        return self._dwell


def test_dwell_beta_zero_without_views() -> None:
    assert RankAgent.compute_dwell_beta(_StubDB(views=0)) == 0.0


def test_dwell_beta_formula_and_true_cap() -> None:
    # beta = min(0.15, views/(views+30) * 0.15 * 1.15) — the 1.15 scale makes
    # the cap engage exactly at 200 views (the pre-fix form only asymptoted).
    assert RankAgent.compute_dwell_beta(_StubDB(views=50)) == pytest.approx(0.10781, abs=1e-4)
    assert RankAgent.compute_dwell_beta(_StubDB(views=200)) == pytest.approx(0.15, abs=1e-6)
    # clamped at the cap for anything larger; never exceeds 0.15
    assert RankAgent.compute_dwell_beta(_StubDB(views=100_000)) == pytest.approx(0.15)
    # strictly increasing below the cap
    assert (
        RankAgent.compute_dwell_beta(_StubDB(views=10))
        < RankAgent.compute_dwell_beta(_StubDB(views=50))
        < RankAgent.compute_dwell_beta(_StubDB(views=200))
    )


def test_dwell_beta_swallows_db_errors() -> None:
    assert RankAgent.compute_dwell_beta(_StubDB(raises=True)) == 0.0


def test_compute_dwell_scores_delegates_with_14_day_window() -> None:
    db = _StubDB(dwell={"科技": 0.8})
    assert RankAgent.compute_dwell_scores(db) == {"科技": 0.8}
    assert db.dwell_days_seen == 14
    assert RankAgent.compute_dwell_scores(_StubDB(raises=True)) == {}


# ---------------------------------------------------------------------------
# score_and_rank — semantic re-rank uses only pre-computed content embeddings
# ---------------------------------------------------------------------------


def _pin_dwell(
    monkeypatch: pytest.MonkeyPatch, *, alpha: float, beta: float, dwell: dict[str, float]
) -> None:
    monkeypatch.setattr(
        RankAgent, "compute_learning_level", staticmethod(lambda db: alpha), raising=True
    )
    monkeypatch.setattr(
        RankAgent, "compute_learned_scores", staticmethod(lambda db: {}), raising=True
    )
    monkeypatch.setattr(
        RankAgent, "compute_dwell_beta", staticmethod(lambda db: beta), raising=True
    )
    monkeypatch.setattr(
        RankAgent, "compute_dwell_scores", staticmethod(lambda db: dwell), raising=True
    )


def _combined_by_topic(result: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for s in result["high_fit"] + result["low_fit"]:
        out[s["row"]["topic_group"]] = s["combined"]
    return out


def test_score_and_rank_semantic_lifts_matching_item(monkeypatch: pytest.MonkeyPatch) -> None:
    _pin_dwell(monkeypatch, alpha=0.0, beta=0.0, dwell={})
    rows = [_row("v1", topic="A", qs=0.5), _row("v2", topic="B", qs=0.5)]
    res = RankAgent.score_and_rank(
        rows=rows,
        intent={},
        db=object(),
        profile_keywords=[],
        q_embed=[1.0, 0.0],
        content_embeds={"v1": [1.0, 0.0]},  # only v1 has a cached vector
    )
    sem = {s["row"]["bvid"]: s["semantic_score"] for s in res["high_fit"] + res["low_fit"]}
    assert sem["v1"] == pytest.approx(1.0)
    assert sem["v2"] == 0.0  # missing from content_embeds → no semantic contribution
    assert _combined_by_topic(res)["A"] > _combined_by_topic(res)["B"]


def test_score_and_rank_ignores_stale_emb_service_kwarg() -> None:
    # The old async-embedding kwarg must be gone; passing it should TypeError.
    with pytest.raises(TypeError):
        RankAgent.score_and_rank(
            rows=[], intent={}, db=object(), profile_keywords=[], emb_service=object()
        )


def test_score_and_rank_dwell_can_flip_tie(monkeypatch: pytest.MonkeyPatch) -> None:
    _pin_dwell(monkeypatch, alpha=0.0, beta=0.10, dwell={"A": 0.1, "B": 0.9})
    rows = [_row("v1", topic="A", qs=0.7), _row("v2", topic="B", qs=0.7)]
    res = RankAgent.score_and_rank(
        rows=rows, intent={}, db=object(), profile_keywords=[], q_embed=None, content_embeds={}
    )
    comb = _combined_by_topic(res)
    assert comb["B"] > comb["A"]  # equal quality; higher dwell wins
    assert all(-1e-9 <= v <= 1.0 + 1e-9 for v in comb.values())


def test_score_and_rank_returns_expected_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    _pin_dwell(monkeypatch, alpha=0.2, beta=0.05, dwell={})
    res = RankAgent.score_and_rank(
        rows=[_row("v1", topic="A", qs=0.6)],
        intent={},
        db=object(),
        profile_keywords=[],
        q_embed=None,
        content_embeds={},
    )
    assert set(res) == {"high_fit", "low_fit", "alpha", "beta"}
    assert res["alpha"] == pytest.approx(0.2)
    assert res["beta"] == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# build_context_text — implicit dwell label rendering
# ---------------------------------------------------------------------------


def test_build_context_text_shows_both_learning_labels() -> None:
    text = RankAgent.build_context_text({"keywords": ["AI"]}, alpha=0.3, beta=0.094)
    assert "点赞学习" in text
    assert "停留学习" in text
    assert "AI" in text


def test_build_context_text_omits_negligible_learning() -> None:
    text = RankAgent.build_context_text({"keywords": ["AI"]}, alpha=0.0, beta=0.0)
    assert "学习" not in text

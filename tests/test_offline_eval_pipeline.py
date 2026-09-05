"""Integration tests for the offline evaluation pipeline (runner + scenario)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openbiliclaw.eval.offline.ground_truth import CandidateItem
from openbiliclaw.eval.offline.runner import run_offline_eval, run_offline_eval_async
from openbiliclaw.eval.offline.scenario import build_units


def _item(content_key: str, *, rel: float, topic: str = "t", style: str = "s") -> CandidateItem:
    cid = content_key.split(":", 1)[-1]
    return CandidateItem(
        content_key=content_key,
        bvid=cid,
        title="title",
        topic_group=topic,
        style_key=style,
        relevance_score=rel,
        candidate_tier="primary",
        source_platform="bilibili",
        content_url=f"https://www.bilibili.com/video/{cid}",
        discovered_at="2026-09-01 00:00:00",
    )


def test_build_units_shapes_and_balance():
    positives = [(_item(f"bilibili:BV00{i}", rel=0.9 - i * 0.1), object()) for i in range(6)]
    negatives = [_item(f"bilibili:BV1{i:02d}", rel=0.3) for i in range(30)]
    units = build_units(
        positives,
        negatives,
        n_units=5,
        positives_per_unit=2,
        unit_size=10,
        seed=1,
    )
    assert len(units) == 5
    for u in units:
        assert len(u.candidates) == 10
        assert sum(u.labels) == 2
        assert len(set(c.content_key for c in u.candidates)) == 10  # no dup within unit


def test_run_offline_eval_aggregation_shape():
    positives = [(_item(f"bilibili:BV00{i}", rel=0.9 - i * 0.1), object()) for i in range(6)]
    negatives = [_item(f"bilibili:BV1{i:02d}", rel=0.3) for i in range(30)]
    units = build_units(positives, negatives, n_units=4, positives_per_unit=2, unit_size=10, seed=3)
    result = run_offline_eval(units, k=5, seed=3, include_random_baseline=True)
    assert set(result["methods"].keys()) == {"engine", "random"}
    for method in result["methods"].values():
        assert method["n_units"] == 4
        for key, agg in method["metrics"].items():
            assert "mean" in agg and "std" in agg
            assert 0.0 <= agg["mean"] <= 1.0 or key in {"topic_coverage", "style_coverage"}


def test_run_offline_eval_engine_beats_random_on_signal():
    # positives have high relevance, negatives low -> engine should rank them first
    positives = [(_item(f"bilibili:BV00{i}", rel=0.95), object()) for i in range(8)]
    negatives = [_item(f"bilibili:BV1{i:02d}", rel=0.1) for i in range(40)]
    units = build_units(
        positives, negatives, n_units=8, positives_per_unit=3, unit_size=12, seed=11
    )
    result = run_offline_eval(units, k=6, seed=11, include_random_baseline=True)
    eng_ndcg = result["methods"]["engine"]["metrics"]["ndcg@6"]["mean"]
    rnd_ndcg = result["methods"]["random"]["metrics"]["ndcg@6"]["mean"]
    assert eng_ndcg > rnd_ndcg


def test_run_offline_eval_without_random():
    positives = [(_item(f"bilibili:BV00{i}", rel=0.9), object()) for i in range(6)]
    negatives = [_item(f"bilibili:BV1{i:02d}", rel=0.2) for i in range(30)]
    units = build_units(positives, negatives, n_units=3, positives_per_unit=2, unit_size=8, seed=5)
    result = run_offline_eval(units, k=4, seed=5, include_random_baseline=False)
    assert set(result["methods"].keys()) == {"engine"}


# ---------------------------------------------------------------------------
# LLM rerank offline eval (async)
# ---------------------------------------------------------------------------


class _MockLLMServiceForEval:
    """Mock LLM service that returns canned rerank scores."""

    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls: int = 0

    async def complete_structured_task(self, **kwargs: object) -> SimpleNamespace:
        self.calls += 1
        if self.should_fail:
            raise RuntimeError("mock LLM failure")
        # Return scores that boost positives (high relevance) and suppress negatives
        return SimpleNamespace(
            content=(
                '[{"content_id":"BV000","score":0.95},'
                '{"content_id":"BV001","score":0.92},'
                '{"content_id":"BV100","score":0.1},'
                '{"content_id":"BV101","score":0.15},'
                '{"content_id":"BV102","score":0.05}]'
            )
        )


def _make_eval_profile() -> SimpleNamespace:
    return SimpleNamespace(
        personality_portrait="测试用户画像",
        core_traits=["测试"],
        deep_needs=["测试需求"],
        preferences=SimpleNamespace(exploration_openness=0.5),
        top_interests=[{"domain": "测试", "weight": 0.9}],
        current_focus="测试焦点",
        recent_topics=["测试1", "测试2"],
    )


@pytest.mark.asyncio
async def test_run_offline_eval_async_includes_llm_rerank_method():
    positives = [(_item(f"bilibili:BV00{i}", rel=0.9), object()) for i in range(4)]
    negatives = [_item(f"bilibili:BV1{i:02d}", rel=0.2) for i in range(20)]
    units = build_units(positives, negatives, n_units=2, positives_per_unit=2, unit_size=8, seed=7)
    result = await run_offline_eval_async(
        units,
        k=4,
        seed=7,
        include_random_baseline=True,
        llm_service=_MockLLMServiceForEval(),
        profile=_make_eval_profile(),
    )
    assert set(result["methods"].keys()) == {"engine", "random", "engine+llm_rerank"}
    for method in result["methods"].values():
        assert method["n_units"] == 2


@pytest.mark.asyncio
async def test_run_offline_eval_async_without_llm_service_skips_rerank():
    positives = [(_item(f"bilibili:BV00{i}", rel=0.9), object()) for i in range(4)]
    negatives = [_item(f"bilibili:BV1{i:02d}", rel=0.2) for i in range(20)]
    units = build_units(positives, negatives, n_units=2, positives_per_unit=2, unit_size=8, seed=7)
    result = await run_offline_eval_async(
        units,
        k=4,
        seed=7,
        include_random_baseline=True,
        llm_service=None,
        profile=_make_eval_profile(),
    )
    assert set(result["methods"].keys()) == {"engine", "random"}


@pytest.mark.asyncio
async def test_run_offline_eval_async_llm_failure_falls_back_gracefully():
    positives = [(_item(f"bilibili:BV00{i}", rel=0.9), object()) for i in range(4)]
    negatives = [_item(f"bilibili:BV1{i:02d}", rel=0.2) for i in range(20)]
    units = build_units(positives, negatives, n_units=2, positives_per_unit=2, unit_size=8, seed=7)
    # LLM fails → should still complete, engine+llm_rerank falls back to relevance scores
    result = await run_offline_eval_async(
        units,
        k=4,
        seed=7,
        include_random_baseline=False,
        llm_service=_MockLLMServiceForEval(should_fail=True),
        profile=_make_eval_profile(),
    )
    assert set(result["methods"].keys()) == {"engine", "engine+llm_rerank"}
    # Both methods should have valid metrics (no crash)
    for method in result["methods"].values():
        assert method["n_units"] == 2
        assert "ndcg@4" in method["metrics"]


@pytest.mark.asyncio
async def test_llm_rerank_can_improve_or_match_engine_ndcg():
    """LLM rerank with good scores should not hurt NDCG vs pure engine."""
    positives = [(_item(f"bilibili:BV00{i}", rel=0.95), object()) for i in range(6)]
    negatives = [_item(f"bilibili:BV1{i:02d}", rel=0.1) for i in range(30)]
    units = build_units(
        positives, negatives, n_units=4, positives_per_unit=2, unit_size=10, seed=13
    )
    result = await run_offline_eval_async(
        units,
        k=5,
        seed=13,
        include_random_baseline=False,
        llm_service=_MockLLMServiceForEval(),
        profile=_make_eval_profile(),
    )
    eng_ndcg = result["methods"]["engine"]["metrics"]["ndcg@5"]["mean"]
    llm_ndcg = result["methods"]["engine+llm_rerank"]["metrics"]["ndcg@5"]["mean"]
    # LLM rerank should not significantly hurt (within tolerance); with good
    # mock scores it may match or slightly improve
    assert llm_ndcg >= eng_ndcg - 0.1

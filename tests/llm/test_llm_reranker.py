"""Tests for the LLM semantic reranker module.

Covers:
- blend_scores (curator + LLM weighted fusion)
- _extract_rerank_entries (LLM JSON response parsing)
- _build_rerank_profile_summary (profile compression)
- LLMReranker.rerank (full pipeline with mock LLM service)
- Failure degradation (LLM error → empty dict → pure curator)
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from obc_discovery.engine import DiscoveredContent

from openbiliclaw.recommendation.llm_reranker import (
    LLMReranker,
    _build_rerank_profile_summary,
    _extract_rerank_entries,
    blend_scores,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_candidate(**overrides: object) -> DiscoveredContent:
    defaults = dict(
        bvid="BV1TEST",
        content_id="BV1TEST",
        title="复杂系统的底层逻辑",
        up_name="系统观察者",
        author_name="系统观察者",
        up_mid=12345,
        duration=600,
        description="从控制论到信息论，一次讲透复杂系统的核心原理",
        cover_url="https://example.com/cover.jpg",
        view_count=50000,
        like_count=3000,
        tags=["科普", "系统论"],
        topic_key="复杂系统",
        topic_group="科学方法",
        style_key="deep_dive",
        source_strategy="explore",
        source_platform="bilibili",
        relevance_score=0.85,
        relevance_reason="deep resonance",
        pool_expression="",
        pool_topic_label="",
        candidate_tier="primary",
        discovered_at="2026-04-08T12:00:00",
        last_scored_at="2026-04-08T12:00:00",
    )
    defaults.update(overrides)
    return DiscoveredContent(**defaults)


def _make_profile(**overrides: object) -> SimpleNamespace:
    prefs = SimpleNamespace(
        interests=[],
        exploration_openness=overrides.pop("exploration_openness", 0.6),
    )
    defaults = dict(
        personality_portrait="你会反复追问问题背后的结构。",
        core_traits=["深究", "克制"],
        deep_needs=["对事物运作原理的深层理解"],
        active_insights=[
            SimpleNamespace(
                hypothesis="这个人在试图理解复杂系统如何自组织",
                confidence=0.8,
            ),
        ],
        preferences=prefs,
        top_interests=[
            {"domain": "AI/机器学习", "weight": 0.9},
            {"domain": "系统科学", "weight": 0.7},
        ],
        current_focus="准备百度广告岗面试",
        recent_topics=["推荐系统", "广告算法", "LLM精排"],
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class _MockLLMService:
    """Mock LLM service that returns canned responses."""

    def __init__(self, response: str = "", should_fail: bool = False) -> None:
        self.response = response
        self.should_fail = should_fail
        self.calls: list[dict[str, Any]] = []

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        max_tokens: int = 2048,
        caller: str = "",
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        inject_core_memory: bool = True,
        **kwargs: Any,
    ) -> Any:
        self.calls.append(
            {
                "system_instruction": system_instruction,
                "user_input": user_input,
                "max_tokens": max_tokens,
                "caller": caller,
            }
        )
        if self.should_fail:
            raise RuntimeError("mock LLM failure")
        return SimpleNamespace(content=self.response)


# ---------------------------------------------------------------------------
# blend_scores
# ---------------------------------------------------------------------------


class TestBlendScores:
    def test_empty_llm_scores_returns_curator_copy(self) -> None:
        curator = {"a": 0.8, "b": 0.6}
        result = blend_scores(curator_scores=curator, llm_scores={}, weight=0.3)
        assert result == curator
        assert result is not curator  # copy, not reference

    def test_zero_weight_returns_curator(self) -> None:
        curator = {"a": 0.8, "b": 0.6}
        llm = {"a": 0.2, "b": 0.9}
        result = blend_scores(curator_scores=curator, llm_scores=llm, weight=0.0)
        assert result == curator

    def test_full_weight_returns_llm(self) -> None:
        curator = {"a": 0.8, "b": 0.6}
        llm = {"a": 0.2, "b": 0.9}
        result = blend_scores(curator_scores=curator, llm_scores=llm, weight=1.0)
        assert result == llm

    def test_blend_weighted_average(self) -> None:
        curator = {"a": 0.8}
        llm = {"a": 0.4}
        result = blend_scores(curator_scores=curator, llm_scores=llm, weight=0.3)
        # 0.7 * 0.8 + 0.3 * 0.4 = 0.56 + 0.12 = 0.68
        assert abs(result["a"] - 0.68) < 0.001

    def test_candidate_only_in_curator_keeps_curator_score(self) -> None:
        curator = {"a": 0.8, "b": 0.6}
        llm = {"a": 0.4}  # b missing
        result = blend_scores(curator_scores=curator, llm_scores=llm, weight=0.3)
        assert result["b"] == 0.6  # unchanged

    def test_candidate_only_in_llm_uses_llm_score(self) -> None:
        curator = {"a": 0.8}
        llm = {"a": 0.4, "b": 0.7}  # b only in llm
        result = blend_scores(curator_scores=curator, llm_scores=llm, weight=0.3)
        assert result["b"] == 0.7

    def test_scores_clamped_to_0_1(self) -> None:
        curator = {"a": 1.5}  # out of range
        llm = {"a": -0.5}  # out of range
        result = blend_scores(curator_scores=curator, llm_scores=llm, weight=0.5)
        assert 0.0 <= result["a"] <= 1.0

    def test_empty_both_returns_empty(self) -> None:
        result = blend_scores(curator_scores={}, llm_scores={}, weight=0.3)
        assert result == {}


# ---------------------------------------------------------------------------
# _extract_rerank_entries
# ---------------------------------------------------------------------------


class TestExtractRerankEntries:
    def test_clean_json_array(self) -> None:
        content = (
            '[{"content_id":"BV1","score":0.8,"rationale":"good"},'
            '{"content_id":"BV2","score":0.5,"rationale":"ok"}]'
        )
        entries = _extract_rerank_entries(content, expected_count=2)
        assert len(entries) == 2
        assert entries[0]["content_id"] == "BV1"
        assert entries[0]["score"] == 0.8

    def test_wrapped_in_results_object(self) -> None:
        content = '{"results": [{"content_id":"BV1","score":0.8}]}'
        entries = _extract_rerank_entries(content, expected_count=1)
        assert len(entries) == 1
        assert entries[0]["content_id"] == "BV1"

    def test_wrapped_in_items_object(self) -> None:
        content = '{"items": [{"content_id":"BV1","score":0.8}]}'
        entries = _extract_rerank_entries(content, expected_count=1)
        assert len(entries) == 1

    def test_singleton_object(self) -> None:
        content = '{"content_id":"BV1","score":0.8,"rationale":"good"}'
        entries = _extract_rerank_entries(content, expected_count=1)
        assert len(entries) == 1
        assert entries[0]["content_id"] == "BV1"

    def test_empty_string_returns_empty(self) -> None:
        entries = _extract_rerank_entries("", expected_count=5)
        assert entries == []

    def test_invalid_json_returns_empty(self) -> None:
        entries = _extract_rerank_entries("not json at all", expected_count=5)
        assert entries == []

    def test_respects_expected_count(self) -> None:
        content = (
            '[{"content_id":"BV1","score":0.8},'
            '{"content_id":"BV2","score":0.7},'
            '{"content_id":"BV3","score":0.6}]'
        )
        entries = _extract_rerank_entries(content, expected_count=2)
        assert len(entries) == 2

    def test_zero_expected_count_returns_all(self) -> None:
        content = '[{"content_id":"BV1","score":0.8},{"content_id":"BV2","score":0.7}]'
        entries = _extract_rerank_entries(content, expected_count=0)
        assert len(entries) == 2

    def test_entries_missing_content_id_filtered_out(self) -> None:
        # Entries missing content_id are filtered out before truncation so
        # they don't consume the expected_count quota.
        content = '[{"score":0.8},{"content_id":"BV2","score":0.7}]'
        entries = _extract_rerank_entries(content, expected_count=2)
        assert len(entries) == 1
        assert entries[0]["content_id"] == "BV2"


# ---------------------------------------------------------------------------
# _build_rerank_profile_summary
# ---------------------------------------------------------------------------


class TestBuildRerankProfileSummary:
    def test_full_profile(self) -> None:
        profile = _make_profile()
        summary = _build_rerank_profile_summary(profile)
        assert "top_interests" in summary
        assert len(summary["top_interests"]) == 2
        assert summary["top_interests"][0]["domain"] == "AI/机器学习"
        assert "current_focus" in summary
        assert "recent_topics" in summary
        assert "exploration_openness" in summary

    def test_empty_profile(self) -> None:
        profile = SimpleNamespace()
        summary = _build_rerank_profile_summary(profile)
        assert summary == {}

    def test_profile_with_dict_interests(self) -> None:
        profile = SimpleNamespace(
            top_interests=[{"domain": "AI", "weight": 0.9}, {"name": "ML", "weight": 0.8}],
        )
        summary = _build_rerank_profile_summary(profile)
        assert len(summary["top_interests"]) == 2
        assert summary["top_interests"][1]["domain"] == "ML"  # falls back to "name"

    def test_profile_with_object_interests(self) -> None:
        profile = SimpleNamespace(
            top_interests=[SimpleNamespace(domain="AI", weight=0.9)],
        )
        summary = _build_rerank_profile_summary(profile)
        assert summary["top_interests"][0]["domain"] == "AI"

    def test_active_goals_list(self) -> None:
        profile = SimpleNamespace(active_goals=["学SQL", "刷LeetCode"])
        summary = _build_rerank_profile_summary(profile)
        assert "active_goals" in summary
        assert len(summary["active_goals"]) == 2

    def test_active_goals_single_string(self) -> None:
        profile = SimpleNamespace(active_goals="学SQL")
        summary = _build_rerank_profile_summary(profile)
        assert summary["active_goals"] == ["学SQL"]

    def test_recent_topics_truncated(self) -> None:
        profile = SimpleNamespace(recent_topics=["a" * 100, "b" * 100] * 10)
        summary = _build_rerank_profile_summary(profile)
        assert len(summary["recent_topics"]) <= 10
        assert all(len(t) <= 50 for t in summary["recent_topics"])


# ---------------------------------------------------------------------------
# LLMReranker — initialization
# ---------------------------------------------------------------------------


class TestLLMRerankerInit:
    def test_defaults(self) -> None:
        reranker = LLMReranker(llm_service=_MockLLMService())
        assert reranker.batch_size == 5
        assert reranker.top_k == 30
        assert reranker.weight == 0.3

    def test_custom_params(self) -> None:
        reranker = LLMReranker(
            llm_service=_MockLLMService(),
            batch_size=10,
            top_k=50,
            weight=0.5,
        )
        assert reranker.batch_size == 10
        assert reranker.top_k == 50
        assert reranker.weight == 0.5

    def test_batch_size_clamped_to_min_1(self) -> None:
        reranker = LLMReranker(llm_service=_MockLLMService(), batch_size=0)
        assert reranker.batch_size == 1

    def test_top_k_clamped_to_min_1(self) -> None:
        reranker = LLMReranker(llm_service=_MockLLMService(), top_k=0)
        assert reranker.top_k == 1

    def test_weight_clamped_to_0_1(self) -> None:
        reranker = LLMReranker(llm_service=_MockLLMService(), weight=1.5)
        assert reranker.weight == 1.0
        reranker2 = LLMReranker(llm_service=_MockLLMService(), weight=-0.5)
        assert reranker2.weight == 0.0


# ---------------------------------------------------------------------------
# LLMReranker.rerank — with mock LLM
# ---------------------------------------------------------------------------


class TestLLMRerankerRerank:
    @pytest.mark.asyncio
    async def test_empty_candidates_returns_empty(self) -> None:
        reranker = LLMReranker(llm_service=_MockLLMService())
        result = await reranker.rerank(candidates=[], profile=_make_profile())
        assert result == {}

    @pytest.mark.asyncio
    async def test_successful_rerank(self) -> None:
        llm_response = (
            '[{"content_id":"BV1","score":0.85,"rationale":"highly relevant"},'
            '{"content_id":"BV2","score":0.45,"rationale":"moderate"}]'
        )
        mock_llm = _MockLLMService(response=llm_response)
        reranker = LLMReranker(llm_service=mock_llm, batch_size=5)

        candidates = [
            _make_candidate(content_id="BV1", bvid="BV1", title="AI推荐系统", relevance_score=0.9),
            _make_candidate(content_id="BV2", bvid="BV2", title="无关内容", relevance_score=0.3),
        ]
        result = await reranker.rerank(candidates=candidates, profile=_make_profile())

        assert len(result) == 2
        assert result["BV1"] == 0.85
        assert result["BV2"] == 0.45
        assert len(mock_llm.calls) == 1  # one batch for 2 candidates

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty(self) -> None:
        mock_llm = _MockLLMService(should_fail=True)
        reranker = LLMReranker(llm_service=mock_llm)

        candidates = [_make_candidate(content_id="BV1", bvid="BV1")]
        result = await reranker.rerank(candidates=candidates, profile=_make_profile())

        assert result == {}  # silent degradation

    @pytest.mark.asyncio
    async def test_llm_returns_invalid_json_returns_empty(self) -> None:
        mock_llm = _MockLLMService(response="not valid json")
        reranker = LLMReranker(llm_service=mock_llm)

        candidates = [_make_candidate(content_id="BV1", bvid="BV1")]
        result = await reranker.rerank(candidates=candidates, profile=_make_profile())

        assert result == {}

    @pytest.mark.asyncio
    async def test_top_k_limits_candidates_sent(self) -> None:
        # Return scores for all candidates
        llm_response = (
            "["
            + ",".join(
                f'{{"content_id":"BV{i}","score":{0.5 + i * 0.01},"rationale":"ok"}}'
                for i in range(1, 6)
            )
            + "]"
        )
        mock_llm = _MockLLMService(response=llm_response)
        reranker = LLMReranker(llm_service=mock_llm, top_k=3, batch_size=5)

        candidates = [
            _make_candidate(content_id=f"BV{i}", bvid=f"BV{i}", relevance_score=0.9 - i * 0.1)
            for i in range(1, 6)
        ]
        result = await reranker.rerank(candidates=candidates, profile=_make_profile())

        # Only top 3 by relevance_score should be sent to LLM
        # relevance scores: BV1=0.9, BV2=0.8, BV3=0.7, BV4=0.6, BV5=0.5
        # top 3: BV1, BV2, BV3
        assert set(result.keys()) == {"BV1", "BV2", "BV3"}

    @pytest.mark.asyncio
    async def test_curator_scores_drive_top_k_selection(self) -> None:
        llm_response = '[{"content_id":"BV_LOW","score":0.9,"rationale":"LLM likes it"}]'
        mock_llm = _MockLLMService(response=llm_response)
        reranker = LLMReranker(llm_service=mock_llm, top_k=1, batch_size=5)

        candidates = [
            _make_candidate(content_id="BV_HIGH", bvid="BV_HIGH", relevance_score=0.2),
            _make_candidate(content_id="BV_LOW", bvid="BV_LOW", relevance_score=0.9),
        ]
        # curator_scores says BV_HIGH is better (0.8), even though relevance_score says BV_LOW
        curator_scores = {"BV_HIGH": 0.8, "BV_LOW": 0.3}

        result = await reranker.rerank(
            candidates=candidates,
            profile=_make_profile(),
            curator_scores=curator_scores,
        )

        # top_k=1, selected by curator_scores → BV_HIGH should be sent
        # But LLM returned BV_LOW (mismatch), so result should be empty for BV_HIGH
        # Actually the LLM response has BV_LOW, which wasn't in the top-1 selected,
        # so it should be filtered out. Result should be empty.
        assert "BV_HIGH" not in result or result.get("BV_HIGH") is None

    @pytest.mark.asyncio
    async def test_batched_calls(self) -> None:
        # 7 candidates, batch_size=3 → 3 batches
        llm_response = (
            "["
            + ",".join(
                f'{{"content_id":"BV{i}","score":0.7,"rationale":"ok"}}' for i in range(1, 4)
            )
            + "]"
        )
        mock_llm = _MockLLMService(response=llm_response)
        reranker = LLMReranker(llm_service=mock_llm, batch_size=3, top_k=10)

        candidates = [
            _make_candidate(content_id=f"BV{i}", bvid=f"BV{i}", relevance_score=0.8)
            for i in range(1, 8)
        ]
        await reranker.rerank(candidates=candidates, profile=_make_profile())

        # 7 candidates / batch_size 3 = ceil(7/3) = 3 batches
        assert len(mock_llm.calls) == 3

    @pytest.mark.asyncio
    async def test_scores_clamped_to_0_1(self) -> None:
        llm_response = (
            '[{"content_id":"BV1","score":1.5,"rationale":"too high"},'
            '{"content_id":"BV2","score":-0.5,"rationale":"too low"}]'
        )
        mock_llm = _MockLLMService(response=llm_response)
        reranker = LLMReranker(llm_service=mock_llm)

        candidates = [
            _make_candidate(content_id="BV1", bvid="BV1"),
            _make_candidate(content_id="BV2", bvid="BV2"),
        ]
        result = await reranker.rerank(candidates=candidates, profile=_make_profile())

        assert result["BV1"] == 1.0
        assert result["BV2"] == 0.0

    @pytest.mark.asyncio
    async def test_entries_missing_content_id_skipped(self) -> None:
        llm_response = '[{"score":0.8},{"content_id":"BV2","score":0.7}]'
        mock_llm = _MockLLMService(response=llm_response)
        reranker = LLMReranker(llm_service=mock_llm)

        candidates = [_make_candidate(content_id="BV2", bvid="BV2")]
        result = await reranker.rerank(candidates=candidates, profile=_make_profile())

        assert "BV2" in result
        assert len(result) == 1  # first entry (no content_id) skipped

    @pytest.mark.asyncio
    async def test_invalid_score_value_skipped(self) -> None:
        llm_response = (
            '[{"content_id":"BV1","score":"not_a_number"},{"content_id":"BV2","score":0.7}]'
        )
        mock_llm = _MockLLMService(response=llm_response)
        reranker = LLMReranker(llm_service=mock_llm)

        candidates = [
            _make_candidate(content_id="BV1", bvid="BV1"),
            _make_candidate(content_id="BV2", bvid="BV2"),
        ]
        result = await reranker.rerank(candidates=candidates, profile=_make_profile())

        assert "BV1" not in result
        assert result["BV2"] == 0.7

    @pytest.mark.asyncio
    async def test_custom_top_k_override(self) -> None:
        llm_response = '[{"content_id":"BV1","score":0.8}]'
        mock_llm = _MockLLMService(response=llm_response)
        reranker = LLMReranker(llm_service=mock_llm, top_k=10)

        candidates = [_make_candidate(content_id="BV1", bvid="BV1")]
        # Override top_k to 1
        result = await reranker.rerank(candidates=candidates, profile=_make_profile(), top_k=1)
        assert result["BV1"] == 0.8

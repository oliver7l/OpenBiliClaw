"""LLM-based semantic reranker for recommendation candidates.

This module inserts an LLM semantic reranking layer between the curator
score and the diversity-selection step in the recommendation pipeline.

Why LLM reranking:
- The curator score is rule/feature-based (relevance, quality, recency,
  Thompson sampling) and cannot capture deep semantic alignment between
  the user's current intent and the candidate's actual content.
- An LLM can read title + description + tags and judge "would this
  specific person, right now, genuinely want to click this?" in a way
  that cosine similarity and hand-tuned weights cannot.
- We only rerank the top-K candidates (default 30) to keep cost
  manageable, and blend with the curator score (default 30% LLM weight)
  so a bad LLM call cannot destroy ranking stability.

Design:
- Batch size 5 (same as LLMDelightScorer) — cache-friendly, amortises
  per-call HTTP overhead.
- Output: {content_id: float_score in [0, 1]}. Missing entries mean
  "LLM did not return a score for this candidate" — caller should fall
  back to the curator score for those.
- Failure mode: any LLM error (rate limit, parse failure, timeout)
  returns an empty dict silently. The caller then uses pure curator
  scores. This is intentional — LLM reranking is an enhancement, not a
  hard dependency.

Usage (in engine.serve()):
    if self._llm_reranker and self._config.scoring.llm_reranker_enabled:
        llm_scores = await self._llm_reranker.rerank(
            candidates=top_candidates,
            profile=profile,
            top_k=self._config.scoring.llm_reranker_top_k,
        )
        score_override = blend_scores(
            curator_scores=score_override,
            llm_scores=llm_scores,
            weight=self._config.scoring.llm_reranker_weight,
        )
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Protocol

from openbiliclaw.llm.generation import generate_structured
from openbiliclaw.llm.json_utils import extract_llm_json_list

logger = logging.getLogger(__name__)

# Default batch size for LLM reranking. 5 keeps each prompt small
# (cache-friendly, fast) while still amortising the per-call HTTP/
# handshake cost. With top_k=30, that's 6 batched calls per refresh.
_DEFAULT_RERANK_BATCH_SIZE: int = 5

# Default number of top candidates to send to the LLM for reranking.
# 30 is a sweet spot: enough to meaningfully reorder the diversity-
# selected batch (typically 10-20), but small enough to keep cost
# low (~6 LLM calls × ~¥0.01 = ¥0.06 per refresh cycle).
_DEFAULT_RERANK_TOP_K: int = 30

# Default weight for the LLM score in the blended final score.
# 0.3 means: final = 0.7 * curator + 0.3 * LLM. This keeps the
# curator's stability (relevance, quality, recency) as the anchor
# while letting the LLM shift ~30% of the ranking signal.
_DEFAULT_RERANK_WEIGHT: float = 0.3


class _SupportsStructuredLLM(Protocol):
    """Protocol for the LLM service — matches LLMDelightScorer's."""

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        max_tokens: int = ...,
        caller: str = ...,
    ) -> Any: ...


class _SupportsRerankCandidate(Protocol):
    """Minimal candidate interface for reranking.

    Matches the fields used from DiscoveredContent.
    """

    @property
    def content_id(self) -> str: ...

    @property
    def title(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def topic_group(self) -> str: ...

    @property
    def source_platform(self) -> str: ...

    @property
    def author_name(self) -> str: ...

    @property
    def relevance_score(self) -> float: ...

    @property
    def tags(self) -> list[str]: ...


# ── System prompt (static, cache-friendly) ────────────────────────────

_RERANK_SYSTEM_PROMPT = """\
你是一个个性化推荐系统的语义精排专家。你的任务是：给定用户画像和一组候选内容，判断每条内容对这个用户当前的真实吸引力。

<评分维度>
1. 语义相关性（40%）：内容主题与用户核心兴趣的匹配深度，不是关键词匹配，
而是"这个人真的会对这个话题感兴趣吗"。
2. 时效性与情境匹配（20%）：内容是否符合用户当前阶段的需求
（如正在准备面试、正在学习某技术、正在规划旅行）。
3. 内容质量与深度（20%）：内容是否有实质信息、独特视角、可操作价值，
而非标题党或浅层搬运。
4. 新鲜感与惊喜度（20%）：在用户兴趣范围内，但提供了用户可能没看过的
新角度、新案例或新框架。

<评分规则>
- score 范围 0.0 到 1.0，保留两位小数。
- 0.0-0.3：不相关或低质量，用户大概率不会点。
- 0.3-0.5：有点相关但吸引力一般，可作为备选。
- 0.5-0.7：明显相关且有一定吸引力，用户可能会点。
- 0.7-0.9：高度相关且质量高，用户很可能会点且觉得有价值。
- 0.9-1.0：完美匹配，用户几乎一定会点且会收藏/分享。
- 不要给所有候选都打高分，必须有区分度。同一批候选中至少要有 2 个低于 0.5。
- rationale 用中文，不超过 30 字，说明为什么给这个分数。

<输出格式>
严格输出 JSON 数组，每个元素包含 content_id、score、rationale 三个字段。
不要输出任何其他文字、解释或 Markdown 代码块标记。

示例：
[{"content_id":"BV1xx411c7mD","score":0.78,"rationale":"深度技术分析，匹配用户AI学习需求"},{"content_id":"BV2yy522d8nE","score":0.35,"rationale":"主题相关但内容浅层，吸引力一般"}]
"""


def _build_rerank_user_prompt(
    profile_summary: dict[str, object],
    content_batch: list[dict[str, object]],
) -> str:
    """Build the user prompt for a rerank batch.

    Uses the same XML-tag structure as build_delight_score_batch_prompt
    for consistency and cache-friendliness.
    """
    import json

    return "\n\n".join(
        [
            "<profile_summary>",
            json.dumps(profile_summary, ensure_ascii=False, indent=2, sort_keys=True),
            "</profile_summary>",
            "<content_batch>",
            json.dumps(content_batch, ensure_ascii=False, indent=2, sort_keys=True),
            "</content_batch>",
            "<instructions>",
            "请对上面 content_batch 中的每一条内容进行语义精排评分。",
            "严格按照 system prompt 中的评分维度和输出格式返回 JSON 数组。",
            "必须覆盖 content_batch 中的所有 content_id，不要遗漏。",
            "</instructions>",
        ]
    )


def _build_rerank_profile_summary(profile: Any) -> dict[str, object]:
    """Build a compact profile summary for the rerank prompt.

    Keeps only the fields the reranking rubric actually uses:
    - top interests (domain + weight)
    - current focus / active goals
    - recent reading topics
    - exploration openness
    """
    summary: dict[str, object] = {}

    # Try to extract top interests from various profile shapes
    top_interests: list[dict[str, object]] = []
    if hasattr(profile, "top_interests") and profile.top_interests:
        for interest in profile.top_interests[:8]:
            if isinstance(interest, dict):
                top_interests.append(
                    {
                        "domain": str(interest.get("domain", interest.get("name", ""))),
                        "weight": round(float(interest.get("weight", 0.0)), 3),
                    }
                )
            elif hasattr(interest, "domain"):
                top_interests.append(
                    {
                        "domain": str(getattr(interest, "domain", "")),
                        "weight": round(float(getattr(interest, "weight", 0.0)), 3),
                    }
                )
    if top_interests:
        summary["top_interests"] = top_interests

    # Current focus / active goals
    if hasattr(profile, "current_focus") and profile.current_focus:
        summary["current_focus"] = str(profile.current_focus)[:200]
    elif hasattr(profile, "active_goals") and profile.active_goals:
        goals = (
            profile.active_goals
            if isinstance(profile.active_goals, list)
            else [profile.active_goals]
        )
        summary["active_goals"] = [str(g)[:100] for g in goals[:5]]

    # Recent reading topics
    if hasattr(profile, "recent_topics") and profile.recent_topics:
        topics = (
            profile.recent_topics
            if isinstance(profile.recent_topics, list)
            else [profile.recent_topics]
        )
        summary["recent_topics"] = [str(t)[:50] for t in topics[:10]]

    # Exploration openness (may be at profile level or preferences level)
    if hasattr(profile, "exploration_openness"):
        summary["exploration_openness"] = round(float(profile.exploration_openness), 3)
    else:
        prefs = getattr(profile, "preferences", None)
        if prefs is not None and hasattr(prefs, "exploration_openness"):
            summary["exploration_openness"] = round(float(prefs.exploration_openness), 3)

    return summary


def _extract_rerank_entries(content: str, *, expected_count: int) -> list[dict[str, Any]]:
    """Extract a list of {content_id, score, rationale} from an LLM response.

    Delegates to the shared LLM JSON extractor so array snippets,
    wrappers, singleton entries, and JSONL are handled consistently.
    """
    text = content.strip()
    if not text:
        return []

    entries = extract_llm_json_list(
        text,
        wrapper_keys=(
            "results",
            "items",
            "scores",
            "candidates",
            "rerank",
            "reranking",
            "data",
            "output",
            "list",
            "array",
        ),
        allow_singleton=True,
        item_predicate=lambda item: "content_id" in item or "score" in item,
    )
    if not entries:
        return []
    result = [dict(item) for item in entries]
    # Filter out entries missing content_id BEFORE truncating, so invalid
    # entries don't consume the expected_count quota and drop valid ones.
    valid = [item for item in result if str(item.get("content_id", "")).strip()]
    return valid[:expected_count] if expected_count > 0 else valid


def _parse_rerank_entries(content: str, *, expected_count: int) -> list[dict[str, Any]] | None:
    """``parse`` adapter for :func:`generate_structured`.

    Returns ``None`` on an empty extraction so the wrapper treats a
    truncated / malformed rerank batch as a miss (and escalates),
    rather than accepting a silent zero-row result.
    """
    entries = _extract_rerank_entries(content, expected_count=expected_count)
    return entries or None


def blend_scores(
    *,
    curator_scores: dict[str, float],
    llm_scores: dict[str, float],
    weight: float = _DEFAULT_RERANK_WEIGHT,
) -> dict[str, float]:
    """Blend curator scores with LLM rerank scores.

    final = (1 - weight) * curator + weight * llm

    For candidates that have a curator score but no LLM score, the
    curator score is used as-is (LLM did not return a score for them,
    likely because they were outside top_k or the LLM omitted them).

    For candidates that have an LLM score but no curator score (should
    not happen in practice, but defensive), the LLM score is used.

    Args:
        curator_scores: Mapping content_id -> curator score [0, 1].
        llm_scores: Mapping content_id -> LLM rerank score [0, 1].
        weight: LLM score weight in [0, 1]. 0 = pure curator, 1 = pure LLM.

    Returns:
        Blended scores mapping content_id -> final score [0, 1].
    """
    if not llm_scores or weight <= 0.0:
        return dict(curator_scores)
    if weight >= 1.0:
        return dict(llm_scores)

    blended: dict[str, float] = {}
    all_ids = set(curator_scores.keys()) | set(llm_scores.keys())

    for content_id in all_ids:
        curator = curator_scores.get(content_id)
        llm = llm_scores.get(content_id)

        if curator is not None and llm is not None:
            blended[content_id] = max(0.0, min(1.0, (1.0 - weight) * curator + weight * llm))
        elif curator is not None:
            blended[content_id] = curator
        elif llm is not None:
            blended[content_id] = llm

    return blended


class LLMReranker:
    """LLM-based semantic reranker for recommendation candidates.

    Inserts between curator scoring and diversity selection. Only reranks
    the top-K candidates to keep cost manageable. Blends with curator
    score so a bad LLM call cannot destroy ranking stability.

    Cost estimate (top_k=30, batch_size=5):
    - 6 LLM calls per refresh cycle
    - ~¥0.01 per call = ~¥0.06 per cycle
    - ~¥0.48/day at 8 cycles (very affordable)

    Failure mode: any error returns empty dict → caller falls back to
    pure curator scores. LLM reranking is an enhancement, not a hard
    dependency.
    """

    def __init__(
        self,
        llm_service: _SupportsStructuredLLM,
        *,
        batch_size: int = _DEFAULT_RERANK_BATCH_SIZE,
        top_k: int = _DEFAULT_RERANK_TOP_K,
        weight: float = _DEFAULT_RERANK_WEIGHT,
    ) -> None:
        """Initialize the LLM reranker.

        Args:
            llm_service: LLM service implementing complete_structured_task.
            batch_size: Number of candidates per LLM call. Default 5.
            top_k: Only rerank the top-K candidates. Default 30.
            weight: LLM score weight in blended final score. Default 0.3.
        """
        self._llm_service = llm_service
        self._batch_size = max(1, batch_size)
        self._top_k = max(1, top_k)
        self._weight = max(0.0, min(1.0, weight))

    @property
    def batch_size(self) -> int:
        return self._batch_size

    @property
    def top_k(self) -> int:
        return self._top_k

    @property
    def weight(self) -> float:
        return self._weight

    async def rerank(
        self,
        candidates: list[Any],
        profile: Any,
        *,
        top_k: int | None = None,
        curator_scores: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """Rerank candidates using LLM semantic scoring.

        Args:
            candidates: List of candidate objects (DiscoveredContent or
                compatible). Must have content_id, title, description,
                topic_group, source_platform, author_name, relevance_score,
                tags attributes.
            profile: User profile object. Used to build the profile summary.
            top_k: Override the default top_k. Only the top-K candidates
                (sorted by curator score or relevance_score) are sent to
                the LLM.
            curator_scores: Optional pre-computed curator scores mapping
                content_id -> score. Used to select which candidates to
                rerank (top-K by curator score). If not provided,
                candidates are sorted by relevance_score.

        Returns:
            Mapping content_id -> LLM score [0, 1]. Empty dict on any
            failure (caller should fall back to curator scores).
        """
        if not candidates:
            return {}

        effective_top_k = top_k or self._top_k

        # Select top-K candidates to rerank
        if curator_scores:
            scored = [
                (c, curator_scores.get(getattr(c, "content_id", "") or getattr(c, "bvid", ""), 0.0))
                for c in candidates
            ]
            scored.sort(key=lambda x: x[1], reverse=True)
            top_candidates = [c for c, _ in scored[:effective_top_k]]
        else:
            sorted_candidates = sorted(
                candidates,
                key=lambda c: float(getattr(c, "relevance_score", 0.0) or 0.0),
                reverse=True,
            )
            top_candidates = sorted_candidates[:effective_top_k]

        if not top_candidates:
            return {}

        # Build profile summary
        profile_summary = _build_rerank_profile_summary(profile)

        results: dict[str, float] = {}

        # Batch LLM calls
        for batch_start in range(0, len(top_candidates), self._batch_size):
            batch = top_candidates[batch_start : batch_start + self._batch_size]
            content_batch = [
                {
                    "content_id": getattr(c, "content_id", "") or getattr(c, "bvid", ""),
                    "title": (getattr(c, "title", "") or "")[:140],
                    "description": (getattr(c, "description", "") or "")[:400],
                    "topic_group": getattr(c, "topic_group", "") or "",
                    "source_platform": getattr(c, "source_platform", "") or "",
                    "author_name": getattr(c, "author_name", "") or getattr(c, "up_name", "") or "",
                    "relevance_score": round(float(getattr(c, "relevance_score", 0.0) or 0.0), 3),
                    "tags": [str(t) for t in (getattr(c, "tags", []) or [])][:8],
                }
                for c in batch
            ]

            user_prompt = _build_rerank_user_prompt(profile_summary, content_batch)

            try:
                entries = (
                    await generate_structured(
                        self._llm_service,
                        system_instruction=_RERANK_SYSTEM_PROMPT,
                        user_input=user_prompt,
                        parse=functools.partial(_parse_rerank_entries, expected_count=len(batch)),
                        caller="recommendation.llm_rerank",
                        max_tokens=2048,
                        label="llm_rerank",
                    )
                    or []
                )
            except Exception as exc:
                # Any LLM error → log and continue with partial results
                logger.warning(
                    "LLM rerank batch failed (batch_start=%d, batch_size=%d): %s",
                    batch_start,
                    len(batch),
                    exc,
                )
                continue

            if not entries:
                logger.warning(
                    "LLM rerank batch produced 0 parseable entries for %d candidates",
                    len(batch),
                )
                continue

            for entry in entries:
                content_id = str(entry.get("content_id", "")).strip()
                if not content_id:
                    continue
                try:
                    score = max(0.0, min(1.0, float(entry.get("score", 0.0) or 0.0)))
                except (TypeError, ValueError):
                    continue
                results[content_id] = score

        return results

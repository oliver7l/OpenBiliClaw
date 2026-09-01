"""Quality Scorer -- LLM-based content quality evaluation and recommendation reason.

Scores recommendation candidates on three dimensions:
1. Interest match (how well it matches the user's profile)
2. Information density (how much useful info the content provides)
3. Content quality (overall quality of the content)

Generates a composite quality score (0.0-1.0) and a one-sentence
recommendation reason explaining why this content is recommended.

The quality score is used in re-ranking:
    final_score = 0.6 * current_score + 0.4 * quality_score
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from openbiliclaw.llm.service import LLMService

logger = logging.getLogger(__name__)

# Default batch size for LLM quality scoring
_QUALITY_LLM_BATCH_SIZE: int = 10

# Quality score threshold -- items below this won't get a quality boost
_QUALITY_SCORE_THRESHOLD: float = 0.3


class SupportsQualityCandidate(Protocol):
    """Minimal interface expected by the quality scorer."""

    bvid: str
    title: str
    description: str
    up_name: str
    source_platform: str
    topic_key: str
    relevance_score: float
    content_type: str
    body_text: str


# ---- Prompt Template ----------------------------------------------------

_QUALITY_BATCH_SCORE_SYSTEM_PROMPT: str = (
    "<task>\n"
    "你是阅读推荐质量评审员。根据用户画像和候选内容,"
    "从三个维度逐条评分,并生成一句推荐理由。\n"
    "</task>\n\n"
    "<dimensions>\n"
    "1. interest_match (0.0-1.0): 内容与用户兴趣画像的匹配度。"
    "用户明确喜欢的领域给高分,用户不感兴趣的领域给低分。\n"
    "2. information_density (0.0-1.0): 内容的信息密度。"
    "有干货、有深度、有信息增量的内容给高分;纯娱乐、水内容给低分。\n"
    "3. content_quality (0.0-1.0): 内容整体质量。"
    "制作精良、逻辑清晰、有价值的内容给高分;粗糙、标题党、低质内容给低分。\n"
    "</dimensions>\n\n"
    "<rules>\n"
    "1. 输出必须是严格 JSON 数组,数组长度与输入数量一致,顺序一一对应。\n"
    "2. 每项必须包含: bvid, quality_score(三项的平均值,0.0-1.0), "
    "interest_match, information_density, content_quality, "
    "reason(10-30字中文推荐理由,解释为什么推荐这篇)。\n"
    "3. 推荐理由要具体,引用内容细节,不要写空话套话。\n"
    '4. 如果内容明显不符合用户兴趣,quality_score 给 0.0-0.3,reason 写不推荐原因。\n'
    "5. 如果用户画像信息不足,基于内容本身质量评分,quality_score 不超过 0.7。\n"
    "</rules>\n\n"
    "<output_schema>\n"
    "[\n"
    '  {"bvid":"BV1xxx","quality_score":0.85,"interest_match":0.9,'
    '"information_density":0.8,"content_quality":0.85,'
    '"reason":"深入拆解了XX机制,和你之前关注的YY方向很契合"},\n'
    '  {"bvid":"BV2xxx","quality_score":0.45,"interest_match":0.3,'
    '"information_density":0.6,"content_quality":0.5,'
    '"reason":"娱乐向内容,信息密度一般,与你兴趣相关性不高"}\n'
    "]\n"
    "</output_schema>"
)


def _profile_summary_for_quality(profile: Any) -> dict[str, object]:
    """Extract a compact profile summary for the quality scoring prompt."""
    summary: dict[str, object] = {}
    try:
        if hasattr(profile, "interest") and profile.interest:
            interest = profile.interest
            if hasattr(interest, "likes") and interest.likes:
                summary["likes"] = [
                    {"domain": d.domain, "specifics": [s.specific for s in (d.specifics or [])]}
                    for d in interest.likes[:5]
                ]
            if hasattr(interest, "dislikes") and interest.dislikes:
                summary["dislikes"] = [
                    d.domain for d in interest.dislikes[:3]
                ]
        if hasattr(profile, "personality_portrait") and profile.personality_portrait:
            summary["portrait"] = profile.personality_portrait[:200]
    except Exception:
        pass
    return summary


def _build_quality_batch_prompt(
    *,
    profile_summary: dict[str, object],
    content_batch: list[dict[str, object]],
) -> list[dict[str, str]]:
    """Build a prompt for batch-scoring quality via LLM."""
    user_prompt = "\n\n".join(
        [
            "<profile_summary>",
            json.dumps(profile_summary, ensure_ascii=False, indent=2, sort_keys=True),
            "</profile_summary>",
            "<content_batch>",
            json.dumps(content_batch, ensure_ascii=False, indent=2, sort_keys=True),
            "</content_batch>",
        ]
    )
    return [
        {"role": "system", "content": _QUALITY_BATCH_SCORE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


class QualityScorer:
    """LLM-based quality scoring for recommendation candidates.

    Scores each candidate on interest_match, information_density,
    content_quality, and generates a composite quality_score + reason.
    """

    def __init__(
        self,
        llm_service: Any,
        *,
        batch_size: int = _QUALITY_LLM_BATCH_SIZE,
    ) -> None:
        self._llm_service = llm_service
        self._batch_size = max(1, batch_size)

    async def score_batch(
        self,
        candidates: list[SupportsQualityCandidate],
        profile: Any,
    ) -> dict[str, dict[str, Any]]:
        """Score a batch of candidates.

        Returns a dict mapping bvid -> {
            "quality_score": float,
            "interest_match": float,
            "information_density": float,
            "content_quality": float,
            "reason": str,
        }
        """
        if not candidates:
            return {}

        profile_summary = _profile_summary_for_quality(profile)
        all_results: dict[str, dict[str, Any]] = {}

        for i in range(0, len(candidates), self._batch_size):
            batch = candidates[i : i + self._batch_size]
            content_batch = [
                {
                    "bvid": c.bvid,
                    "title": c.title,
                    "description": (c.description or "")[:300],
                    "up_name": c.up_name or "",
                    "source_platform": c.source_platform or "",
                    "topic_key": c.topic_key or "",
                    "relevance_score": getattr(c, "relevance_score", 0.0),
                    "content_type": getattr(c, "content_type", "video"),
                }
                for c in batch
            ]

            messages = _build_quality_batch_prompt(
                profile_summary=profile_summary,
                content_batch=content_batch,
            )

            try:
                response = await self._llm_service.complete_with_core_memory(
                    system_instruction=messages[0]["content"],
                    user_input=messages[1]["content"],
                    temperature=0.3,
                    max_tokens=2048,
                    json_mode=False,
                    caller="recommendation.quality_score",
                    inject_core_memory=False,
                )

                raw = response.content.strip()
                if not raw:
                    raise ValueError("Empty LLM response")

                # Try to extract JSON array from the response
                # Some providers wrap JSON in markdown code blocks
                json_str = raw
                if "```json" in raw:
                    json_str = raw.split("```json")[1]
                    if "```" in json_str:
                        json_str = json_str.split("```")[0]
                elif "```" in raw:
                    json_str = raw.split("```")[1]
                    if "```" in json_str:
                        json_str = json_str.split("```")[0]
                json_str = json_str.strip()

                parsed = json.loads(json_str)
                for item in parsed:
                    bvid = item.get("bvid", "")
                    if bvid:
                        all_results[bvid] = {
                            "quality_score": max(0.0, min(1.0, float(item.get("quality_score", 0.0)))),
                            "interest_match": max(0.0, min(1.0, float(item.get("interest_match", 0.0)))),
                            "information_density": max(0.0, min(1.0, float(item.get("information_density", 0.0)))),
                            "content_quality": max(0.0, min(1.0, float(item.get("content_quality", 0.0)))),
                            "reason": str(item.get("reason", "")),
                        }
            except Exception as exc:
                logger.warning("Quality scoring batch failed for %d items: %s", len(batch), exc)
                # Log the raw response for debugging (first 200 chars)
                try:
                    raw_preview = response.content[:200]
                except Exception:
                    raw_preview = "<no response>"
                logger.warning("Raw LLM response preview: %s", raw_preview)
                # Fallback: assign default scores
                for c in batch:
                    all_results[c.bvid] = {
                        "quality_score": 0.5,
                        "interest_match": 0.5,
                        "information_density": 0.5,
                        "content_quality": 0.5,
                        "reason": "",
                    }

        return all_results
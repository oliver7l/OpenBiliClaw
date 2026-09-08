"""Conversational recommendation — generative recommendation, step 2.

Lets the user talk to the recommendation engine in natural language:
    "给我推荐几个周末看的技术视频"
    "有没有更短一点的？"
    "再讲讲第二个为什么推荐"
    "换一批"

Pipeline:
    user message → parse_chat_intent() → RecommendationEngine.serve()
    → generate_chat_response() → natural language reply

Design notes:
- Intent parsing uses LLM (structured output) for robustness, with a
  keyword-based fallback when LLM is unavailable.
- Session state is lightweight: last recommendations + turn count +
  accumulated filters. No persistent storage — caller decides persistence.
- Response generation is LLM-driven but always includes the actual
  recommendation data (title, url, expression) so it never hallucinates
  content that doesn't exist.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("recommendation.chat")


# ---------------------------------------------------------------------------
# Intent data structure
# ---------------------------------------------------------------------------


@dataclass
class ChatIntent:
    """Parsed intent from a user's chat message.

    Attributes:
        intent_type: One of ``recommend``, ``more``, ``explain``,
            ``filter``, ``refresh``, ``greeting``, ``other``.
        keywords: Topic / interest keywords extracted from the message.
        platform: Target platform filter (bilibili / xiaohongshu / None).
        limit: Requested number of recommendations (None = default).
        explain_index: 0-based index of the recommendation to explain
            (only for ``explain`` intent).
        raw_message: The original user message (for logging / fallback).

    """

    intent_type: str = "recommend"
    keywords: list[str] = field(default_factory=list)
    platform: str | None = None
    limit: int | None = None
    explain_index: int | None = None
    raw_message: str = ""

    def is_recommendation_request(self) -> bool:
        return self.intent_type in {"recommend", "more", "refresh", "filter"}


# ---------------------------------------------------------------------------
# Intent parsing
# ---------------------------------------------------------------------------

# Keyword-based fallback patterns (used when LLM is unavailable or fails).
_KEYWORD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("more", re.compile(r"(再来|再要|更多|换一批|下一批|还有吗|别的)", re.IGNORECASE)),
    ("refresh", re.compile(r"(刷新|重新|换个|换一换|reshuffle)", re.IGNORECASE)),
    ("explain", re.compile(r"(为什么|讲讲|解释|说说|详细|怎么|哪个.*好)", re.IGNORECASE)),
    ("greeting", re.compile(r"^(你好|hi|hello|嗨|在吗|在不在)\s*[!！?？.。]*$", re.IGNORECASE)),
]

_PLATFORM_KEYWORDS: dict[str, list[str]] = {
    "bilibili": ["b站", "bilibili", "哔哩", "视频", "up主", "番剧"],
    "xiaohongshu": ["小红书", "xhs", "笔记", "种草"],
    "zhihu": ["知乎", "zhihu", "专栏", "回答"],
    "douyin": ["抖音", "douyin", "短视频"],
    "youtube": ["youtube", "油管", "yt"],
}


def _parse_intent_keywords(message: str) -> ChatIntent:
    """Fallback intent parser using regex keywords (no LLM needed)."""
    intent = ChatIntent(raw_message=message)

    # Detect intent type
    for intent_type, pattern in _KEYWORD_PATTERNS:
        if pattern.search(message):
            intent.intent_type = intent_type
            break

    # Detect platform
    for platform, keywords in _PLATFORM_KEYWORDS.items():
        if any(kw in message.lower() for kw in keywords):
            intent.platform = platform
            break

    # Detect limit (number followed by 个/条/篇)
    limit_match = re.search(r"(\d+)\s*(个|条|篇|个视频|个笔记)", message)
    if limit_match:
        intent.limit = min(20, max(1, int(limit_match.group(1))))

    # Extract explain index (第二个 / 第2个 / #2)
    explain_match = re.search(r"第\s*(\d+|[一二三四五六七八九十])\s*[个条篇]", message)
    if explain_match:
        raw = explain_match.group(1)
        cn_map = {
            "一": 1,
            "二": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }
        idx = cn_map.get(raw, int(raw) if raw.isdigit() else 1) - 1
        intent.explain_index = max(0, idx)

    # Extract rough keywords (nouns after 关于/关于/想看/喜欢)
    kw_match = re.search(r"(?:关于|想看|喜欢|找|推荐)(.+?)(?:的|视频|笔记|文章|内容|$)", message)
    if kw_match:
        raw_kw = kw_match.group(1).strip()
        if 1 < len(raw_kw) < 30:
            intent.keywords = [raw_kw]

    return intent


_INTENT_SYSTEM_PROMPT = (
    "你是一个推荐系统的意图解析器。把用户的自然语言消息解析成结构化意图。\n"
    "intent_type 只能是以下之一：\n"
    "- recommend: 用户请求推荐内容（默认）\n"
    "- more: 用户想要更多/下一批/换一批\n"
    "- refresh: 用户想要刷新/重新推荐\n"
    "- explain: 用户想了解某个推荐的理由或详情\n"
    "- filter: 用户在添加筛选条件（平台、主题、时长等）\n"
    "- greeting: 简单问候\n"
    "- other: 无法归类\n\n"
    "输出严格 JSON，不要输出任何其他文字。\n"
    "字段说明：\n"
    "- intent_type: 上述类型之一\n"
    "- keywords: 从消息中提取的主题/兴趣关键词数组（如 ['机器学习', '面试']），没有则空数组\n"
    "- platform: 平台过滤（bilibili/xiaohongshu/zhihu/douyin/youtube），没有则 null\n"
    "- limit: 用户明确要求的推荐数量（数字），没有则 null\n"
    "- explain_index: 用户想了解第几个推荐（0-based 数字），没有则 null"
)


async def parse_chat_intent(
    message: str,
    *,
    llm_service: Any | None = None,
) -> ChatIntent:
    """Parse a user's chat message into a structured ChatIntent.

    Uses LLM structured output when available; falls back to keyword
    regex parsing otherwise. The fallback ensures the chat recommender
    works even without LLM access.
    """
    if not message or not message.strip():
        return ChatIntent(intent_type="other", raw_message=message)

    if llm_service is not None:
        try:
            from openbiliclaw.llm.generation import generate_structured

            def _parse_intent(content: str) -> ChatIntent | None:
                import json

                try:
                    parsed = json.loads(content)
                except (json.JSONDecodeError, ValueError):
                    return None
                return ChatIntent(
                    intent_type=str(parsed.get("intent_type", "recommend")),
                    keywords=[str(k) for k in parsed.get("keywords", [])],
                    platform=parsed.get("platform"),
                    limit=parsed.get("limit"),
                    explain_index=parsed.get("explain_index"),
                    raw_message=message,
                )

            intent = await generate_structured(
                llm_service,
                system_instruction=_INTENT_SYSTEM_PROMPT,
                user_input=message,
                parse=_parse_intent,
                caller="recommendation.chat_intent",
                max_tokens=1024,
                label="chat_intent",
            )
            if intent is not None:
                logger.debug(
                    "LLM intent parse: type=%s keywords=%s",
                    intent.intent_type,
                    intent.keywords,
                )
                return intent
        except Exception:
            logger.exception("LLM intent parse failed, falling back to keywords")

    return _parse_intent_keywords(message)


# ---------------------------------------------------------------------------
# Chat session
# ---------------------------------------------------------------------------


@dataclass
class ChatSession:
    """Lightweight conversational recommendation session state.

    Attributes:
        session_id: Unique session identifier.
        profile: User's soul profile (snapshot at session start).
        history: List of ``{"role": "user"|"assistant", "content": str}``.
        last_recommendations: The most recent batch of recommendations
            (for "more" / "explain" / "filter" follow-ups).
        turn_count: Number of turns in this session.
        accumulated_filters: Accumulated platform / keyword filters from
            previous turns (applied to subsequent recommendations).

    """

    session_id: str
    profile: Any  # SoulProfile
    history: list[dict[str, str]] = field(default_factory=list)
    last_recommendations: list[Any] = field(default_factory=list)  # list[Recommendation]
    turn_count: int = 0
    accumulated_filters: dict[str, Any] = field(default_factory=dict)

    def add_user_message(self, message: str) -> None:
        self.history.append({"role": "user", "content": message})
        self.turn_count += 1

    def add_assistant_message(self, message: str) -> None:
        self.history.append({"role": "assistant", "content": message})


# ---------------------------------------------------------------------------
# Response generation
# ---------------------------------------------------------------------------

_RESPONSE_SYSTEM_PROMPT = (
    "你是一个懂用户的朋友，正在给用户推荐内容。用自然、口语化的中文回复，"
    "像朋友聊天一样，不要像机器播报。\n\n"
    "规则：\n"
    "1. 必须基于提供的推荐数据回复，不要编造不存在的内容。\n"
    "2. 每个推荐包含：序号、标题、一句话推荐理由、链接。\n"
    "3. 开头用一句自然的话引入，不要说'以下是为您推荐的'这种套话。\n"
    "4. 如果用户问'为什么推荐第X个'，就详细展开那个推荐的理由，结合用户画像。\n"
    "5. 如果用户说'换一批'，就说'好，换个口味'然后给出新推荐。\n"
    "6. 回复控制在 200 字以内，简洁有力。\n"
    "7. 不要用 markdown 标题、列表符号，用纯文本自然段落。"
)


def _format_recommendations_for_prompt(recommendations: list[Any]) -> str:
    """Format recommendations into a text block for the LLM prompt."""
    lines = []
    for i, rec in enumerate(recommendations):
        content = getattr(rec, "content", None)
        title = getattr(content, "title", "未知标题") if content else "未知标题"
        url = getattr(content, "content_url", "") if content else ""
        expression = getattr(rec, "expression", "")
        platform = getattr(content, "source_platform", "") if content else ""
        lines.append(
            f"[{i + 1}] {title}\n    平台: {platform}\n    推荐理由: {expression}\n    链接: {url}"
        )
    return "\n\n".join(lines)


async def generate_chat_response(
    intent: ChatIntent,
    recommendations: list[Any],
    *,
    llm_service: Any | None = None,
    session: ChatSession | None = None,
) -> str:
    """Generate a natural-language chat response from recommendations.

    Uses LLM when available; falls back to a simple template otherwise.
    The response always includes real recommendation data (title, url).
    """
    # Greeting / no recommendations edge cases
    if intent.intent_type == "greeting":
        return "嗨！想看点什么？我可以根据你的喜好推荐视频、笔记或文章，直接说就行。"

    if not recommendations:
        return "暂时没找到合适的内容，换个关键词试试？或者说'换一批'我再找找。"

    # Explain a specific recommendation
    if intent.intent_type == "explain" and intent.explain_index is not None:
        idx = intent.explain_index
        if 0 <= idx < len(recommendations):
            rec = recommendations[idx]
            content = getattr(rec, "content", None)
            title = getattr(content, "title", "未知标题") if content else "未知标题"
            expression = getattr(rec, "expression", "")
            url = getattr(content, "content_url", "") if content else ""
            return (
                f"关于《{title}》：{expression}\n\n"
                f"这个推荐是结合你最近的兴趣和浏览习惯挑的，如果你感兴趣可以看看：{url}"
            )

    # LLM-driven response
    if llm_service is not None:
        try:
            from openbiliclaw.llm.generation import generate_structured

            rec_block = _format_recommendations_for_prompt(recommendations)
            profile_summary = ""
            if session is not None:
                top_interests = getattr(session.profile, "top_interests", []) or []
                interest_domains = ", ".join(i.get("domain", "") for i in top_interests[:3])
                profile_summary = (
                    f"用户画像关键词: {getattr(session.profile, 'current_focus', '')}, "
                    f"兴趣: {interest_domains}"
                )

            user_prompt = (
                f"用户消息: {intent.raw_message}\n"
                f"解析意图: {intent.intent_type}\n"
                f"{profile_summary}\n\n"
                f"推荐数据:\n{rec_block}\n\n"
                f"请用朋友聊天的语气给用户回复，包含以上推荐内容。"
            )

            def _parse_text(content: str) -> str | None:
                text = content.strip()
                return text if len(text) > 10 else None

            content = await generate_structured(
                llm_service,
                system_instruction=_RESPONSE_SYSTEM_PROMPT,
                user_input=user_prompt,
                parse=_parse_text,
                caller="recommendation.chat_response",
                max_tokens=1024,
                label="chat_response",
                temperature=0.8,
            )
            if content:
                return str(content)
        except Exception:
            logger.exception("LLM response generation failed, falling back to template")

    # Template fallback
    return _template_response(intent, recommendations)


def _template_response(intent: ChatIntent, recommendations: list[Any]) -> str:
    """Simple template-based response (no LLM needed)."""
    if intent.intent_type == "more":
        intro = "好，再来几个："
    elif intent.intent_type == "refresh":
        intro = "换个口味，这几个看看："
    elif intent.intent_type == "filter":
        intro = "按你的条件筛了一下："
    else:
        intro = "这几个我觉得你可能会喜欢："

    lines = [intro]
    for i, rec in enumerate(recommendations[:5]):
        content = getattr(rec, "content", None)
        title = getattr(content, "title", "未知标题") if content else "未知标题"
        expression = getattr(rec, "expression", "")
        url = getattr(content, "content_url", "") if content else ""
        lines.append(f"\n{i + 1}. {title}")
        if expression:
            lines.append(f"   {expression}")
        if url:
            lines.append(f"   {url}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core chat recommend function
# ---------------------------------------------------------------------------


async def chat_recommend(
    message: str,
    *,
    session: ChatSession,
    engine: Any,  # RecommendationEngine
    llm_service: Any | None = None,
    default_limit: int = 5,
) -> str:
    """One turn of conversational recommendation.

    Pipeline:
        1. Parse user message → ChatIntent
        2. Apply accumulated filters + intent filters
        3. Call RecommendationEngine.serve() (or reuse last for "more")
        4. Generate natural-language response
        5. Update session state

    Args:
        message: User's natural-language message.
        session: The chat session (carries profile, history, filters).
        engine: The RecommendationEngine instance.
        llm_service: Optional LLM service for intent parsing + response gen.
        default_limit: Default number of recommendations when not specified.

    Returns:
        The assistant's natural-language reply.

    """
    session.add_user_message(message)

    # 1. Parse intent
    intent = await parse_chat_intent(message, llm_service=llm_service)

    # 2. Handle non-recommendation intents
    if intent.intent_type in {"greeting", "other"}:
        response = await generate_chat_response(
            intent, [], llm_service=llm_service, session=session
        )
        session.add_assistant_message(response)
        return response

    # 3. Accumulate filters
    if intent.platform:
        session.accumulated_filters["platform"] = intent.platform
    if intent.keywords:
        existing = session.accumulated_filters.get("keywords", [])
        session.accumulated_filters["keywords"] = list(set(existing + intent.keywords))

    # 4. Determine limit
    limit = intent.limit or session.accumulated_filters.get("limit") or default_limit

    # 5. Get recommendations
    recommendations: list[Any] = []
    try:
        if intent.intent_type in {"more", "refresh"}:
            # Exclude already-shown content
            excluded = frozenset(
                getattr(getattr(r, "content", None), "bvid", "")
                for r in session.last_recommendations
                if getattr(r, "content", None) is not None
            )
            recommendations = await engine.serve(
                session.profile,
                limit=limit,
                excluded_bvids=excluded,
                platform=session.accumulated_filters.get("platform"),
            )
        elif intent.intent_type == "explain" and session.last_recommendations:
            # Use existing recommendations for explain
            recommendations = session.last_recommendations
        else:
            # Fresh recommendation
            recommendations = await engine.serve(
                session.profile,
                limit=limit,
                platform=session.accumulated_filters.get("platform"),
            )
    except Exception:
        logger.exception("RecommendationEngine.serve() failed in chat_recommend")
        recommendations = []

    # 6. Update session (only for actual new recommendations, not explain)
    if intent.intent_type != "explain":
        session.last_recommendations = recommendations

    # 7. Generate response
    response = await generate_chat_response(
        intent, recommendations, llm_service=llm_service, session=session
    )
    session.add_assistant_message(response)
    return response

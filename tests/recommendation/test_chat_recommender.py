"""Unit tests for the conversational recommendation module."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.recommendation.chat_recommender import (
    ChatIntent,
    ChatSession,
    _parse_intent_keywords,
    _template_response,
    chat_recommend,
    parse_chat_intent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rec(title: str = "测试视频", url: str = "https://example.com/1") -> SimpleNamespace:
    return SimpleNamespace(
        content=SimpleNamespace(
            title=title,
            content_url=url,
            source_platform="bilibili",
            bvid="BV123",
        ),
        expression="这个视频讲得很清楚，适合你最近在学的主题。",
        topic_label="技术学习",
    )


def _make_profile() -> SimpleNamespace:
    return SimpleNamespace(
        current_focus="算法面试",
        top_interests=[
            {"domain": "机器学习", "weight": 0.9},
            {"domain": "推荐系统", "weight": 0.8},
        ],
    )


# ---------------------------------------------------------------------------
# Keyword intent parsing
# ---------------------------------------------------------------------------


class TestKeywordIntentParsing:
    def test_recommend_default(self):
        intent = _parse_intent_keywords("给我推荐几个视频")
        assert intent.intent_type == "recommend"
        assert intent.raw_message == "给我推荐几个视频"

    def test_more_keywords(self):
        for msg in ["再来几个", "换一批", "还有吗", "下一批"]:
            intent = _parse_intent_keywords(msg)
            assert intent.intent_type == "more", f"failed for: {msg}"

    def test_refresh_keywords(self):
        for msg in ["刷新一下", "重新推荐", "换个口味"]:
            intent = _parse_intent_keywords(msg)
            assert intent.intent_type == "refresh", f"failed for: {msg}"

    def test_explain_keywords(self):
        for msg in ["为什么推荐这个", "讲讲第二个", "解释一下"]:
            intent = _parse_intent_keywords(msg)
            assert intent.intent_type == "explain", f"failed for: {msg}"

    def test_greeting(self):
        for msg in ["你好", "hi", "在吗"]:
            intent = _parse_intent_keywords(msg)
            assert intent.intent_type == "greeting", f"failed for: {msg}"

    def test_platform_detection(self):
        intent = _parse_intent_keywords("给我推荐几个B站视频")
        assert intent.platform == "bilibili"

        intent = _parse_intent_keywords("小红书上有什么好的笔记")
        assert intent.platform == "xiaohongshu"

    def test_limit_detection(self):
        intent = _parse_intent_keywords("推荐3个视频")
        assert intent.limit == 3

        intent = _parse_intent_keywords("来5个")
        assert intent.limit == 5

    def test_explain_index_detection(self):
        intent = _parse_intent_keywords("讲讲第二个")
        assert intent.explain_index == 1

        intent = _parse_intent_keywords("为什么推荐第3个")
        assert intent.explain_index == 2

    def test_empty_message(self):
        intent = _parse_intent_keywords("")
        assert intent.intent_type == "recommend"
        assert intent.raw_message == ""


# ---------------------------------------------------------------------------
# LLM intent parsing (with mock)
# ---------------------------------------------------------------------------


class TestLLMIntentParsing:
    @pytest.mark.asyncio
    async def test_llm_parse_success(self):
        # LLM path is tested via integration; here we verify the keyword
        # fallback path produces a valid intent when llm_service is None.
        intent = await parse_chat_intent("推荐机器学习视频", llm_service=None)
        assert intent.intent_type == "recommend"

    @pytest.mark.asyncio
    async def test_llm_none_falls_back_to_keywords(self):
        intent = await parse_chat_intent("再来几个", llm_service=None)
        assert intent.intent_type == "more"

    @pytest.mark.asyncio
    async def test_empty_message_returns_other(self):
        intent = await parse_chat_intent("   ", llm_service=None)
        assert intent.intent_type == "other"


# ---------------------------------------------------------------------------
# ChatSession
# ---------------------------------------------------------------------------


class TestChatSession:
    def test_add_messages(self):
        session = ChatSession(session_id="test", profile=_make_profile())
        assert session.turn_count == 0
        session.add_user_message("你好")
        assert session.turn_count == 1
        assert session.history[-1]["role"] == "user"
        session.add_assistant_message("你好！")
        assert session.history[-1]["role"] == "assistant"
        assert session.turn_count == 1  # assistant doesn't increment

    def test_accumulated_filters(self):
        session = ChatSession(session_id="test", profile=_make_profile())
        session.accumulated_filters["platform"] = "bilibili"
        session.accumulated_filters["keywords"] = ["ML"]
        assert session.accumulated_filters["platform"] == "bilibili"


# ---------------------------------------------------------------------------
# Template response
# ---------------------------------------------------------------------------


class TestTemplateResponse:
    def test_basic_recommend(self):
        recs = [_make_rec("视频1"), _make_rec("视频2")]
        intent = ChatIntent(intent_type="recommend", raw_message="推荐几个")
        response = _template_response(intent, recs)
        assert "视频1" in response
        assert "视频2" in response
        assert "https://example.com/1" in response

    def test_more_intent_intro(self):
        recs = [_make_rec()]
        intent = ChatIntent(intent_type="more", raw_message="再来几个")
        response = _template_response(intent, recs)
        assert "再来" in response or "好" in response

    def test_refresh_intent_intro(self):
        recs = [_make_rec()]
        intent = ChatIntent(intent_type="refresh", raw_message="换一批")
        response = _template_response(intent, recs)
        assert "换" in response or "口味" in response

    def test_empty_recommendations(self):
        intent = ChatIntent(intent_type="recommend", raw_message="推荐")
        response = _template_response(intent, [])
        assert len(response) > 0


# ---------------------------------------------------------------------------
# chat_recommend end-to-end (mock engine)
# ---------------------------------------------------------------------------


class TestChatRecommend:
    @pytest.mark.asyncio
    async def test_greeting_flow(self):
        session = ChatSession(session_id="test", profile=_make_profile())
        engine = AsyncMock()
        response = await chat_recommend("你好", session=session, engine=engine, llm_service=None)
        assert "你好" in response or "嗨" in response or "想看" in response
        assert session.turn_count == 1
        engine.serve.assert_not_called()

    @pytest.mark.asyncio
    async def test_recommend_flow(self):
        session = ChatSession(session_id="test", profile=_make_profile())
        recs = [_make_rec("测试视频1"), _make_rec("测试视频2")]
        engine = AsyncMock()
        engine.serve = AsyncMock(return_value=recs)
        response = await chat_recommend(
            "推荐几个视频", session=session, engine=engine, llm_service=None
        )
        assert "测试视频1" in response
        assert "测试视频2" in response
        engine.serve.assert_called_once()
        assert len(session.last_recommendations) == 2

    @pytest.mark.asyncio
    async def test_more_flow_excludes_previous(self):
        session = ChatSession(session_id="test", profile=_make_profile())
        session.last_recommendations = [_make_rec("旧视频")]
        recs = [_make_rec("新视频1"), _make_rec("新视频2")]
        engine = AsyncMock()
        engine.serve = AsyncMock(return_value=recs)
        response = await chat_recommend(
            "再来几个", session=session, engine=engine, llm_service=None
        )
        assert "新视频1" in response
        engine.serve.assert_called_once()
        # Check that excluded_bvids was passed
        call_kwargs = engine.serve.call_args[1]
        assert "excluded_bvids" in call_kwargs
        assert len(call_kwargs["excluded_bvids"]) >= 1

    @pytest.mark.asyncio
    async def test_explain_flow_uses_last_recs(self):
        session = ChatSession(session_id="test", profile=_make_profile())
        session.last_recommendations = [_make_rec("第一个"), _make_rec("第二个")]
        engine = AsyncMock()
        response = await chat_recommend(
            "讲讲第二个", session=session, engine=engine, llm_service=None
        )
        assert "第二个" in response
        engine.serve.assert_not_called()  # explain doesn't call serve

    @pytest.mark.asyncio
    async def test_engine_failure_graceful(self):
        session = ChatSession(session_id="test", profile=_make_profile())
        engine = AsyncMock()
        engine.serve = AsyncMock(side_effect=RuntimeError("DB down"))
        response = await chat_recommend(
            "推荐几个", session=session, engine=engine, llm_service=None
        )
        assert "没找到" in response or "暂时" in response
        assert session.last_recommendations == []

    @pytest.mark.asyncio
    async def test_platform_filter_accumulated(self):
        session = ChatSession(session_id="test", profile=_make_profile())
        recs = [_make_rec()]
        engine = AsyncMock()
        engine.serve = AsyncMock(return_value=recs)
        await chat_recommend(
            "推荐几个B站视频", session=session, engine=engine, llm_service=None
        )
        call_kwargs = engine.serve.call_args[1]
        assert call_kwargs.get("platform") == "bilibili"
        assert session.accumulated_filters.get("platform") == "bilibili"


# ---------------------------------------------------------------------------
# ChatIntent helper methods
# ---------------------------------------------------------------------------


class TestChatIntentHelpers:
    def test_is_recommendation_request(self):
        assert ChatIntent(intent_type="recommend").is_recommendation_request()
        assert ChatIntent(intent_type="more").is_recommendation_request()
        assert ChatIntent(intent_type="refresh").is_recommendation_request()
        assert ChatIntent(intent_type="filter").is_recommendation_request()
        assert not ChatIntent(intent_type="explain").is_recommendation_request()
        assert not ChatIntent(intent_type="greeting").is_recommendation_request()
        assert not ChatIntent(intent_type="other").is_recommendation_request()

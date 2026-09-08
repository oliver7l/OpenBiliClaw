"""Chat recommend routes for OpenBiliClaw API."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException

from openbiliclaw.api.models import (
    ChatRecommendIn,
    ChatRecommendResponse,
    RecommendationOut,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


def register_chat_recommend_routes(
    app: FastAPI,
    ctx: Any,
    *,
    serialize_recommendation_items: Callable[[list[Any]], list[RecommendationOut]],
) -> None:
    """Register conversational recommendation endpoints on the FastAPI app."""
    _chat_recommend_sessions: dict[str, Any] = {}

    @app.post("/api/chat/recommend", response_model=ChatRecommendResponse)
    async def chat_recommend_endpoint(payload: ChatRecommendIn) -> ChatRecommendResponse:
        """Conversational recommendation: talk to the recommendation engine in natural language.

        Supports multi-turn context: "给我推荐几个广告算法视频" → "再来几个" → "讲讲第二个".
        Session state is kept in memory (not persistent); pass session_id to continue a conversation.  # noqa: E501
        """
        from openbiliclaw.recommendation.chat_recommender import ChatSession, chat_recommend

        if ctx.recommendation_engine is None:
            raise HTTPException(status_code=503, detail="Recommendation engine not initialized.")

        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="Message is required.")

        # Get or create session
        session_id = (
            payload.session_id
            or f"rec-{int(asyncio.get_event_loop().time() * 1000)}-{id(message) % 10000}"
        )
        session = _chat_recommend_sessions.get(session_id)
        if session is None:
            # Snapshot user profile at session start
            profile = None
            if ctx.soul_engine is not None:
                try:
                    profile = ctx.soul_engine.load_profile()
                except Exception:
                    logger.exception("Failed to load soul profile for chat recommend session")
            session = ChatSession(session_id=session_id, profile=profile)
            _chat_recommend_sessions[session_id] = session

        try:
            reply = await chat_recommend(
                message,
                session=session,
                engine=ctx.recommendation_engine,
                llm_service=getattr(ctx, "llm_service", None),
                default_limit=payload.limit,
            )
        except Exception:
            logger.exception("Chat recommend failed")
            reply = "推荐出了点问题，稍后再试。"

        # Serialize last recommendations for the frontend
        recs_out: list[RecommendationOut] = []
        last_recs = getattr(session, "last_recommendations", []) or []
        if last_recs:
            try:
                recs_out = serialize_recommendation_items(last_recs)
            except Exception:
                logger.exception("Failed to serialize chat recommendations")

        return ChatRecommendResponse(reply=reply, session_id=session_id, recommendations=recs_out)

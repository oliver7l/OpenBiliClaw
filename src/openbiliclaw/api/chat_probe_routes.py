"""Chat and probe routes for OpenBiliClaw API."""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress
from typing import TYPE_CHECKING, Any, cast

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from openbiliclaw.api.models import (
    ChatIn,
    ChatTurnIn,
    ChatTurnListResponse,
    ChatTurnOut,
    RecommendationOut,
)
from openbiliclaw.soul.dislike_writeback import apply_new_dislikes, topics_for_confirmed_avoidance

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


# ── Lazy imports for module-level helpers ─────


def _get_probe_metadata_for_payload(item: object) -> tuple[str, bool]:
    from openbiliclaw.api.app import _probe_metadata_for_payload

    return _probe_metadata_for_payload(item)


# ── Route registration ───────────────────────────────────────────


def register_chat_probe_routes(
    app: FastAPI,
    ctx: Any,
    *,
    fire_and_forget_tasks: set | None = None,
    serialize_recommendation_items: Callable[[list[Any]], list[RecommendationOut]] | None = None,
) -> None:
    """Register chat turns and interest/avoidance probe endpoints."""
    chat_turn_lock = asyncio.Lock()
    fallback_chat_turns: dict[str, dict[str, Any]] = {}
    running_chat_turn_tasks: set[str] = set()
    # RAG citations per chat turn, keyed by turn_id. Kept in memory: these are
    # ephemeral UI hints that don't need to survive a restart (the durable
    # turn row itself is the source of truth for the reply text).
    chat_turn_references: dict[str, list[dict[str, Any]]] = {}

    def _normalize_chat_scope(scope: str) -> str:
        normalized = scope.strip().lower()
        if normalized in {"chat", "delight", "probe", "avoidance_probe"}:
            return normalized
        return "chat"

    def _normalize_chat_turn(row: dict[str, Any]) -> ChatTurnOut:
        return ChatTurnOut(
            turn_id=str(row.get("turn_id", "")),
            session=str(row.get("session", "popup") or "popup"),
            scope=_normalize_chat_scope(str(row.get("scope", "chat"))),
            subject_id=str(row.get("subject_id", "") or ""),
            subject_title=str(row.get("subject_title", "") or ""),
            message=str(row.get("message", "") or ""),
            reply=str(row.get("reply", "") or ""),
            status=str(row.get("status", "pending") or "pending"),
            error=str(row.get("error", "") or ""),
            created_at=str(row.get("created_at", "") or ""),
            updated_at=str(row.get("updated_at", "") or ""),
            references=list(chat_turn_references.get(str(row.get("turn_id", "")), [])),
        )

    def _chat_db_method(name: str) -> Any | None:
        method = getattr(ctx.database, name, None)
        return method if callable(method) else None

    def _get_chat_turn_row(turn_id: str) -> dict[str, Any] | None:
        get_chat_turn = _chat_db_method("get_chat_turn")
        if get_chat_turn is not None:
            return cast("dict[str, Any] | None", get_chat_turn(turn_id))
        row = fallback_chat_turns.get(turn_id)
        return dict(row) if row else None

    def _list_chat_turn_rows(
        *,
        session: str = "popup",
        scope: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        list_chat_turns = _chat_db_method("list_chat_turns")
        if list_chat_turns is not None:
            return cast(
                "list[dict[str, Any]]",
                list_chat_turns(session=session, scope=scope, limit=limit),
            )
        rows = [
            dict(row)
            for row in fallback_chat_turns.values()
            if row.get("session") == session and (not scope or row.get("scope") == scope)
        ]
        rows.sort(key=lambda row: (str(row.get("created_at", "")), str(row.get("turn_id", ""))))
        return rows[-max(1, int(limit)) :]

    def _create_chat_turn_row(payload: ChatTurnIn, *, turn_id: str) -> dict[str, Any]:
        create_chat_turn = _chat_db_method("create_chat_turn")
        if create_chat_turn is not None:
            return cast(
                "dict[str, Any]",
                create_chat_turn(
                    turn_id=turn_id,
                    session=payload.session.strip() or "popup",
                    scope=_normalize_chat_scope(payload.scope),
                    subject_id=payload.subject_id.strip(),
                    subject_title=payload.subject_title.strip(),
                    message=payload.message.strip(),
                ),
            )

        from datetime import datetime

        now = datetime.now().isoformat(sep=" ")
        fallback_chat_turns.setdefault(
            turn_id,
            {
                "turn_id": turn_id,
                "session": payload.session.strip() or "popup",
                "scope": _normalize_chat_scope(payload.scope),
                "subject_id": payload.subject_id.strip(),
                "subject_title": payload.subject_title.strip(),
                "message": payload.message.strip(),
                "status": "pending",
                "reply": "",
                "error": "",
                "created_at": now,
                "updated_at": now,
            },
        )
        return dict(fallback_chat_turns[turn_id])

    def _complete_chat_turn_row(turn_id: str, *, reply: str) -> None:
        complete_chat_turn = _chat_db_method("complete_chat_turn")
        if complete_chat_turn is not None:
            complete_chat_turn(turn_id, reply=reply)
            return
        if turn_id in fallback_chat_turns:
            from datetime import datetime

            fallback_chat_turns[turn_id].update(
                {
                    "status": "completed",
                    "reply": reply,
                    "error": "",
                    "updated_at": datetime.now().isoformat(sep=" "),
                }
            )

    def _fail_chat_turn_row(turn_id: str, *, error: str, reply: str = "") -> None:
        fail_chat_turn = _chat_db_method("fail_chat_turn")
        if fail_chat_turn is not None:
            fail_chat_turn(turn_id, error=error, reply=reply)
            return
        if turn_id in fallback_chat_turns:
            from datetime import datetime

            fallback_chat_turns[turn_id].update(
                {
                    "status": "failed",
                    "reply": reply,
                    "error": error,
                    "updated_at": datetime.now().isoformat(sep=" "),
                }
            )

    async def _rag_retrieve(
        message: str, top_k: int = 4
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Retrieve RAG context + citations for a chat message.

        Returns ``(context_block, references)``. Both are empty when the
        article index is missing or has nothing relevant, so chat degrades
        cleanly to its normal (non-grounded) behaviour. The blocking embed +
        scan runs off the event loop behind a short budget so a slow embedder
        can never stall a reply.
        """
        if not message or not message.strip():
            return None, []
        try:
            from openbiliclaw.rag.retriever import get_retriever

            retr = get_retriever()
            loop = asyncio.get_running_loop()
            hits = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: retr.retrieve_chunks(message, top_k=top_k)),
                timeout=15,
            )
        except Exception:
            logger.debug("RAG retrieval skipped for this turn", exc_info=True)
            return None, []
        if not hits:
            return None, []
        context = retr.format_context(hits)
        references = [
            {
                "title": str(h.get("title", "") or ""),
                "url": str(h.get("url", "") or ""),
                "author": str(h.get("author", "") or ""),
                "source_table": str(h.get("source_table", "") or "articles"),
                "score": float(h.get("score", 0.0) or 0.0),
            }
            for h in hits
        ]
        logger.info("RAG context injected for chat (%d refs)", len(references))
        return context, references

    @app.post("/api/chat")
    async def chat(payload: ChatIn) -> Any:
        from fastapi.responses import JSONResponse

        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="Chat message is required.")
        # Pause discovery LLM calls while user is chatting
        concurrency = getattr(ctx.discovery_engine, "_concurrency", None)
        if concurrency is not None:
            concurrency.chat_active = True
        # RAG: ground the reply in the user's crawled reading library whenever
        # the index has something relevant (no-op while the index is still
        # being built, so chat behaves exactly as before until then).
        retrieval_context, references = await _rag_retrieve(message, top_k=4)
        try:
            # Bumped from 30s to 120s — deepseek with reasoning_effort=max
            # routinely takes 60-90s for one dialogue turn, so a 30s budget
            # truncated essentially every reply. Extension's AbortController
            # is sized to be generous enough to cover this end-to-end.
            reply = await asyncio.wait_for(
                ctx.dialogue.respond(message, retrieval_context=retrieval_context or None),
                timeout=120,
            )
        except TimeoutError:
            reply = "后台正忙，等一下再聊。"
        except Exception:
            logger.exception("Chat dialogue failed")
            reply = "聊天出了点问题，稍后再试。"
        finally:
            if concurrency is not None:
                concurrency.chat_active = False
        return JSONResponse(content={"reply": reply, "references": references})

    def _record_probe_cognition(
        summary: str,
        domain: str,
        action: str,
        *,
        source: str = "interest_probe",
        detail: str = "",
    ) -> None:
        """Write a cognition update so probe feedback shows in '阿b最近记住了什么'."""
        from datetime import datetime

        try:
            updates = ctx.memory_manager.load_cognition_updates()
            updates.append(
                {
                    "summary": summary,
                    "detail": detail or f"兴趣探针反馈：{action} — {domain}",
                    "created_at": datetime.now().isoformat(),
                    "source": source,
                    "tone": "success" if action == "confirmed" else "info",
                }
            )
            ctx.memory_manager.save_cognition_updates(updates)
        except Exception:
            logger.exception("Failed to record probe cognition update")

    async def _publish_probe_event(event_type: str, message: str, domain: str) -> None:
        """Push a probe result event via WebSocket."""
        event_hub = getattr(ctx.runtime_controller, "event_hub", None)
        publish = getattr(event_hub, "publish", None)
        if callable(publish):
            await publish(
                {
                    "type": event_type,
                    "phase": "ready",
                    "message": message,
                    "domain": domain,
                }
            )

    def _probe_metadata_from_active_item(
        get_active: Any,
        domain: str,
        *,
        include_category: bool = False,
        include_source_mode: bool = False,
    ) -> dict[str, object]:
        """Read active probe metadata before confirm/reject mutates state."""
        from openbiliclaw.soul.speculator import build_probe_axis

        if not callable(get_active):
            return {"domain": domain}
        try:
            active_items = list(get_active())
        except Exception:
            logger.debug("Failed to read active probe metadata", exc_info=True)
            return {"domain": domain}

        for item in active_items:
            spec_domain = str(getattr(item, "domain", "")).strip()
            if spec_domain.lower() != domain.lower():
                continue
            specifics = [
                str(getattr(specific, "name", "")).strip()
                for specific in getattr(item, "specifics", [])
                if str(getattr(specific, "name", "")).strip()
            ]
            axis = build_probe_axis(
                experience_mode=getattr(item, "experience_mode", ""),
                entry_load=getattr(item, "entry_load", ""),
            )
            metadata: dict[str, object] = {
                "domain": spec_domain or domain,
                "reason": str(getattr(item, "reason", "")).strip(),
            }
            if include_category:
                metadata["category"] = str(getattr(item, "category", "")).strip()
            if include_source_mode:
                source_mode = str(getattr(item, "source_mode", "")).strip()
                source_signal = str(getattr(item, "source_signal", "")).strip()
                if source_mode:
                    metadata["source_mode"] = source_mode
                if source_signal:
                    metadata["source_signal"] = source_signal
            if axis:
                metadata["axis"] = axis
            if specifics:
                metadata["specifics"] = specifics
            return metadata
        return {"domain": domain}

    def _probe_metadata_from_active_speculation(
        speculator: Any,
        domain: str,
    ) -> dict[str, object]:
        """Read active interest probe metadata before state mutation."""
        return _probe_metadata_from_active_item(
            getattr(speculator, "get_active_speculations", None),
            domain,
            include_category=True,
        )

    def _probe_metadata_from_active_avoidance(
        speculator: Any,
        domain: str,
    ) -> dict[str, object]:
        """Read active avoidance probe metadata before state mutation."""
        return _probe_metadata_from_active_item(
            getattr(speculator, "get_active_avoidances", None),
            domain,
            include_source_mode=True,
        )

    def _record_probe_feedback_history(
        domain: str,
        response: str,
        *,
        speculator: Any,
        message: str = "",
        classification: str = "",
        classifier: str = "",
        resulting_action: str = "",
        state_key: str = "probe_feedback_history",
        metadata_fn: Any | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Persist explicit user feedback for future probe novelty checks."""
        from openbiliclaw.soul.speculator import append_probe_feedback_history

        memory_manager = getattr(ctx, "memory_manager", None)
        if memory_manager is None:
            memory_manager = getattr(ctx.runtime_controller, "memory_manager", None)
        load_state = getattr(memory_manager, "load_discovery_runtime_state", None)
        save_state = getattr(memory_manager, "save_discovery_runtime_state", None)
        update_state = getattr(memory_manager, "update_discovery_runtime_state", None)
        if not callable(update_state) and (not callable(load_state) or not callable(save_state)):
            return
        try:
            if metadata is not None:
                entry = dict(metadata)
            elif metadata_fn is not None:
                entry = metadata_fn(domain)
            else:
                entry = _probe_metadata_from_active_speculation(speculator, domain)
            entry["response"] = response
            if message:
                entry["message"] = message
                entry["raw_text_excerpt"] = message[:240]
            if classification:
                entry["classification"] = classification
            if classifier:
                entry["classifier"] = classifier
            if resulting_action:
                entry["resulting_action"] = resulting_action

            def _mutate(state: dict[str, object]) -> None:
                state[state_key] = append_probe_feedback_history(
                    state.get(state_key, []),
                    entry,
                )

            if callable(update_state):
                update_state(_mutate)
            else:
                load_state_fn = cast("Callable[[], dict[str, object]]", load_state)
                save_state_fn = cast("Callable[[dict[str, object]], None]", save_state)
                state = load_state_fn()
                _mutate(state)
                save_state_fn(state)
        except Exception:
            logger.exception("Failed to record probe feedback history")

    async def _judge_probe_sentiment(
        user_message: str,
        ai_reply: str,
        domain: str,
    ) -> str:
        """Judge the user's probe chat as a 4-way confirmation signal."""
        sentiment, _classifier = await _classify_probe_sentiment(
            user_message,
            ai_reply,
            domain,
        )
        return sentiment

    async def _classify_probe_sentiment(
        user_message: str,
        ai_reply: str,
        domain: str,
    ) -> tuple[str, str]:
        """Return ``(classification, classifier)`` for probe chat feedback."""
        llm_result = await _llm_judge_sentiment(user_message, ai_reply, domain)
        if llm_result in {"strong_positive", "weak_positive", "negative"}:
            return llm_result, "llm"
        keyword_result = _keyword_judge_sentiment(user_message)
        if keyword_result != "neutral":
            return keyword_result, "keyword"
        return "neutral", "neutral_default"

    def _keyword_judge_sentiment(user_message: str) -> str:
        """Fallback keyword-based sentiment detection."""
        msg = user_message.lower()
        negative_terms = {
            "不喜欢",
            "不感兴趣",
            "不是这个意思",
            "别推",
            "没兴趣",
            "不想看",
        }
        strong_positive_terms = {
            "以后多推",
            "这就是我想看的",
            "我就喜欢",
            "加入我的画像",
        }
        weak_positive_terms = {
            "有点意思",
            "可以看看",
            "偶尔看看",
            "还行",
            "先试试",
        }
        if any(kw in msg for kw in negative_terms):
            return "negative"
        if any(kw in msg for kw in strong_positive_terms):
            return "strong_positive"
        if any(kw in msg for kw in weak_positive_terms):
            return "weak_positive"
        return "neutral"

    async def _llm_judge_sentiment(
        user_message: str,
        ai_reply: str,
        domain: str,
    ) -> str:
        """LLM-based sentiment judgment for probe chat."""
        if ctx.recommendation_engine is None:
            return "neutral"
        llm = getattr(ctx.recommendation_engine, "_llm", None)
        if llm is None:
            return "neutral"
        try:
            response = await asyncio.wait_for(
                llm.complete_with_core_memory(
                    system_instruction=(
                        "任务：判断用户对一个兴趣方向的态度。\n\n"
                        "规则：\n"
                        "1. 只输出一个英文标签："
                        "strong_positive、weak_positive、neutral 或 negative\n"
                        "2. 不要输出任何其他内容\n\n"
                        "判断标准：\n"
                        "- strong_positive = 用户明确要加入画像、以后多推、这就是想看的\n"
                        "- weak_positive = 用户表达轻微兴趣、可以看看、偶尔看看，但未直接确认\n"
                        "- negative = 用户表达了不喜欢、不感兴趣、太难、太无聊\n"
                        "- neutral = 态度不明确\n"
                    ),
                    user_input=f"方向：{domain}\n用户：{user_message}",
                    max_tokens=8,
                    temperature=0.0,
                    json_mode=False,
                    caller="api.sentiment",
                    bypass_semaphore=True,
                ),
                timeout=15,
            )
            raw = str(getattr(response, "content", "")).strip().lower()
            # Extract the first recognizable word
            for word in raw.split():
                cleaned = word.strip("\"'.,:;!?")
                if cleaned in (
                    "strong_positive",
                    "weak_positive",
                    "negative",
                    "neutral",
                ):
                    logger.info("Sentiment LLM for '%s': %s (raw=%r)", domain, cleaned, raw)
                    return cleaned
            logger.info(
                "Sentiment LLM for '%s': unrecognized (raw=%r), trying keywords", domain, raw
            )
            return "neutral"
        except Exception:
            logger.info("Sentiment LLM for '%s' failed, trying keywords", domain)
            return "neutral"

    def _confirm_speculation_with_source(
        speculator: Any,
        domain: str,
        *,
        confirmation_source: str,
    ) -> bool:
        confirm = getattr(speculator, "user_confirm_speculation", None)
        if not callable(confirm):
            return False
        try:
            return bool(confirm(domain, confirmation_source=confirmation_source))
        except TypeError:
            return bool(confirm(domain))

    def _promote_exploration_buffer_entries(
        promoted: list[dict[str, object]],
    ) -> None:
        if not promoted:
            return
        from openbiliclaw.soul.interest_writeback import merge_confirmed_interest
        from openbiliclaw.soul.profile import OnionProfile

        memory_manager = getattr(ctx, "memory_manager", None)
        get_layer = getattr(memory_manager, "get_layer", None)
        if not callable(get_layer):
            return
        try:
            soul_layer = get_layer("soul")
            raw_profile = getattr(soul_layer, "data", {})
            profile = (
                OnionProfile.from_dict(raw_profile)
                if isinstance(raw_profile, dict) and raw_profile
                else OnionProfile()
            )
            changed = False
            for entry in promoted:
                raw_specifics = entry.get("specifics", [])
                specifics = (
                    [str(item) for item in raw_specifics if str(item).strip()]
                    if isinstance(raw_specifics, list)
                    else []
                )
                changed = (
                    merge_confirmed_interest(
                        profile,
                        domain=str(entry.get("domain", "")),
                        specifics=specifics,
                        source=str(entry.get("confirmation_source", "buffer_promoted")),
                        first_seen=str(entry.get("first_seen", "")),
                        last_seen=str(entry.get("last_seen", "")),
                    )
                    or changed
                )
            if not changed:
                return
            if isinstance(raw_profile, dict):
                raw_profile.clear()
                raw_profile.update(profile.to_dict())
            save = getattr(soul_layer, "save", None)
            if callable(save):
                save()
            sync_profile_files = getattr(memory_manager, "sync_profile_files", None)
            if callable(sync_profile_files):
                sync_profile_files(profile)
        except Exception:
            logger.exception("Failed to promote exploration buffer entries")

    def _record_exploration_buffer_event(
        *,
        domain: str,
        source_event: str,
        specifics: list[str] | None = None,
        evidence_id: str = "",
    ) -> None:
        from datetime import UTC, datetime

        from openbiliclaw.soul.exploration_buffer import (
            pop_promotable_buffer_entries,
            record_buffer_event,
        )

        clean_domain = domain.strip()
        if not clean_domain:
            return
        memory_manager = getattr(ctx, "memory_manager", None)
        load_state = getattr(memory_manager, "load_discovery_runtime_state", None)
        save_state = getattr(memory_manager, "save_discovery_runtime_state", None)
        update_state = getattr(memory_manager, "update_discovery_runtime_state", None)
        if not callable(update_state) and (not callable(load_state) or not callable(save_state)):
            return
        try:
            now = datetime.now(UTC)

            promoted: list[dict[str, object]] = []

            def _mutate(state: dict[str, object]) -> None:
                nonlocal promoted
                raw_buffer_state = state.get("short_term_exploration_buffer", {})
                existing_buffer_state = (
                    raw_buffer_state if isinstance(raw_buffer_state, dict) else {}
                )
                buffer_state = record_buffer_event(
                    existing_buffer_state,
                    domain=clean_domain,
                    source_event=source_event,
                    specifics=specifics or [],
                    evidence_id=evidence_id,
                    now=now,
                )
                promoted, buffer_state = pop_promotable_buffer_entries(buffer_state, now=now)
                state["short_term_exploration_buffer"] = buffer_state

            if callable(update_state):
                update_state(_mutate)
            else:
                load_state_fn = cast("Callable[[], dict[str, object]]", load_state)
                save_state_fn = cast("Callable[[dict[str, object]], None]", save_state)
                state = load_state_fn()
                if not isinstance(state, dict):
                    state = {}
                _mutate(state)
                save_state_fn(state)
            _promote_exploration_buffer_entries(promoted)
        except Exception:
            logger.exception("Failed to record exploration buffer event")

    def _recommendation_buffer_domain(row: dict[str, object]) -> tuple[str, list[str]]:
        title = str(row.get("title", "")).strip()
        domain = (
            str(row.get("topic_group", "")).strip()
            or str(row.get("topic_label", "")).strip()
            or str(row.get("topic", "")).strip()
            or str(row.get("topic_key", "")).strip()
            or title
        )
        specifics = [title] if title and title != domain else []
        return domain, specifics

    def _contextual_chat_message(turn: ChatTurnOut) -> str:
        if turn.scope == "delight":
            label = turn.subject_title or turn.subject_id or "这条惊喜推荐"
            return f"[关于惊喜推荐「{label}」的反馈] {turn.message}"
        if turn.scope == "probe":
            label = turn.subject_title or turn.subject_id or "这个方向"
            return f"[关于猜测兴趣「{label}」的反馈] {turn.message}"
        if turn.scope == "avoidance_probe":
            label = turn.subject_title or turn.subject_id or "这个避雷方向"
            return f"[关于避雷方向「{label}」的反馈] {turn.message}"
        return turn.message

    async def _generate_durable_chat_reply(turn: ChatTurnOut) -> str:
        if ctx.dialogue is None:
            return "对话引擎暂不可用。"

        # RAG: ground the reply in the user's crawled reading library. Only the
        # plain "chat" scope is grounded — delight/probe scopes are feedback
        # about one specific recommendation, where library passages would just
        # be noise. References are stashed per turn so the UI can show the
        # "已参考 N 篇收藏" badge.
        retrieval_context: str | None = None
        references: list[dict[str, Any]] = []
        if turn.scope == "chat":
            retrieval_context, references = await _rag_retrieve(turn.message, top_k=4)
            if references:
                chat_turn_references[str(turn.turn_id)] = references

        concurrency = getattr(ctx.discovery_engine, "_concurrency", None)
        if concurrency is not None:
            concurrency.chat_active = True
        try:
            async with chat_turn_lock:
                reply = await asyncio.wait_for(
                    ctx.dialogue.respond(
                        _contextual_chat_message(turn),
                        retrieval_context=retrieval_context,
                    ),
                    timeout=120,
                )
                reply = str(reply)
        except TimeoutError:
            return "后台正忙，等一下再聊。"
        except Exception:
            logger.exception("Durable chat turn failed: %s", turn.turn_id)
            return "聊天出了点问题，稍后再试。"
        finally:
            if concurrency is not None:
                concurrency.chat_active = False

        if turn.scope == "delight":
            label = turn.subject_title or turn.subject_id
            _record_probe_cognition(
                f"关于惊喜推荐「{label}」你说：{turn.message}",
                turn.subject_id or label,
                "delight_chat",
                detail=f"你的反馈：{turn.message}\n阿b的回复：{reply}",
            )
            await _publish_probe_event(
                "delight.chat",
                f"关于「{label}」你说：{turn.message}",
                turn.subject_id or label,
            )
        elif turn.scope == "probe":
            domain = turn.subject_id or turn.subject_title
            sentiment, classifier = await _classify_probe_sentiment(turn.message, reply, domain)
            speculator = getattr(ctx.soul_engine, "_speculator", None)
            chat_response = "chat_neutral"
            resulting_action = "none"
            if sentiment == "negative":
                chat_response = "chat_rejected"
                resulting_action = "rejected"
                if speculator is not None:
                    with suppress(Exception):
                        speculator.user_reject_speculation(domain, cooldown_days=14)
                summary = f"你对「{domain}」的反馈偏负面（{turn.message}），已暂时搁置 14 天。"
            elif sentiment == "strong_positive":
                chat_response = "chat_confirmed"
                resulting_action = "confirmed"
                if speculator is not None:
                    with suppress(Exception):
                        _confirm_speculation_with_source(
                            speculator,
                            domain,
                            confirmation_source="chat_confirmed",
                        )
                summary = f"你明确确认了对「{domain}」的兴趣，已加入画像。"
            elif sentiment == "weak_positive":
                chat_response = "weak_positive"
                resulting_action = "weak_positive_deferred"
                _record_exploration_buffer_event(
                    domain=domain,
                    source_event="weak_positive_chat",
                )
                summary = f"你对「{domain}」有轻微信号，先作为短期探索方向观察。"
            else:
                summary = f"关于「{domain}」你说：{turn.message}"
            if speculator is not None:
                _record_probe_feedback_history(
                    domain,
                    chat_response,
                    speculator=speculator,
                    message=turn.message,
                    classification=sentiment,
                    classifier=classifier,
                    resulting_action=resulting_action,
                )
            _record_probe_cognition(
                summary,
                domain,
                "chat",
                detail=f"你的反馈：{turn.message}\n阿b的回复：{reply}",
            )
            await _publish_probe_event("interest.chat", summary, domain)
        elif turn.scope == "avoidance_probe":
            domain = turn.subject_id or turn.subject_title
            sentiment, classifier = await _classify_probe_sentiment(turn.message, reply, domain)
            speculator = getattr(ctx.soul_engine, "_avoidance_speculator", None)
            if sentiment == "negative":
                chat_response = "avoidance_chat_confirmed"
                resulting_action = "confirmed"
                if speculator is not None:
                    with suppress(Exception):
                        speculator.observe(
                            [
                                {
                                    "event_type": "dislike",
                                    "title": domain,
                                    "metadata": {
                                        "feedback_type": "dislike",
                                        "user_message": turn.message,
                                        "source": "avoidance_probe_chat",
                                    },
                                }
                            ]
                        )
                summary = f"你确认「{domain}」偏向不喜欢，确认度 +1。"
            elif sentiment in {"strong_positive", "weak_positive"}:
                chat_response = "avoidance_chat_rejected"
                resulting_action = "rejected"
                if speculator is not None:
                    reject_fn = getattr(speculator, "user_reject_avoidance", None)
                    if callable(reject_fn):
                        with suppress(Exception):
                            reject_fn(domain, cooldown_days=14)
                summary = f"你表示其实不排斥「{domain}」，已暂时搁置 14 天。"
            else:
                chat_response = "avoidance_chat_neutral"
                resulting_action = "none"
                summary = f"关于避雷方向「{domain}」你说：{turn.message}"
            if speculator is not None:
                _record_probe_feedback_history(
                    domain,
                    chat_response,
                    speculator=speculator,
                    message=turn.message,
                    classification=sentiment,
                    classifier=classifier,
                    resulting_action=resulting_action,
                    state_key="avoidance_probe_feedback_history",
                    metadata_fn=lambda item_domain: _probe_metadata_from_active_avoidance(
                        speculator,
                        item_domain,
                    ),
                )
            _record_probe_cognition(
                summary,
                domain,
                "chat",
                source="avoidance_probe",
                detail=f"你的反馈：{turn.message}\n阿b的回复：{reply}",
            )
            await _publish_probe_event("avoidance.chat", summary, domain)

        return reply

    async def _complete_durable_chat_turn(turn_id: str) -> None:
        if turn_id in running_chat_turn_tasks:
            return
        running_chat_turn_tasks.add(turn_id)
        try:
            row = _get_chat_turn_row(turn_id)
            if row is None:
                return
            turn = _normalize_chat_turn(row)
            if turn.status != "pending":
                return
            reply = await _generate_durable_chat_reply(turn)
            _complete_chat_turn_row(turn_id, reply=reply)
        except Exception as exc:
            logger.exception("Failed to complete durable chat turn %s", turn_id)
            _fail_chat_turn_row(turn_id, error=str(exc), reply="聊天出了点问题，稍后再试。")
        finally:
            running_chat_turn_tasks.discard(turn_id)

    @app.post("/api/chat/turns", response_model=ChatTurnOut)
    async def start_chat_turn(payload: ChatTurnIn) -> ChatTurnOut:
        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="Chat message is required.")
        raw_turn_id = payload.turn_id.strip()
        turn_id = raw_turn_id or f"turn-{uuid.uuid4().hex}"
        existing = _get_chat_turn_row(turn_id)
        if existing is not None:
            turn = _normalize_chat_turn(existing)
            if turn.status == "pending":
                asyncio.create_task(_complete_durable_chat_turn(turn.turn_id))
            return turn
        row = _create_chat_turn_row(payload, turn_id=turn_id)
        asyncio.create_task(_complete_durable_chat_turn(turn_id))
        return _normalize_chat_turn(row)

    @app.get("/api/chat/turns", response_model=ChatTurnListResponse)
    async def list_chat_turns(
        session: str = "popup",
        scope: str = "",
        limit: int = Query(default=50, ge=1, le=200),
    ) -> ChatTurnListResponse:
        normalized_scope = _normalize_chat_scope(scope) if scope else ""
        rows = _list_chat_turn_rows(
            session=session.strip() or "popup",
            scope=normalized_scope,
            limit=limit,
        )
        return ChatTurnListResponse(items=[_normalize_chat_turn(row) for row in rows])

    @app.get("/api/chat/turns/{turn_id}", response_model=ChatTurnOut)
    async def get_chat_turn(turn_id: str) -> ChatTurnOut:
        row = _get_chat_turn_row(turn_id.strip())
        if row is None:
            raise HTTPException(status_code=404, detail="Chat turn not found.")
        turn = _normalize_chat_turn(row)
        if turn.status == "pending":
            asyncio.create_task(_complete_durable_chat_turn(turn.turn_id))
        return turn

    @app.post("/api/interest-probes/trigger")
    async def trigger_interest_probe() -> dict[str, Any]:
        """Manually trigger an interest probe push via WebSocket.

        Useful when ``run_forever`` is blocked by a long refresh cycle
        and the probe wouldn't fire on its own for several minutes.
        """
        controller = ctx.runtime_controller
        if controller is None:
            raise HTTPException(status_code=503, detail="Runtime controller not available")
        publish = getattr(controller, "_publish_interest_probe_if_available", None)
        if not callable(publish):
            raise HTTPException(status_code=503, detail="Probe publisher not available")
        await publish()
        return {"ok": True, "action": "probe_triggered"}

    @app.get("/api/interest-probes/pending")
    async def pending_interest_probes() -> dict[str, Any]:
        """Return active speculative interests that the user hasn't responded to.

        The mobile web UI polls this on page load / bell-click so probes
        survive page refreshes (unlike WebSocket-only delivery).
        """
        try:
            from openbiliclaw.soul.speculator import load_speculative_state

            spec_state = load_speculative_state(ctx.config.data_path)
            active = [item for item in spec_state.active if item.status == "active"]
            items = []
            for item in active[:6]:
                probe_mode, challenge = _get_probe_metadata_for_payload(item)
                items.append(
                    {
                        "domain": item.domain,
                        "reason": item.reason,
                        "confidence": item.confidence,
                        "status": item.status,
                        "probe_mode": probe_mode,
                        "challenge": challenge,
                    }
                )
            return {"items": items}
        except Exception:
            return {"items": []}

    @app.post("/api/interest-probes/respond")
    async def respond_to_interest_probe(payload: dict[str, Any]) -> Any:
        """User responds to a speculated interest probe.

        Body: { "domain": "...", "response": "confirm" | "reject" | "chat", "message": "..." }

        - confirm: Force-promote the speculation
        - reject: Move to cooldown (30 days)
        - chat: Forward to dialogue engine with probe context, return reply
        """
        domain = str(payload.get("domain", "")).strip()
        response_type = str(payload.get("response", "")).strip().lower()

        if not domain:
            raise HTTPException(status_code=422, detail="domain is required")
        if response_type not in {"confirm", "reject", "chat"}:
            raise HTTPException(status_code=422, detail="response must be confirm, reject, or chat")

        speculator = getattr(ctx.soul_engine, "_speculator", None)
        if speculator is None:
            raise HTTPException(status_code=503, detail="Speculator not available")

        if response_type == "confirm":
            requested_source = str(payload.get("confirmation_source", "")).strip()
            surface = str(payload.get("surface", "")).strip().lower()
            confirmation_source = requested_source or (
                "profile_confirmed" if surface == "profile" else "probe_confirmed"
            )
            metadata = _probe_metadata_from_active_speculation(speculator, domain)
            ok = _confirm_speculation_with_source(
                speculator,
                domain,
                confirmation_source=confirmation_source,
            )
            if ok:
                _record_probe_feedback_history(
                    domain,
                    "confirm",
                    speculator=speculator,
                    resulting_action="confirmed",
                    metadata=metadata,
                )
                # Force_tick generates 5 new probes via LLM (~30-60s).
                # Running it inline blocks the response past the
                # browser fetch timeout (35s) — the user gives up,
                # AbortError fires, and the next click hits a stale UI.
                # Schedule it as a background task so the API returns
                # immediately; the new probes will be visible on the
                # next profile-summary refresh.
                tick_fn = getattr(speculator, "force_tick", None)
                if callable(tick_fn):

                    async def _bg_force_tick() -> None:
                        try:
                            profile = await ctx.soul_engine.get_profile()
                            feedback_history: object = []
                            load_runtime_state = getattr(
                                ctx.memory_manager,
                                "load_discovery_runtime_state",
                                None,
                            )
                            if callable(load_runtime_state):
                                runtime_state = load_runtime_state()
                                if isinstance(runtime_state, dict):
                                    feedback_history = runtime_state.get(
                                        "probe_feedback_history",
                                        [],
                                    )

                            def _load_feedback_history() -> object:
                                if not callable(load_runtime_state):
                                    return []
                                runtime_state = load_runtime_state()
                                if not isinstance(runtime_state, dict):
                                    return []
                                return runtime_state.get("probe_feedback_history", [])

                            if asyncio.iscoroutinefunction(tick_fn):
                                try:
                                    await tick_fn(
                                        profile,
                                        feedback_history=feedback_history,
                                        feedback_history_loader=_load_feedback_history,
                                    )
                                except TypeError:
                                    try:
                                        await tick_fn(
                                            profile,
                                            feedback_history=feedback_history,
                                        )
                                    except TypeError:
                                        await tick_fn(profile)
                            else:
                                try:
                                    tick_fn(
                                        profile,
                                        feedback_history=feedback_history,
                                        feedback_history_loader=_load_feedback_history,
                                    )
                                except TypeError:
                                    try:
                                        tick_fn(profile, feedback_history=feedback_history)
                                    except TypeError:
                                        tick_fn(profile)
                        except Exception:
                            logger.exception("Background force_tick after confirm failed")

                    asyncio.create_task(_bg_force_tick())
                # Record cognition update so it shows in "阿b最近记住了什么"
                _record_probe_cognition(
                    f"你确认了对「{domain}」的兴趣，已加入画像。",
                    domain,
                    "confirmed",
                )
                # Notify frontend via WebSocket
                await _publish_probe_event(
                    "interest.confirmed",
                    f"你确认了对「{domain}」的兴趣，已加入画像。",
                    domain,
                )
            return {"ok": ok, "action": "confirmed", "domain": domain}

        if response_type == "reject":
            metadata = _probe_metadata_from_active_speculation(speculator, domain)
            ok = speculator.user_reject_speculation(domain)
            if ok:
                _record_probe_feedback_history(
                    domain,
                    "reject",
                    speculator=speculator,
                    metadata=metadata,
                )
                _record_probe_cognition(
                    f"你对「{domain}」暂时不感兴趣，30 天内不再推送。",
                    domain,
                    "rejected",
                )
                await _publish_probe_event(
                    "interest.rejected",
                    f"已记录：你对「{domain}」暂时不感兴趣，30 天内不再推送。",
                    domain,
                )
            return {"ok": ok, "action": "rejected", "domain": domain}

        # Chat: forward to dialogue with domain context injected
        raw_message = str(payload.get("message", "")).strip()
        if not raw_message:
            raw_message = f"我想聊聊你猜我可能感兴趣的「{domain}」这个方向"
        # Inject domain context so dialogue engine + learn_from_dialogue
        # understand this is feedback on a specific speculated interest
        contextual_message = f"[关于猜测兴趣「{domain}」的反馈] {raw_message}"
        if ctx.dialogue is None:
            return {"ok": False, "action": "chat", "domain": domain, "reply": "对话引擎暂不可用。"}
        # Pause discovery LLM calls while user is chatting
        concurrency = getattr(ctx.discovery_engine, "_concurrency", None)
        if concurrency is not None:
            concurrency.chat_active = True
        try:
            reply = await asyncio.wait_for(
                ctx.dialogue.respond(contextual_message),
                timeout=30,
            )
            # Judge sentiment while discovery is still paused
            sentiment, classifier = await _classify_probe_sentiment(raw_message, reply, domain)
        except TimeoutError:
            return {
                "ok": False,
                "action": "chat",
                "domain": domain,
                "reply": "后台正忙，等一下再聊。",
            }
        except Exception:
            logger.exception("Dialogue failed for probe chat: %s", domain)
            return {
                "ok": False,
                "action": "chat",
                "domain": domain,
                "reply": "聊天出了点问题，稍后再试。",
            }
        finally:
            if concurrency is not None:
                concurrency.chat_active = False

        chat_response = "chat_neutral"
        resulting_action = "none"
        if sentiment == "negative":
            chat_response = "chat_rejected"
            resulting_action = "rejected"
            speculator.user_reject_speculation(domain, cooldown_days=14)
            summary = f"你对「{domain}」的反馈偏负面（{raw_message}），已暂时搁置 14 天。"
        elif sentiment == "strong_positive":
            chat_response = "chat_confirmed"
            resulting_action = "confirmed"
            _confirm_speculation_with_source(
                speculator,
                domain,
                confirmation_source="chat_confirmed",
            )
            summary = f"你明确确认了对「{domain}」的兴趣，已加入画像。"
        elif sentiment == "weak_positive":
            chat_response = "weak_positive"
            resulting_action = "weak_positive_deferred"
            _record_exploration_buffer_event(
                domain=domain,
                source_event="weak_positive_chat",
            )
            summary = f"你对「{domain}」有轻微信号，先作为短期探索方向观察。"
        else:
            summary = f"关于「{domain}」你说：{raw_message}"

        _record_probe_feedback_history(
            domain,
            chat_response,
            speculator=speculator,
            message=raw_message,
            classification=sentiment,
            classifier=classifier,
            resulting_action=resulting_action,
        )

        detail = f"你的反馈：{raw_message}\n阿b的回复：{reply}"
        _record_probe_cognition(summary, domain, "chat", detail=detail)
        await _publish_probe_event(
            "interest.chat",
            summary,
            domain,
        )
        from fastapi.responses import JSONResponse

        return JSONResponse(
            content={"ok": True, "action": "chat", "domain": domain, "reply": reply}
        )

    @app.post("/api/avoidance-probes/trigger")
    async def trigger_avoidance_probe() -> dict[str, Any]:
        """Manually trigger an avoidance probe push via WebSocket."""
        controller = ctx.runtime_controller
        if controller is None:
            raise HTTPException(status_code=503, detail="Runtime controller not available")
        publish = getattr(controller, "_publish_avoidance_probe_if_available", None)
        if not callable(publish):
            raise HTTPException(status_code=503, detail="Avoidance probe publisher not available")
        await publish()
        return {"ok": True, "action": "avoidance_probe_triggered"}

    @app.get("/api/avoidance-probes/pending")
    async def pending_avoidance_probes() -> dict[str, Any]:
        """Return active speculative avoidances awaiting user response."""
        try:
            from openbiliclaw.soul.avoidance_speculator import load_avoidance_state

            runtime_config = getattr(ctx, "config", None)
            avoidance_state = load_avoidance_state(runtime_config.data_path)
            active = [item for item in avoidance_state.active if item.status == "active"]
            items = [
                {
                    "domain": item.domain,
                    "reason": item.reason,
                    "confidence": item.confidence,
                    "source_mode": item.source_mode,
                    "source_signal": item.source_signal,
                    "status": item.status,
                    "specifics": [
                        {"name": specific.name, "confirmation_count": specific.confirmation_count}
                        for specific in item.specifics
                        if specific.name.strip()
                    ],
                }
                for item in active[:6]
            ]
            return {"items": items}
        except Exception:
            logger.debug("Failed to load pending avoidance probes", exc_info=True)
            return {"items": []}

    @app.post("/api/avoidance-probes/respond")
    async def respond_to_avoidance_probe(payload: dict[str, Any]) -> Any:
        """User responds to a speculated avoidance probe."""
        domain = str(payload.get("domain", "")).strip()
        response_type = str(payload.get("response", "")).strip().lower()

        if not domain:
            raise HTTPException(status_code=422, detail="domain is required")
        if response_type not in {"confirm", "reject", "chat"}:
            raise HTTPException(status_code=422, detail="response must be confirm, reject, or chat")

        speculator = getattr(ctx.soul_engine, "_avoidance_speculator", None)
        if speculator is None:
            raise HTTPException(status_code=503, detail="Avoidance speculator not available")

        def metadata_fn(item_domain: str) -> dict[str, object]:
            return _probe_metadata_from_active_avoidance(
                speculator,
                item_domain,
            )

        if response_type == "confirm":
            metadata = metadata_fn(domain)
            confirm_fn = getattr(speculator, "user_confirm_avoidance", None)
            active_avoidance = confirm_fn(domain) if callable(confirm_fn) else None
            ok = active_avoidance is not None
            if ok:
                _record_probe_feedback_history(
                    domain,
                    "confirm",
                    speculator=speculator,
                    state_key="avoidance_probe_feedback_history",
                    metadata=metadata,
                )
                topics = topics_for_confirmed_avoidance(active_avoidance)
                summary = f"你确认了避开「{domain}」，已开始更新不喜欢方向。"
                _record_probe_cognition(
                    summary,
                    domain,
                    "confirmed",
                    source="avoidance_probe",
                )
                await _publish_probe_event("avoidance.confirmed", summary, domain)

                async def _apply_confirmed_avoidance() -> None:
                    try:
                        changes = await apply_new_dislikes(
                            memory=ctx.memory_manager,
                            database=getattr(ctx, "database", None)
                            or getattr(ctx.memory_manager, "_database", None),
                            embedding_service=getattr(ctx.soul_engine, "_embedding_service", None),
                            llm_service=getattr(ctx, "llm_service", None),
                            topics=topics,
                        )
                        if changes:
                            _record_probe_cognition(
                                f"避雷方向「{domain}」的不喜欢画像已更新。",
                                domain,
                                "confirmed",
                                source="avoidance_probe",
                                detail="\n".join(changes),
                            )
                    except Exception:
                        logger.exception(
                            "Background avoidance dislike writeback failed: %s",
                            domain,
                        )

                task = asyncio.create_task(_apply_confirmed_avoidance())
                fire_and_forget_tasks.add(task)
                task.add_done_callback(fire_and_forget_tasks.discard)
            return {"ok": ok, "action": "confirmed", "domain": domain}

        if response_type == "reject":
            metadata = metadata_fn(domain)
            reject_fn = getattr(speculator, "user_reject_avoidance", None)
            ok = bool(reject_fn(domain) if callable(reject_fn) else False)
            if ok:
                _record_probe_feedback_history(
                    domain,
                    "reject",
                    speculator=speculator,
                    state_key="avoidance_probe_feedback_history",
                    metadata=metadata,
                )
                _record_probe_cognition(
                    f"你表示并不需要避开「{domain}」，30 天内不再推送。",
                    domain,
                    "rejected",
                    source="avoidance_probe",
                )
                await _publish_probe_event(
                    "avoidance.rejected",
                    f"已记录：你并不需要避开「{domain}」，30 天内不再推送。",
                    domain,
                )
            return {"ok": ok, "action": "rejected", "domain": domain}

        raw_message = str(payload.get("message", "")).strip()
        if not raw_message:
            raw_message = f"我想聊聊你猜我可能想避开的「{domain}」这个方向"
        contextual_message = f"[关于避雷方向「{domain}」的反馈] {raw_message}"
        if ctx.dialogue is None:
            return {"ok": False, "action": "chat", "domain": domain, "reply": "对话引擎暂不可用。"}

        concurrency = getattr(ctx.discovery_engine, "_concurrency", None)
        if concurrency is not None:
            concurrency.chat_active = True
        try:
            reply = await asyncio.wait_for(
                ctx.dialogue.respond(contextual_message),
                timeout=30,
            )
            sentiment, classifier = await _classify_probe_sentiment(
                raw_message,
                reply,
                domain,
            )
        except TimeoutError:
            return {
                "ok": False,
                "action": "chat",
                "domain": domain,
                "reply": "后台正忙，等一下再聊。",
            }
        except Exception:
            logger.exception("Dialogue failed for avoidance probe chat: %s", domain)
            return {
                "ok": False,
                "action": "chat",
                "domain": domain,
                "reply": "聊天出了点问题，稍后再试。",
            }
        finally:
            if concurrency is not None:
                concurrency.chat_active = False

        if sentiment == "negative":
            chat_response = "avoidance_chat_confirmed"
            resulting_action = "confirmed"
            speculator.observe(
                [
                    {
                        "event_type": "dislike",
                        "title": domain,
                        "metadata": {
                            "feedback_type": "dislike",
                            "user_message": raw_message,
                            "source": "avoidance_probe_chat",
                        },
                    }
                ]
            )
            summary = f"你确认「{domain}」偏向不喜欢，确认度 +1。"
        elif sentiment in {"strong_positive", "weak_positive"}:
            chat_response = "avoidance_chat_rejected"
            resulting_action = "rejected"
            reject_fn = getattr(speculator, "user_reject_avoidance", None)
            if callable(reject_fn):
                reject_fn(domain, cooldown_days=14)
            summary = f"你表示其实不排斥「{domain}」，已暂时搁置 14 天。"
        else:
            chat_response = "avoidance_chat_neutral"
            resulting_action = "none"
            summary = f"关于避雷方向「{domain}」你说：{raw_message}"

        _record_probe_feedback_history(
            domain,
            chat_response,
            speculator=speculator,
            message=raw_message,
            classification=sentiment,
            classifier=classifier,
            resulting_action=resulting_action,
            state_key="avoidance_probe_feedback_history",
            metadata_fn=metadata_fn,
        )
        detail = f"你的反馈：{raw_message}\n阿b的回复：{reply}"
        _record_probe_cognition(
            summary,
            domain,
            "chat",
            source="avoidance_probe",
            detail=detail,
        )
        await _publish_probe_event("avoidance.chat", summary, domain)
        return JSONResponse(
            content={"ok": True, "action": "chat", "domain": domain, "reply": reply}
        )

    return {
        "record_exploration_buffer_event": _record_exploration_buffer_event,
        "recommendation_buffer_domain": _recommendation_buffer_domain,
    }

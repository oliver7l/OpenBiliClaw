"""Feedback, topics, and insights routes for OpenBiliClaw API."""

from __future__ import annotations

import logging
import re
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException

from openbiliclaw.api.models import (
    FeedbackIn,
    FeedbackResponse,
    InsightFeedbackIn,
    InsightFeedbackResponse,
    RecommendationClickIn,
    RecommendationClickResponse,
    TopicCreateIn,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)


# ── Lazy imports for module-level helpers ─────


def _get_fallback_recommendation_click_url(
    source_platform: str, content_id: str, bvid: str = ""
) -> str:
    from openbiliclaw.api.app import _fallback_recommendation_click_url

    return _fallback_recommendation_click_url(
        source_platform=source_platform, content_id=content_id, bvid=bvid
    )


def _get_infer_source_platform_from_url(url: str) -> str:
    from openbiliclaw.api.app import _infer_source_platform_from_url

    return _infer_source_platform_from_url(url)


def _get_normalize_source_platform(raw: str) -> str:
    from openbiliclaw.api.app import _normalize_source_platform

    return _normalize_source_platform(raw)


def _get_project_root() -> Path:
    from openbiliclaw.api.app import _PROJECT_ROOT

    return _PROJECT_ROOT


# ── Route registration ───────────────────────────────────────────


def register_feedback_topics_routes(
    app: FastAPI,
    ctx: Any,
    *,
    schedule_post_feedback_tasks: Callable[[], None] | None = None,
    record_exploration_buffer_event: Callable[..., None] | None = None,
    recommendation_buffer_domain: Callable[[dict[str, object]], tuple[str, list[str]]]
    | None = None,
) -> None:
    """Register feedback, topics, and insight endpoints on the FastAPI app."""

    @app.post("/api/feedback", response_model=FeedbackResponse)
    async def feedback(payload: FeedbackIn) -> FeedbackResponse:
        feedback_type = payload.feedback_type.strip().lower()
        note = payload.note.strip()
        if feedback_type not in {"like", "dislike", "comment", "dismiss"}:
            raise HTTPException(status_code=422, detail="Unsupported feedback type.")
        if feedback_type == "comment" and not note:
            raise HTTPException(status_code=422, detail="Comment feedback requires note.")

        recommendation = ctx.database.get_recommendation_by_id(payload.recommendation_id)
        if recommendation is None:
            raise HTTPException(status_code=404, detail="Recommendation not found.")

        ctx.database.update_recommendation_feedback(
            payload.recommendation_id,
            feedback_type=feedback_type,
            feedback_note=note,
        )
        from openbiliclaw.sources.event_format import (
            SOURCE_BILIBILI,
            build_event,
        )

        rec_title = str(recommendation.get("title", ""))
        # Tailor a natural-language context per feedback type — the
        # "feedback" verb in the generic table doesn't capture the
        # like/dislike/comment distinction the LLM cares about.
        feedback_label = {
            "like": "点赞了",
            "dislike": "踩了",
            "comment": "评论了",
            "dismiss": "忽略了",
        }.get(feedback_type, "反馈了")
        feedback_context = f"在 B 站{feedback_label}《{rec_title}》"
        if note:
            feedback_context = f"{feedback_context},备注:{note}"
        await ctx.memory_manager.propagate_event(
            build_event(
                event_type="feedback",
                source_platform=SOURCE_BILIBILI,
                title=rec_title,
                context=feedback_context,
                metadata={
                    "recommendation_id": payload.recommendation_id,
                    "bvid": recommendation.get("bvid", ""),
                    "feedback_type": feedback_type,
                    "feedback_note": note,
                },
            )
        )
        buffer_domain: str = ""
        buffer_specifics: list[str] = []
        if recommendation_buffer_domain is not None:
            buffer_domain, buffer_specifics = recommendation_buffer_domain(recommendation)
        if feedback_type == "like" and record_exploration_buffer_event is not None:
            record_exploration_buffer_event(
                domain=buffer_domain,
                specifics=buffer_specifics,
                source_event="card_like",
                evidence_id=str(recommendation.get("bvid", "")),
            )
        elif feedback_type == "dislike" and record_exploration_buffer_event is not None:
            record_exploration_buffer_event(
                domain=buffer_domain,
                specifics=buffer_specifics,
                source_event="negative",
                evidence_id=str(recommendation.get("bvid", "")),
            )
        record_immediate_feedback_cognition = getattr(
            ctx.soul_engine,
            "record_immediate_feedback_cognition",
            None,
        )
        if callable(record_immediate_feedback_cognition):
            with suppress(Exception):
                record_immediate_feedback_cognition(
                    feedback_type=feedback_type,
                    title=str(recommendation.get("title", "")),
                    note=note,
                )
        if schedule_post_feedback_tasks is not None:
            schedule_post_feedback_tasks()
        return FeedbackResponse(
            ok=True,
            recommendation_id=payload.recommendation_id,
            feedback_type=feedback_type,
        )

    @app.post(
        "/api/recommendation-click",
        response_model=RecommendationClickResponse,
    )
    async def recommendation_click(
        payload: RecommendationClickIn,
    ) -> RecommendationClickResponse:
        """Ingest a recommendation click-through as a strong profile signal.

        The click is evidence that the user actively chose to watch a
        recommended video. It is treated as a strong signal that bypasses
        the pipeline's min_signals gate and updates Interest + Surface
        immediately. If the recommendation_id resolves to a stored card,
        its metadata (title, topic, up_name) is pulled from the database
        so the payload reaches the pipeline even when the extension sends
        only a bare BV id.
        """
        from obc_soul.pipeline import signal_from_recommendation_click

        recommendation: dict[str, object] | None = None
        if payload.recommendation_id is not None:
            recommendation = ctx.database.get_recommendation_by_id(
                payload.recommendation_id,
            )

        bvid = (payload.bvid or "").strip()
        content_id = (payload.content_id or "").strip()
        content_url = (payload.content_url or "").strip()
        source_platform_raw = (payload.source_platform or "").strip()
        title = (payload.title or "").strip()
        topic_label = (payload.topic_label or "").strip()
        up_name = (payload.up_name or "").strip()

        if recommendation is not None:
            bvid = bvid or str(recommendation.get("bvid", "") or "").strip()
            content_id = content_id or str(recommendation.get("content_id", "") or "").strip()
            content_url = content_url or str(recommendation.get("content_url", "") or "").strip()
            source_platform_raw = (
                source_platform_raw or str(recommendation.get("source_platform", "") or "").strip()
            )
            title = title or str(recommendation.get("title", "") or "").strip()
            topic_label = topic_label or str(recommendation.get("topic_label", "") or "").strip()
            up_name = up_name or str(recommendation.get("up_name", "") or "").strip()

        content_id = content_id or bvid
        bvid = bvid or content_id
        if not bvid:
            raise HTTPException(status_code=422, detail="bvid is required.")
        if not source_platform_raw:
            source_platform_raw = _get_infer_source_platform_from_url(content_url)
        source_platform = _get_normalize_source_platform(source_platform_raw)
        if not content_url:
            content_url = _get_fallback_recommendation_click_url(
                source_platform=source_platform,
                content_id=content_id,
                bvid=bvid,
            )

        # Persist the click as an event so history/query paths can see it.
        from openbiliclaw.sources.event_format import (
            build_event,
            format_event_context,
        )

        click_extra_parts: list[str] = []
        if topic_label:
            click_extra_parts.append(f"主题:{topic_label}")
        click_context = format_event_context(
            event_type="click",
            source_platform=source_platform,
            title=title,
            author=up_name,
            extra=",".join(click_extra_parts),
        )
        click_metadata: dict[str, object] = {
            "recommendation_id": payload.recommendation_id,
            "bvid": bvid,
            "content_id": content_id,
            "content_url": content_url,
            "source_platform": source_platform,
            "topic_label": topic_label,
            "up_name": up_name,
            "source": "recommendation_click",
        }
        # v0.3.x event-satisfaction: forward dwell so the persisted
        # click row can be classified as meaningful_dwell vs quick_exit.
        # Absent fields stay absent; storage classifier degrades to
        # unknown / missing_dwell. Storage is the single classification
        # owner — do not classify here.
        if payload.watch_seconds is not None:
            click_metadata["watch_seconds"] = payload.watch_seconds
        if payload.video_duration_seconds is not None:
            click_metadata["video_duration_seconds"] = payload.video_duration_seconds
        with suppress(Exception):
            await ctx.memory_manager.propagate_event(
                build_event(
                    event_type="click",
                    source_platform=source_platform,
                    title=title,
                    url=content_url,
                    author=up_name,
                    context=click_context,
                    metadata=click_metadata,
                )
            )
        buffer_domain = ""
        buffer_specifics: list[str] = []
        if recommendation_buffer_domain is not None:
            buffer_domain, buffer_specifics = recommendation_buffer_domain(
                {
                    "title": title,
                    "topic_label": topic_label,
                    "bvid": bvid,
                }
            )
        if record_exploration_buffer_event is not None:
            record_exploration_buffer_event(
            domain=buffer_domain,
            specifics=buffer_specifics,
            source_event="plain_click",
            evidence_id=bvid,
        )

        # Push a strong signal into the profile update pipeline.
        layers_updated: list[str] = []
        pipeline = getattr(ctx.soul_engine, "pipeline", None) if ctx.soul_engine else None
        if pipeline is not None:
            signal = signal_from_recommendation_click(
                bvid=bvid,
                title=title,
                recommendation_id=payload.recommendation_id,
                topic_label=topic_label,
                up_name=up_name,
                content_id=content_id,
                content_url=content_url,
                source_platform=source_platform,
            )
            try:
                ingest_result = await pipeline.ingest(signal)
            except Exception:
                logger.exception("Failed to ingest recommendation_click signal")
            else:
                layers_updated = [r.layer.value for r in ingest_result.layers_updated]

        # E1: close the exposure→click loop. A click-through is consumption —
        # mark the stored card presented+clicked so it stops being re-served
        # (get_recommendations(exclude_processed=True) drops clicked rows) and
        # presented_at/clicked_at yield real CTR data for online metrics.
        recommendation_ids_to_mark: list[int] = []
        if payload.recommendation_id is not None:
            recommendation_ids_to_mark.append(int(payload.recommendation_id))
        else:
            # 上报缺 id 时按 bvid/content_id 回查最近一条推荐卡（mobile 历史卡片
            # 的 reportClick 与旧版扩展都可能不带 id）。
            with suppress(Exception):
                resolved_id = ctx.database.find_latest_recommendation_id_by_bvid(bvid)
                if resolved_id is not None:
                    recommendation_ids_to_mark.append(resolved_id)
        if recommendation_ids_to_mark:
            try:
                ctx.database.mark_recommendations_clicked(recommendation_ids_to_mark)
            except Exception:
                logger.exception("mark_recommendations_clicked failed")

        return RecommendationClickResponse(
            ok=True,
            bvid=bvid,
            layers_updated=layers_updated,
        )

    # ── Topics (专题) ─────────────────────────────────────────────
    # User-curated collections (e.g. 广告, 去有风的地方) continuously
    # collected from multiple sites via scripts/collect_topic.py. The API
    # exposes list/detail/create and a manual "collect now" trigger.

    @app.get("/api/topics", response_model=None)
    def api_topics_list() -> list[dict[str, object]]:
        """List all topics with item counts (newest first)."""
        import json as _json

        topics = ctx.database.list_topics(include_paused=True)
        out: list[dict[str, object]] = []
        for t in topics:
            out.append(
                {
                    "id": t["id"],
                    "name": t["name"],
                    "slug": t["slug"],
                    "description": t.get("description") or "",
                    "keywords": _json.loads(t.get("keywords") or "[]"),
                    "platforms": _json.loads(t.get("platforms") or '["bilibili"]'),
                    "status": t.get("status") or "active",
                    "item_count": int(t.get("item_count") or 0),
                    "last_collected_at": t.get("last_collected_at"),
                    "created_at": t.get("created_at"),
                    "updated_at": t.get("updated_at"),
                }
            )
        return out

    @app.post("/api/topics", response_model=None)
    async def api_topics_create(payload: TopicCreateIn) -> dict[str, object]:
        """Create a new topic. slug must be url-safe; keywords/platforms optional."""
        import json as _json

        name = (payload.name or "").strip()
        slug = (payload.slug or "").strip().lower()
        if not name or not slug:
            raise HTTPException(status_code=422, detail="name and slug are required.")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
            raise HTTPException(status_code=422, detail="slug 只能包含小写字母、数字和连字符。")
        if ctx.database.get_topic_by_slug(slug) is not None:
            raise HTTPException(status_code=409, detail=f"slug 已存在: {slug}")
        keywords = list(payload.keywords or [])
        platforms = list(payload.platforms or ["bilibili"])
        try:
            topic_id = ctx.database.create_topic(
                name=name,
                slug=slug,
                description=(payload.description or "").strip(),
                keywords=keywords,
                platforms=platforms,
            )
        except Exception:
            logger.exception("create_topic failed")
            raise HTTPException(
                status_code=409, detail="专题创建失败（名称或 slug 冲突？）"
            ) from None
        topic = ctx.database.get_topic_by_id(topic_id)
        assert topic is not None  # just created
        return {
            "id": topic["id"],
            "name": topic["name"],
            "slug": topic["slug"],
            "description": topic.get("description") or "",
            "keywords": _json.loads(topic.get("keywords") or "[]"),
            "platforms": _json.loads(topic.get("platforms") or '["bilibili"]'),
            "status": topic.get("status") or "active",
            "item_count": 0,
        }

    @app.get("/api/topics/{slug}", response_model=None)
    def api_topic_detail(slug: str) -> dict[str, object]:
        """Topic detail + collected items (newest first, paginated)."""
        import json as _json

        topic = ctx.database.get_topic_by_slug(slug)
        if topic is None:
            raise HTTPException(status_code=404, detail=f"未找到专题: {slug}")
        items = ctx.database.get_topic_items(topic["id"], limit=200)
        return {
            "id": topic["id"],
            "name": topic["name"],
            "slug": topic["slug"],
            "description": topic.get("description") or "",
            "keywords": _json.loads(topic.get("keywords") or "[]"),
            "platforms": _json.loads(topic.get("platforms") or '["bilibili"]'),
            "status": topic.get("status") or "active",
            "item_count": int(topic.get("item_count") or 0),
            "last_collected_at": topic.get("last_collected_at"),
            "created_at": topic.get("created_at"),
            "items": items,
        }

    @app.post("/api/topics/{slug}/collect", response_model=None)
    async def api_topic_collect(slug: str) -> dict[str, object]:
        """Trigger a collection pass for one topic (autocli, may take ~30s+)."""
        topic = ctx.database.get_topic_by_slug(slug)
        if topic is None:
            raise HTTPException(status_code=404, detail=f"未找到专题: {slug}")
        try:
            import importlib.util as _ilu

            script = _get_project_root() / "scripts" / "collect_topic.py"
            spec = _ilu.spec_from_file_location("collect_topic", script)
            assert spec and spec.loader is not None
            module = _ilu.module_from_spec(spec)
            spec.loader.exec_module(module)
            stats = module.collect_topic(ctx.database, topic, limit=8)
        except Exception:
            logger.exception("topic collect failed")
            raise HTTPException(status_code=500, detail="专题搜集失败，详见服务日志。") from None
        return {
            "ok": True,
            "slug": slug,
            "new": stats["new"],
            "dup": stats["dup"],
            "failed": stats["failed"],
            "searches": stats["searches"],
            "item_count": ctx.database.count_topic_items(topic["id"]),
        }

    @app.post("/api/insights/feedback", response_model=InsightFeedbackResponse)
    async def insight_feedback(payload: InsightFeedbackIn) -> InsightFeedbackResponse:
        """Calibrate an insight hypothesis from a user confirm/reject.

        The popup's insight cards surface ``active_insights`` (hypothesis +
        confidence). This endpoint routes a confirm/reject back into
        ``SoulEngine.update_from_feedback`` so the hypothesis is validated and
        re-weighted (confirm → confidence ≥0.75; reject → ≤0.35), closing the
        loop that was previously implemented but unwired.
        """
        signal = payload.signal.strip().lower()
        if signal not in {"confirm", "like", "support", "reject", "dislike", "deny"}:
            raise HTTPException(status_code=422, detail="Unsupported insight feedback signal.")
        hypothesis = payload.hypothesis.strip()
        if not hypothesis:
            raise HTTPException(status_code=422, detail="hypothesis is required.")
        if ctx.soul_engine is None:
            raise HTTPException(status_code=503, detail="Soul engine not ready.")

        result = await ctx.soul_engine.update_from_feedback(
            {"hypothesis": hypothesis, "signal": signal}
        )
        return InsightFeedbackResponse(
            ok=True,
            matched=bool(result.get("matched", False)),
            hypothesis=str(result.get("hypothesis", hypothesis)),
            signal=str(result.get("signal", signal)),
            validated=bool(result.get("validated", False)),
            confidence=float(result.get("confidence", 0.0)),
        )

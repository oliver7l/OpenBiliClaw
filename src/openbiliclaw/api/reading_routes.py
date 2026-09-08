"""Reading management API routes（从 app.py 提取）。

包含今日建议、每日简报、意图搜索、阅读统计、自动标签、相似文章等端点。
通过 ``register_reading_routes(app, ctx)`` 注册。
"""

from __future__ import annotations

import logging
import time
from contextlib import suppress
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from openbiliclaw.api.utils import (
    READING_VALID_SOURCE_TYPES as _READING_VALID_SOURCE_TYPES,
)
from openbiliclaw.api.utils import (
    READING_VALID_STATUSES as _READING_VALID_STATUSES,
)
from openbiliclaw.api.utils import apply_reading_exclusions as _apply_reading_exclusions
from openbiliclaw.api.utils import article_fit_score as _article_fit_score
from openbiliclaw.api.utils import load_interest_keywords as _load_interest_keywords
from openbiliclaw.api.utils import rule_parse_reading_intent as _rule_parse_reading_intent

logger = logging.getLogger(__name__)


def register_reading_routes(app: FastAPI, ctx: Any) -> None:
    """Register reading-related routes onto *app*."""

    def _rank_unread_by_interest(database: Any, *, limit: int) -> list[dict[str, Any]]:
        """未读文章按兴趣契合度排序（今日建议与每日简报共用）。

        抽样最近未读文章（优先有正文），按 soul 兴趣画像关键词打分，
        返回 top ``limit``，附人读得懂的命中理由。
        """
        keywords = _load_interest_keywords()
        cands = database.get_recent_articles(limit=300, status="unread")
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in cands:
            text = " ".join(
                [
                    str(item.get("title") or ""),
                    str(item.get("summary") or ""),
                    str(item.get("tags") or ""),
                ]
            ).lower()
            score = 0.0
            reasons: list[str] = []
            for name, weight in keywords:
                if name and name.lower() in text:
                    score += weight
                    if len(reasons) < 2:
                        reasons.append(name)
            if score > 0:
                item["fit_score"] = round(min(1.0, score / 1.5), 3)
                item["fit_reason"] = reasons
                scored.append((score, item))
        scored.sort(key=lambda kv: kv[0], reverse=True)
        return [it for _, it in scored[: max(1, int(limit))]]

    @app.get("/api/reading/suggestions")
    def daily_reading_suggestions(limit: int = 5) -> JSONResponse:
        """Today's reading picks: unread articles ranked by interest fit."""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        limit = max(1, min(int(limit), 20))
        items = _rank_unread_by_interest(database, limit=limit)
        return JSONResponse(
            {
                "ok": True,
                "items": items,
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    @app.get("/api/reading/daily-brief")
    def reading_daily_brief() -> JSONResponse:
        """每日简报（确定性聚合，零 LLM）：阅读库页顶部卡片的数据源。

        三个板块：
        - ``reading``: 今日已读回顾——当天标记 finished 的文章数、来源
          分布、主题标签（``Database.get_daily_reading_summary``）。
        - ``profile``: 画像今天学到什么——今天的认知更新（含手动纠偏），
          与画像页共用 ``memory_manager.load_cognition_updates``；soul
          未初始化时该板块为空，不阻塞其余板块。
        - ``tomorrow``: 明日值得看——未读文章按兴趣契合度 top 5
          （与今日建议共用 ``_rank_unread_by_interest``）。
        """
        import datetime as _dt

        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        today = _dt.datetime.now().strftime("%Y-%m-%d")

        reading = database.get_daily_reading_summary(day=today)

        profile_updates: list[dict[str, Any]] = []
        load_cognition_updates = getattr(ctx.memory_manager, "load_cognition_updates", None)
        if callable(load_cognition_updates):
            with suppress(Exception):
                for item in load_cognition_updates():
                    created = str(item.get("created_at") or "")
                    if not created.startswith(today):
                        continue
                    summary_text = str(item.get("summary") or "").strip()
                    if not summary_text:
                        continue
                    profile_updates.append(
                        {
                            "summary": summary_text,
                            "source_label": str(item.get("source_label") or ""),
                            "created_at": created,
                        }
                    )
                    if len(profile_updates) >= 5:
                        break

        tomorrow: list[dict[str, Any]] = []
        with suppress(Exception):
            tomorrow = [
                {
                    "id": it.get("id"),
                    "title": it.get("title"),
                    "source_type": it.get("source_type"),
                    "fit_score": it.get("fit_score"),
                    "fit_reason": it.get("fit_reason", []),
                }
                for it in _rank_unread_by_interest(database, limit=5)
            ]

        return JSONResponse(
            {
                "ok": True,
                "date": today,
                "reading": reading,
                "profile": {"updates": profile_updates},
                "tomorrow": tomorrow,
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    @app.get("/api/reading/intent-search")
    async def reading_intent_search(
        q: str = "",
        limit: int = 30,
        source_type: str = "",
        status: str = "",
    ) -> JSONResponse:
        """自然语言意图搜索阅读库：把口语查询解析成关键词 / 排除 / 来源 / 状态。

        与旧的 ``/api/articles?q=`` 纯子串匹配不同，这里先「理解」查询：

        - **主路径 LLM**：用 ``soul_engine.llm_ask`` 把 ``q`` 拆成
          ``{keywords, exclude, source_type, status}``（能处理同义词、
          「不要营销号」这类排除、「最近想读点轻松的」这类口语）。
        - **规则回退**：LLM 不可用 / 未配置 / 解析失败时走
          :func:`_rule_parse_reading_intent`，按词表剥离来源、状态与
          「不要 X」排除，剩余作关键词。
        - 关键词并集检索（复用 FTS ``search_articles``）→ 排除过滤 →
          按兴趣画像契合度（``_article_fit_score``）重排。

        显式传入的 ``source_type`` / ``status`` 覆盖模型推断值，保证与
        前端来源页 / 状态下拉一致。返回附 ``intent`` 供前端回显「我理解成
        了什么」，让纠偏有据可依。
        """
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        q = (q or "").strip()
        limit = max(1, min(int(limit), 60))
        if not q:
            return JSONResponse({"ok": True, "items": [], "total": 0, "intent": {}})

        intent: dict[str, Any] | None = None
        soul_engine = getattr(ctx, "soul_engine", None)
        llm_ask = getattr(soul_engine, "llm_ask", None) if soul_engine is not None else None
        if callable(llm_ask) and len(q) >= 3:
            with suppress(Exception):
                import json as _json

                sys_prompt = (
                    "你是阅读库搜索的意图解析器。把用户的自然语言查询拆成结构化检索意图，"
                    '只输出 JSON：{"keywords":[检索关键词],'
                    '"exclude":[要排除的词，如『不要营销号』里的『营销号』],'
                    '"source_type":来源或null,'
                    '"status":unread|reading|finished|archived 之一或null}。'
                    f"source_type 只能取这些值之一：{sorted(_READING_VALID_SOURCE_TYPES)}；"
                    "不符合的填 null。keywords 用具体、聚焦的词，去掉停用词。"
                )
                raw = await llm_ask(sys_prompt, q)
                if raw:
                    parsed = _json.loads(raw)
                    if isinstance(parsed, dict):
                        kws = [
                            str(k).strip() for k in (parsed.get("keywords") or []) if str(k).strip()
                        ]
                        exc = [
                            str(e).strip() for e in (parsed.get("exclude") or []) if str(e).strip()
                        ]
                        src = str(parsed.get("source_type") or "").strip().lower()
                        stt = str(parsed.get("status") or "").strip().lower()
                        intent = {
                            "keywords": kws[:6],
                            "exclude": exc,
                            "source_type": src if src in _READING_VALID_SOURCE_TYPES else "",
                            "status": stt if stt in _READING_VALID_STATUSES else "",
                            "llm_used": True,
                        }
        if intent is None:
            intent = _rule_parse_reading_intent(q)

        # 显式查询参数优先于模型推断，避免与前端筛选下拉打架。
        if source_type.strip():
            intent["source_type"] = source_type.strip().lower()
        if status.strip():
            intent["status"] = status.strip().lower()

        source_type_filter: str | None = intent["source_type"] or None
        st = intent["status"] or None
        terms = intent["keywords"] or [q]

        merged: dict[int, dict[str, Any]] = {}
        order: list[int] = []
        per_term_limit = max(limit, 30)
        for term in terms:
            rows = database.search_articles(
                q=term,
                limit=per_term_limit,
                offset=0,
                source_type=source_type_filter,
                status=st,
            )
            for row in rows:
                try:
                    rid = int(row.get("id"))
                except (TypeError, ValueError):
                    continue
                if rid not in merged:
                    merged[rid] = row
                    order.append(rid)
        items = [merged[rid] for rid in order]
        items = _apply_reading_exclusions(items, intent.get("exclude") or [])

        for item in items:
            text = " ".join(
                [
                    str(item.get("title") or ""),
                    str(item.get("summary") or ""),
                    str(item.get("tags") or ""),
                ]
            )
            item["fit_score"] = _article_fit_score(text)
        items.sort(
            key=lambda it: (
                float(it.get("fit_score") or 0.0),
                str(it.get("published_at") or ""),
            ),
            reverse=True,
        )
        items = items[:limit]
        return JSONResponse(
            {
                "ok": True,
                "items": items,
                "total": len(items),
                "intent": intent,
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    @app.get("/api/reading/stats")
    def reading_stats() -> JSONResponse:
        """Reading-library dashboard: totals, monthly finished trend,
        source mix, top tags, notes count, and a light interest-shift
        view (recently-read tags vs the current interest profile)."""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        stats = database.get_article_reading_stats()
        # 兴趣迁移：最近在读/读完文章打到的画像关键词
        recent = database.get_articles_for_reading_stats(limit=100)
        interest_shift: dict[str, Any] = {"matched": [], "recent_articles": len(recent)}
        keywords = _load_interest_keywords()
        hit_counter: dict[str, float] = {}
        for item in recent:
            text = " ".join(
                [
                    str(item.get("title") or ""),
                    str(item.get("tags") or ""),
                ]
            ).lower()
            for name, weight in keywords:
                if name and name.lower() in text:
                    hit_counter[name] = hit_counter.get(name, 0.0) + weight
        interest_shift["matched"] = sorted(hit_counter.items(), key=lambda kv: kv[1], reverse=True)[
            :10
        ]
        return JSONResponse({"ok": True, "stats": stats, "interest_shift": interest_shift})

    @app.post("/api/reading/auto-tag")
    def reading_auto_tag(
        limit: int = 500,
        status: str | None = None,
        only_sparse: bool = True,
        max_new: int = 5,
        min_weight: float = 0.15,
    ) -> JSONResponse:
        """给阅读库补打轻量兴趣标签（确定性规则：画像关键词 + ``#话题``）。

        命中即 merge 进现有 ``tags``（保留来源标签、大小写去重、幂等），
        零 LLM、零网络。冷画像（无兴趣词）时直接跳过，不臆造标签。
        """
        import json as _json

        from openbiliclaw.reading.tags import generate_tags, merge_tag_lists

        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        keywords = _load_interest_keywords()
        if not keywords:
            return JSONResponse(
                {
                    "ok": True,
                    "scanned": 0,
                    "updated": 0,
                    "added": 0,
                    "note": "no interest profile yet; skipped",
                },
                status_code=200,
            )
        rows = database.iter_articles_for_tagging(
            limit=limit, status=status, only_sparse=only_sparse
        )
        updated = 0
        added_total = 0
        for row in rows:
            try:
                existing = _json.loads(row.get("tags") or "[]")
            except Exception:
                existing = []
            if not isinstance(existing, list):
                existing = []
            new_tags = generate_tags(
                title=str(row.get("title") or ""),
                summary=str(row.get("summary") or ""),
                content_text=str(row.get("content_text") or ""),
                interest_keywords=keywords,
                existing=[str(t) for t in existing],
                max_new=max_new,
                min_weight=min_weight,
            )
            if not new_tags:
                continue
            merged = merge_tag_lists([str(t) for t in existing], new_tags)
            try:
                if database.update_article_tags(int(row["id"]), merged):
                    updated += 1
                    added_total += len(new_tags)
            except Exception:
                logger.exception("auto-tag write failed for article id=%s", row.get("id"))
        return JSONResponse(
            {"ok": True, "scanned": len(rows), "updated": updated, "added": added_total}
        )

    @app.get("/api/reading/similar")
    def reading_similar(id: int, k: int = 8, candidate_limit: int = 500) -> JSONResponse:
        """库内找相似：按 tag + 标题 bigram 的确定性相似度（零 LLM / 零网络）。"""
        from openbiliclaw.reading.tags import similarity

        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        target = database.get_article(id)
        if not target:
            return JSONResponse({"ok": False, "error": "article not found"}, status_code=404)
        k = max(1, min(int(k), 30))
        cands = database.get_recent_articles(limit=max(1, min(int(candidate_limit), 1000)))
        t_title = str(target.get("title") or "")
        t_tags = target.get("tags")
        scored: list[tuple[float, dict[str, Any]]] = []
        for c in cands:
            if str(c.get("id")) == str(id):
                continue
            if str(c.get("status") or "") == "hidden":
                continue
            s = similarity(
                t_title,
                t_tags,
                str(c.get("title") or ""),
                c.get("tags"),
            )
            if s > 0:
                scored.append((s, c))
        scored.sort(key=lambda kv: kv[0], reverse=True)
        items = [
            {
                "id": c.get("id"),
                "title": c.get("title"),
                "url": c.get("url"),
                "source_type": c.get("source_type"),
                "tags": c.get("tags"),
                "similarity": round(s, 3),
            }
            for s, c in scored[:k]
        ]
        return JSONResponse({"ok": True, "target_id": id, "items": items})

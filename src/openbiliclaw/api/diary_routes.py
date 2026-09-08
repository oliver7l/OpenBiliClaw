"""日记系统 API 路由（从 app.py 提取）。

包含日记 CRUD、导入、数据洞察、周报/月度/年度反思、知识图谱、
自进化循环、主动洞察引擎、三层/高级记忆、情绪分析、智能时间线、
碎片随手记、标签与人物提取、RAG 语义搜索与问答。
通过 ``register_diary_routes(app, ctx)`` 注册。
"""

import logging
from datetime import datetime
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from openbiliclaw.api.runtime_context import RuntimeContext
from openbiliclaw.diary import DiaryEntryCreate, DiaryEntryUpdate, DiaryService
from openbiliclaw.diary.importer import DiaryImporter

logger = logging.getLogger(__name__)

_diary_service: DiaryService | None = None
_diary_rag_service = None


def register_diary_routes(app: FastAPI, ctx: RuntimeContext) -> None:
    def _get_diary_service() -> DiaryService | None:
        """获取或创建日记服务实例（懒加载）。"""
        global _diary_service
        if _diary_service is not None:
            return _diary_service
        database = getattr(ctx, "database", None)
        if database is None:
            return None
        llm_service = getattr(ctx, "llm_service", None)
        _diary_service = DiaryService(database=database, llm_service=llm_service)
        return _diary_service

    @app.get("/api/diary")
    def diary_list(
        limit: int = 50,
        offset: int = 0,
        start_date: str | None = None,
        end_date: str | None = None,
        mood: str | None = None,
        source: str | None = None,
        tag: str | None = None,
        search: str | None = None,
        sort_by: str = "entry_date",
        sort_order: str = "DESC",
    ) -> JSONResponse:
        """列出日记，支持多条件筛选与搜索。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        mood_enum = MoodLevel(mood) if mood else None
        entries, total = svc.list_entries(
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
            start_date=start_date,
            end_date=end_date,
            mood=mood_enum,
            source=source,
            tag=tag,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [e.model_dump(mode="json") for e in entries],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )

    @app.get("/api/diary/stats")
    def diary_stats() -> JSONResponse:
        """获取日记统计信息。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        stats = svc.get_stats()
        return JSONResponse({"ok": True, "stats": stats.model_dump(mode="json")})

    @app.get("/api/diary/timeline")
    def diary_timeline(year: int | None = None, month: int | None = None) -> JSONResponse:
        """获取日记时间线视图。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        entries = svc.get_timeline(year=year, month=month)
        return JSONResponse(
            {
                "ok": True,
                "items": [e.model_dump(mode="json") for e in entries],
                "year": year,
                "month": month,
            }
        )

    @app.get("/api/diary/search")
    def diary_search(q: str, limit: int = 50) -> JSONResponse:
        """全文搜索日记。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        entries = svc.search_entries(q, limit=max(1, min(int(limit), 200)))
        return JSONResponse(
            {
                "ok": True,
                "query": q,
                "items": [e.model_dump(mode="json") for e in entries],
                "total": len(entries),
            }
        )

    @app.get("/api/diary/{entry_id:int}")
    def diary_get(entry_id: int) -> JSONResponse:
        """获取单篇日记详情（含分析结果）。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            entry = svc.get_entry(entry_id)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        analysis = svc.get_analysis(entry_id)
        return JSONResponse(
            {
                "ok": True,
                "entry": entry.model_dump(mode="json"),
                "analysis": analysis.model_dump(mode="json") if analysis else None,
            }
        )

    @app.post("/api/diary")
    def diary_create(payload: dict[str, Any]) -> JSONResponse:
        """创建一篇日记。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = DiaryEntryCreate(**payload)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"invalid payload: {exc}"}, status_code=400)
        entry = svc.create_entry(data)
        return JSONResponse({"ok": True, "entry": entry.model_dump(mode="json")}, status_code=201)

    @app.put("/api/diary/{entry_id}")
    def diary_update(entry_id: int, payload: dict[str, Any]) -> JSONResponse:
        """更新日记。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = DiaryEntryUpdate(**payload)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"invalid payload: {exc}"}, status_code=400)
        try:
            entry = svc.update_entry(entry_id, data)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        return JSONResponse({"ok": True, "entry": entry.model_dump(mode="json")})

    @app.delete("/api/diary/{entry_id}")
    def diary_delete(entry_id: int) -> JSONResponse:
        """删除日记。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_entry(entry_id)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        return JSONResponse({"ok": True, "deleted": entry_id})

    @app.get("/api/diary/{entry_id}/analysis")
    def diary_get_analysis(entry_id: int) -> JSONResponse:
        """获取日记的分析结果。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        analysis = svc.get_analysis(entry_id)
        if analysis is None:
            return JSONResponse({"ok": True, "analysis": None, "message": "未分析"})
        return JSONResponse({"ok": True, "analysis": analysis.model_dump(mode="json")})

    @app.post("/api/diary/{entry_id}/analyze")
    async def diary_analyze(entry_id: int, force: bool = False) -> JSONResponse:
        """分析单篇日记（调用 LLM）。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        if svc.llm_service is None:
            return JSONResponse({"ok": False, "error": "LLM service 未配置"}, status_code=503)
        try:
            analysis = await svc.analyze_entry(entry_id, force=force)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        if analysis is None:
            return JSONResponse({"ok": False, "error": "分析失败"}, status_code=500)
        return JSONResponse({"ok": True, "analysis": analysis.model_dump(mode="json")})

    @app.post("/api/diary/analyze-batch")
    async def diary_analyze_batch(limit: int = 50, concurrency: int = 3) -> JSONResponse:
        """批量分析所有未分析的日记。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        if svc.llm_service is None:
            return JSONResponse({"ok": False, "error": "LLM service 未配置"}, status_code=503)
        results = await svc.analyze_unanalyzed(limit=max(1, min(int(limit), 200)), concurrency=max(1, min(int(concurrency), 10)))
        success = sum(1 for v in results.values() if v is not None)
        return JSONResponse(
            {
                "ok": True,
                "total": len(results),
                "success": success,
                "failed": len(results) - success,
                "results": {str(k): (v.model_dump(mode="json") if v else None) for k, v in results.items()},
            }
        )

    @app.post("/api/diary/import")
    def diary_import(payload: dict[str, Any]) -> JSONResponse:
        """导入日记文件。

        请求体：
        - file_path: 本地文件路径
        - format: lele / text / markdown（默认自动检测）
        - source: 来源标识
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        file_path = payload.get("file_path", "")
        if not file_path:
            return JSONResponse({"ok": False, "error": "缺少 file_path"}, status_code=400)
        fmt = payload.get("format", "auto")
        source = payload.get("source", "")
        importer = DiaryImporter(svc)
        try:
            if fmt == "lele" or (fmt == "auto" and "lele" in file_path.lower()):
                count, entries = importer.import_lele_diary(file_path, source=source or "import_lele")
            elif fmt == "markdown" or (fmt == "auto" and file_path.lower().endswith(".md")):
                count, entries = importer.import_markdown_file(file_path, source=source or "import_markdown")
            else:
                count, entries = importer.import_text_file(file_path, source=source or "import_text")
        except FileNotFoundError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            logger.exception("日记导入失败")
            return JSONResponse({"ok": False, "error": f"导入失败: {exc}"}, status_code=500)
        return JSONResponse(
            {
                "ok": True,
                "imported": count,
                "entries": [e.model_dump(mode="json") for e in entries],
                "format": fmt,
                "file_path": file_path,
            }
        )

    # ─── 日记数据洞察 API ───────────────────────────────────────────

    @app.get("/api/diary/insights/mood-trend")
    def diary_insights_mood_trend(
        granularity: str = "month",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> JSONResponse:
        """获取情绪趋势数据。

        Args:
            granularity: month / year
            start_date: 起始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import DiaryInsightsService

        insights = DiaryInsightsService(svc.store)
        trend = insights.get_mood_trend(granularity, start_date, end_date)
        return JSONResponse(
            {
                "ok": True,
                "granularity": granularity,
                "data": [
                    {
                        "period": p.period,
                        "avg_score": p.avg_score,
                        "entry_count": p.entry_count,
                        "mood_distribution": p.mood_distribution,
                    }
                    for p in trend
                ],
            }
        )

    @app.get("/api/diary/insights/streak")
    def diary_insights_streak() -> JSONResponse:
        """获取写作连续打卡统计。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import DiaryInsightsService

        insights = DiaryInsightsService(svc.store)
        streak = insights.get_writing_streak()
        return JSONResponse(
            {
                "ok": True,
                "current_streak": streak.current_streak,
                "longest_streak": streak.longest_streak,
                "total_days": streak.total_days,
                "this_week_count": streak.this_week_count,
                "this_month_count": streak.this_month_count,
            }
        )

    @app.get("/api/diary/insights/word-trend")
    def diary_insights_word_trend(granularity: str = "month") -> JSONResponse:
        """获取字数趋势数据。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import DiaryInsightsService

        insights = DiaryInsightsService(svc.store)
        trend = insights.get_word_trend(granularity)
        return JSONResponse({"ok": True, "granularity": granularity, "data": trend})

    @app.get("/api/diary/insights/keywords")
    def diary_insights_keywords(
        limit: int = 50,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> JSONResponse:
        """获取高频关键词（词云数据）。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import DiaryInsightsService

        insights = DiaryInsightsService(svc.store)
        keywords = insights.get_top_keywords(limit, start_date, end_date)
        return JSONResponse(
            {
                "ok": True,
                "data": [{"word": w, "count": c} for w, c in keywords],
            }
        )

    @app.get("/api/diary/insights/yearly/{year}")
    def diary_insights_yearly(year: int) -> JSONResponse:
        """获取年度洞察统计数据。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import DiaryInsightsService

        insights = DiaryInsightsService(svc.store)
        stats = insights.get_yearly_insight_stats(year)
        return JSONResponse({"ok": True, "year": year, "data": stats})

    @app.post("/api/diary/insights/yearly/{year}/generate")
    async def diary_insights_yearly_generate(year: int) -> JSONResponse:
        """生成年度洞察报告（调用 LLM）。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import DiaryInsightsService

        insights = DiaryInsightsService(svc.store)
        stats = insights.get_yearly_insight_stats(year)
        if stats.get("entry_count", 0) == 0:
            return JSONResponse({"ok": False, "error": f"{year}年暂无日记"}, status_code=404)

        prompt = DiaryInsightsService.build_yearly_report_prompt(year, stats)
        try:
            report = await svc._call_llm(prompt)  # noqa: SLF001
            return JSONResponse({"ok": True, "year": year, "report": report, "stats": stats})
        except Exception as exc:
            logger.exception("年度洞察报告生成失败")
            return JSONResponse(
                {"ok": False, "error": f"生成失败: {exc}", "stats": stats},
                status_code=500,
            )

    # ─── 日记反思 API（周报/月度反思/年度回顾/里程碑） ─────────────────

    @app.get("/api/diary/reflection/weekly")
    def diary_reflection_weekly(week_start: str | None = None) -> JSONResponse:
        """获取周报统计数据。

        Query:
        - week_start: 周开始日期（YYYY-MM-DD），默认本周一
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import ReflectionService

        reflection = ReflectionService(svc.store)
        report = reflection.generate_weekly_report(week_start)
        return JSONResponse({"ok": True, "data": report.__dict__})

    @app.post("/api/diary/reflection/weekly/generate")
    async def diary_reflection_weekly_generate(payload: dict[str, Any] | None = None) -> JSONResponse:
        """生成 AI 周报。

        请求体（可选）：
        - week_start: 周开始日期（YYYY-MM-DD），默认本周一
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import ReflectionService

        payload = payload or {}
        reflection = ReflectionService(svc.store)
        report = reflection.generate_weekly_report(payload.get("week_start"))
        if report.entry_count == 0:
            return JSONResponse({"ok": False, "error": "本周暂无日记"}, status_code=404)

        prompt = reflection.build_weekly_report_prompt(report)
        try:
            ai_result = await svc._call_llm(prompt)  # noqa: SLF001
            return JSONResponse({
                "ok": True,
                "data": report.__dict__,
                "ai_result": ai_result,
            })
        except Exception as exc:
            logger.exception("周报生成失败")
            return JSONResponse(
                {"ok": False, "error": f"生成失败: {exc}", "data": report.__dict__},
                status_code=500,
            )

    @app.get("/api/diary/reflection/monthly/{year}/{month}")
    def diary_reflection_monthly(year: int, month: int) -> JSONResponse:
        """获取月度反思统计数据。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import ReflectionService

        reflection = ReflectionService(svc.store)
        result = reflection.generate_monthly_reflection(year, month)
        return JSONResponse({"ok": True, "data": result.__dict__})

    @app.post("/api/diary/reflection/monthly/{year}/{month}/generate")
    async def diary_reflection_monthly_generate(year: int, month: int) -> JSONResponse:
        """生成 AI 月度反思。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import ReflectionService

        reflection = ReflectionService(svc.store)
        result = reflection.generate_monthly_reflection(year, month)
        if result.entry_count == 0:
            return JSONResponse({"ok": False, "error": f"{year}年{month}月暂无日记"}, status_code=404)

        prompt = reflection.build_monthly_reflection_prompt(result)
        try:
            ai_result = await svc._call_llm(prompt)  # noqa: SLF001
            return JSONResponse({
                "ok": True,
                "data": result.__dict__,
                "ai_result": ai_result,
            })
        except Exception as exc:
            logger.exception("月度反思生成失败")
            return JSONResponse(
                {"ok": False, "error": f"生成失败: {exc}", "data": result.__dict__},
                status_code=500,
            )

    @app.get("/api/diary/reflection/yearly/{year}")
    def diary_reflection_yearly(year: int) -> JSONResponse:
        """获取年度回顾统计数据。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import ReflectionService

        reflection = ReflectionService(svc.store)
        result = reflection.generate_yearly_review(year)
        return JSONResponse({"ok": True, "data": result.__dict__})

    @app.post("/api/diary/reflection/yearly/{year}/generate")
    async def diary_reflection_yearly_generate(year: int) -> JSONResponse:
        """生成 AI 年度回顾。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import ReflectionService

        reflection = ReflectionService(svc.store)
        result = reflection.generate_yearly_review(year)
        if result.entry_count == 0:
            return JSONResponse({"ok": False, "error": f"{year}年暂无日记"}, status_code=404)

        prompt = reflection.build_yearly_review_prompt(result)
        try:
            ai_result = await svc._call_llm(prompt)  # noqa: SLF001
            return JSONResponse({
                "ok": True,
                "data": result.__dict__,
                "ai_result": ai_result,
            })
        except Exception as exc:
            logger.exception("年度回顾生成失败")
            return JSONResponse(
                {"ok": False, "error": f"生成失败: {exc}", "data": result.__dict__},
                status_code=500,
            )

    @app.get("/api/diary/reflection/milestones")
    def diary_reflection_milestones(
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        """获取人生里程碑列表。

        Query:
        - start_date: 开始日期（YYYY-MM-DD），默认 2000-01-01
        - end_date: 结束日期（YYYY-MM-DD），默认今天
        - limit: 返回数量上限，默认 50
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import ReflectionService

        reflection = ReflectionService(svc.store)
        milestones = reflection.detect_milestones(start_date, end_date)
        milestones = milestones[:limit]
        return JSONResponse({
            "ok": True,
            "data": [m.__dict__ for m in milestones],
            "total": len(milestones),
        })

    # ─── 知识图谱 API（标签关联+人物关系+知识网络） ─────────────────

    @app.get("/api/diary/knowledge-graph/tag-network")
    def diary_kg_tag_network(
        start_date: str | None = None,
        end_date: str | None = None,
        min_count: int = 2,
        max_nodes: int = 50,
    ) -> JSONResponse:
        """获取标签关联网络。

        Query:
        - start_date: 开始日期
        - end_date: 结束日期
        - min_count: 最小出现次数，默认 2
        - max_nodes: 最大节点数，默认 50
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import KnowledgeGraphService

        kg = KnowledgeGraphService(svc.store)
        graph = kg.build_tag_network(start_date, end_date, min_count, max_nodes)
        return JSONResponse({"ok": True, "data": graph.to_dict()})

    @app.get("/api/diary/knowledge-graph/person-network")
    def diary_kg_person_network(
        start_date: str | None = None,
        end_date: str | None = None,
        min_count: int = 1,
        max_nodes: int = 30,
    ) -> JSONResponse:
        """获取人物关系图谱。

        Query:
        - start_date: 开始日期
        - end_date: 结束日期
        - min_count: 最小出现次数，默认 1
        - max_nodes: 最大节点数，默认 30
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import KnowledgeGraphService

        kg = KnowledgeGraphService(svc.store)
        graph = kg.build_person_network(start_date, end_date, min_count, max_nodes)
        return JSONResponse({"ok": True, "data": graph.to_dict()})

    @app.get("/api/diary/knowledge-graph/mixed")
    def diary_kg_mixed(
        start_date: str | None = None,
        end_date: str | None = None,
        min_count: int = 2,
        max_nodes: int = 60,
    ) -> JSONResponse:
        """获取混合知识网络（标签 + 人物 + 标签-人物关联）。

        Query:
        - start_date: 开始日期
        - end_date: 结束日期
        - min_count: 最小出现次数，默认 2
        - max_nodes: 最大节点数，默认 60
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import KnowledgeGraphService

        kg = KnowledgeGraphService(svc.store)
        graph = kg.build_mixed_network(start_date, end_date, min_count, max_nodes)
        return JSONResponse({"ok": True, "data": graph.to_dict()})

    @app.get("/api/diary/knowledge-graph/stats")
    def diary_kg_stats(
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> JSONResponse:
        """获取知识网络统计信息。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import KnowledgeGraphService

        kg = KnowledgeGraphService(svc.store)
        stats = kg.get_network_stats(start_date, end_date)
        return JSONResponse({"ok": True, "data": stats})

    @app.get("/api/diary/knowledge-graph/node/{node_id}")
    def diary_kg_node_detail(
        node_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 20,
    ) -> JSONResponse:
        """获取知识节点详情。

        Path:
        - node_id: 节点 ID（格式：tag:xxx 或 person:xxx）

        Query:
        - start_date: 开始日期
        - end_date: 结束日期
        - limit: 相关日记数量上限，默认 20
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import KnowledgeGraphService

        kg = KnowledgeGraphService(svc.store)
        detail = kg.get_node_detail(node_id, start_date, end_date, limit)
        if detail is None:
            return JSONResponse({"ok": False, "error": "节点不存在或无相关日记"}, status_code=404)
        return JSONResponse({"ok": True, "data": detail.__dict__})

    @app.get("/api/diary/knowledge-graph/person/{person_name}/relations")
    def diary_kg_person_relations(
        person_name: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> JSONResponse:
        """分析某个人物与其他人物的关系。

        Path:
        - person_name: 人物名称

        Query:
        - start_date: 开始日期
        - end_date: 结束日期
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import KnowledgeGraphService

        kg = KnowledgeGraphService(svc.store)
        relations = kg.analyze_person_relations(person_name, start_date, end_date)
        return JSONResponse({
            "ok": True,
            "data": [r.__dict__ for r in relations],
            "total": len(relations),
        })

    # ─── 自进化 API（夜间自我改进循环） ─────────────────────────────

    @app.get("/api/diary/self-evolution/profile")
    def diary_se_profile(
        target_date: str | None = None,
    ) -> JSONResponse:
        """获取用户画像。

        Query:
        - target_date: 目标日期，默认今天
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import SelfEvolutionService

        se = SelfEvolutionService(svc.store)
        profile = se.get_user_profile(target_date)
        if profile is None:
            return JSONResponse({"ok": False, "error": "用户画像不存在，请先运行夜间循环"}, status_code=404)
        return JSONResponse({"ok": True, "data": profile.to_dict()})

    @app.get("/api/diary/self-evolution/profile/history")
    def diary_se_profile_history(
        limit: int = 30,
    ) -> JSONResponse:
        """获取画像历史快照。

        Query:
        - limit: 返回数量上限，默认 30
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import SelfEvolutionService

        se = SelfEvolutionService(svc.store)
        history = se.get_profile_history(limit)
        return JSONResponse({"ok": True, "data": history, "total": len(history)})

    @app.get("/api/diary/self-evolution/drifts")
    def diary_se_drifts(
        drift_type: str | None = None,
        severity: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        """获取漂移事件列表。

        Query:
        - drift_type: 按类型筛选（behavior/emotion/focus/relationship/writing）
        - severity: 按严重程度筛选（info/warning/alert）
        - status: 按状态筛选（new/acknowledged/dismissed）
        - limit: 返回数量上限，默认 50
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import SelfEvolutionService

        se = SelfEvolutionService(svc.store)
        drifts = se.get_drifts(drift_type, severity, status, limit)
        return JSONResponse({
            "ok": True,
            "data": [d.to_dict() for d in drifts],
            "total": len(drifts),
        })

    @app.get("/api/diary/self-evolution/nightly-logs")
    def diary_se_nightly_logs(
        limit: int = 30,
    ) -> JSONResponse:
        """获取夜间日志列表。

        Query:
        - limit: 返回数量上限，默认 30
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import SelfEvolutionService

        se = SelfEvolutionService(svc.store)
        logs = se.get_nightly_logs(limit)
        return JSONResponse({"ok": True, "data": logs, "total": len(logs)})

    @app.get("/api/diary/self-evolution/nightly-logs/{log_date}")
    def diary_se_nightly_log_detail(
        log_date: str,
    ) -> JSONResponse:
        """获取指定日期的夜间日志详情。

        Path:
        - log_date: 日志日期（YYYY-MM-DD）
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import SelfEvolutionService

        se = SelfEvolutionService(svc.store)
        log = se.get_nightly_log(log_date)
        if log is None:
            return JSONResponse({"ok": False, "error": "夜间日志不存在"}, status_code=404)
        return JSONResponse({"ok": True, "data": log.to_dict()})

    @app.post("/api/diary/self-evolution/run-nightly")
    def diary_se_run_nightly(
        target_date: str | None = None,
    ) -> JSONResponse:
        """手动触发夜间自我改进循环。

        Query:
        - target_date: 目标日期，默认昨天
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import SelfEvolutionService

        se = SelfEvolutionService(svc.store)
        nightly_log = se.run_nightly_cycle(target_date)
        return JSONResponse({
            "ok": True,
            "message": "夜间循环完成",
            "data": nightly_log.to_dict(),
        })

    @app.get("/api/diary/self-evolution/tag-optimizations")
    def diary_se_tag_optimizations(
        target_date: str | None = None,
    ) -> JSONResponse:
        """获取标签优化建议。

        Query:
        - target_date: 目标日期，默认今天
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from dataclasses import asdict

        from openbiliclaw.diary import SelfEvolutionService

        se = SelfEvolutionService(svc.store)
        if target_date is None:
            target_date = datetime.now().strftime("%Y-%m-%d")
        optimization = se.optimize_tags(target_date)
        return JSONResponse({"ok": True, "data": asdict(optimization)})

    # ─── 主动洞察引擎 API（第二阶段） ────────────────────────────────

    @app.get("/api/diary/insights/memory-on-this-day")
    def diary_insights_memory_on_this_day(
        target_date: str | None = None,
    ) -> JSONResponse:
        """获取历史上的今天。

        Query:
        - target_date: 目标日期，默认今天
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import InsightEngineService

        engine = InsightEngineService(svc.store)
        memory = engine.get_memory_on_this_day(target_date)
        return JSONResponse({"ok": True, "data": asdict(memory)})

    @app.get("/api/diary/insights/patterns")
    def diary_insights_patterns(
        lookback_days: int = 90,
    ) -> JSONResponse:
        """发现日记中的模式。

        Query:
        - lookback_days: 回溯天数，默认 90
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import InsightEngineService

        engine = InsightEngineService(svc.store)
        patterns = engine.discover_patterns(lookback_days)
        return JSONResponse({
            "ok": True,
            "data": [asdict(p) for p in patterns],
            "total": len(patterns),
        })

    @app.get("/api/diary/insights/morning-briefing")
    def diary_insights_morning_briefing(
        briefing_date: str | None = None,
    ) -> JSONResponse:
        """获取晨间简报。

        Query:
        - briefing_date: 简报日期，默认今天
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import InsightEngineService

        engine = InsightEngineService(svc.store)
        briefing = engine.get_morning_briefing(briefing_date)
        if briefing is None:
            # 如果不存在，生成一个
            briefing = engine.generate_morning_briefing(briefing_date)
        return JSONResponse({"ok": True, "data": briefing.to_dict()})

    @app.post("/api/diary/insights/morning-briefing/generate")
    def diary_insights_generate_morning_briefing(
        briefing_date: str | None = None,
    ) -> JSONResponse:
        """生成晨间简报。

        Query:
        - briefing_date: 简报日期，默认今天
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import InsightEngineService

        engine = InsightEngineService(svc.store)
        briefing = engine.generate_morning_briefing(briefing_date)
        return JSONResponse({"ok": True, "data": briefing.to_dict()})

    @app.get("/api/diary/insights/open-loops")
    def diary_insights_open_loops(
        status: str | None = None,
        loop_type: str | None = None,
        priority: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        """获取开放循环列表。

        Query:
        - status: 状态筛选（open/in_progress/completed/abandoned）
        - loop_type: 类型筛选（promise/goal/todo/question/idea）
        - priority: 优先级筛选（high/medium/low）
        - limit: 返回数量上限，默认 50
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import InsightEngineService

        engine = InsightEngineService(svc.store)
        loops = engine.get_open_loops(status, loop_type, priority, limit)
        return JSONResponse({
            "ok": True,
            "data": [l.to_dict() for l in loops],
            "total": len(loops),
        })

    @app.post("/api/diary/insights/open-loops/scan")
    def diary_insights_scan_open_loops(
        lookback_days: int = 365,
    ) -> JSONResponse:
        """扫描日记中的开放循环。

        Query:
        - lookback_days: 回溯天数，默认 365
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import InsightEngineService

        engine = InsightEngineService(svc.store)
        loops = engine.scan_open_loops(lookback_days)
        return JSONResponse({
            "ok": True,
            "data": [l.to_dict() for l in loops],
            "total": len(loops),
            "message": f"扫描完成，发现 {len(loops)} 个开放循环",
        })

    @app.put("/api/diary/insights/open-loops/{loop_id}/status")
    def diary_insights_update_open_loop_status(
        loop_id: str,
        status: str,
    ) -> JSONResponse:
        """更新开放循环状态。

        Path:
        - loop_id: 循环 ID

        Query:
        - status: 新状态（open/in_progress/completed/abandoned）
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import InsightEngineService

        engine = InsightEngineService(svc.store)
        success = engine.update_open_loop_status(loop_id, status)
        if success:
            return JSONResponse({"ok": True, "message": "状态更新成功"})
        return JSONResponse({"ok": False, "error": "状态更新失败"}, status_code=400)

    @app.get("/api/diary/insights/report")
    def diary_insights_report(
        target_date: str | None = None,
    ) -> JSONResponse:
        """生成综合洞察报告。

        Query:
        - target_date: 目标日期，默认今天
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import InsightEngineService

        engine = InsightEngineService(svc.store)
        report = engine.generate_insight_report(target_date)
        return JSONResponse({"ok": True, "data": report.to_dict()})

    # ─── 三层记忆系统 API（第三阶段） ────────────────────────────────

    @app.get("/api/diary/memory/stats")
    def diary_memory_stats() -> JSONResponse:
        """获取记忆系统统计。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import MemorySystemService

        memory = MemorySystemService(svc.store)
        stats = memory.get_memory_stats()
        return JSONResponse({"ok": True, "data": stats.to_dict()})

    @app.post("/api/diary/memory/update-tiers")
    def diary_memory_update_tiers() -> JSONResponse:
        """更新所有日记的记忆层级。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import MemorySystemService

        memory = MemorySystemService(svc.store)
        updated = memory.update_memory_tiers()
        return JSONResponse({"ok": True, "updated": updated, "message": f"更新了 {updated} 个记忆条目的层级"})

    @app.post("/api/diary/memory/compress-cold")
    def diary_memory_compress_cold() -> JSONResponse:
        """压缩所有 Cold 层记忆。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import MemorySystemService

        memory = MemorySystemService(svc.store)
        result = memory.compress_all_cold_memories()
        return JSONResponse({"ok": True, "data": result.to_dict()})

    @app.post("/api/diary/memory/maintenance")
    def diary_memory_maintenance() -> JSONResponse:
        """运行记忆维护（分层更新 + 压缩 + 重要性重算）。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import MemorySystemService

        memory = MemorySystemService(svc.store)
        result = memory.run_memory_maintenance()
        return JSONResponse({"ok": True, "data": result.to_dict()})

    @app.get("/api/diary/memory/search")
    def diary_memory_search(
        query: str = "",
        tier: str | None = None,
        limit: int = 20,
        min_importance: float = 0.0,
    ) -> JSONResponse:
        """搜索记忆。

        Query:
        - query: 搜索关键词
        - tier: 记忆层级过滤（hot/warm/cold）
        - limit: 返回数量上限，默认 20
        - min_importance: 最低重要性评分，默认 0
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import MemorySystemService

        memory = MemorySystemService(svc.store)
        results = memory.search_memories(query, tier, limit, min_importance)
        return JSONResponse({
            "ok": True,
            "data": results,
            "total": len(results),
        })

    @app.get("/api/diary/memory/{diary_id}")
    def diary_memory_get_by_id(diary_id: int) -> JSONResponse:
        """根据 ID 获取记忆条目。

        Path:
        - diary_id: 日记 ID
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import MemorySystemService

        memory = MemorySystemService(svc.store)
        entry = memory.get_memory_by_id(diary_id)
        if entry is None:
            return JSONResponse({"ok": False, "error": "记忆条目不存在"}, status_code=404)
        return JSONResponse({"ok": True, "data": entry.to_dict()})

    # ─── 情绪系统（效价/唤醒二维模型）API ────────────────────────────

    @app.get("/api/diary/emotion/stats")
    def diary_emotion_stats() -> JSONResponse:
        """获取情绪统计概览。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import EmotionAnalyzer

        analyzer = EmotionAnalyzer(svc.store)
        stats = analyzer.get_emotion_stats()
        return JSONResponse({"ok": True, "data": stats})

    @app.get("/api/diary/emotion/trend")
    def diary_emotion_trend(days: int = 30) -> JSONResponse:
        """获取情绪趋势（按天聚合）。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import EmotionAnalyzer

        analyzer = EmotionAnalyzer(svc.store)
        trend = analyzer.get_emotion_trend(days=days)
        return JSONResponse({"ok": True, "data": [t.to_dict() for t in trend]})

    @app.get("/api/diary/emotion/forecast")
    def diary_emotion_forecast(days: int = 7) -> JSONResponse:
        """预测未来 N 天的情绪。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import EmotionAnalyzer

        analyzer = EmotionAnalyzer(svc.store)
        forecasts = analyzer.forecast_emotion(days=days)
        return JSONResponse({"ok": True, "data": [f.to_dict() for f in forecasts]})

    @app.get("/api/diary/emotion/burnout")
    def diary_emotion_burnout(days: int = 30) -> JSONResponse:
        """倦怠评估。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import EmotionAnalyzer

        analyzer = EmotionAnalyzer(svc.store)
        assessment = analyzer.assess_burnout(days=days)
        return JSONResponse({"ok": True, "data": assessment.to_dict()})

    @app.post("/api/diary/emotion/analyze-all")
    def diary_emotion_analyze_all() -> JSONResponse:
        """分析所有日记的情绪。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import EmotionAnalyzer

        analyzer = EmotionAnalyzer(svc.store)
        count = analyzer.analyze_all_diaries()
        return JSONResponse({"ok": True, "data": {"analyzed": count}})

    # ─── 高级记忆系统（6层记忆 + 信念 + 巩固）API ───────────────────

    @app.get("/api/diary/advanced-memory/overview")
    def diary_advanced_memory_overview() -> JSONResponse:
        """获取高级记忆系统概览。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import AdvancedMemoryService

        am = AdvancedMemoryService(svc.store)
        overview = am.get_overview()
        return JSONResponse({"ok": True, "data": overview})

    @app.get("/api/diary/advanced-memory/layers")
    def diary_advanced_memory_layers() -> JSONResponse:
        """获取各记忆层统计。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import AdvancedMemoryService

        am = AdvancedMemoryService(svc.store)
        stats = am.get_memory_layer_stats()
        return JSONResponse({"ok": True, "data": [s.to_dict() for s in stats]})

    @app.get("/api/diary/advanced-memory/beliefs")
    def diary_advanced_memory_beliefs(category: str | None = None) -> JSONResponse:
        """获取信念列表。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import AdvancedMemoryService

        am = AdvancedMemoryService(svc.store)
        beliefs = am.get_beliefs(category=category)
        return JSONResponse({"ok": True, "data": [b.to_dict() for b in beliefs]})

    @app.get("/api/diary/advanced-memory/conflicts")
    def diary_advanced_memory_conflicts() -> JSONResponse:
        """检测并获取信念冲突。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import AdvancedMemoryService

        am = AdvancedMemoryService(svc.store)
        conflicts = am.detect_belief_conflicts()
        return JSONResponse({"ok": True, "data": [c.to_dict() for c in conflicts]})

    @app.post("/api/diary/advanced-memory/build")
    def diary_advanced_memory_build() -> JSONResponse:
        """从日记构建 6 层记忆。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import AdvancedMemoryService

        am = AdvancedMemoryService(svc.store)
        result = am.build_memory_layers()
        return JSONResponse({"ok": True, "data": result})

    @app.post("/api/diary/advanced-memory/consolidate")
    def diary_advanced_memory_consolidate() -> JSONResponse:
        """运行记忆巩固（遗忘曲线 + 提升/降级/遗忘）。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import AdvancedMemoryService

        am = AdvancedMemoryService(svc.store)
        result = am.consolidate_memories()
        return JSONResponse({"ok": True, "data": result.to_dict()})

    @app.post("/api/diary/advanced-memory/dream-review")
    def diary_advanced_memory_dream_review() -> JSONResponse:
        """运行夜间梦境状态回顾（验证教训）。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import AdvancedMemoryService

        am = AdvancedMemoryService(svc.store)
        result = am.dream_state_review()
        return JSONResponse({"ok": True, "data": result.to_dict()})

    @app.get("/api/diary/advanced-memory/search")
    def diary_advanced_memory_search(
        query: str = "",
        layer: str | None = None,
        min_importance: float = 0.0,
        limit: int = 20,
    ) -> JSONResponse:
        """搜索记忆。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import AdvancedMemoryService

        am = AdvancedMemoryService(svc.store)
        results = am.search_memories(query=query, layer=layer, min_importance=min_importance, limit=limit)
        return JSONResponse({"ok": True, "data": results})

    # ─── 智能时间线卡片 API ───────────────────────────────────────────

    @app.get("/api/diary/timeline/cards")
    def diary_timeline_cards(
        card_type: str | None = None,
        entity: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        min_importance: float = 0.0,
        limit: int = 50,
        offset: int = 0,
    ) -> JSONResponse:
        """查询时间线卡片。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import TimelineService

        tl = TimelineService(svc.store)
        cards = tl.get_cards(
            card_type=card_type,
            entity=entity,
            start_date=start_date,
            end_date=end_date,
            min_importance=min_importance,
            limit=limit,
            offset=offset,
        )
        return JSONResponse({"ok": True, "data": [c.to_dict() for c in cards]})

    @app.get("/api/diary/timeline/stats")
    def diary_timeline_stats() -> JSONResponse:
        """获取时间线统计。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import TimelineService

        tl = TimelineService(svc.store)
        stats = tl.get_stats()
        return JSONResponse({"ok": True, "data": stats.to_dict()})

    @app.get("/api/diary/timeline/milestones")
    def diary_timeline_milestones(limit: int = 20) -> JSONResponse:
        """获取里程碑卡片。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import TimelineService

        tl = TimelineService(svc.store)
        milestones = tl.get_milestones(limit=limit)
        return JSONResponse({"ok": True, "data": [m.to_dict() for m in milestones]})

    @app.post("/api/diary/timeline/generate-all")
    def diary_timeline_generate_all() -> JSONResponse:
        """为所有日记生成时间线卡片。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import TimelineService

        tl = TimelineService(svc.store)
        total = tl.generate_all_cards()
        return JSONResponse({"ok": True, "data": {"total_cards": total}})

    # ─── 碎片（随手记）API ───────────────────────────────────────────

    @app.get("/api/diary/fragments")
    def diary_fragments_list(
        fragment_date: str | None = None,
        limit: int = 100,
        offset: int = 0,
        fragment_type: str | None = None,
    ) -> JSONResponse:
        """列出碎片，可按日期和类型筛选。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        fragments = svc.list_fragments(fragment_date, limit, offset, fragment_type)
        total = svc.store.count_fragments(fragment_date)
        return JSONResponse(
            {
                "ok": True,
                "data": [f.model_dump(mode="json") for f in fragments],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )

    @app.post("/api/diary/fragments")
    def diary_fragments_create(payload: dict[str, Any]) -> JSONResponse:
        """创建一条碎片。

        请求体：
        - content: 碎片内容（必填）
        - mood: 情绪标签（可选）
        - fragment_date: 日期（可选，默认今天）
        - source: 来源（可选）
        - fragment_type: 碎片类型（可选，text/image/voice/link）
        - media_path: 媒体文件路径（可选）
        - media_description: 媒体内容描述（可选）
        - tags: 标签列表（可选）
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        content = payload.get("content", "").strip()
        if not content:
            return JSONResponse({"ok": False, "error": "缺少 content"}, status_code=400)
        mood_str = payload.get("mood", "unknown")
        try:
            from openbiliclaw.diary import MoodLevel

            mood = MoodLevel(mood_str)
        except (ValueError, KeyError):
            mood = MoodLevel.UNKNOWN
        fragment = svc.create_fragment(
            content=content,
            mood=mood,
            fragment_date=payload.get("fragment_date"),
            source=payload.get("source", "manual"),
            fragment_type=payload.get("fragment_type", "text"),
            media_path=payload.get("media_path", ""),
            media_description=payload.get("media_description", ""),
            tags=payload.get("tags", []),
        )
        return JSONResponse({"ok": True, "data": fragment.model_dump(mode="json")})

    @app.delete("/api/diary/fragments/{fragment_id}")
    def diary_fragments_delete(fragment_id: int) -> JSONResponse:
        """删除一条碎片。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        ok = svc.delete_fragment(fragment_id)
        return JSONResponse({"ok": ok, "id": fragment_id})

    @app.post("/api/diary/fragments/{fragment_id}/auto-tag")
    async def diary_fragments_auto_tag(fragment_id: int) -> JSONResponse:
        """对单条碎片执行 AI 自动标签和情绪识别。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            fragment = await svc.auto_tag_fragment(fragment_id)
            if fragment is None:
                return JSONResponse({"ok": False, "error": "碎片不存在"}, status_code=404)
            return JSONResponse({"ok": True, "data": fragment.model_dump(mode="json")})
        except Exception as exc:
            logger.exception(f"碎片 {fragment_id} 自动标签失败")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @app.post("/api/diary/fragments/auto-tag-batch")
    async def diary_fragments_auto_tag_batch(payload: dict[str, Any] | None = None) -> JSONResponse:
        """批量对未标注的碎片执行自动标签和情绪识别。

        请求体（可选）：
        - limit: 处理数量上限（默认 50）
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        payload = payload or {}
        try:
            stats = await svc.batch_auto_tag_fragments(limit=payload.get("limit", 50))
            return JSONResponse({"ok": True, "data": stats})
        except Exception as exc:
            logger.exception("碎片批量自动标签失败")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @app.post("/api/diary/fragments/generate-diary")
    async def diary_fragments_generate_diary(payload: dict[str, Any] | None = None) -> JSONResponse:
        """从当天碎片 AI 聚合生成一篇完整日记（证据驱动版）。

        请求体（可选）：
        - fragment_date: 碎片日期（默认今天）
        - auto_delete: 生成后是否删除碎片（默认 true）
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        payload = payload or {}
        try:
            entry = await svc.generate_diary_from_fragments(
                fragment_date=payload.get("fragment_date"),
                auto_delete=payload.get("auto_delete", True),
            )
            if entry is None:
                return JSONResponse({"ok": False, "error": "当天没有碎片"}, status_code=404)
            return JSONResponse({"ok": True, "data": entry.model_dump(mode="json")})
        except Exception as exc:
            logger.exception("碎片生成日记失败")
            return JSONResponse({"ok": False, "error": f"生成失败: {exc}"}, status_code=500)

    # ── 日记标签与人物提取 API ────────────────────────────────

    @app.get("/api/diary/tags")
    def diary_tags_list(
        type: str | None = None,
        limit: int = 200,
        min_count: int = 1,
    ) -> JSONResponse:
        """获取标签列表，可按类型筛选。

        Query:
        - type: 标签类型（emotion/topic/event/location/work/family/health/finance/other）
        - limit: 返回数量上限
        - min_count: 最小使用次数
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            from openbiliclaw.diary.models import TagType
            tag_type = TagType(type) if type else None
            tags = svc.get_tags(tag_type=tag_type, limit=limit, min_count=min_count)
            return JSONResponse({
                "ok": True,
                "data": [t.model_dump(mode="json") for t in tags],
                "total": len(tags),
            })
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/diary/persons")
    def diary_persons_list(
        relation: str | None = None,
        limit: int = 200,
        min_appearances: int = 1,
    ) -> JSONResponse:
        """获取人物列表，可按关系筛选。

        Query:
        - relation: 关系筛选（家人/朋友/同事等）
        - limit: 返回数量上限
        - min_appearances: 最小出现次数
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            persons = svc.get_persons(relation=relation, limit=limit, min_appearances=min_appearances)
            return JSONResponse({
                "ok": True,
                "data": [p.model_dump(mode="json") for p in persons],
                "total": len(persons),
            })
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/diary/persons/{person_id}")
    def diary_persons_detail(person_id: int) -> JSONResponse:
        """获取人物详情，包含相关日记列表。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            detail = svc.get_person_detail(person_id)
            if detail is None:
                return JSONResponse({"ok": False, "error": "人物不存在"}, status_code=404)
            return JSONResponse({"ok": True, "data": detail})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/diary/{entry_id}/tags")
    def diary_entry_tags(entry_id: int) -> JSONResponse:
        """获取某篇日记的标签和人物。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = svc.get_entry_tags_and_persons(entry_id)
            return JSONResponse({"ok": True, "data": data})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/api/diary/{entry_id}/extract")
    async def diary_entry_extract(entry_id: int) -> JSONResponse:
        """对单篇日记执行 AI 标签和人物提取。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            result = await svc.extract_tags_and_persons(entry_id)
            if result is None:
                return JSONResponse({"ok": False, "error": "提取失败或日记不存在"}, status_code=404)
            return JSONResponse({"ok": True, "data": result.model_dump(mode="json")})
        except Exception as exc:
            logger.exception(f"提取日记 {entry_id} 失败")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @app.post("/api/diary/extract-batch")
    async def diary_extract_batch(payload: dict[str, Any] | None = None) -> JSONResponse:
        """批量提取日记的标签和人物。

        请求体（可选）：
        - limit: 处理数量上限（默认 100）
        - start_id: 起始日记 ID
        - only_unextracted: 只处理未提取过的日记（默认 true）
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        payload = payload or {}
        try:
            stats = await svc.batch_extract(
                limit=payload.get("limit", 100),
                start_id=payload.get("start_id"),
                only_unextracted=payload.get("only_unextracted", True),
            )
            return JSONResponse({"ok": True, "data": stats})
        except Exception as exc:
            logger.exception("批量提取失败")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @app.get("/api/diary/extraction-stats")
    def diary_extraction_stats() -> JSONResponse:
        """获取标签和人物提取的统计信息。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            stats = svc.get_extraction_stats()
            return JSONResponse({"ok": True, "data": stats})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    # ─── RAG 语义搜索与问答 API ─────────────────────────────────────

    def _get_diary_rag_service():
        """获取或创建日记 RAG 服务实例（懒加载）。"""
        global _diary_rag_service
        if _diary_rag_service is not None:
            return _diary_rag_service
        database = getattr(ctx, "database", None)
        if database is None:
            return None
        from openbiliclaw.diary import DiaryRAGService

        rag = DiaryRAGService(database=database)
        # 注入 embedding 和 llm 服务
        embedding_service = getattr(ctx, "embedding_service", None)
        llm_service = getattr(ctx, "llm_service", None)
        if embedding_service is not None:
            rag.set_embedding_service(embedding_service)
        if llm_service is not None:
            rag.set_llm_service(llm_service)
        _diary_rag_service = rag
        return rag

    @app.get("/api/diary/rag/stats")
    def diary_rag_stats() -> JSONResponse:
        """获取向量生成统计信息。"""
        rag = _get_diary_rag_service()
        if rag is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        stats = rag.get_embedding_stats()
        return JSONResponse({"ok": True, "data": stats})

    @app.post("/api/diary/rag/generate-embeddings")
    async def diary_rag_generate_embeddings(payload: dict[str, Any] | None = None) -> JSONResponse:
        """批量为日记生成 embedding 向量。

        请求体（可选）：
        - limit: 最多处理多少篇（默认 100）
        - batch_size: 每批并发数（默认 10）
        """
        rag = _get_diary_rag_service()
        if rag is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        if rag.embedding_service is None:
            return JSONResponse({"ok": False, "error": "Embedding 服务未配置，请先配置 LLM provider"}, status_code=400)
        payload = payload or {}
        try:
            stats = await rag.batch_generate_embeddings(
                limit=payload.get("limit", 100),
                batch_size=payload.get("batch_size", 10),
            )
            return JSONResponse({"ok": True, "data": stats})
        except Exception as exc:
            logger.exception("批量生成 embedding 失败")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @app.get("/api/diary/rag/search")
    async def diary_rag_search(
        q: str,
        top_k: int = 10,
        min_score: float = 0.3,
        start_date: str | None = None,
        end_date: str | None = None,
        source: str | None = None,
    ) -> JSONResponse:
        """语义搜索日记（用自然语言搜索，按语义相似度排序）。

        参数：
        - q: 搜索查询（自然语言）
        - top_k: 返回最多多少条（默认 10）
        - min_score: 最低相似度阈值 0-1（默认 0.3）
        - start_date / end_date: 日期范围过滤
        - source: 来源过滤
        """
        rag = _get_diary_rag_service()
        if rag is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        if rag.embedding_service is None:
            return JSONResponse({"ok": False, "error": "Embedding 服务未配置"}, status_code=400)
        if not q.strip():
            return JSONResponse({"ok": False, "error": "缺少搜索关键词 q"}, status_code=400)
        try:
            results = await rag.semantic_search(
                query=q,
                top_k=top_k,
                min_score=min_score,
                start_date=start_date,
                end_date=end_date,
                source=source,
            )
            return JSONResponse({
                "ok": True,
                "query": q,
                "count": len(results),
                "results": [
                    {
                        "id": r.entry.id,
                        "date": r.entry.entry_date,
                        "title": r.entry.title,
                        "content": r.entry.content[:500] + ("..." if len(r.entry.content) > 500 else ""),
                        "source": r.entry.source,
                        "mood": r.entry.mood.value,
                        "score": round(r.score, 4),
                        "highlight": r.highlight,
                    }
                    for r in results
                ],
            })
        except Exception as exc:
            logger.exception("语义搜索失败")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @app.get("/api/diary/rag/similar/{entry_id}")
    def diary_rag_similar(entry_id: int, top_k: int = 5, min_score: float = 0.5) -> JSONResponse:
        """查找与指定日记相似的历史日记。

        参数：
        - entry_id: 目标日记 ID
        - top_k: 返回最多多少条（默认 5）
        - min_score: 最低相似度阈值（默认 0.5）
        """
        rag = _get_diary_rag_service()
        if rag is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            results = rag.find_similar_entries(entry_id=entry_id, top_k=top_k, min_score=min_score)
            return JSONResponse({
                "ok": True,
                "entry_id": entry_id,
                "count": len(results),
                "results": [
                    {
                        "id": r.entry.id,
                        "date": r.entry.entry_date,
                        "title": r.entry.title,
                        "content": r.entry.content[:300] + ("..." if len(r.entry.content) > 300 else ""),
                        "score": round(r.score, 4),
                    }
                    for r in results
                ],
            })
        except Exception as exc:
            logger.exception("相似日记查询失败")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @app.post("/api/diary/rag/ask")
    async def diary_rag_ask(payload: dict[str, Any]) -> JSONResponse:
        """基于日记内容回答问题（RAG 问答）。

        请求体：
        - question: 用户问题（必填）
        - top_k: 检索多少篇相关日记（默认 8）
        """
        rag = _get_diary_rag_service()
        if rag is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        if rag.llm_service is None:
            return JSONResponse({"ok": False, "error": "LLM 服务未配置"}, status_code=400)
        question = payload.get("question", "").strip()
        if not question:
            return JSONResponse({"ok": False, "error": "缺少 question"}, status_code=400)
        try:
            answer = await rag.ask_question(
                question=question,
                top_k=payload.get("top_k", 8),
            )
            return JSONResponse({
                "ok": True,
                "question": question,
                "answer": answer.answer,
                "sources": answer.sources,
                "related_questions": answer.related_questions,
            })
        except Exception as exc:
            logger.exception("RAG 问答失败")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)



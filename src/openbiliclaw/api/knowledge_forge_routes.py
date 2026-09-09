"""Knowledge Forge API 路由（设计文档 §4）。

提供分层摘要、实体/概念、知识 Wiki、缺口分析、质量审计的只读查询
与手动触发端点。通过 ``register_knowledge_forge_routes(app, ctx)`` 注册。

说明：
    - 全部端点只读或触发后台分析，不阻塞主流程
    - LLM 相关触发端点默认要求 ``confirm=1``，避免误触成本
    - 数据库不可用时返回 503
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from openbiliclaw.knowledge_forge.models import now_cn

if TYPE_CHECKING:
    from openbiliclaw.api.runtime_context import RuntimeContext

logger = logging.getLogger(__name__)


def _db_path(ctx: RuntimeContext) -> str | None:
    """从运行时上下文解析 knowledge_audit 库路径。

    v0.4.0+: knowledge_forge 相关表（audit_issues/gap_records/
    article_quality_scores 等）已迁移到独立的 knowledge_audit.db，
    与主库锁域隔离。
    """
    database = getattr(ctx, "database", None)
    if database is not None:
        p = getattr(database, "db_path", None) or getattr(database, "path", None)
        if p:
            # 主库路径 -> 替换为 knowledge_audit.db
            main_path = Path(str(p))
            return str(main_path.with_name("knowledge_audit.db"))
    config = getattr(ctx, "config", None)
    if config is not None:
        storage = getattr(config, "storage", None)
        if storage is not None and getattr(storage, "db_path", None):
            main_path = Path(str(storage.db_path))
            return str(main_path.with_name("knowledge_audit.db"))
    return None


def _connect(ctx: RuntimeContext) -> sqlite3.Connection | None:
    p = _db_path(ctx)
    if not p:
        return None
    conn = sqlite3.connect(p, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # ATTACH 主库，使跨库 JOIN（如 audit_issues JOIN articles）正常工作
    main_path = Path(p).with_name("openbiliclaw.db")
    if main_path.exists():
        with suppress(sqlite3.OperationalError):
            conn.execute("ATTACH DATABASE ? AS main_db", (str(main_path),))
    # v0.4.0+: articles 表迁移到 content.db，ATTACH 以便跨库查询
    content_path = Path(p).with_name("content.db")
    if content_path.exists():
        with suppress(sqlite3.OperationalError):
            conn.execute("ATTACH DATABASE ? AS content", (str(content_path),))
    # P8: entities/entity_relations 独立存于 knowledge.db，ATTACH 以便加前缀访问
    knowledge_path = Path(p).with_name("knowledge.db")
    if knowledge_path.exists():
        with suppress(sqlite3.OperationalError):
            conn.execute("ATTACH DATABASE ? AS knowledge", (str(knowledge_path),))
    return conn


def _err(msg: str, status: int = 500) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg}, status_code=status)


def _require_db(ctx: RuntimeContext) -> sqlite3.Connection | JSONResponse:
    conn = _connect(ctx)
    if conn is None:
        return _err("database unavailable", 503)
    return conn


def register_knowledge_forge_routes(app: FastAPI, ctx: RuntimeContext) -> None:
    """注册 Knowledge Forge API 路由。"""

    # ── 4.1 分层摘要 ──
    @app.get("/api/articles/{article_id}/summary")
    def article_summary(article_id: int, level: str = "detailed") -> JSONResponse:
        conn = _require_db(ctx)
        if isinstance(conn, JSONResponse):
            return conn
        try:
            col = {
                "detailed": "summary_detailed",
                "compact": "summary_compact",
                "ultra_compact": "summary_ultra_compact",
            }.get(level)
            if col is None:
                return _err("level must be detailed|compact|ultra_compact", 400)
            row = conn.execute(
                f"SELECT {col} AS s, summary_quality AS q FROM articles WHERE id = ?",
                (article_id,),
            ).fetchone()
            if row is None:
                return _err("article not found", 404)
            return JSONResponse(
                {
                    "ok": True,
                    "article_id": article_id,
                    "level": level,
                    "summary": row["s"] or "",
                    "quality": row["q"],
                    "generated": bool(row["s"]),
                }
            )
        finally:
            conn.close()

    @app.post("/api/articles/{article_id}/summary/generate")
    async def summary_generate(article_id: int) -> JSONResponse:
        """重新生成单篇三层摘要（异步调用 LLM，阻塞该请求直到完成）。"""
        try:
            from openbiliclaw.knowledge_forge.summary_engine import SummaryEngine

            engine = SummaryEngine()
            result = await engine.generate_by_id(article_id, force=True)
            if result.error:
                return _err(result.error, 400)
            return JSONResponse(
                {
                    "ok": True,
                    "article_id": article_id,
                    "detailed_len": len(result.detailed or ""),
                    "compact_len": len(result.compact or ""),
                    "ultra_len": len(result.ultra_compact or ""),
                    "quality": result.quality,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("summary generate failed: %s", article_id)
            return _err(f"summary generation failed: {exc}")

    # ── 4.2 实体/概念 ──
    # 注意：/api/authors 与 /api/topics 已被既有端点占用（话题管理），
    # 作者/主题/概念列表统一走 /api/entities?type=...（设计文档 4.2 主入口）。
    @app.get("/api/entities")
    def entities_list(
        entity_type: str = Query("", alias="type"),
        page: int = 1,
        size: int = 20,
        search: str = "",
    ) -> JSONResponse:
        conn = _require_db(ctx)
        if isinstance(conn, JSONResponse):
            return conn
        try:
            where, params = ["1=1"], []
            if entity_type:
                where.append("type = ?")
                params.append(entity_type)
            if search:
                where.append("name LIKE ?")
                params.append(f"%{search}%")
            size = max(1, min(int(size), 100))
            offset = max(0, (int(page) - 1) * size)
            total = conn.execute(
                f"SELECT COUNT(*) FROM knowledge.entities WHERE {' AND '.join(where)}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT id, name, type, description, article_count, first_seen_at,"
                f" last_updated_at FROM knowledge.entities WHERE {' AND '.join(where)}"
                f" ORDER BY article_count DESC, id LIMIT ? OFFSET ?",
                [*params, size, offset],
            ).fetchall()
            return JSONResponse(
                {
                    "ok": True,
                    "total": total,
                    "items": [dict(r) for r in rows],
                }
            )
        finally:
            conn.close()

    @app.get("/api/entities/{entity_id}")
    def entity_detail(entity_id: int, months: int = 8) -> JSONResponse:
        """实体详情。months>0 时引用时间线仅返回最近 N 个月；months<=0 返回全量。"""
        conn = _require_db(ctx)
        if isinstance(conn, JSONResponse):
            return conn
        try:
            row = conn.execute("SELECT * FROM knowledge.entities WHERE id = ?", (entity_id,)).fetchone()
            if row is None:
                return _err("entity not found", 404)
            entity = dict(row)
            # 3.2 实体页增强：引用时间线（按月聚合，默认最近 8 个月，months<=0 全量）
            _tl_sql = """SELECT substr(a.published_at, 1, 7) AS ym, COUNT(*) AS n
                   FROM article_entities ae JOIN articles a ON a.id = ae.article_id
                   WHERE ae.entity_id = ?
                     AND a.published_at IS NOT NULL AND a.published_at != ''
                   GROUP BY ym ORDER BY ym DESC""" + (
                f" LIMIT {int(months)}" if int(months) > 0 else ""
            )
            timeline_rows = conn.execute(_tl_sql, (entity_id,)).fetchall()
            entity["timeline"] = [{"month": r["ym"], "count": int(r["n"])} for r in timeline_rows][
                ::-1
            ]
            # 3.2 实体页增强：相关实体（优先读持久化 entity_relations，
            # 表缺失或数据为空时回退实时共现计算）
            try:
                related_rows = conn.execute(
                    """SELECT e.id, e.name, e.type, er.co_occur AS co_occur
                       FROM knowledge.entity_relations er
                       JOIN knowledge.entities e ON e.id = CASE
                         WHEN er.entity_id_a = ? THEN er.entity_id_b
                         ELSE er.entity_id_a END
                       WHERE (er.entity_id_a = ? OR er.entity_id_b = ?)
                         AND er.relation_type = 'co_occur'
                       ORDER BY er.confidence DESC LIMIT 8""",
                    (entity_id, entity_id, entity_id),
                ).fetchall()
            except sqlite3.OperationalError:
                related_rows = []
            if not related_rows:
                related_rows = conn.execute(
                    """SELECT e.id, e.name, e.type, COUNT(*) AS co_occur
                       FROM article_entities me
                       JOIN article_entities oe ON oe.article_id = me.article_id
                       JOIN knowledge.entities e ON e.id = oe.entity_id
                       WHERE me.entity_id = ? AND oe.entity_id != ?
                       GROUP BY oe.entity_id ORDER BY co_occur DESC LIMIT 8""",
                    (entity_id, entity_id),
                ).fetchall()
            entity["related"] = [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "type": r["type"],
                    "co_occur": int(r["co_occur"]),
                }
                for r in related_rows
            ]
            return JSONResponse({"ok": True, "entity": entity})
        finally:
            conn.close()

    @app.get("/api/entities/{entity_id}/articles")
    def entity_articles(entity_id: int, page: int = 1, size: int = 20) -> JSONResponse:
        conn = _require_db(ctx)
        if isinstance(conn, JSONResponse):
            return conn
        try:
            size = max(1, min(int(size), 100))
            offset = max(0, (int(page) - 1) * size)
            total = conn.execute(
                "SELECT COUNT(*) FROM article_entities WHERE entity_id = ?",
                (entity_id,),
            ).fetchone()[0]
            rows = conn.execute(
                """SELECT a.id, a.title, a.source_type, a.url, a.published_at,
                          ae.relevance
                   FROM article_entities ae JOIN articles a ON a.id = ae.article_id
                   WHERE ae.entity_id = ? ORDER BY ae.relevance DESC LIMIT ? OFFSET ?""",
                (entity_id, size, offset),
            ).fetchall()
            return JSONResponse({"ok": True, "total": total, "items": [dict(r) for r in rows]})
        finally:
            conn.close()

    # ── 4.3 知识 Wiki ──
    @app.get("/api/articles/{article_id}/related")
    def article_related(article_id: int, limit: int = 10) -> JSONResponse:
        conn = _require_db(ctx)
        if isinstance(conn, JSONResponse):
            return conn
        try:
            limit = max(1, min(int(limit), 50))
            rows = conn.execute(
                """SELECT r.relation_type, r.confidence, r.description,
                          a.id AS article_id, a.title, a.source_type, a.url
                   FROM article_relations r
                   JOIN articles a ON a.id = CASE
                     WHEN r.article_id_a = ? THEN r.article_id_b
                     ELSE r.article_id_a END
                   WHERE (r.article_id_a = ? OR r.article_id_b = ?)
                     AND r.relation_type IN ('similar', 'same_topic')
                   ORDER BY r.confidence DESC LIMIT ?""",
                (article_id, article_id, article_id, limit),
            ).fetchall()
            return JSONResponse(
                {"ok": True, "article_id": article_id, "items": [dict(r) for r in rows]}
            )
        finally:
            conn.close()

    @app.get("/api/articles/{article_id}/contradictions")
    def article_contradictions(article_id: int, limit: int = 10) -> JSONResponse:
        conn = _require_db(ctx)
        if isinstance(conn, JSONResponse):
            return conn
        try:
            limit = max(1, min(int(limit), 50))
            rows = conn.execute(
                """SELECT r.confidence, r.description,
                          a.id AS article_id, a.title, a.source_type, a.url
                   FROM article_relations r
                   JOIN articles a ON a.id = CASE
                     WHEN r.article_id_a = ? THEN r.article_id_b
                     ELSE r.article_id_a END
                   WHERE (r.article_id_a = ? OR r.article_id_b = ?)
                     AND r.relation_type = 'contradiction'
                   ORDER BY r.confidence DESC LIMIT ?""",
                (article_id, article_id, article_id, limit),
            ).fetchall()
            return JSONResponse(
                {"ok": True, "article_id": article_id, "items": [dict(r) for r in rows]}
            )
        finally:
            conn.close()

    @app.get("/api/contradictions")
    def contradictions_list(page: int = 1, size: int = 20) -> JSONResponse:
        """观点矛盾的文章对列表（分页，供矛盾报告页使用）。"""
        conn = _require_db(ctx)
        if isinstance(conn, JSONResponse):
            return conn
        try:
            size = max(1, min(int(size), 100))
            offset = max(0, (int(page) - 1) * size)
            # 旧库可能无 status 列：动态构造（误报条目不再展示）
            _cols = {
                str(r["name"])
                for r in conn.execute("PRAGMA table_info(article_relations)").fetchall()
            }
            has_status = "status" in _cols
            where = "r.relation_type = 'contradiction'"
            if has_status:
                where += " AND r.status IS NOT 'false_positive'"
            total = conn.execute(
                f"SELECT COUNT(*) FROM article_relations r WHERE {where}"
            ).fetchone()[0]
            sel_cols = "r.confidence, r.description, r.rowid AS relation_id"
            if has_status:
                sel_cols += ", r.status"
            rows = conn.execute(
                f"""SELECT {sel_cols},
                          a1.title AS title_a, a1.url AS url_a, a1.source_type AS source_a,
                          a2.title AS title_b, a2.url AS url_b, a2.source_type AS source_b
                   FROM article_relations r
                   JOIN articles a1 ON a1.id = r.article_id_a
                   JOIN articles a2 ON a2.id = r.article_id_b
                   WHERE {where}
                   ORDER BY r.confidence DESC LIMIT ? OFFSET ?""",
                (size, offset),
            ).fetchall()
            return JSONResponse({"ok": True, "total": total, "items": [dict(r) for r in rows]})
        finally:
            conn.close()

    @app.post("/api/contradictions/{relation_id}/resolve")
    def contradiction_resolve(relation_id: int, action: str = "false_positive") -> JSONResponse:
        """标记矛盾对处理结果：confirmed（确认属实留档）/ false_positive（误报，列表不再展示）。

        幂等补 article_relations.status / resolved_at 列；旧库无列时自动 ALTER。
        """
        conn = _require_db(ctx)
        if isinstance(conn, JSONResponse):
            return conn
        try:
            action = action if action in ("confirmed", "false_positive") else "false_positive"
            _cols = {
                str(r["name"])
                for r in conn.execute("PRAGMA table_info(article_relations)").fetchall()
            }
            if "status" not in _cols:
                conn.execute("ALTER TABLE article_relations ADD COLUMN status TEXT")
            if "resolved_at" not in _cols:
                conn.execute("ALTER TABLE article_relations ADD COLUMN resolved_at TEXT")
            cur = conn.execute(
                "UPDATE article_relations SET status = ?, resolved_at = ? WHERE rowid = ?",
                (action, now_cn(), relation_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                return _err("relation not found", 404)
            return JSONResponse({"ok": True, "status": action})
        finally:
            conn.close()

    @app.get("/api/knowledge-graph")
    def knowledge_graph(entity_type: str = "", limit: int = 100) -> JSONResponse:
        """知识图谱数据：实体节点 + 实体-文章边。"""
        conn = _require_db(ctx)
        if isinstance(conn, JSONResponse):
            return conn
        try:
            limit = max(1, min(int(limit), 500))
            where, params = ["1=1"], []
            if entity_type:
                where.append("e.type = ?")
                params.append(entity_type)
            entities = conn.execute(
                f"SELECT id, name, type, article_count FROM knowledge.entities e"
                f" WHERE {' AND '.join(where)} ORDER BY article_count DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
            ids = [int(e["id"]) for e in entities]
            edges: list[dict[str, Any]] = []
            if ids:
                placeholders = ",".join("?" * len(ids))
                edge_rows = conn.execute(
                    f"""SELECT ae.entity_id, ae.article_id, a.title
                        FROM article_entities ae
                        JOIN articles a ON a.id = ae.article_id
                        WHERE ae.entity_id IN ({placeholders})
                        ORDER BY ae.relevance DESC LIMIT ?""",
                    [*ids, limit * 20],
                ).fetchall()
                edges = [dict(r) for r in edge_rows]
            return JSONResponse(
                {
                    "ok": True,
                    "nodes": [dict(e) for e in entities],
                    "edges": edges,
                }
            )
        finally:
            conn.close()

    # ── 4.4 知识缺口分析 ──
    @app.post("/api/gap-analysis/run")
    def gap_analysis_run() -> JSONResponse:
        try:
            from openbiliclaw.knowledge_forge.gap_analyst import run_gap_analysis

            result = run_gap_analysis()
            return JSONResponse(
                {
                    "ok": True,
                    "gaps_found": result["gaps_found"],
                    "high": result["high"],
                    "medium": result["medium"],
                    "low": result["low"],
                    "report": result["report"],
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("gap analysis run failed")
            return _err(f"gap analysis failed: {exc}")

    @app.get("/api/gap-records")
    def gap_records_list(
        severity: str = "", status: str = "open", page: int = 1, size: int = 20
    ) -> JSONResponse:
        conn = _require_db(ctx)
        if isinstance(conn, JSONResponse):
            return conn
        try:
            where, params = ["1=1"], []
            if severity:
                where.append("severity = ?")
                params.append(severity)
            if status:
                where.append("status = ?")
                params.append(status)
            size = max(1, min(int(size), 100))
            offset = max(0, (int(page) - 1) * size)
            total = conn.execute(
                f"SELECT COUNT(*) FROM gap_records WHERE {' AND '.join(where)}",
                params,
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM gap_records WHERE {' AND '.join(where)}"
                f" ORDER BY CASE severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, id"
                f" LIMIT ? OFFSET ?",
                [*params, size, offset],
            ).fetchall()
            return JSONResponse({"ok": True, "total": total, "items": [dict(r) for r in rows]})
        finally:
            conn.close()

    @app.post("/api/gap-records/{record_id}/resolve")
    def gap_record_resolve(record_id: int) -> JSONResponse:
        conn = _require_db(ctx)
        if isinstance(conn, JSONResponse):
            return conn
        try:
            cur = conn.execute(
                "UPDATE gap_records SET status='resolved' WHERE id = ?", (record_id,)
            )
            conn.commit()
            if cur.rowcount == 0:
                return _err("gap record not found", 404)
            return JSONResponse({"ok": True})
        finally:
            conn.close()

    # ── 4.5 质量审计 ──
    @app.post("/api/audit/run")
    def audit_run(confirm: bool = Query(False)) -> JSONResponse:
        """触发全量审计（同步，耗时数秒）。需 confirm=1。"""
        if not confirm:
            return _err("full audit is heavy; pass ?confirm=true", 400)
        try:
            from openbiliclaw.knowledge_forge.quality_auditor import QualityAuditor

            stats = QualityAuditor().run_full_audit()
            return JSONResponse(
                {
                    "ok": True,
                    "task_id": stats["task_id"],
                    "total": stats["total"],
                    "issues_found": stats["issues_found"],
                    "duplicate_groups": stats["duplicate_groups"],
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("audit run failed")
            return _err(f"audit failed: {exc}")

    @app.get("/api/audit/tasks")
    def audit_tasks_list(page: int = 1, size: int = 20) -> JSONResponse:
        conn = _require_db(ctx)
        if isinstance(conn, JSONResponse):
            return conn
        try:
            size = max(1, min(int(size), 100))
            offset = max(0, (int(page) - 1) * size)
            total = conn.execute("SELECT COUNT(*) FROM audit_tasks").fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM audit_tasks ORDER BY id DESC LIMIT ? OFFSET ?",
                (size, offset),
            ).fetchall()
            return JSONResponse({"ok": True, "total": total, "items": [dict(r) for r in rows]})
        finally:
            conn.close()

    @app.get("/api/audit/tasks/{task_id}")
    def audit_task_detail(task_id: int) -> JSONResponse:
        conn = _require_db(ctx)
        if isinstance(conn, JSONResponse):
            return conn
        try:
            row = conn.execute("SELECT * FROM audit_tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                return _err("audit task not found", 404)
            return JSONResponse({"ok": True, "task": dict(row)})
        finally:
            conn.close()

    @app.get("/api/audit/tasks/{task_id}/report")
    def audit_task_report(task_id: int) -> JSONResponse:
        try:
            from openbiliclaw.knowledge_forge.quality_auditor import QualityAuditor

            report = QualityAuditor().generate_report(task_id)
            return JSONResponse({"ok": True, "task_id": task_id, "report": report})
        except Exception as exc:  # noqa: BLE001
            logger.exception("audit report failed")
            return _err(f"audit report failed: {exc}")

    @app.get("/api/audit/summary")
    def audit_summary() -> JSONResponse:
        """审计问题概览（按严重级别计数，含待处理总数）。"""
        conn = _require_db(ctx)
        if isinstance(conn, JSONResponse):
            return conn
        try:
            rows = conn.execute(
                "SELECT severity, COUNT(*) AS n FROM audit_issues GROUP BY severity"
            ).fetchall()
            summary: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "open": 0}
            for r in rows:
                key = str(r["severity"] or "")
                if key in summary:
                    summary[key] = int(r["n"])
            summary["open"] = int(
                conn.execute("SELECT COUNT(*) FROM audit_issues WHERE status = 'open'").fetchone()[
                    0
                ]
            )
            return JSONResponse({"ok": True, "summary": summary})
        finally:
            conn.close()

    @app.get("/api/audit/issues")
    def audit_issues_list(
        issue_type: str = "",
        severity: str = "",
        status: str = "open",
        page: int = 1,
        size: int = 20,
    ) -> JSONResponse:
        conn = _require_db(ctx)
        if isinstance(conn, JSONResponse):
            return conn
        try:
            where, params = ["1=1"], []
            if issue_type:
                where.append("ai.issue_type = ?")
                params.append(issue_type)
            if severity:
                where.append("ai.severity = ?")
                params.append(severity)
            if status:
                where.append("ai.status = ?")
                params.append(status)
            size = max(1, min(int(size), 100))
            offset = max(0, (int(page) - 1) * size)
            total = conn.execute(
                f"SELECT COUNT(*) FROM audit_issues ai WHERE {' AND '.join(where)}",
                params,
            ).fetchone()[0]
            rows = conn.execute(
                f"""SELECT ai.id, ai.article_id, ai.issue_type, ai.severity,
                           ai.description, ai.status, ai.fix_suggestion, ai.created_at,
                           a.title
                    FROM audit_issues ai LEFT JOIN articles a ON a.id = ai.article_id
                    WHERE {" AND ".join(where)}
                    ORDER BY CASE ai.severity WHEN 'high' THEN 1
                             WHEN 'medium' THEN 2 ELSE 3 END, ai.id
                    LIMIT ? OFFSET ?""",
                [*params, size, offset],
            ).fetchall()
            return JSONResponse({"ok": True, "total": total, "items": [dict(r) for r in rows]})
        finally:
            conn.close()

    @app.post("/api/audit/issues/{issue_id}/fix")
    def audit_issue_fix(issue_id: int) -> JSONResponse:
        """标记单个审计问题为已修复（仅状态变更，不含自动修复）。"""
        conn = _require_db(ctx)
        if isinstance(conn, JSONResponse):
            return conn
        try:
            cur = conn.execute(
                "UPDATE audit_issues SET status='fixed', fixed_at=? WHERE id = ?",
                (__import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"), issue_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                return _err("issue not found", 404)
            return JSONResponse({"ok": True})
        finally:
            conn.close()

    @app.get("/api/articles/{article_id}/quality-score")
    def article_quality_score(article_id: int) -> JSONResponse:
        conn = _require_db(ctx)
        if isinstance(conn, JSONResponse):
            return conn
        try:
            row = conn.execute(
                "SELECT * FROM article_quality_scores WHERE article_id = ?",
                (article_id,),
            ).fetchone()
            if row is None:
                return _err("quality score not found", 404)
            return JSONResponse({"ok": True, "score": dict(row)})
        finally:
            conn.close()

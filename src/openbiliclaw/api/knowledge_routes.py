"""Knowledge-base API routes（从 app.py 提取）。

包含概念反向索引、概念详情、知识库统计、知识图谱等端点。
通过 ``register_knowledge_routes(app, ctx)`` 注册。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)


def _conn_with_content(database: Any) -> sqlite3.Connection:
    """返回 ATTACH 了 content.db 的主库连接（用于跨库 JOIN articles）。"""
    conn = _conn_with_content(database)
    # v0.4.0+: articles 表迁移到 content.db，ATTACH 以便跨库查询
    with suppress(Exception):
        db_path = getattr(database, "_db_path", None)
        if db_path:
            content_path = Path(str(db_path)).with_name("content.db")
            if content_path.exists():
                conn.execute("ATTACH DATABASE ? AS content", (str(content_path),))
    return conn


def register_knowledge_routes(app: FastAPI, ctx: Any) -> None:
    """Register knowledge-base related routes onto *app*."""

    @app.get("/api/knowledge/concepts")
    def knowledge_concepts(
        q: str = "",
        source: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> JSONResponse:
        """搜索概念反向索引。"""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            conn = _conn_with_content(database)
            where = []
            params: list = []
            if q:
                where.append("kc.concept LIKE ?")
                params.append(f"%{q}%")
            if source:
                where.append("kc.source_site = ?")
                params.append(source)
            where_clause = " AND ".join(where) if where else "1=1"

            # 统计
            total = conn.execute(
                f"SELECT COUNT(DISTINCT kc.concept) FROM knowledge_concepts kc WHERE {where_clause}",
                params,
            ).fetchone()[0]

            # 分组查询
            rows = conn.execute(
                f"""SELECT kc.concept, kc.concept_type, kc.source_site,
                           COUNT(*) as ref_count
                    FROM knowledge_concepts kc
                    WHERE {where_clause}
                    GROUP BY kc.concept, kc.source_site
                    ORDER BY ref_count DESC
                    LIMIT ? OFFSET ?""",
                params + [limit, offset],
            ).fetchall()

            items = [
                {
                    "concept": r[0],
                    "type": r[1],
                    "source": r[2],
                    "ref_count": r[3],
                }
                for r in rows
            ]

            return JSONResponse({"ok": True, "items": items, "total": total})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.get("/api/knowledge/concepts/{concept_name}")
    def knowledge_concept_detail(concept_name: str, source: str = "") -> JSONResponse:
        """查看某个概念在哪些文章中被提及。"""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            conn = _conn_with_content(database)
            where = ["kb.target_concept = ?"]
            params: list = [concept_name]
            if source:
                where.append("kb.source_site = ?")
                params.append(source)

            where_clause = " AND ".join(where)

            rows = conn.execute(
                f"""SELECT kb.source_article_id, kb.source_title, kb.source_url,
                           kb.source_site, kb.target_type, a.summary, a.tags
                    FROM knowledge_backlinks kb
                    LEFT JOIN articles a ON kb.source_article_id = a.id
                    WHERE {where_clause}
                    ORDER BY kb.source_site, kb.source_title""",
                params,
            ).fetchall()

            # 获取概念类型从 knowledge_concepts
            concept_type = "concept"
            if source:
                ct_row = conn.execute(
                    "SELECT DISTINCT concept_type FROM knowledge_concepts"
                    " WHERE concept = ? AND source_site = ? LIMIT 1",
                    (concept_name, source),
                ).fetchone()
                if ct_row:
                    concept_type = ct_row[0] or "concept"
            else:
                ct_row = conn.execute(
                    "SELECT DISTINCT concept_type FROM knowledge_concepts"
                    " WHERE concept = ? LIMIT 1",
                    (concept_name,),
                ).fetchone()
                if ct_row:
                    concept_type = ct_row[0] or "concept"

            # 按来源站点分组
            by_source: dict[str, list[dict]] = {}
            for r in rows:
                site = r[3] or "unknown"
                if site not in by_source:
                    by_source[site] = []
                by_source[site].append(
                    {
                        "article_id": r[0],
                        "title": r[1],
                        "url": r[2],
                        "summary": r[5] or "",
                        "tags": json.loads(r[6]) if r[6] else [],
                    }
                )

            return JSONResponse(
                {
                    "ok": True,
                    "concept": concept_name,
                    "type": concept_type,
                    "total": len(rows),
                    "by_source": by_source,
                }
            )
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.get("/api/knowledge/stats")
    def knowledge_stats() -> JSONResponse:
        """知识库统计数据。"""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            conn = _conn_with_content(database)
            total_concepts = conn.execute(
                "SELECT COUNT(DISTINCT concept) FROM knowledge_concepts"
            ).fetchone()[0]
            total_backlinks = conn.execute("SELECT COUNT(*) FROM knowledge_backlinks").fetchone()[0]

            sources = conn.execute(
                "SELECT source_site, COUNT(*) as cnt FROM knowledge_backlinks "
                "GROUP BY source_site ORDER BY cnt DESC"
            ).fetchall()
            source_stats = {r[0]: r[1] for r in sources}

            return JSONResponse(
                {
                    "ok": True,
                    "total_concepts": total_concepts,
                    "total_backlinks": total_backlinks,
                    "sources": source_stats,
                }
            )
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.get("/api/knowledge/graph")
    def knowledge_graph(limit: int = 50) -> JSONResponse:
        """知识图谱数据（节点 + 边），用于可视化。"""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            conn = _conn_with_content(database)
            # 取 TOP 概念作为节点
            nodes_raw = conn.execute(
                "SELECT concept, concept_type, source_site, COUNT(*) as w "
                "FROM knowledge_concepts "
                "GROUP BY concept "
                "ORDER BY w DESC LIMIT ?",
                (limit,),
            ).fetchall()
            nodes = [
                {"id": r[0], "type": r[1] or "concept", "source": r[2], "weight": r[3]}
                for r in nodes_raw
            ]

            node_names = [n["id"] for n in nodes]

            # 取边：同一篇文章中同时出现的概念对
            if not node_names:
                return JSONResponse({"ok": True, "nodes": [], "edges": []})

            # 从 backlinks 构建边
            edges_raw = conn.execute(
                """SELECT kb1.target_concept as c1, kb2.target_concept as c2, COUNT(*) as w
                FROM knowledge_backlinks kb1
                JOIN knowledge_backlinks kb2 ON kb1.source_article_id = kb2.source_article_id
                    AND kb1.target_concept < kb2.target_concept
                WHERE kb1.target_concept IN ({}) AND kb2.target_concept IN ({})
                GROUP BY c1, c2
                ORDER BY w DESC
                LIMIT 200""".format(
                    ",".join("?" * len(node_names)), ",".join("?" * len(node_names))
                ),
                node_names + node_names,
            ).fetchall()
            edges = [{"source": r[0], "target": r[1], "weight": r[2]} for r in edges_raw]

            return JSONResponse({"ok": True, "nodes": nodes, "edges": edges})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

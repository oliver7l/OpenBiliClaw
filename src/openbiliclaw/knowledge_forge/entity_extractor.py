"""3.2 实体/概念提取器（Entity & Concept Extractor）。

为每篇文章提取实体并写入 ``entities`` / ``article_entities`` 表，供
作者页 / 主题页 / 概念页使用。

提取流程（文档 3.2.3）：
    1. 作者提取（确定性）：从 ``articles.author`` 字段提取
    2. 主题提取（确定性+LLM）：从 ``articles.tags`` 提取 + LLM 补充 2-5 个
    3. 概念提取（LLM）：从正文提取 3-8 个技术概念（阶段二启用）
    4. 实体页更新：更新 article_count / last_updated_at

实体类型（文档 3.2.1）：author / topic / concept / organization / person / platform
"""

from __future__ import annotations

import json
import logging
import sqlite3
from openbiliclaw.storage.database import open_db_conn
from contextlib import suppress
from pathlib import Path
from typing import Any

from .config import EntityConfig, KnowledgeForgeConfig, load_kf_config
from .models import now_cn
from .prompts import CONCEPT_EXTRACT_PROMPT, TOPIC_EXTRACT_PROMPT, parse_json_array
from .utils import get_llm_client

logger = logging.getLogger(__name__)


def _default_db_path() -> Path:
    try:
        from openbiliclaw.config import load_config

        cfg = load_config()
        p = getattr(cfg, "storage", None)
        if p is not None and getattr(p, "db_path", None):
            return Path(str(p.db_path))
    except Exception:  # noqa: BLE001
        pass
    return Path("data/openbiliclaw.db")


class EntityExtractor:
    """实体/概念提取器。"""

    def __init__(
        self,
        *,
        config: KnowledgeForgeConfig | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.config = config or load_kf_config()
        self.entity_cfg: EntityConfig = self.config.entity
        self.db_path = Path(db_path) if db_path else _default_db_path()
        self.llm = get_llm_client()
        self.spec = self.entity_cfg.llm

    # ------------------------------------------------------------------ 主入口
    async def extract_article(self, row: dict[str, Any]) -> dict[str, Any]:
        """对一篇文章执行全部实体提取；返回统计。"""
        article_id = int(row.get("id") or 0)
        stats: dict[str, Any] = {
            "article_id": article_id,
            "author": None,
            "topics": [],
            "concepts": [],
        }

        # 1. 作者（确定性）
        author = str(row.get("author") or "").strip()
        if author and author.lower() not in ("unknown", "匿名用户", "null"):
            entity_id = self._upsert_entity(
                author, "author", metadata={"source_type": row.get("source_type") or ""}
            )
            if entity_id:
                self._link(article_id, entity_id, 1.0, "author字段")
                stats["author"] = author

        # 2. 主题（标签确定性 + LLM 补充）
        topics = self._extract_topics_from_tags(row)
        if self.entity_cfg.extraction_enabled:
            try:
                llm_topics = await self._llm_topics(row)
                for t in llm_topics:
                    if t not in topics:
                        topics.append(t)
            except Exception as exc:  # noqa: BLE001
                logger.warning("article %d topic LLM extraction failed: %s", article_id, exc)
        for topic in topics:
            entity_id = self._upsert_entity(topic, "topic")
            if entity_id:
                self._link(article_id, entity_id, 0.9, "tags/LLM主题")
        stats["topics"] = topics

        # 3. 概念（LLM，阶段二；默认启用，可配置关闭）
        if self.entity_cfg.concept_extraction_enabled:
            try:
                concepts = await self._llm_concepts(row)
                for c in concepts:
                    entity_id = self._upsert_entity(c, "concept")
                    if entity_id:
                        self._link(article_id, entity_id, 0.8, "LLM概念")
                stats["concepts"] = concepts
            except Exception as exc:  # noqa: BLE001
                logger.warning("article %d concept LLM extraction failed: %s", article_id, exc)

        return stats

    async def extract_batch(
        self,
        *,
        limit: int = 100,
        source_type: str | None = None,
        min_id: int = 0,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """批量提取：增量选择无 article_entities 关联的文章。"""
        rows = self._fetch_candidates(limit=limit, source_type=source_type, min_id=min_id)
        stats = {"processed": 0, "ok": 0, "failed": 0, "topics_added": 0, "concepts_added": 0}
        for row in rows:
            stats["processed"] += 1
            try:
                r = await self.extract_article(row)
                stats["ok"] += 1
                stats["topics_added"] += len(r.get("topics") or [])
                stats["concepts_added"] += len(r.get("concepts") or [])
            except Exception as exc:  # noqa: BLE001
                stats["failed"] += 1
                logger.warning("article %s extraction failed: %s", row.get("id"), exc)
            if dry_run and stats["processed"] >= 5:
                break
        return stats

    # ------------------------------------------------------------------ 主题
    def _extract_topics_from_tags(self, row: dict[str, Any]) -> list[str]:
        """从 tags 字段（JSON 数组字符串）提取主题。"""
        raw = str(row.get("tags") or "")
        if not raw:
            return []
        try:
            tags = json.loads(raw)
        except Exception:  # noqa: BLE001
            tags = [t.strip() for t in raw.split(",") if t.strip()]
        if not isinstance(tags, list):
            return []
        out = []
        for t in tags:
            s = str(t or "").strip()
            if s and len(s) <= 30:
                out.append(s)
        return out[:10]

    async def _llm_topics(self, row: dict[str, Any]) -> list[str]:
        """LLM 从正文提取 2-5 个核心主题；复用已有主题写法。"""
        content = self._pick_content(row)
        if len(content) < 100:
            return []
        existing = self._existing_topic_names(limit=50)
        resp = await self.llm.complete(
            self.spec,
            system_instruction="你是知识库主题标注器。只输出 JSON 数组。",
            user_input=TOPIC_EXTRACT_PROMPT.format(
                title=str(row.get("title") or ""),
                content=self._truncate(content, 3000),
                existing_topics="、".join(existing),
            ),
            max_tokens=300,
            temperature=0.2,
            caller="knowledge_forge.entity.topic",
            json_mode=False,
        )
        return parse_json_array((resp.content or "").strip())

    async def _llm_concepts(self, row: dict[str, Any]) -> list[str]:
        """LLM 从正文提取 3-8 个技术概念。"""
        content = self._pick_content(row)
        if len(content) < 100:
            return []
        resp = await self.llm.complete(
            self.spec,
            system_instruction="你是知识库概念标注器。只输出 JSON 数组。",
            user_input=CONCEPT_EXTRACT_PROMPT.format(
                title=str(row.get("title") or ""),
                content=self._truncate(content, 3000),
            ),
            max_tokens=400,
            temperature=0.2,
            caller="knowledge_forge.entity.concept",
            json_mode=False,
        )
        return parse_json_array((resp.content or "").strip())

    # ------------------------------------------------------------------ 存储
    def _connect(self) -> sqlite3.Connection:
        conn = open_db_conn(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _upsert_entity(
        self, name: str, etype: str, metadata: dict[str, Any] | None = None
    ) -> int | None:
        """查找或创建实体，返回 entity id。

        entities.name 是 UNIQUE 约束（文档 3.2.2），因此按 name 查找复用；
        已存在但 type 不同时保留原 type（首个出现者优先），仅更新元数据。
        """
        if not name:
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, type, metadata FROM entities WHERE name = ?",
                (name,),
            ).fetchone()
            ts = now_cn()
            if row:
                entity_id = int(row["id"])
                old_meta: dict[str, Any] = {}
                with suppress(Exception):
                    old_meta = json.loads(row["metadata"] or "{}")
                if metadata:
                    old_meta.update(metadata)
                conn.execute(
                    "UPDATE entities SET last_updated_at = ?, metadata = ? WHERE id = ?",
                    (ts, json.dumps(old_meta, ensure_ascii=False), entity_id),
                )
                conn.commit()
                return entity_id
            conn.execute(
                "INSERT INTO entities (name, type, description, article_count,"
                " first_seen_at, last_updated_at, metadata) "
                "VALUES (?, ?, '', 0, ?, ?, ?)",
                (name, etype, ts, ts, json.dumps(metadata or {}, ensure_ascii=False)),
            )
            conn.commit()
            return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        finally:
            conn.close()

    def _link(self, article_id: int, entity_id: int, relevance: float, context: str) -> None:
        """建立文章-实体关联（幂等：已存在则更新相关度/上下文）。"""
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO article_entities (article_id, entity_id, relevance, context)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(article_id, entity_id)
                   DO UPDATE SET relevance = excluded.relevance, context = excluded.context""",
                (article_id, entity_id, relevance, context),
            )
            # 计数同步
            conn.execute(
                "UPDATE entities SET article_count ="
                " (SELECT COUNT(*) FROM article_entities WHERE entity_id = ?)"
                " WHERE id = ?",
                (entity_id, entity_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _fetch_candidates(
        self, *, limit: int, source_type: str | None, min_id: int
    ) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            sql = """SELECT a.id, a.title, a.author, a.source_type, a.tags,
                            a.content_text, a.content_cleaned
                     FROM articles a
                     WHERE a.id >= ?
                       AND NOT EXISTS (
                           SELECT 1 FROM article_entities ae WHERE ae.article_id = a.id
                       )
                  """
            params: list[Any] = [min_id]
            if source_type:
                sql += " AND a.source_type = ?"
                params.append(source_type)
            sql += " ORDER BY a.id LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _fetch_row(self, article_id: int) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _existing_topic_names(self, limit: int) -> list[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT name FROM entities WHERE type = 'topic'"
                " ORDER BY article_count DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [str(r["name"]) for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------ 工具
    def _pick_content(self, row: dict[str, Any]) -> str:
        for key in ("content_cleaned", "content_text"):
            v = str(row.get(key) or "").strip()
            if v:
                return v
        return ""

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        return text if len(text) <= limit else text[:limit]


def extract_article(article_id: int) -> dict[str, Any]:
    """同步入口（供 CLI/脚本调用）。"""
    import asyncio

    async def _run() -> dict[str, Any]:
        engine = EntityExtractor()
        row = engine._fetch_row(article_id)
        if row is None:
            return {"article_id": article_id, "error": "article_not_found"}
        return await engine.extract_article(row)

    return asyncio.run(_run())

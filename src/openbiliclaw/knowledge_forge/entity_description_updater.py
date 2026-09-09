"""实体描述自动更新（设计 3.2：实体页增强 - 描述自动生成/持续更新）。

取实体的高相关度文章标题+摘要，用 LLM 生成 1-2 句中文简介并写回
``entities.description`` + ``last_updated_at``。幂等策略：

- 只处理 description 为空或超过 ``refresh_days`` 天未更新的实体；
- 单实体单条 LLM 调用，失败不阻断其余实体；
- ``dry_run`` 只输出将更新的实体与候选描述，不落库。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from openbiliclaw.storage.database import open_db_conn
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from openbiliclaw.knowledge_forge.config import KnowledgeForgeConfig, load_kf_config
from openbiliclaw.knowledge_forge.models import now_cn
from openbiliclaw.knowledge_forge.utils import get_llm_client

logger = logging.getLogger(__name__)

DESCRIPTION_PROMPT = """根据实体「{name}」的相关文章信息，为该实体撰写 1-2 句中文简介。
要求：客观、具体、概括实体是什么/做什么/核心特点；不要编造文章中不存在的细节；不要使用"本文/本库"等表述。

相关文章：
{articles}

只输出 JSON：{{"description": "实体简介"}}"""


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


class EntityDescriptionUpdater:
    """实体描述自动更新器。"""

    def __init__(
        self,
        *,
        config: KnowledgeForgeConfig | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.config = config or load_kf_config()
        self.db_path = Path(db_path) if db_path else _default_db_path()
        self._llm = get_llm_client()

    # ------------------------------------------------------------------ 主入口
    async def run(
        self,
        *,
        limit: int = 10,
        entity_type: str = "",
        refresh_days: int = 30,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """执行一轮描述更新。返回统计。"""
        stats: dict[str, Any] = {
            "scanned": 0,
            "updated": 0,
            "failed": 0,
            "dry_run": dry_run,
            "updates": [],
        }
        candidates = self._fetch_candidates(
            limit=limit, entity_type=entity_type, refresh_days=refresh_days
        )
        stats["scanned"] = len(candidates)
        for ent in candidates:
            articles = self._fetch_articles(ent["id"])
            if not articles:
                continue
            description = await self._generate(ent["name"], articles)
            if not description:
                stats["failed"] += 1
                continue
            stats["updates"].append({"id": ent["id"], "name": ent["name"], "type": ent["type"]})
            if not dry_run:
                self._write_description(ent["id"], description)
        stats["updated"] = len(stats["updates"])
        return stats

    # ------------------------------------------------------------------ 内部
    def _fetch_candidates(
        self, *, limit: int, entity_type: str, refresh_days: int
    ) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            cutoff = (datetime.now() - timedelta(days=max(0, refresh_days))).strftime("%Y-%m-%d")
            where, params = (
                ["(description IS NULL OR description = '' OR last_updated_at < ?)"],
                [cutoff],
            )
            if entity_type:
                where.append("type = ?")
                params.append(entity_type)
            rows = conn.execute(
                f"""SELECT id, name, type FROM entities
                    WHERE {" AND ".join(where)}
                    ORDER BY article_count DESC LIMIT ?""",
                [*params, limit],
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _fetch_articles(self, entity_id: int) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT a.title, COALESCE(a.summary_compact, '') AS summary_compact
                   FROM article_entities ae JOIN articles a ON a.id = ae.article_id
                   WHERE ae.entity_id = ?
                   ORDER BY ae.relevance DESC LIMIT 6""",
                (entity_id,),
            ).fetchall()
            return [
                {
                    "title": r["title"] or "",
                    "summary": (r["summary_compact"] or "")[:120],
                }
                for r in rows
            ]
        finally:
            conn.close()

    async def _generate(self, name: str, articles: list[dict[str, Any]]) -> str:
        sample = "\n".join(
            f"- 《{a['title']}》{('：' + a['summary']) if a['summary'] else ''}" for a in articles
        )
        try:
            resp = await self._llm.complete(
                self.config.entity.llm,
                system_instruction=(
                    "你是知识库编辑。为实体撰写客观、具体的中文简介。"
                    '只输出 JSON：{"description": "实体简介"}'
                ),
                user_input=DESCRIPTION_PROMPT.format(name=name, articles=sample),
                json_mode=True,
                max_tokens=256,
                temperature=0.3,
                caller="knowledge_forge.entity_describe",
            )
            data = json.loads((resp.content or "").strip())
            desc = str(data.get("description") or "").strip()
            return desc if len(desc) >= 8 else ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("实体「%s」描述生成失败：%s", name, exc)
            return ""

    def _write_description(self, entity_id: int, description: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE entities SET description = ?, last_updated_at = ? WHERE id = ?",
                (description, now_cn(), entity_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = open_db_conn(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


def run_entity_describe(
    *,
    limit: int = 10,
    entity_type: str = "",
    refresh_days: int = 30,
    dry_run: bool = False,
) -> dict[str, Any]:
    """同步入口。"""

    async def _run() -> dict[str, Any]:
        return await EntityDescriptionUpdater().run(
            limit=limit, entity_type=entity_type, refresh_days=refresh_days, dry_run=dry_run
        )

    return asyncio.run(_run())

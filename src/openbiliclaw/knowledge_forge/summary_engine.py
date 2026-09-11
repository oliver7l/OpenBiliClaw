"""3.1 分层摘要引擎（Summary Engine）。

每篇文章生成三层摘要（文档 3.1.1）：
    - detailed（详细版，2000-5000 字）：文章详情页、专题生成、深度阅读
    - compact（精简版，500-1000 字）：推荐列表、搜索结果、专题列表
    - ultra_compact（超精简版，50-200 字）：知识图谱节点、快速浏览

生成流程（文档 3.1.2）：
    清理后的正文（content_cleaned，回退 content_text）
      → 1. LLM 生成 detailed 摘要
      → 2. 从 detailed 压缩生成 compact
      → 3. 从 compact 压缩生成 ultra_compact
      → 4. 质量自检（可选，LLM 评分 0-1）

为什么逐级压缩而不是分别生成（文档 3.1.2）：
    - 保证三层摘要一致性；detailed 只读一次全文省 token；质量可控。

兼容策略（文档 3.1.3）：现有 ``ai_summary`` 字段保留作为 compact 兼容字段；
读取时优先 ``summary_compact``，为空回退 ``ai_summary``。
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from openbiliclaw.storage.database import open_db_conn

from .config import KnowledgeForgeConfig, load_kf_config
from .models import SummaryResult, now_cn
from .prompts import (
    COMPACT_SUMMARY_PROMPT,
    DETAILED_SUMMARY_PROMPT,
    SUMMARY_QUALITY_PROMPT,
    ULTRA_COMPACT_SUMMARY_PROMPT,
)
from .utils import get_llm_client

logger = logging.getLogger(__name__)


def _default_db_path() -> Path:
    """knowledge_forge 模块使用独立的 knowledge_audit.db（v0.4.0+）。"""
    try:
        from openbiliclaw.config import load_config

        cfg = load_config()
        p = getattr(cfg, "storage", None)
        if p is not None and getattr(p, "db_path", None):
            return Path(str(p.db_path)).with_name("knowledge_audit.db")
    except Exception:  # noqa: BLE001 — 配置不可用回退默认路径
        pass
    return Path("data/knowledge_audit.db")


class SummaryEngine:
    """分层摘要引擎。"""

    def __init__(
        self,
        *,
        config: KnowledgeForgeConfig | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.config = config or load_kf_config()
        self.db_path = Path(db_path) if db_path else _default_db_path()
        self.llm = get_llm_client()
        self.spec = self.config.summary

    # ------------------------------------------------------------------ 生成
    async def generate(
        self,
        row: dict[str, Any],
        *,
        force: bool = False,
        caller: str = "knowledge_forge.summary",
    ) -> SummaryResult:
        """为一篇文章生成三层摘要；返回 SummaryResult。

        ``row`` 需含 id/title/author/content_text/content_cleaned。
        已有完整三层摘要且未 force 时跳过（幂等）。
        """
        article_id = int(row.get("id") or 0)
        if not force and self._has_summary(row):
            return SummaryResult(article_id=article_id, error="skipped:cached")

        content = self._pick_content(row)
        title = str(row.get("title") or "").strip()
        author = str(row.get("author") or "").strip()
        if len(content) < 50:
            return SummaryResult(article_id=article_id, error=f"content_too_short:{len(content)}")

        try:
            # 1. detailed（读一次全文）
            detailed = await self._llm_text(
                DETAILED_SUMMARY_PROMPT.format(
                    title=title,
                    author=author,
                    content=self._truncate(content, 16000),
                ),
                caller=caller,
                max_tokens=6000,
            )
            if not detailed:
                return SummaryResult(article_id=article_id, error="detailed_empty")

            # 2. compact（从 detailed 压缩）
            compact = await self._llm_text(
                COMPACT_SUMMARY_PROMPT.format(detailed_summary=detailed),
                caller=caller,
                max_tokens=2000,
            )
            if not compact:
                compact = self._fallback_compact(detailed)

            # 3. ultra_compact（从 compact 压缩）
            ultra = await self._llm_text(
                ULTRA_COMPACT_SUMMARY_PROMPT.format(compact_summary=compact),
                caller=caller,
                max_tokens=600,
            )

            # 4. 质量自检（可选）
            quality: float | None = None
            if self.config.summary_quality_check and detailed and compact:
                quality = await self._quality_score(
                    content,
                    detailed,
                    compact,
                    caller=caller,
                )

            result = SummaryResult(
                article_id=article_id,
                detailed=detailed,
                compact=compact,
                ultra_compact=ultra or "",
                quality=quality,
                provider="",
            )
            self._persist(row, result)
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception("summary generation failed for article %d", article_id)
            return SummaryResult(article_id=article_id, error=str(exc))

    async def generate_by_id(self, article_id: int, *, force: bool = False) -> SummaryResult:
        """按 ID 加载文章并生成摘要。"""
        row = self._fetch_row(article_id)
        if row is None:
            return SummaryResult(article_id=article_id, error="article_not_found")
        return await self.generate(row, force=force)

    async def generate_batch(
        self,
        *,
        limit: int = 100,
        source_type: str | None = None,
        min_id: int = 0,
        force: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """批量生成摘要（增量：有正文、缺 summary_compact）。

        返回统计：processed / ok / failed / skipped。
        """
        rows = self._fetch_candidates(limit=limit, source_type=source_type, min_id=min_id)
        stats: dict[str, Any] = {
            "processed": 0,
            "ok": 0,
            "failed": 0,
            "skipped": 0,
            "errors": [],
        }
        for row in rows:
            stats["processed"] += 1
            result = await self.generate(row, force=force)
            if result.error:
                if result.error.startswith("skipped"):
                    stats["skipped"] += 1
                else:
                    stats["failed"] += 1
                    stats["errors"].append({"article_id": result.article_id, "error": result.error})
            else:
                stats["ok"] += 1
            if dry_run and stats["processed"] >= 5:
                break
        return stats

    # ------------------------------------------------------------------ LLM
    async def _llm_text(
        self,
        prompt: str,
        *,
        caller: str,
        max_tokens: int,
    ) -> str:
        resp = await self.llm.complete(
            self.spec,
            system_instruction="你是知识库助手。只依据给定材料输出，不要添加原文没有的信息。",
            user_input=prompt,
            max_tokens=max_tokens,
            temperature=0.3,
            caller=caller,
        )
        return (resp.content or "").strip()

    async def _quality_score(
        self,
        content: str,
        detailed: str,
        compact: str,
        *,
        caller: str,
    ) -> float | None:
        """LLM 评估摘要是否覆盖原文核心，输出 0-1 浮点数。失败返回 None。"""
        try:
            resp = await self.llm.complete(
                self.spec,
                system_instruction="只输出一个 0-1 之间的浮点数。",
                user_input=SUMMARY_QUALITY_PROMPT.format(
                    content_head=self._truncate(content, 1500),
                    detailed=detailed,
                    compact=compact,
                ),
                max_tokens=50,
                temperature=0.0,
                caller=caller,
                json_mode=False,
            )
            raw = (resp.content or "").strip()
            m = re.search(r"0\.\d{1,2}|1\.0|1", raw)
            if not m:
                return None
            v = float(m.group(0))
            return max(0.0, min(1.0, v))
        except Exception:  # noqa: BLE001
            logger.debug("summary quality check failed", exc_info=True)
            return None

    # ------------------------------------------------------------------ 工具
    def _pick_content(self, row: dict[str, Any]) -> str:
        """优先 content_cleaned（清理后的正文），回退 content_text。"""
        for key in ("content_cleaned", "content_text"):
            v = str(row.get(key) or "").strip()
            if v:
                return v
        return ""

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        return text if len(text) <= limit else text[:limit]

    @staticmethod
    def _fallback_compact(detailed: str) -> str:
        """LLM 压缩失败时：取 detailed 的前 3 个分点作为降级 compact。"""
        lines = [ln for ln in detailed.split("\n") if ln.strip()]
        head = lines[:8]
        return "\n".join(head)[:1000] or detailed[:1000]

    def _has_summary(self, row: dict[str, Any]) -> bool:
        c = str(row.get("summary_compact") or "").strip()
        d = str(row.get("summary_detailed") or "").strip()
        return bool(c and d)

    # ------------------------------------------------------------------ 存储
    def _connect(self) -> sqlite3.Connection:
        conn = open_db_conn(self.db_path)
        conn.row_factory = sqlite3.Row
        # ATTACH 主库，使跨库查询（如 JOIN articles）正常工作
        from contextlib import suppress as _suppress
        from pathlib import Path as _Path
        _main_path = _Path(str(self.db_path)).with_name('openbiliclaw.db')
        if _main_path.exists():
            with _suppress(Exception):
                conn.execute('ATTACH DATABASE ? AS main_db', (str(_main_path),))
            # v0.4.0+: articles 表迁移到 content.db，ATTACH 以便跨库查询
            _content_path = _Path(str(self.db_path)).with_name('content.db')
            if _content_path.exists():
                with _suppress(Exception):
                    conn.execute('ATTACH DATABASE ? AS content', (str(_content_path),))
        return conn

    def _fetch_row(self, article_id: int) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _fetch_candidates(
        self,
        *,
        limit: int,
        source_type: str | None,
        min_id: int,
    ) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            sql = """SELECT id, title, author, source_type, content_text, content_cleaned,
                            summary_compact, summary_detailed
                     FROM articles
                     WHERE (LENGTH(COALESCE(content_cleaned,'')) > 50
                             OR LENGTH(COALESCE(content_text,'')) > 50)
                       AND (COALESCE(summary_compact,'') = ''
                            OR COALESCE(summary_detailed,'') = '')
                       AND id >= ?
                  """
            params: list[Any] = [min_id]
            if source_type:
                sql += " AND source_type = ?"
                params.append(source_type)
            sql += " ORDER BY id LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _persist(self, row: dict[str, Any], result: SummaryResult) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """UPDATE articles SET
                       summary_detailed = ?, summary_compact = ?,
                       summary_ultra_compact = ?, summary_quality = ?,
                       summary_version = COALESCE(summary_version, 0) + 1,
                       summary_generated_at = ?
                   WHERE id = ?""",
                (
                    result.detailed,
                    result.compact,
                    result.ultra_compact,
                    result.quality,
                    now_cn(),
                    result.article_id,
                ),
            )
            conn.commit()
            logger.info(
                "article %d summary saved (detailed=%d chars, compact=%d, ultra=%d)",
                result.article_id,
                len(result.detailed),
                len(result.compact),
                len(result.ultra_compact),
            )
        finally:
            conn.close()


def summarize_article(article_id: int, *, force: bool = False) -> SummaryResult:
    """同步入口（供 CLI/脚本调用）：内部跑事件循环。"""
    import asyncio

    return asyncio.run(SummaryEngine().generate_by_id(article_id, force=force))

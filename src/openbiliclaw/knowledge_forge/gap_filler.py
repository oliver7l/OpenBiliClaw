"""自动补充闭环（设计 3.3）。

缺口分析 → 自动搜索 → 自动入库（含正文清理）→ 更新知识库：

1. 取 ``gap_records`` 中 open 且高危的主题覆盖缺口（description 形如
   「主题「RL」仅 2 篇，覆盖不足」）；
2. 解析主题名，用 B 站搜索 API 检索候选内容；
3. 候选去重（articles.url 已存在跳过），用 url_processors 提取结构化内容，
   经 ``Database.upsert_article`` 入库（入库钩子自动完成正文清理）；
4. 单缺口成功入库 ≥1 篇后标记 ``resolved``，记录填补文章数。

安全：尊重 B 站搜索冷却（冷却期内跳过搜索直接返回）、单缺口最多补
``fill_per_gap`` 篇、缺口级容错（单个缺口失败不阻断后续）。
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from openbiliclaw.storage.database import open_db_conn
from pathlib import Path
from typing import Any

from openbiliclaw.bilibili.api import BilibiliAPIClient
from openbiliclaw.knowledge_forge.config import KnowledgeForgeConfig, load_kf_config

logger = logging.getLogger(__name__)

_TOPIC_RE = re.compile(r"「(.+?)」")
# 每缺口最多填补篇数（避免单缺口大量抓取）
_DEFAULT_FILL_PER_GAP = 3
# 搜索每主题取候选数
_DEFAULT_SEARCH_PER_TOPIC = 10


def _default_db_path() -> Path:
    """knowledge_forge 模块使用独立的 knowledge_audit.db（v0.4.0+）。

    从配置的主库路径派生 knowledge_audit.db 路径，保持与主库同目录。
    """
    try:
        from openbiliclaw.config import load_config

        cfg = load_config()
        p = getattr(cfg, "storage", None)
        if p is not None and getattr(p, "db_path", None):
            return Path(str(p.db_path)).with_name("knowledge_audit.db")
    except Exception:  # noqa: BLE001
        pass
    return Path("data/knowledge_audit.db")


class GapFiller:
    """自动补充闭环执行器。"""

    def __init__(
        self,
        *,
        config: KnowledgeForgeConfig | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.config = config or load_kf_config()
        self.db_path = Path(db_path) if db_path else _default_db_path()

    # ------------------------------------------------------------------ 主入口
    async def fill(
        self,
        *,
        limit_gaps: int = 5,
        fill_per_gap: int = _DEFAULT_FILL_PER_GAP,
        search_per_topic: int = _DEFAULT_SEARCH_PER_TOPIC,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """执行一轮自动补充。返回统计。"""
        stats: dict[str, Any] = {
            "gaps_scanned": 0,
            "gaps_filled": 0,
            "topics_searched": 0,
            "candidates": 0,
            "articles_filled": 0,
            "already_exists": 0,
            "extract_failed": 0,
            "search_skipped_cooldown": 0,
            "filled": [],
        }
        gaps = self._fetch_open_gaps(limit=limit_gaps)
        stats["gaps_scanned"] = len(gaps)
        if not gaps:
            return stats

        bili = BilibiliAPIClient()

        for gap in gaps:
            topic = self._parse_topic(gap)
            if not topic:
                logger.warning("缺口 %s 无法解析主题名：%s", gap["id"], gap["description"])
                continue
            try:
                res = await self._fill_gap(
                    gap, topic, bili, fill_per_gap, search_per_topic, dry_run
                )
                for k, v in res.items():
                    stats[k] += v
                if res["articles_filled"] > 0:
                    stats["gaps_filled"] += 1
                    stats["filled"].append({"gap_id": gap["id"], "topic": topic})
            except Exception as exc:  # noqa: BLE001 — 缺口级容错
                logger.warning("缺口 %s（%s）自动补充失败：%s", gap["id"], topic, exc)
        return stats

    async def _fill_gap(
        self,
        gap: dict[str, Any],
        topic: str,
        bili: Any,
        fill_per_gap: int,
        search_per_topic: int,
        dry_run: bool,
    ) -> dict[str, Any]:
        out = {
            "topics_searched": 0,
            "candidates": 0,
            "articles_filled": 0,
            "already_exists": 0,
            "extract_failed": 0,
            "search_skipped_cooldown": 0,
        }
        # 1. 搜索
        if bili.search_cooldown_remaining() > 0:
            out["search_skipped_cooldown"] = 1
            logger.info("B 站搜索冷却中，跳过主题 %s", topic)
            return out
        results = await bili.search(topic, page=1, page_size=search_per_topic)
        out["topics_searched"] = 1
        out["candidates"] = len(results)
        if not results:
            return out

        # 2. 候选去重 + 提取 + 入库
        filled = 0
        for item in results[: fill_per_gap * 2]:  # 留冗余候选（提取失败可换）
            if filled >= fill_per_gap:
                break
            url = self._candidate_url(item)
            if not url:
                continue
            if self._url_exists(url):
                out["already_exists"] += 1
                continue
            ok = await self._extract_and_save(url, dry_run=dry_run)
            if ok:
                filled += 1
                out["articles_filled"] += 1
            else:
                out["extract_failed"] += 1

        # 3. 标记缺口
        if filled > 0 and not dry_run:
            self._mark_gap_resolved(gap["id"], topic, filled)
        return out

    # ------------------------------------------------------------------ 工具
    def _fetch_open_gaps(self, *, limit: int = 5) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT id, gap_type, entity_id, severity, description, suggestion
                   FROM gap_records
                   WHERE status='open' AND gap_type='topic_coverage' AND severity='high'
                   ORDER BY current_count ASC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _parse_topic(self, gap: dict[str, Any]) -> str:
        m = _TOPIC_RE.search(str(gap.get("description") or ""))
        return m.group(1).strip() if m else ""

    def _candidate_url(self, item: dict[str, Any]) -> str:
        """从 B 站搜索结果构造视频 URL。"""
        bvid = str(item.get("bvid") or "")
        if bvid:
            return f"https://www.bilibili.com/video/{bvid}"
        arcurl = str(item.get("arcurl") or "")
        return arcurl if arcurl.startswith("http") else ""

    def _url_exists(self, url: str) -> bool:
        conn = self._connect()
        try:
            row = conn.execute("SELECT COUNT(*) FROM articles WHERE url = ?", (url,)).fetchone()
            return bool(row and int(row[0] or 0) > 0)
        finally:
            conn.close()

    async def _extract_and_save(self, url: str, *, dry_run: bool) -> bool:
        """url_processors 提取 + upsert_article 入库（含正文清理钩子）。"""
        from openbiliclaw.sources.url_processors import match_processor
        from openbiliclaw.sources.url_processors.base import ProcessorStatus

        try:
            result = await match_processor(url).process(url)
            if result.status != ProcessorStatus.success or not (result.content_text or "").strip():
                logger.info("提取失败 %s: %s", url, result.error)
                return False
            if dry_run:
                return True
            from openbiliclaw.storage.database import Database

            db = Database(self.db_path)
            db.initialize()
            try:
                article_id = db.upsert_article(
                    source_type=result.source_type or "",
                    source_name=result.source_name or "",
                    title=result.title or "",
                    url=result.url or url,
                    author=result.author or "",
                    summary=result.summary or "",
                    content_text=result.content_text or "",
                    published_at=result.published_at or "",
                    tags=result.tags or [],
                )
                return article_id is not None
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001 — 单条提取失败不阻断
            logger.warning("提取/入库失败 %s: %s", url, exc)
            return False

    def _mark_gap_resolved(self, gap_id: int, topic: str, filled: int) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE gap_records SET status='resolved', description=? WHERE id=?",
                (f"已自动补充 {filled} 篇（主题「{topic}」）", gap_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = open_db_conn(self.db_path)
        conn.row_factory = sqlite3.Row
        # ATTACH 主库，使跨库查询（如 JOIN articles）正常工作
        from pathlib import Path as _Path
        from contextlib import suppress as _suppress
        _main_path = _Path(str(self.db_path)).with_name('openbiliclaw.db')
        if _main_path.exists():
            with _suppress(Exception):
                conn.execute('ATTACH DATABASE ? AS main_db', (str(_main_path),))
        return conn


def run_gap_fill(
    *,
    limit_gaps: int = 5,
    fill_per_gap: int = _DEFAULT_FILL_PER_GAP,
    search_per_topic: int = _DEFAULT_SEARCH_PER_TOPIC,
    dry_run: bool = False,
) -> dict[str, Any]:
    """同步入口。"""

    async def _run() -> dict[str, Any]:
        return await GapFiller().fill(
            limit_gaps=limit_gaps,
            fill_per_gap=fill_per_gap,
            search_per_topic=search_per_topic,
            dry_run=dry_run,
        )

    return asyncio.run(_run())

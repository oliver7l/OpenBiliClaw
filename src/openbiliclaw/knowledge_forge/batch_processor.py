"""历史文章批量处理（设计 3.5）。

对存量文章分批补全 Knowledge Forge 各阶段产物：
正文清理 → 分层摘要 → 标签 → 实体提取 → 质量分。

- ``priority=high``：优先处理高价值文章（已清理且缺摘要的长文，len≥500），
  按 id 升序分批；``priority=all``：补齐全部缺口。
- 单篇失败不阻断批次；每批完成后将进度写入 ``audit_tasks``（task_type=batch_backfill），
  支持 CLI 用 ``--offset`` 断点续跑。
- 实体提取复用 EntityExtractor，质量分复用 QualityAuditor 的写入逻辑。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from openbiliclaw.storage.database import open_db_conn
from pathlib import Path
from typing import Any

from openbiliclaw.knowledge_forge.config import KnowledgeForgeConfig, load_kf_config
from openbiliclaw.knowledge_forge.models import now_cn

logger = logging.getLogger(__name__)

# 高价值阈值：正文清理后 ≥ 500 字视为可处理的完整文章
_MIN_HIGH_VALUE_LEN = 500


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


class BatchProcessor:
    """历史文章批量处理管线。"""

    def __init__(
        self,
        *,
        config: KnowledgeForgeConfig | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.config = config or load_kf_config()
        self.db_path = Path(db_path) if db_path else _default_db_path()

    # ------------------------------------------------------------------ 主入口
    async def backfill(
        self,
        *,
        limit: int = 10,
        batch_size: int = 10,
        priority: str = "high",
        offset: int = 0,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """分批补全。返回统计（processed/summarized/tagged/entities/cleaned/scores）。"""
        stats: dict[str, Any] = {
            "processed": 0,
            "cleaned": 0,
            "summarized": 0,
            "tagged": 0,
            "entities": 0,
            "scored": 0,
            "failed": 0,
            "task_id": None,
            "last_id": offset,
        }
        if priority not in ("high", "all"):
            raise ValueError("priority 仅支持 high/all")

        task_id = None
        if not dry_run:
            task_id = self._create_task(limit, priority, offset)
            stats["task_id"] = task_id

        done = 0
        cursor = offset
        while done < limit:
            batch = self._fetch_batch(cursor=cursor, size=batch_size, priority=priority)
            if not batch:
                break
            results = await asyncio.gather(*(self._process_one(r) for r in batch))
            for row, res in zip(batch, results, strict=False):
                cursor = max(cursor, int(row["id"]))
                done += 1
                if res is None:
                    stats["failed"] += 1
                    continue
                stats["processed"] += 1
                for k in ("cleaned", "summarized", "tagged", "entities", "scored"):
                    stats[k] += int(res.get(k, 0))
            stats["last_id"] = cursor
            if not dry_run:
                self._update_progress(task_id, done, stats)
            if len(batch) < batch_size:
                break
        if task_id is not None:
            self._finish_task(task_id, done, stats)
        return stats

    # ------------------------------------------------------------------ 候选
    def _fetch_batch(self, *, cursor: int, size: int, priority: str) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            if priority == "high":
                # 已清理且缺摘要的长文优先（高价值）
                rows = conn.execute(
                    """SELECT id, title, source_type, source_name, author, content_text,
                              content_cleaned, summary_compact, tags, url
                       FROM articles
                       WHERE id > ? AND COALESCE(content_cleaned,'') <> ''
                         AND length(content_cleaned) >= ? AND COALESCE(summary_compact,'') = ''
                       ORDER BY id ASC LIMIT ?""",
                    (cursor, _MIN_HIGH_VALUE_LEN, size),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT id, title, source_type, source_name, author, content_text,
                              content_cleaned, summary_compact, tags, url
                       FROM articles
                       WHERE id > ?
                       ORDER BY id ASC LIMIT ?""",
                    (cursor, size),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    async def _process_one(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """单篇完整管线：清理 → 摘要 → 标签 → 实体 → 质量分。

        步骤级容错：单步失败记 warning 继续后续步骤，已完成部分计入统计
        （绝不因摘要失败丢弃已清理的文章）。
        """
        out = {"cleaned": 0, "summarized": 0, "tagged": 0, "entities": 0, "scored": 0}
        try:
            aid = int(row["id"])

            # 1. 正文清理（缺 content_cleaned 时）
            if not (row.get("content_cleaned") or "").strip():
                try:
                    from openbiliclaw.knowledge_forge.content_cleaner import ContentCleaner

                    cr = ContentCleaner().clean(
                        str(row.get("content_text") or ""),
                        title=str(row.get("title") or ""),
                        source_type=str(row.get("source_type") or ""),
                        article_id=aid,
                    )
                    self._update_clean(aid, cr)
                    out["cleaned"] = 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("批量清理失败 article=%s: %s", aid, exc)

            # 2. 分层摘要（缺 summary_compact 时）
            if not (row.get("summary_compact") or "").strip():
                try:
                    from openbiliclaw.knowledge_forge.summary_engine import SummaryEngine

                    result = await SummaryEngine(db_path=self.db_path).generate_by_id(aid)
                    if not result.error and result.detailed:
                        out["summarized"] = 1
                    else:
                        logger.warning("批量摘要失败 article=%s: %s", aid, result.error)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("批量摘要异常 article=%s: %s", aid, exc)

            # 3. 标签（缺 tags 时）
            if not (row.get("tags") or "").strip() or row.get("tags") in ("[]", "null"):
                try:
                    from openbiliclaw.knowledge_forge.auto_fixer import IssueFixer

                    fake = {
                        "id": 0,
                        "article_id": aid,
                        "issue_type": "missing_tags",
                        "details": {},
                    }
                    if await IssueFixer(db_path=self.db_path)._fix_tags(fake):
                        out["tagged"] = 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("批量补标签失败 article=%s: %s", aid, exc)

            # 4. 实体提取（复用 EntityExtractor）
            try:
                from openbiliclaw.knowledge_forge.entity_extractor import EntityExtractor

                extractor = EntityExtractor(db_path=self.db_path)
                full = extractor._fetch_row(aid)
                if full:
                    estats = await extractor.extract_article(full)
                    if estats.get("topics") or estats.get("concepts"):
                        out["entities"] = 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("批量实体提取失败 article=%s: %s", aid, exc)

            # 5. 质量分（本地计算，与 QualityAuditor 维度一致）
            try:
                self._write_quality_score(aid)
                out["scored"] = 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("批量质量分失败 article=%s: %s", aid, exc)

            return out
        except Exception as exc:  # noqa: BLE001 — 单篇失败不阻断批次
            logger.warning("批量处理失败 article=%s: %s", row.get("id"), exc)
            return None

    def _update_clean(self, aid: int, cr: Any) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """UPDATE articles SET content_cleaned = ?, content_clean_score = ?,
                   content_clean_log = ?, content_verified = ?, content_verify_result = ?,
                   updated_at = datetime('now','localtime') WHERE id = ?""",
                (
                    cr.cleaned_text or None,
                    cr.clean_score,
                    json.dumps(cr.operations, ensure_ascii=False, default=str),
                    1 if cr.verified else 0,
                    json.dumps(cr.verify_issues, ensure_ascii=False, default=str),
                    aid,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _write_quality_score(self, aid: int) -> None:
        """单篇质量分（与 QualityAuditor 维度一致：完整度/内容/链接/唯一性）。"""
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT content_text, content_cleaned, ai_summary, summary_compact,
                          tags, author, content_hash, source_type
                   FROM articles WHERE id = ?""",
                (aid,),
            ).fetchone()
            if not row:
                return
            content, cleaned, summary, compact, tags, author, chash, stype = row
            completeness = 100.0
            if not (compact or "").strip():
                completeness -= 15.0
            if not (tags or "").strip() or tags in ("[]", "null"):
                completeness -= 10.0
            if not (author or "").strip():
                completeness -= 5.0
            if not (chash or "").strip():
                completeness -= 3.0
            content_score = 100.0
            if not (content or "").strip():
                content_score = 20.0
            elif len(content) < 200:
                content_score -= 25.0
            if not (cleaned or "").strip() and (content or "").strip():
                content_score -= 10.0
            conn.execute(
                """INSERT INTO article_quality_scores
                   (article_id, overall_score, completeness_score, content_score,
                    link_score, uniqueness_score, last_audited_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(article_id) DO UPDATE SET
                     overall_score=excluded.overall_score,
                     completeness_score=excluded.completeness_score,
                     content_score=excluded.content_score,
                     link_score=excluded.link_score,
                     uniqueness_score=excluded.uniqueness_score,
                     last_audited_at=excluded.last_audited_at""",
                (
                    aid,
                    round((completeness + content_score) / 2, 1),
                    round(completeness, 1),
                    round(content_score, 1),
                    100.0,
                    100.0,
                    now_cn(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ 任务进度
    def _create_task(self, limit: int, priority: str, offset: int) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(
                """INSERT INTO audit_tasks (task_type, status, started_at, total_articles,
                   created_at) VALUES ('batch_backfill', 'running', ?, ?, ?)""",
                (now_cn(), limit, now_cn()),
            )
            conn.execute(
                "UPDATE audit_tasks SET details = ? WHERE id = ?",
                (json.dumps({"priority": priority, "offset": offset}), cur.lastrowid),
            )
            conn.commit()
            return int(cur.lastrowid or 0)
        finally:
            conn.close()

    def _update_progress(self, task_id: int | None, done: int, stats: dict[str, Any]) -> None:
        if task_id is None:
            return
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE audit_tasks SET details = ? WHERE id = ?",
                (json.dumps({"done": done, "last_id": stats["last_id"]}), task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _finish_task(self, task_id: int | None, done: int, stats: dict[str, Any]) -> None:
        if task_id is None:
            return
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE audit_tasks SET status='completed', completed_at=?, issues_found=?"
                " WHERE id=?",
                (now_cn(), done, task_id),
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
            # v0.4.0+: articles 表迁移到 content.db，ATTACH 以便跨库查询
            _content_path = _Path(str(self.db_path)).with_name('content.db')
            if _content_path.exists():
                with _suppress(Exception):
                    conn.execute('ATTACH DATABASE ? AS content', (str(_content_path),))
        return conn


def run_backfill(
    *,
    limit: int = 10,
    batch_size: int = 10,
    priority: str = "high",
    offset: int = 0,
    dry_run: bool = False,
) -> dict[str, Any]:
    """同步入口。"""

    async def _run() -> dict[str, Any]:
        return await BatchProcessor().backfill(
            limit=limit, batch_size=batch_size, priority=priority, offset=offset, dry_run=dry_run
        )

    return asyncio.run(_run())

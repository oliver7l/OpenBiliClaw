"""低质量内容检测（设计 2.5，LLM 抽样）。

对疑似低质量文章（广告/垃圾/正文与标题不符/无意义文本）做 LLM 抽样判定：
优先从审计已有疑似问题（content_contamination / too_short / not_cleaned）的文章中抽样，
不足再从全库随机补足，控制 LLM 成本（抽样比例默认 5%）。
低质量命中写入 ``audit_issues(issue_type=low_quality, severity=high)``。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from openbiliclaw.knowledge_forge.config import (
    KnowledgeForgeConfig,
    LowQualityConfig,
    load_kf_config,
)
from openbiliclaw.knowledge_forge.models import now_cn
from openbiliclaw.knowledge_forge.prompts import LOW_QUALITY_DETECT_PROMPT
from openbiliclaw.knowledge_forge.utils import get_llm_client
from openbiliclaw.storage.database import open_db_conn

logger = logging.getLogger(__name__)

# 疑似问题类型：优先从这些 open 问题对应的文章抽样
_SUSPECT_ISSUE_TYPES = ("content_contamination", "too_short", "not_cleaned")


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


class LowQualityDetector:
    """低质量内容检测器（LLM 抽样）。"""

    def __init__(
        self,
        *,
        config: KnowledgeForgeConfig | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.config = config or load_kf_config()
        self.lq_cfg: LowQualityConfig = self.config.low_quality
        self.db_path = Path(db_path) if db_path else _default_db_path()
        self._llm = get_llm_client()

    # ------------------------------------------------------------------ 主入口
    async def detect(
        self,
        *,
        limit: int = 0,
        sample_ratio: float | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """执行低质量内容抽样检测。返回统计。

        limit>0 时精确取前 limit 篇候选；limit=0 时按 sample_ratio 抽样。
        """
        ratio = sample_ratio if sample_ratio is not None else self.lq_cfg.sample_ratio
        candidates = self._fetch_candidates(limit=limit, ratio=ratio)
        stats: dict[str, Any] = {
            "sampled": 0,
            "low_quality": 0,
            "normal": 0,
            "failed": 0,
            "task_id": None,
        }
        if not candidates:
            return stats

        task_id = None
        if not dry_run:
            task_id = self._create_task(len(candidates))
            stats["task_id"] = task_id

        sem = asyncio.Semaphore(3)

        async def _judge(row: dict[str, Any]) -> dict[str, Any] | None:
            async with sem:
                return await self._judge_one(row)

        results = await asyncio.gather(*(_judge(r) for r in candidates))
        for row, res in zip(candidates, results, strict=False):
            if res is None:
                stats["failed"] += 1
                continue
            stats["sampled"] += 1
            if res["low_quality"]:
                stats["low_quality"] += 1
                if not dry_run:
                    self._write_issue(row, res, task_id)
            else:
                stats["normal"] += 1

        if task_id is not None:
            self._finish_task(task_id, stats)
        return stats

    # ------------------------------------------------------------------ 候选
    def _fetch_candidates(self, *, limit: int = 0, ratio: float) -> list[dict[str, Any]]:
        """优先取疑似问题文章，不足随机补足。"""
        conn = self._connect()
        try:
            wanted = (
                limit
                if limit > 0
                else min(
                    int(self.lq_cfg.max_samples),
                    max(1, int(self._count_articles(conn) * ratio)),
                )
            )
            if wanted <= 0:
                return []
            ids: list[int] = []
            # 1. 疑似问题文章（已有 open 审计问题的）
            rows = conn.execute(
                f"""SELECT DISTINCT ai.article_id FROM audit_issues ai
                    WHERE ai.status='open' AND ai.issue_type IN (
                        {",".join("?" for _ in _SUSPECT_ISSUE_TYPES)})""",
                list(_SUSPECT_ISSUE_TYPES),
            ).fetchall()
            ids.extend(r[0] for r in rows)
            # 2. 随机补足
            have = set(ids)
            if len(ids) < wanted:
                rows = conn.execute(
                    """SELECT id FROM articles
                       WHERE content_text IS NOT NULL AND length(content_text) > 0
                       ORDER BY RANDOM() LIMIT ?""",
                    (wanted - len(ids),),
                ).fetchall()
                ids.extend(r[0] for r in rows if r[0] not in have)
            if not ids:
                return []
            picked = list(dict.fromkeys(ids))[:wanted]
            rows = conn.execute(
                f"""SELECT id, title, source_type, content_text, content_cleaned
                    FROM articles WHERE id IN ({",".join("?" for _ in picked)})""",
                picked,
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _count_articles(self, conn: sqlite3.Connection) -> int:
        return int(conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] or 0)

    async def _judge_one(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """单篇 LLM 判定。失败返回 None（不阻断）。"""
        title = str(row.get("title") or "")
        content = str(row.get("content_cleaned") or row.get("content_text") or "")
        if not content:
            return None
        try:
            resp = await self._llm.complete(
                self.lq_cfg.llm,
                system_instruction=(
                    "你是内容质量审核员。只根据提供的标题和正文判断文章是否属于"
                    "低质量内容（广告/垃圾/无意义文本/正文与标题不符）。"
                    '只输出 JSON：{"low_quality": true/false, "reason": "原因"}'
                ),
                user_input=LOW_QUALITY_DETECT_PROMPT.format(
                    title=title[:200], content=content[:1500]
                ),
                json_mode=True,
                max_tokens=512,
                temperature=0.0,
                caller="knowledge_forge.low_quality",
            )
            data = json.loads((resp.content or "").strip())
            low = bool(data.get("low_quality", False))
            return {
                "low_quality": low,
                "reason": str(data.get("reason") or ("低质量内容" if low else "")),
            }
        except Exception as exc:  # noqa: BLE001 — LLM 失败不阻断
            logger.warning("低质量判定失败 article=%s: %s", row.get("id"), exc)
            return None

    # ------------------------------------------------------------------ 落库
    def _write_issue(self, row: dict[str, Any], res: dict[str, Any], task_id: int | None) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO audit_issues
                   (article_id, issue_type, severity, description, details, status, created_at)
                   VALUES (?, 'low_quality', 'high', ?, ?, 'open', ?)""",
                (
                    row["id"],
                    f"LLM 判定低质量：{res['reason']}",
                    json.dumps({"llm_reason": res["reason"], "task_id": task_id}),
                    now_cn(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _create_task(self, total: int) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO audit_tasks"
                " (task_type, status, started_at, total_articles, created_at)"
                " VALUES ('low_quality_check', 'running', ?, ?, ?)",
                (now_cn(), total, now_cn()),
            )
            conn.commit()
            return int(cur.lastrowid or 0)
        finally:
            conn.close()

    def _finish_task(self, task_id: int, stats: dict[str, Any]) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE audit_tasks SET status='completed', completed_at=?, issues_found=?"
                " WHERE id=?",
                (now_cn(), stats["low_quality"], task_id),
            )
            conn.commit()
        finally:
            conn.close()

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


def run_low_quality_detection(
    *, limit: int = 0, sample_ratio: float | None = None, dry_run: bool = False
) -> dict[str, Any]:
    """同步入口。"""

    async def _run() -> dict[str, Any]:
        return await LowQualityDetector().detect(
            limit=limit, sample_ratio=sample_ratio, dry_run=dry_run
        )

    return asyncio.run(_run())

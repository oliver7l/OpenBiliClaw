"""实体间关联持久化（设计 3.2：实体页增强 - 实体间关联）。

从 ``article_entities`` 计算实体共现（同一篇文章中出现的实体互为关联），
批量写入 ``entity_relations`` 表（relation_type='co_occur'），使知识图谱/
实体详情页的相关实体查询从实时自连接计算变为持久化读取。

策略：
- 全量重算（幂等 upsert）：``article_entities`` 规模较小时直接重算；
- ``min_co_occur`` 过滤低价值共现（默认 1，即同文章共现即记录）；
- ``confidence`` = min(1, 共现文章数 / 10)（归一化相关度）；
- ``description`` 记录共现文章数。
"""

from __future__ import annotations

import logging
import sqlite3
from openbiliclaw.storage.database import open_db_conn
from collections import Counter
from pathlib import Path
from typing import Any

from openbiliclaw.knowledge_forge.models import now_cn

logger = logging.getLogger(__name__)


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


class EntityRelationBuilder:
    """实体共现关系构建器。"""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else _default_db_path()

    def build(
        self,
        *,
        min_co_occur: int = 1,
        relation_type: str = "co_occur",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """执行一轮共现计算。返回统计。"""
        pairs = self._compute_co_occurrences()
        stats: dict[str, Any] = {
            "pairs_computed": len(pairs),
            "pairs_written": 0,
            "skipped_below_min": 0,
            "dry_run": dry_run,
        }
        written = 0
        for (a, b), count in pairs.items():
            if count < max(1, min_co_occur):
                stats["skipped_below_min"] += 1
                continue
            if not dry_run:
                self._upsert_relation(a, b, count, relation_type)
            written += 1
        stats["pairs_written"] = written
        return stats

    # ------------------------------------------------------------------ 内部
    def _compute_co_occurrences(self) -> Counter[tuple[int, int]]:
        """按文章聚合实体，统计两两共现次数。"""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT article_id, entity_id FROM article_entities").fetchall()
        finally:
            conn.close()
        by_article: dict[int, list[int]] = {}
        for r in rows:
            by_article.setdefault(int(r["article_id"]), []).append(int(r["entity_id"]))
        pairs: Counter[tuple[int, int]] = Counter()
        for entity_ids in by_article.values():
            uniq = sorted(set(entity_ids))
            for i in range(len(uniq)):
                for j in range(i + 1, len(uniq)):
                    pairs[(uniq[i], uniq[j])] += 1
        return pairs

    def _upsert_relation(self, a: int, b: int, count: int, relation_type: str) -> None:
        conn = self._connect()
        try:
            confidence = min(1.0, count / 10.0)
            conn.execute(
                """INSERT INTO entity_relations
                       (entity_id_a, entity_id_b, relation_type, confidence, description, co_occur)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (entity_id_a, entity_id_b, relation_type) DO UPDATE SET
                       confidence = excluded.confidence,
                       description = excluded.description,
                       co_occur = excluded.co_occur""",
                (
                    a,
                    b,
                    relation_type,
                    confidence,
                    f"共现 {count} 篇文章（更新于 {now_cn()}）",
                    count,
                ),
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


def run_entity_relations(
    *,
    min_co_occur: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    """同步入口。"""
    return EntityRelationBuilder().build(min_co_occur=min_co_occur, dry_run=dry_run)

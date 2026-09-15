"""3.3 知识 Wiki 构建器（Wiki Builder）。

建立文章间语义关联，写入 ``article_relations`` 表（文档 3.3.2）。

关联类型（文档 3.3.1）：
    - similar：内容相似（embedding 相似度 > 阈值，默认 0.85）
    - same_topic：共享核心主题标签（确定性，不依赖 embedding）
    - references / extends / contradiction：阶段二/三（LLM 判定，默认关闭）

实现说明：
    - embedding 使用配置的 embedding 服务（默认硅基流动 bge-m3，本地 Ollama fallback）
    - 基于 ``content_cleaned`` 计算，避免污染内容影响相似度（文档 3.0.7）
    - 批量计算，逐对比较改为「候选分组 + 组内两两」，控制 O(n²) 规模
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from openbiliclaw.config import _project_root
from openbiliclaw.storage.database import open_db_conn

from .config import KnowledgeForgeConfig, load_kf_config
from .models import now_cn

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
    return (_project_root() / "data" / "knowledge_audit.db")


def _build_embedding_service() -> Any:
    """惰性构建 embedding 服务（配置 [llm.embedding]，含 fallback）。"""
    from openbiliclaw.config import load_config
    from openbiliclaw.llm._compat_registry import build_embedding_service as _build

    cfg = load_config()
    registry = None
    try:
        from openbiliclaw.llm._compat_registry import build_llm_registry

        registry = build_llm_registry(getattr(cfg, "llm", cfg))
    except Exception:  # noqa: BLE001
        logger.warning("LLM registry build failed for embedding", exc_info=True)
    if registry is None:
        return None
    llm_cfg = cfg.llm if hasattr(cfg, "llm") else cfg
    return _build(llm_cfg, registry)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """两个向量的余弦相似度。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return float(dot / (na * nb))


class WikiBuilder:
    """知识 Wiki 构建器。"""

    def __init__(
        self,
        *,
        config: KnowledgeForgeConfig | None = None,
        db_path: str | Path | None = None,
        embedding_service: Any | None = None,
    ) -> None:
        self.config = config or load_kf_config()
        self.db_path = Path(db_path) if db_path else _default_db_path()
        self._embedding = embedding_service
        self.similarity_threshold = 0.85

    @property
    def embedding(self) -> Any:
        if self._embedding is None:
            self._embedding = _build_embedding_service()
            if self._embedding is None:
                logger.warning("embedding 服务不可用，语义关联将降级为标签匹配")
        return self._embedding

    # ------------------------------------------------------------------ 主入口
    async def detect_relations(
        self,
        *,
        limit: int = 100,
        min_id: int = 0,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """增量检测文章间语义关联。

        策略：取一批文章（content_cleaned 非空），按主题标签分组，
        组内两两计算 embedding 相似度；无 embedding 时降级为 same_topic 关联。
        """
        rows = self._fetch_candidates(limit=limit, min_id=min_id)
        stats: dict[str, Any] = {
            "processed": len(rows),
            "relations": 0,
            "similar": 0,
            "same_topic": 0,
            "embedding_available": self.embedding is not None,
        }
        if len(rows) < 2:
            return stats

        # 分组：按 tags 共享分组
        groups = self._group_by_tags(rows)

        # 阶段一：same_topic（确定性，不依赖 embedding；组内两两共享标签）
        for tag_group in groups:
            if len(tag_group) < 2:
                continue
            ids = [int(r["id"]) for r in tag_group]
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a, b = ids[i], ids[j]
                    shared = self._shared_tags(tag_group[i], tag_group[j])
                    if not shared:
                        continue
                    self._write_relation(
                        a, b, "same_topic", 0.7, f"共享主题标签：{'、'.join(shared[:3])}"
                    )
                    stats["relations"] += 1
                    stats["same_topic"] += 1

        # 阶段二：similar（embedding 相似度 >= 阈值）
        emb = self.embedding
        if emb is not None:
            for tag_group in groups:
                if len(tag_group) < 2:
                    continue
                vecs: dict[int, list[float]] = {}
                for r in tag_group:
                    try:
                        vec = await self._embed_text(emb, self._pick_content(r))
                        if vec:
                            vecs[int(r["id"])] = vec
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("embedding failed for article %s: %s", r.get("id"), exc)
                ids = list(vecs.keys())
                for i in range(len(ids)):
                    for j in range(i + 1, len(ids)):
                        a, b = ids[i], ids[j]
                        sim = cosine_similarity(vecs[a], vecs[b])
                        if sim >= self.similarity_threshold:
                            self._write_relation(
                                a, b, "similar", round(sim, 3), f"内容相似度 {sim:.1%}"
                            )
                            stats["relations"] += 1
                            stats["similar"] += 1
                        elif dry_run and stats["relations"] >= 10:
                            return stats
        return stats

    # ------------------------------------------------------------------ 候选与分组
    def _fetch_candidates(self, *, limit: int, min_id: int) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT id, title, tags, content_cleaned, content_text
                   FROM articles
                   WHERE id >= ?
                     AND (LENGTH(COALESCE(content_cleaned,'')) > 50
                          OR LENGTH(COALESCE(content_text,'')) > 50)
                   ORDER BY id LIMIT ?""",
                (min_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def _parse_tags(raw: str) -> list[str]:
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return (
                [str(t).strip() for t in data if str(t).strip()] if isinstance(data, list) else []
            )
        except Exception:  # noqa: BLE001
            return [t.strip() for t in raw.split(",") if t.strip()]

    def _group_by_tags(self, rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        """按共享标签分组；无标签的文章归入 'untagged' 单组（组内仍可两两）。"""
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            tags = self._parse_tags(str(r.get("tags") or ""))
            key = "|".join(sorted(tags[:3])) if tags else "untagged"
            groups[key].append(r)
        return list(groups.values())

    @staticmethod
    def _shared_tags(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
        ta = set(WikiBuilder._parse_tags(str(a.get("tags") or "")))
        tb = set(WikiBuilder._parse_tags(str(b.get("tags") or "")))
        return sorted(ta & tb)

    @staticmethod
    def _pick_content(row: dict[str, Any]) -> str:
        for key in ("content_cleaned", "content_text"):
            v = str(row.get(key) or "").strip()
            if v:
                return v[:2000]
        return ""

    async def _embed_text(self, emb: Any, text: str) -> list[float]:
        try:
            lookup = getattr(emb, "lookup_cached", None)
            if lookup is not None:
                cached = lookup(text)
                if cached:
                    return [float(x) for x in cached]
            vec = await emb.embed(text)
            return [float(x) for x in vec]
        except Exception:  # noqa: BLE001
            return []

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

    def _write_relation(
        self, a: int, b: int, rtype: str, confidence: float, description: str
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO article_relations
                   (article_id_a, article_id_b, relation_type, confidence,
                    description, created_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(article_id_a, article_id_b, relation_type)
                   DO UPDATE SET confidence=excluded.confidence,
                       description=excluded.description""",
                (a, b, rtype, confidence, description, now_cn()),
            )
            conn.commit()
        finally:
            conn.close()


def detect_relations(limit: int = 100) -> dict[str, Any]:
    """同步入口（供 CLI 调用）。"""
    return asyncio.run(WikiBuilder().detect_relations(limit=limit))

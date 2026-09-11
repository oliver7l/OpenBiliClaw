"""自动修复器（设计 2.5 剩余 + 3.5.2 阶段四）。

对 ``audit_issues`` 中 open 的可自动修复问题执行修复：

+---------------------------+-----------------------------------------------+
| issue_type                | 修复动作                                       |
+---------------------------+-----------------------------------------------+
| content_contamination     | 调用 ContentCleaner 重新清理正文，更新五列     |
| not_cleaned               | 同上（生成缺失的 content_cleaned）             |
| format_error              | 同上（清理 HTML 标签/乱码）                    |
| missing_hash              | 计算 content_hash 补充                         |
| missing_tags              | LLM 补充标签写入 tags                          |
| missing_summary           | SummaryEngine 生成三层摘要                     |
| duplicate                 | 将 details.duplicate_ids 的文章标记 duplicate   |
+---------------------------+-----------------------------------------------+

设计文档规定 dead_link / too_short / low_quality / missing_author 标记待人工处理，
不自动修复。修复受 audit.auto_fix_enabled 与显式 ``enable`` 参数控制。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from openbiliclaw.knowledge_forge.config import KnowledgeForgeConfig, load_kf_config
from openbiliclaw.knowledge_forge.models import now_cn
from openbiliclaw.knowledge_forge.prompts import TAG_SUPPLEMENT_PROMPT, parse_json_array
from openbiliclaw.knowledge_forge.utils import content_hash, get_llm_client
from openbiliclaw.storage.database import open_db_conn

logger = logging.getLogger(__name__)

# issue_type -> 修复函数名；其余类型（dead_link/too_short/low_quality/missing_author）待人工
_AUTO_FIXABLE = {
    "content_contamination": "clean",
    "not_cleaned": "clean",
    "format_error": "clean",
    "missing_hash": "hash",
    "missing_tags": "tags",
    "missing_summary": "summary",
    "duplicate": "duplicate",
}

# 设计 3.5.2：这些类型不做数据自动修改，但生成明确的处理建议写回
# fix_suggestion（标记 + 建议），从"待人工"升级为"有明确处理路径的待人工"。
_SUGGEST_ONLY = {
    "dead_link": "URL 无法访问，建议重新抓取源站；若源站已下线，建议从推荐流剔除该文章",
    "too_short": "正文过短（可能抓取失败），建议重新抓取源站",
    "low_quality": "LLM 判定为低质量内容，建议人工审核后删除或下架",
    "missing_author": "作者字段为空，建议人工补充作者信息",
}


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


class IssueFixer:
    """审计问题自动修复器。"""

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
    async def fix_all(
        self,
        *,
        issue_types: list[str] | None = None,
        limit: int = 0,
        enable: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """修复 open 的可自动修复问题。返回统计。

        enable=True 强制开启（覆盖 audit.auto_fix_enabled=false）；
        limit>0 时最多修复前 limit 条问题（按创建时间排序）。
        """
        stats: dict[str, Any] = {
            "scanned": 0,
            "fixed": 0,
            "suggested": 0,
            "skipped_manual": 0,
            "failed": 0,
            "by_type": {},
        }
        # 设计 3.5.2 阶段四：自动修复默认关闭（audit.auto_fix_enabled=false），
        # 需显式 enable 或 config 开启后才执行。
        if not enable and not bool(self.config.audit.auto_fix_enabled):
            stats["disabled"] = True
            stats["reason"] = "auto_fix_enabled=false（需 --enable 或 config 开启）"
            return stats
        issues = self._fetch_open_issues(issue_types=issue_types, limit=limit)
        stats["scanned"] = len(issues)
        if not issues:
            return stats

        for issue in issues:
            it = str(issue["issue_type"])
            fixer = _AUTO_FIXABLE.get(it)
            suggestion = _SUGGEST_ONLY.get(it)
            if fixer is None and suggestion is None:
                stats["skipped_manual"] += 1
                continue
            if fixer is not None:
                ok = await self._apply(issue, fixer, dry_run=dry_run)
                stats["by_type"][it] = stats["by_type"].get(it, 0) + 1
                if ok:
                    stats["fixed"] += 1
                else:
                    stats["failed"] += 1
                continue
            # 建议型：不修改数据，写回 fix_suggestion（dry_run 只统计）
            if not dry_run:
                assert suggestion is not None  # 走到这里必有建议文案
                self._write_suggestion(int(issue["id"]), suggestion)
            stats["suggested"] += 1
            stats["by_type"][it] = stats["by_type"].get(it, 0) + 1
        return stats

    # ------------------------------------------------------------------ 候选
    def _fetch_open_issues(
        self, *, issue_types: list[str] | None = None, limit: int = 0
    ) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            where = ["status = 'open'"]
            params: list[Any] = []
            known = set(_AUTO_FIXABLE) | set(_SUGGEST_ONLY)
            types = [t for t in (issue_types or []) if t in known]
            if types:
                where.append(f"issue_type IN ({','.join('?' for _ in types)})")
                params.extend(types)
            sql = f"""SELECT id, article_id, issue_type, details
                      FROM audit_issues WHERE {" AND ".join(where)}
                      ORDER BY created_at ASC"""
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            out: list[dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                if d.get("details"):
                    try:
                        d["details"] = json.loads(d["details"])
                    except (TypeError, json.JSONDecodeError):
                        d["details"] = {}
                out.append(d)
            return out
        finally:
            conn.close()

    # ------------------------------------------------------------------ 修复
    def _write_suggestion(self, issue_id: int, suggestion: str) -> None:
        """写回处理建议（不修改数据，仅标记 + 建议）。"""
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE audit_issues SET fix_suggestion = ? WHERE id = ?",
                (suggestion, issue_id),
            )
            conn.commit()
        finally:
            conn.close()

    async def _apply(self, issue: dict[str, Any], fixer: str, *, dry_run: bool) -> bool:
        try:
            if fixer == "clean":
                ok = self._fix_clean(issue)
            elif fixer == "hash":
                ok = self._fix_hash(issue)
            elif fixer == "tags":
                ok = await self._fix_tags(issue)
            elif fixer == "summary":
                ok = await self._fix_summary(issue)
            elif fixer == "duplicate":
                ok = self._fix_duplicate(issue)
            else:
                ok = False
            if ok and not dry_run:
                self._mark_fixed(issue)
            return ok
        except Exception as exc:  # noqa: BLE001 — 单条失败不阻断
            logger.warning("修复失败 issue=%s (%s): %s", issue["id"], fixer, exc)
            return False

    def _fix_clean(self, issue: dict[str, Any]) -> bool:
        """重新清理正文，更新 content_cleaned 五列。"""
        from openbiliclaw.knowledge_forge.content_cleaner import ContentCleaner

        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT title, source_type, content_text FROM articles WHERE id = ?",
                (issue["article_id"],),
            ).fetchone()
            if not row or not (row[2] or "").strip():
                return False
            title, source_type, content_text = row
            cr = ContentCleaner().clean(
                content_text, title=title or "", source_type=source_type or ""
            )
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
                    issue["article_id"],
                ),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def _fix_hash(self, issue: dict[str, Any]) -> bool:
        """补充 content_hash。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT content_text FROM articles WHERE id = ?", (issue["article_id"],)
            ).fetchone()
            if not row:
                return False
            h = content_hash(row[0] or "")
            if not h:
                return False
            conn.execute(
                "UPDATE articles SET content_hash = ?, updated_at = datetime('now','localtime')"
                " WHERE id = ?",
                (h, issue["article_id"]),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    async def _fix_tags(self, issue: dict[str, Any]) -> bool:
        """LLM 补充标签（失败返回 False，不写入）。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT title, content_text FROM articles WHERE id = ?",
                (issue["article_id"],),
            ).fetchone()
            if not row:
                return False
            title, content = row
            resp = await self._llm.complete(
                self.config.entity.llm,
                system_instruction=(
                    "你是标签提取助手。根据文章标题和正文提取 3-6 个主题标签，只输出 JSON 数组。"
                ),
                user_input=TAG_SUPPLEMENT_PROMPT.format(
                    title=title or "", content=(content or "")[:2000]
                ),
                json_mode=True,
                max_tokens=512,
                temperature=0.3,
                caller="knowledge_forge.auto_fix.tags",
            )
            tags = parse_json_array(resp.content or "")
            if not tags:
                return False
            conn.execute(
                "UPDATE articles SET tags = ?, updated_at = datetime('now','localtime')"
                " WHERE id = ?",
                (json.dumps(tags, ensure_ascii=False), issue["article_id"]),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    async def _fix_summary(self, issue: dict[str, Any]) -> bool:
        """调用 SummaryEngine 生成三层摘要。"""
        from openbiliclaw.knowledge_forge.summary_engine import SummaryEngine

        result = await SummaryEngine(db_path=self.db_path).generate_by_id(int(issue["article_id"]))
        return bool(result and not result.error and result.detailed)

    def _fix_duplicate(self, issue: dict[str, Any]) -> bool:
        """将 details.duplicate_ids 中的文章标记为 duplicate。"""
        conn = self._connect()
        try:
            details = issue.get("details") or {}
            dup_ids = details.get("duplicate_ids") or []
            if not dup_ids:
                return False
            for dup_id in dup_ids:
                conn.execute(
                    "UPDATE articles SET status = 'duplicate',"
                    " updated_at = datetime('now','localtime') WHERE id = ?",
                    (int(dup_id),),
                )
            conn.commit()
            return True
        finally:
            conn.close()

    def _mark_fixed(self, issue: dict[str, Any]) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE audit_issues SET status = 'fixed', fixed_at = ? WHERE id = ?",
                (now_cn(), issue["id"]),
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


def run_auto_fix(
    *,
    issue_types: list[str] | None = None,
    limit: int = 0,
    enable: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """同步入口。"""

    async def _run() -> dict[str, Any]:
        return await IssueFixer().fix_all(
            issue_types=issue_types, limit=limit, enable=enable, dry_run=dry_run
        )

    return asyncio.run(_run())

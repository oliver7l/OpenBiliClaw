"""3.5 死链检测器（Dead Link Checker）。

对文章 URL 发 HEAD 请求检查可用性，404/410 标记为死链，超时标记待确认，
结果写入 ``audit_issues``（issue_type=dead_link）与 ``audit_tasks``。

设计约束（文档 3.5.2 / 9.1）：
    - **分批异步**：单批 batch_size 篇，并发 dead_link_concurrency，避免风控
    - **平台差异**：知乎/小红书更谨慎（更长延迟 + 浏览器 UA），B 站/其他正常
    - **状态码语义**：200/3xx 视为正常（3xx 记录重定向），404/410 死链，
      超时/网络错误 待确认，其他 4xx/5xx 记录为异常
    - **增量**：上次检查正常的文章跳过（以 audit_issues 最近状态为准）

用法：
    from openbiliclaw.knowledge_forge.dead_link_checker import run_dead_link_check
    stats = run_dead_link_check(limit=50)
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from openbiliclaw.storage.database import open_db_conn

from .config import AuditConfig, KnowledgeForgeConfig, load_kf_config
from .models import now_cn

logger = logging.getLogger(__name__)

# 平台 → 检查策略：延迟范围(秒) / 是否使用浏览器 UA
_PLATFORM_POLICY: dict[str, dict[str, Any]] = {
    "zhihu": {"delay": (1.0, 3.0), "browser_ua": True},
    "xiaohongshu": {"delay": (2.0, 4.0), "browser_ua": True},
    "bilibili": {"delay": (0.2, 0.8), "browser_ua": True},
    "weibo": {"delay": (1.0, 2.5), "browser_ua": True},
}
_DEFAULT_DELAY = (0.3, 1.0)

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_PLAIN_UA = "openbiliclaw-knowledge-forge/0.2 (article link check)"


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


class DeadLinkChecker:
    """死链检测器。"""

    def __init__(
        self,
        *,
        config: KnowledgeForgeConfig | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.config = config or load_kf_config()
        self.audit_cfg: AuditConfig = self.config.audit
        self.db_path = Path(db_path) if db_path else _default_db_path()
        self._pending: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ 主入口
    async def check(
        self,
        *,
        limit: int = 0,
        batch_size: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """执行死链检查（异步分批）。返回统计。

        limit=0 表示检查全部待检文章；批次内并发受 audit.dead_link_concurrency 限制。
        """
        rows = self._fetch_candidates(limit=limit)
        stats: dict[str, Any] = {
            "checked": 0,
            "alive": 0,
            "dead": 0,
            "pending": 0,
            "redirect": 0,
            "error": 0,
            "skipped_recent": 0,
            "task_id": None,
        }
        stats["skipped_recent"] = rows["skipped_recent"]
        candidates = rows["rows"]
        if not candidates:
            return stats

        task_id = None
        if not dry_run:
            task_id = self._create_task(len(candidates))
            stats["task_id"] = task_id

        batch = batch_size or self.audit_cfg.batch_size
        sem = asyncio.Semaphore(self.audit_cfg.dead_link_concurrency)

        async def _check(row: dict[str, Any]) -> dict[str, Any]:
            async with sem:
                return await self._check_one(row)

        for i in range(0, len(candidates), batch):
            chunk = candidates[i : i + batch]
            results = await asyncio.gather(*(_check(r) for r in chunk))
            for row, res in zip(chunk, results, strict=False):
                stats[res["status_group"]] += 1
                stats["checked"] += 1
                if not dry_run:
                    self._write_issue(row, res, task_id)
            if i + batch < len(candidates):
                await asyncio.sleep(random.uniform(0.5, 1.5))  # 批次间冷却
        if not dry_run:
            self._finish_task(task_id, stats)
        return stats

    # ------------------------------------------------------------------ 候选
    def _fetch_candidates(self, *, limit: int = 0) -> dict[str, Any]:
        """取待检文章：有 URL、非空、且近期未检测过。

        近期跳过：距上次 dead_link 检查 ≥ 7 天（避免重复请求触发风控）。
        """
        conn = self._connect()
        try:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, source_type, url FROM articles"
                    " WHERE url IS NOT NULL AND url <> '' AND url LIKE 'http%'"
                ).fetchall()
            ]
            recent = self._recently_checked_ids(conn)
        finally:
            conn.close()

        candidates = [r for r in rows if int(r["id"]) not in recent]
        if limit and limit > 0:
            candidates = candidates[:limit]
        return {"rows": candidates, "skipped_recent": len(rows) - len(candidates)}

    def _recently_checked_ids(self, conn: sqlite3.Connection) -> set[int]:
        """过去 7 天检查过（结果有效）的文章 id。"""
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        try:
            rows = conn.execute(
                """SELECT article_id FROM audit_issues
                   WHERE issue_type='dead_link' AND created_at >= ?
                     AND details LIKE '%"status_group":"alive"%'""",
                (cutoff,),
            ).fetchall()
            return {int(r["article_id"]) for r in rows}
        except Exception:  # noqa: BLE001
            return set()

    # ------------------------------------------------------------------ 单篇检查
    async def _check_one(self, row: dict[str, Any]) -> dict[str, Any]:
        url = str(row.get("url") or "")
        source = str(row.get("source_type") or "").lower()
        policy = _PLATFORM_POLICY.get(source, {})
        delay = policy.get("delay", _DEFAULT_DELAY)
        await asyncio.sleep(random.uniform(*delay))  # 平台差异延迟
        ua = _BROWSER_UA if policy.get("browser_ua", False) else _PLAIN_UA

        timeout = self.audit_cfg.dead_link_timeout or 10
        try:
            import httpx

            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                headers={"User-Agent": ua, "Accept": "*/*"},
            ) as client:
                # HEAD 优先；405/403 等拒绝 HEAD 时降级 GET
                resp = await client.head(url)
                if resp.status_code in (405, 403, 400):
                    resp = await client.get(url)
                code = resp.status_code
        except Exception as exc:  # noqa: BLE001
            return {
                "status_group": "pending",
                "status_code": None,
                "detail": f"网络错误：{type(exc).__name__}",
                "ok": False,
            }

        if code < 400:
            group = "redirect" if 300 <= code < 400 else "alive"
            return {
                "status_group": group,
                "status_code": code,
                "detail": f"HTTP {code}",
                "ok": code < 300,
            }
        if code in (404, 410):
            return {
                "status_group": "dead",
                "status_code": code,
                "detail": f"HTTP {code} 内容已删除",
                "ok": False,
            }
        return {
            "status_group": "error",
            "status_code": code,
            "detail": f"HTTP {code}",
            "ok": False,
        }

    # ------------------------------------------------------------------ 存储
    def _write_issue(self, row: dict[str, Any], res: dict[str, Any], task_id: int | None) -> None:
        conn = self._connect()
        try:
            group = res["status_group"]
            if group == "alive":
                severity = "low"
                desc = "链接有效"
                fix = ""
            elif group == "redirect":
                severity = "low"
                desc = "链接重定向"
                fix = "检查是否需要更新 URL"
            elif group == "dead":
                severity = "high"
                desc = f"死链：{res['detail']}"
                fix = "标记为失效或重新抓取"
            elif group == "pending":
                severity = "medium"
                desc = f"待确认：{res['detail']}"
                fix = "稍后重试或人工确认"
            else:
                severity = "medium"
                desc = f"检查异常：{res['detail']}"
                fix = "稍后重试"
            conn.execute(
                """INSERT INTO audit_issues
                   (article_id, issue_type, severity, description, details,
                    status, fix_suggestion, created_at)
                   VALUES (?, 'dead_link', ?, ?, ?, 'open', ?, ?)""",
                (
                    int(row["id"]),
                    severity,
                    desc,
                    json.dumps(
                        {
                            "url": str(row.get("url") or ""),
                            "status_code": res.get("status_code"),
                            "status_group": group,
                            "task_id": task_id,
                        },
                        ensure_ascii=False,
                    ),
                    fix,
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
                "INSERT INTO audit_tasks (task_type, status, started_at, created_at)"
                " VALUES ('dead_link_check', 'running', ?, ?)",
                (now_cn(), now_cn()),
            )
            conn.commit()
            lastrowid = cur.lastrowid
            return int(lastrowid) if lastrowid is not None else 0
        finally:
            conn.close()

    def _finish_task(self, task_id: int | None, stats: dict[str, Any]) -> None:
        if task_id is None:
            return
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE audit_tasks SET status='completed', completed_at=?,"
                " total_articles=?, issues_found=? WHERE id=?",
                (
                    now_cn(),
                    stats["checked"],
                    stats["dead"] + stats["pending"] + stats["error"],
                    task_id,
                ),
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


# --------------------------------------------------------------------------- #
# 同步便捷入口
# --------------------------------------------------------------------------- #
def run_dead_link_check(
    *,
    limit: int = 0,
    batch_size: int | None = None,
    dry_run: bool = False,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """同步执行死链检查（内部跑事件循环）。"""

    async def _run() -> dict[str, Any]:
        return await DeadLinkChecker(db_path=db_path).check(
            limit=limit, batch_size=batch_size, dry_run=dry_run
        )

    return asyncio.run(_run())

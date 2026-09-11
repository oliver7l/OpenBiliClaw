"""定时任务集成（设计 3.4）。

提供组合任务入口 ``run_scheduled``：每周自动运行 质量审计 → 缺口分析 →
（可选）死链检查，并生成 Markdown 汇总报告写入 ``audit_tasks``。
与系统 cron/launchd 集成：CLI 提供 ``schedule-show`` 输出推荐 crontab 行，
``run-scheduled`` 为无交互执行入口（供 cron 调用）。
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
from openbiliclaw.storage.database import open_db_conn

logger = logging.getLogger(__name__)

# 推荐 crontab：每周一 03:30
DEFAULT_CRON_LINE = "30 3 * * 1"


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


def build_schedule_guide() -> str:
    """生成接入系统 crontab 的说明文本。"""
    return (
        "# Knowledge Forge 定时任务（设计 3.4）\n"
        "每周自动运行：质量审计 → 缺口分析 → 死链检查 → 报告落库。\n"
        "推荐 crontab（每周一 03:30，本机时间）：\n"
        f"{DEFAULT_CRON_LINE} cd {Path.cwd()} && "
        ".venv/bin/python -m openbiliclaw.knowledge_forge.cli run-scheduled"
        " >/tmp/kf_scheduled.log 2>&1\n"
        "macOS 也可用 launchd：将上述命令封装为 plist 的 ProgramArguments。\n"
    )


class Scheduler:
    """定时组合任务。"""

    def __init__(
        self,
        *,
        config: KnowledgeForgeConfig | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.config = config or load_kf_config()
        self.db_path = Path(db_path) if db_path else _default_db_path()

    async def run_scheduled(
        self,
        *,
        include_dead_link: bool = False,
        include_gap_fill: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """执行一轮定时任务组合：质量审计 → 缺口分析 → 实体共现刷新
        → （可选）死链检查 → （可选）自动补充闭环。

        - 实体共现刷新：保持 entity_relations 关系网络与最新文章同步；
        - include_gap_fill 默认关闭（B 站搜索有冷却/风控，需显式开启）；
        返回各步骤统计与报告文本；不抛异常（单步失败记入报告继续下一步）。
        """
        from openbiliclaw.knowledge_forge.dead_link_checker import DeadLinkChecker
        from openbiliclaw.knowledge_forge.gap_analyst import GapAnalyst
        from openbiliclaw.knowledge_forge.quality_auditor import QualityAuditor

        steps: dict[str, Any] = {}
        report_lines = ["# Knowledge Forge 定时运行报告", f"- 时间：{now_cn()}", ""]

        # 1. 质量审计（同步）
        try:
            audit_stats = QualityAuditor(db_path=self.db_path).run_full_audit()
            steps["audit"] = audit_stats
            report_lines.append(
                f"## 质量审计\n- 扫描 {audit_stats.get('total')} 篇，发现 "
                f"{audit_stats.get('issues_found')} 个问题。"
            )
        except Exception as exc:  # noqa: BLE001 — 单步失败不阻断
            steps["audit"] = {"error": str(exc)}
            report_lines.append(f"## 质量审计\n- 失败：{exc}")

        # 2. 缺口分析（同步）
        try:
            gap_stats = GapAnalyst(db_path=self.db_path).run()
            steps["gap"] = gap_stats
            report_lines.append(
                f"## 缺口分析\n- 发现 {gap_stats.get('gaps_found', 0)} 个缺口"
                f"（高危 {gap_stats.get('high', 0)}）。"
            )
        except Exception as exc:  # noqa: BLE001
            steps["gap"] = {"error": str(exc)}
            report_lines.append(f"## 缺口分析\n- 失败：{exc}")

        # 3. 实体共现刷新（同步，保持关系网络最新）
        try:
            from openbiliclaw.knowledge_forge.entity_relation_builder import (
                EntityRelationBuilder,
            )

            er_stats = EntityRelationBuilder(db_path=self.db_path).build(
                min_co_occur=1, dry_run=dry_run
            )
            steps["entity_relations"] = er_stats
            report_lines.append(
                f"## 实体共现刷新\n- 共现对 {er_stats.get('pairs_computed', 0)} 个，"
                f"写入 {er_stats.get('pairs_written', 0)}。"
            )
        except Exception as exc:  # noqa: BLE001
            steps["entity_relations"] = {"error": str(exc)}
            report_lines.append(f"## 实体共现刷新\n- 失败：{exc}")

        # 4. 死链检查（可选，默认关闭避免风控）
        if include_dead_link:
            try:
                dl_stats = await DeadLinkChecker(db_path=self.db_path).check(limit=200)
                steps["dead_link"] = dl_stats
                report_lines.append(
                    f"## 死链检查\n- 检查 {dl_stats.get('checked', 0)} 条，"
                    f"死链 {dl_stats.get('dead', 0)}。"
                )
            except Exception as exc:  # noqa: BLE001
                steps["dead_link"] = {"error": str(exc)}
                report_lines.append(f"## 死链检查\n- 失败：{exc}")

        # 5. 自动补充闭环（可选，默认关闭——B 站搜索有冷却/风控）
        if include_gap_fill:
            try:
                from openbiliclaw.knowledge_forge.gap_filler import GapFiller

                gf_stats = await GapFiller(db_path=self.db_path).fill(
                    limit_gaps=5, fill_per_gap=2, dry_run=dry_run
                )
                steps["gap_fill"] = gf_stats
                report_lines.append(
                    f"## 自动补充\n- 填补缺口 {gf_stats.get('gaps_filled', 0)} 个，"
                    f"入库 {gf_stats.get('articles_filled', 0)} 篇。"
                )
            except Exception as exc:  # noqa: BLE001
                steps["gap_fill"] = {"error": str(exc)}
                report_lines.append(f"## 自动补充\n- 失败：{exc}")

        report = "\n".join(report_lines)
        if not dry_run:
            self._record_run(steps, report)
        return {"steps": steps, "report": report}

    def _record_run(self, steps: dict[str, Any], report: str) -> None:
        """落库 scheduled_run；issues_found = audit 问题 + 缺口 + 死链数。"""
        total_issues = 0
        for s in steps.values():
            if not isinstance(s, dict):
                continue
            total_issues += int(s.get("issues_found", 0) or 0)
            total_issues += int(s.get("gaps_found", 0) or 0)
            total_issues += int(s.get("dead", 0) or 0)
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO audit_tasks (task_type, status, started_at, completed_at,
                   issues_found, details, created_at)
                   VALUES ('scheduled_run', 'completed', ?, ?, ?, ?, ?)""",
                (
                    now_cn(),
                    now_cn(),
                    total_issues,
                    json.dumps(steps, ensure_ascii=False, default=str),
                    now_cn(),
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


def run_scheduled(
    *,
    include_dead_link: bool = False,
    include_gap_fill: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """同步入口。"""

    async def _run() -> dict[str, Any]:
        return await Scheduler().run_scheduled(
            include_dead_link=include_dead_link,
            include_gap_fill=include_gap_fill,
            dry_run=dry_run,
        )

    return asyncio.run(_run())

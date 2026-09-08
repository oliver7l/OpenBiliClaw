"""3.4 知识缺口分析器（Gap Analyst）。

识别知识库覆盖不足的主题/概念，生成补充建议（文档 3.4）。

分析维度（文档 3.4.1，阶段一实现）：
    - 主题覆盖度：每个标签/专题的文章数量、时间分布、平台分布
    - 概念覆盖度：引用文章数 <=1 的"冷门概念"
    - 时间衰减：超过 N 天无新内容的主题
    - 跨平台差异：某个主题平台分布严重不均

输出：写入 gap_analysis_tasks / gap_records + Markdown 报告（文档 3.4.3）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import GapConfig, KnowledgeForgeConfig, load_kf_config
from .models import now_cn

logger = logging.getLogger(__name__)


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


def _parse_tags(raw: str) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return [str(t).strip() for t in data if str(t).strip()] if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return [t.strip() for t in raw.split(",") if t.strip()]


def _parse_time(s: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19].strip(), fmt)
        except Exception:  # noqa: BLE001
            continue
    return None


class GapAnalyst:
    """知识缺口分析器。"""

    def __init__(
        self,
        *,
        config: KnowledgeForgeConfig | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.config = config or load_kf_config()
        self.gap_cfg: GapConfig = self.config.gap
        self.db_path = Path(db_path) if db_path else _default_db_path()

    # ------------------------------------------------------------------ 主入口
    def run(self, *, task_type: str = "weekly") -> dict[str, Any]:
        """执行缺口分析，写入任务/记录表；返回统计。"""
        started = now_cn()
        task_id = self._create_task(task_type, started)
        gaps: list[dict[str, Any]] = []

        gaps.extend(self._analyze_topic_coverage())
        gaps.extend(self._analyze_concept_coverage())
        gaps.extend(self._analyze_time_decay())
        gaps.extend(self._analyze_platform_gap())

        # 排序：高优先级在前
        order = {"high": 0, "medium": 1, "low": 2}
        gaps.sort(key=lambda g: order.get(g.get("severity", "low"), 3))
        self._write_records(task_id, gaps)
        self._finish_task(task_id, gaps_found=len(gaps))

        report = self.generate_report(gaps)
        return {
            "task_id": task_id,
            "gaps_found": len(gaps),
            "high": sum(1 for g in gaps if g.get("severity") == "high"),
            "medium": sum(1 for g in gaps if g.get("severity") == "medium"),
            "low": sum(1 for g in gaps if g.get("severity") == "low"),
            "report": report,
        }

    # ------------------------------------------------------------------ 维度
    def _analyze_topic_coverage(self) -> list[dict[str, Any]]:
        """主题覆盖度：标签文章数过少 → 高优先级缺口。"""
        conn = self._connect()
        gaps: list[dict[str, Any]] = []
        try:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, source_type, tags, published_at, created_at FROM articles"
                ).fetchall()
            ]
            tag_counts: Counter[str] = Counter()
            tag_latest: dict[str, str] = {}
            tag_platforms: dict[str, Counter[str]] = defaultdict(Counter)

            for r in rows:
                tags = _parse_tags(str(r.get("tags") or ""))
                src = str(r.get("source_type") or "")
                ts = r.get("published_at") or r.get("created_at") or ""
                for t in tags:
                    if len(t) > 30:
                        continue
                    tag_counts[t] += 1
                    tag_platforms[t][src] += 1
                    cur = tag_latest.get(t, "")
                    if str(ts) > cur:
                        tag_latest[t] = str(ts)

            threshold = self.gap_cfg.high_priority_threshold
            for tag, count in tag_counts.most_common():
                if count > threshold:
                    continue
                gaps.append(
                    {
                        "gap_type": "topic_coverage",
                        "entity": tag,
                        "severity": "high" if count <= 2 else "medium",
                        "description": f"主题「{tag}」仅 {count} 篇，覆盖不足",
                        "current_count": count,
                        "suggested_count": max(threshold, 10),
                        "suggestion": f"补充 {max(threshold, 10) - count} 篇「{tag}」相关文章",
                        "platforms": dict(tag_platforms.get(tag, {})),
                    }
                )
        finally:
            conn.close()
        return gaps

    def _analyze_concept_coverage(self) -> list[dict[str, Any]]:
        """概念覆盖度：entities.type=concept 且 article_count<=1 → 冷门概念。"""
        conn = self._connect()
        gaps: list[dict[str, Any]] = []
        try:
            rows = conn.execute(
                "SELECT name, article_count FROM entities WHERE type='concept'"
            ).fetchall()
            for r in rows:
                n = int(r["article_count"] or 0)
                if n > 1:
                    continue
                gaps.append(
                    {
                        "gap_type": "concept_coverage",
                        "entity": str(r["name"]),
                        "severity": "low",
                        "description": f"概念「{r['name']}」仅被 {n} 篇文章引用",
                        "current_count": n,
                        "suggested_count": 3,
                        "suggestion": f"寻找更多引用「{r['name']}」的文章或补充相关概念文章",
                    }
                )
        finally:
            conn.close()
        return gaps

    def _analyze_time_decay(self) -> list[dict[str, Any]]:
        """时间衰减：主题最近 N 天无新内容 → 被遗忘主题。"""
        conn = self._connect()
        gaps: list[dict[str, Any]] = []
        cutoff = datetime.now() - timedelta(days=self.gap_cfg.time_decay_days)
        try:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, tags, published_at, created_at FROM articles"
                ).fetchall()
            ]
            tag_latest: dict[str, datetime] = {}
            for r in rows:
                tags = _parse_tags(str(r.get("tags") or ""))
                ts = r.get("published_at") or r.get("created_at") or ""
                dt = _parse_time(str(ts))
                if dt is None:
                    continue
                for t in tags:
                    if len(t) > 30:
                        continue
                    if t not in tag_latest or dt > tag_latest[t]:
                        tag_latest[t] = dt
            for tag, latest in tag_latest.items():
                if latest >= cutoff:
                    continue
                days = (datetime.now() - latest).days
                gaps.append(
                    {
                        "gap_type": "time_decay",
                        "entity": tag,
                        "severity": "medium",
                        "description": (
                            f"主题「{tag}」最后更新 {latest:%Y-%m-%d}（已 {days} 天无新内容）"
                        ),
                        "current_count": days,
                        "suggested_count": 3,
                        "suggestion": f"关注「{tag}」最新进展，补充最近内容",
                    }
                )
        finally:
            conn.close()
        return gaps

    def _analyze_platform_gap(self) -> list[dict[str, Any]]:
        """跨平台差异：同一主题平台分布不均（某平台明显缺失）。"""
        conn = self._connect()
        gaps: list[dict[str, Any]] = []
        try:
            rows = [
                dict(r)
                for r in conn.execute("SELECT id, source_type, tags FROM articles").fetchall()
            ]
            tag_platforms: dict[str, Counter[str]] = defaultdict(Counter)
            for r in rows:
                src = str(r.get("source_type") or "")
                for t in _parse_tags(str(r.get("tags") or "")):
                    if len(t) > 30:
                        continue
                    tag_platforms[t][src] += 1
            for tag, pc in tag_platforms.items():
                if sum(pc.values()) < 5:
                    continue
                # 找出 0 覆盖的主流平台（已知平台集合）
                known = {"zhihu", "xiaohongshu", "bilibili", "youtube", "v2ex", "weibo"}
                missing = [p for p in known if pc.get(p, 0) == 0]
                if missing and len(missing) >= 2:
                    gaps.append(
                        {
                            "gap_type": "platform_gap",
                            "entity": tag,
                            "severity": "low",
                            "description": f"主题「{tag}」在 {'、'.join(sorted(missing))} 无覆盖",
                            "current_count": sum(pc.values()),
                            "suggested_count": 3,
                            "suggestion": f"在 {'、'.join(sorted(missing)[:2])} 补充「{tag}」内容",
                            "platforms": dict(pc),
                        }
                    )
        finally:
            conn.close()
        return gaps

    # ------------------------------------------------------------------ 报告
    def generate_report(self, gaps: list[dict[str, Any]] | None = None) -> str:
        if gaps is None:
            gaps = self._load_gaps()
        lines = [
            "# 📊 知识缺口分析报告",
            "",
            f"生成时间：{now_cn()}",
            "",
        ]
        if not gaps:
            lines.append("未发现明显知识缺口。")
            return "\n".join(lines)

        for sev, label in (
            ("high", "🔴 高优先级缺口"),
            ("medium", "🟡 中优先级缺口"),
            ("low", "🔵 低优先级/观察项"),
        ):
            subset = [g for g in gaps if g.get("severity") == sev]
            if not subset:
                continue
            lines.append(f"## {label}")
            for g in subset[:15]:
                lines.append(f"- **{g.get('entity')}**：{g.get('description')}")
                lines.append(f"  - 建议：{g.get('suggestion')}")
            lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------ 存储
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _create_task(self, task_type: str, started: str) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO gap_analysis_tasks (status, started_at, created_at)"
                " VALUES ('running', ?, ?)",
                (started, started),
            )
            conn.commit()
            lastrowid = cur.lastrowid
            return int(lastrowid) if lastrowid is not None else 0
        finally:
            conn.close()

    def _finish_task(self, task_id: int, *, gaps_found: int) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE gap_analysis_tasks SET status='completed', completed_at=?,"
                " gaps_found=? WHERE id=?",
                (now_cn(), gaps_found, task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _write_records(self, task_id: int, gaps: list[dict[str, Any]]) -> None:
        if not gaps:
            return
        conn = self._connect()
        try:
            # 关联实体 id（若 entities 表已有同名字段）
            conn.executemany(
                """INSERT INTO gap_records
                   (task_id, gap_type, entity_id, severity, description,
                    current_count, suggested_count, suggestion, status, created_at)
                   VALUES (?, ?, (SELECT id FROM entities WHERE name=? LIMIT 1),
                           ?, ?, ?, ?, ?, 'open', ?)""",
                [
                    (
                        task_id,
                        g["gap_type"],
                        g.get("entity", ""),
                        g.get("severity", "low"),
                        g.get("description", ""),
                        int(g.get("current_count", 0)),
                        int(g.get("suggested_count", 0)),
                        g.get("suggestion", ""),
                        now_cn(),
                    )
                    for g in gaps
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def _load_gaps(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM gap_records ORDER BY id DESC LIMIT 200").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def run_gap_analysis() -> dict[str, Any]:
    """同步入口（供 CLI 调用）。"""
    return GapAnalyst().run()

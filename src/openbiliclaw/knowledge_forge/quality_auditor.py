"""3.5 文章质量审计器（Quality Auditor）。

审计维度（文档 3.5.1，阶段一实现）：
    - 基础统计：总数、平台/状态分布、字数分布、时间分布
    - 重复内容：URL 去重、content_hash 去重、simhash 相似度（>阈值）
    - 缺失检测：缺失摘要 / 标签 / 作者 / content_hash
    - 格式错误：HTML 标签残留、乱码字符
    - 内容污染：评论特征词、广告特征词、未清理（content_cleaned 缺失）

阶段一为**只读扫描**：写入 audit_tasks / audit_issues / article_quality_scores，
不修改文章数据。自动修复（阶段二）默认关闭（auto_fix_enabled=false）。

流程（文档 3.5.2）：
    阶段一 全量扫描 → 阶段二 问题分类与优先级排序 → 阶段三 修复建议
    → 阶段四 自动修复（可选，需确认）→ 阶段五 审计报告
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from openbiliclaw.storage.database import open_db_conn
from collections import Counter
from pathlib import Path
from typing import Any

from .config import AuditConfig, KnowledgeForgeConfig, load_kf_config
from .models import AuditIssue, now_cn

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


# 评论特征词（复用 content_cleaner 的清单，扫描历史污染）
_COMMENT_MARKERS = (
    "一看就是",
    "原以为",
    "我觉得吧",
    "楼上",
    "谢邀",
    "泻药",
    "利益相关",
    "评论区",
    "赞同了",
    "收藏了",
    "已关注",
    "写得真好",
    "受教了",
    "不敢苟同",
    "实名反对",
    "补充一下",
    "插个眼",
    "马克一下",
)
_AD_MARKERS = (
    "扫码关注",
    "公众号",
    "微信号",
    "加微信",
    "商务合作",
    "广告合作",
    "点击链接",
    "优惠券",
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_GARBLED_RE = re.compile(r"\ufffd")


class QualityAuditor:
    """文章质量审计器。"""

    def __init__(
        self,
        *,
        config: KnowledgeForgeConfig | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.config = config or load_kf_config()
        self.audit_cfg: AuditConfig = self.config.audit
        self.db_path = Path(db_path) if db_path else _default_db_path()

    # ------------------------------------------------------------------ 主入口
    def run_full_audit(self, *, task_type: str = "full_audit") -> dict[str, Any]:
        """执行全量审计（只读扫描），写入审计任务/问题/质量分，返回统计。

        审计采用快照式：新一轮全量审计开始前，将上一轮遗留的 open 问题
        标记为 superseded，避免重复堆积（audit_issues 无 task_id 外键，
        按快照取代语义清理）。
        """
        started = now_cn()
        self._supersede_old_issues()
        task_id = self._create_task(task_type, started)

        issues: list[AuditIssue] = []
        stats: dict[str, Any] = {"total": 0}

        # 1. 基础统计扫描
        base = self._scan_basic_stats()
        stats.update(base)

        # 2. 重复内容检测
        dupes = self._scan_duplicates()
        issues.extend(dupes["issues"])
        stats["duplicate_groups"] = dupes["groups"]
        stats["duplicate_articles"] = dupes["count"]

        # 3. 缺失检测 + 格式错误 + 内容污染（逐行）
        row_issues = self._scan_row_issues()
        issues.extend(row_issues)
        stats.update(self._count_row_issue_types(row_issues))

        # 4. 质量分
        self._write_quality_scores(issues)

        # 5. 写入问题
        self._write_issues(task_id, issues)

        # 6. 更新任务状态
        self._finish_task(
            task_id,
            completed_at=now_cn(),
            total_articles=stats["total"],
            issues_found=len(issues),
        )
        stats["task_id"] = task_id
        stats["issues_found"] = len(issues)
        return stats

    # ------------------------------------------------------------------ 阶段一：基础统计
    def _scan_basic_stats(self) -> dict[str, Any]:
        conn = self._connect()
        try:
            total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            by_source = dict(
                conn.execute(
                    "SELECT source_type, COUNT(*) FROM articles"
                    " GROUP BY source_type ORDER BY 2 DESC"
                ).fetchall()
            )
            by_status = dict(
                conn.execute(
                    "SELECT status, COUNT(*) FROM articles GROUP BY status ORDER BY 2 DESC"
                ).fetchall()
            )
            with_summary = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE LENGTH(COALESCE(ai_summary,'')) > 100"
            ).fetchone()[0]
            with_tags = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE COALESCE(tags,'') <> '' AND tags <> '[]'"
            ).fetchone()[0]
            with_author = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE LENGTH(COALESCE(author,'')) > 0"
            ).fetchone()[0]
            length_buckets = self._scan_length_buckets(conn)
            return {
                "total": total,
                "by_source": by_source,
                "by_status": by_status,
                "with_summary": with_summary,
                "without_summary": total - with_summary,
                "with_tags": with_tags,
                "without_tags": total - with_tags,
                "with_author": with_author,
                "without_author": total - with_author,
                "length_buckets": length_buckets,
            }
        finally:
            conn.close()

    def _scan_length_buckets(self, conn: sqlite3.Connection) -> dict[str, int]:
        buckets = {"<200": 0, "200-500": 0, "500-1000": 0, "1000-5000": 0, ">5000": 0, "empty": 0}
        for (n,) in conn.execute(
            "SELECT LENGTH(COALESCE(content_text,'')) FROM articles"
        ).fetchall():
            if n == 0:
                buckets["empty"] += 1
            elif n < 200:
                buckets["<200"] += 1
            elif n < 500:
                buckets["200-500"] += 1
            elif n < 1000:
                buckets["500-1000"] += 1
            elif n <= 5000:
                buckets["1000-5000"] += 1
            else:
                buckets[">5000"] += 1
        return buckets

    # ------------------------------------------------------------------ 阶段一：重复检测
    def _scan_duplicates(self) -> dict[str, Any]:
        """按 URL 完全重复 + content_hash 完全重复检测；simhash 近重（抽样）另计。"""
        conn = self._connect()
        issues: list[AuditIssue] = []
        groups = 0
        count = 0
        try:
            # URL 重复
            url_rows = conn.execute(
                """SELECT url, COUNT(*) c, MIN(id) keep_id FROM articles
                   WHERE url IS NOT NULL AND url <> ''
                   GROUP BY url HAVING c > 1 LIMIT 500"""
            ).fetchall()
            for url, c, keep_id in url_rows:
                groups += 1
                count += c - 1
                dup_ids = [
                    r[0]
                    for r in conn.execute(
                        "SELECT id FROM articles WHERE url = ? AND id <> ?",
                        (url, keep_id),
                    ).fetchall()
                ]
                issues.append(
                    AuditIssue(
                        article_id=int(keep_id),
                        issue_type="duplicate",
                        severity="high",
                        description=f"相同 URL 重复入库 {c} 次",
                        details={"url": url, "duplicate_ids": dup_ids[:20]},
                        fix_suggestion="保留最完整的一篇，其余标记为 duplicate",
                    )
                )
            # content_hash 重复
            hash_rows = conn.execute(
                """SELECT content_hash, COUNT(*) c, MIN(id) keep_id FROM articles
                   WHERE COALESCE(content_hash,'') <> ''
                   GROUP BY content_hash HAVING c > 1 LIMIT 500"""
            ).fetchall()
            for _hash, c, keep_id in hash_rows:
                groups += 1
                count += c - 1
                dup_ids = [
                    r[0]
                    for r in conn.execute(
                        "SELECT id FROM articles WHERE content_hash = ? AND id <> ?",
                        (_hash, keep_id),
                    ).fetchall()
                ]
                issues.append(
                    AuditIssue(
                        article_id=int(keep_id),
                        issue_type="duplicate",
                        severity="high",
                        description=f"内容完全重复（content_hash）{c} 次",
                        details={"content_hash": _hash, "duplicate_ids": dup_ids[:20]},
                        fix_suggestion="保留最新/最完整的一篇，其余标记为 duplicate",
                    )
                )
        finally:
            conn.close()
        return {"issues": issues, "groups": groups, "count": count}

    # ------------------------------------------------------------------ 阶段一：逐行问题
    def _scan_row_issues(self) -> list[AuditIssue]:
        conn = self._connect()
        issues: list[AuditIssue] = []
        try:
            rows = conn.execute(
                """SELECT id, title, content_text, content_cleaned, ai_summary, tags,
                          author, content_hash, source_type
                   FROM articles"""
            ).fetchall()
            for row in rows:
                issues.extend(self._inspect_row(dict(row)))
        finally:
            conn.close()
        return issues

    def _inspect_row(self, row: dict[str, Any]) -> list[AuditIssue]:
        article_id = int(row["id"])
        out: list[AuditIssue] = []
        cfg = self.audit_cfg

        content = str(row.get("content_text") or "")
        cleaned = str(row.get("content_cleaned") or "")
        summary = str(row.get("ai_summary") or "")
        tags = str(row.get("tags") or "")
        author = str(row.get("author") or "")
        chash = str(row.get("content_hash") or "")

        # 缺失摘要
        if len(summary) < cfg.min_summary_length:
            out.append(
                AuditIssue(
                    article_id=article_id,
                    issue_type="missing_summary",
                    severity="medium",
                    description=f"ai_summary 为空或过短（{len(summary)}<{cfg.min_summary_length}）",
                    fix_suggestion="调用 LLM 重新生成摘要",
                )
            )
        # 内容过短（可能是抓取失败）
        if content and len(content) < cfg.min_content_length:
            out.append(
                AuditIssue(
                    article_id=article_id,
                    issue_type="too_short",
                    severity="medium",
                    description=f"正文过短（{len(content)}<{cfg.min_content_length}），可能抓取失败",
                    fix_suggestion="标记并建议重新抓取",
                )
            )
        # 缺失标签
        if not tags or tags in ("[]", "null"):
            out.append(
                AuditIssue(
                    article_id=article_id,
                    issue_type="missing_tags",
                    severity="low",
                    description="tags 为空",
                    fix_suggestion="调用 LLM 补充标签",
                )
            )
        # 缺失作者
        if not author:
            out.append(
                AuditIssue(
                    article_id=article_id,
                    issue_type="missing_author",
                    severity="low",
                    description="author 为空",
                    fix_suggestion="建议补充作者",
                )
            )
        # 格式错误：HTML 标签残留
        if content and len(_HTML_TAG_RE.findall(content)) >= 3:
            out.append(
                AuditIssue(
                    article_id=article_id,
                    issue_type="format_error",
                    severity="medium",
                    description="正文包含大量 HTML 标签",
                    details={"tag_count": len(_HTML_TAG_RE.findall(content))},
                    fix_suggestion="自动清理 HTML 标签",
                )
            )
        # 乱码
        if content and len(_GARBLED_RE.findall(content)) >= 3:
            out.append(
                AuditIssue(
                    article_id=article_id,
                    issue_type="format_error",
                    severity="medium",
                    description="正文包含乱码替换字符",
                    fix_suggestion="自动清理乱码或重新抓取",
                )
            )
        # content_hash 缺失
        if not chash:
            out.append(
                AuditIssue(
                    article_id=article_id,
                    issue_type="missing_hash",
                    severity="low",
                    description="content_hash 为空，无法精确去重",
                    fix_suggestion="自动计算补充",
                )
            )
        # 内容污染：评论特征
        if content:
            hit_comments = [m for m in _COMMENT_MARKERS if m in content]
            if hit_comments:
                out.append(
                    AuditIssue(
                        article_id=article_id,
                        issue_type="content_contamination",
                        severity="high",
                        description=f"正文疑似混入评论内容：{hit_comments[:3]}",
                        fix_suggestion="调用正文清理器重新清理",
                    )
                )
            hit_ads = [m for m in _AD_MARKERS if m in content]
            if hit_ads:
                out.append(
                    AuditIssue(
                        article_id=article_id,
                        issue_type="content_contamination",
                        severity="high",
                        description=f"正文疑似混入广告/营销内容：{hit_ads[:3]}",
                        fix_suggestion="调用正文清理器重新清理",
                    )
                )
            # 未清理：有正文但 content_cleaned 为空
            if content and not cleaned:
                out.append(
                    AuditIssue(
                        article_id=article_id,
                        issue_type="not_cleaned",
                        severity="low",
                        description="正文未经过正文清理器处理",
                        fix_suggestion="调用正文清理器生成 content_cleaned",
                    )
                )
        return out

    def _count_row_issue_types(self, issues: list[AuditIssue]) -> dict[str, Any]:
        c: Counter[str] = Counter(i.issue_type for i in issues)
        return {"row_issue_types": dict(c)}

    # ------------------------------------------------------------------ 质量分
    def _write_quality_scores(self, issues: list[AuditIssue]) -> None:
        conn = self._connect()
        try:
            scores: dict[int, dict[str, float]] = {}
            severity_penalty = {"high": 25.0, "medium": 10.0, "low": 3.0}
            for issue in issues:
                d = scores.setdefault(
                    issue.article_id,
                    {
                        "overall": 100.0,
                        "completeness": 100.0,
                        "content": 100.0,
                        "link": 100.0,
                        "uniqueness": 100.0,
                    },
                )
                penalty = severity_penalty.get(issue.severity, 5.0)
                d["overall"] = max(0.0, d["overall"] - penalty)
                if issue.issue_type in (
                    "missing_summary",
                    "missing_tags",
                    "missing_author",
                    "missing_hash",
                ):
                    d["completeness"] = max(0.0, d["completeness"] - penalty)
                elif issue.issue_type in (
                    "too_short",
                    "format_error",
                    "content_contamination",
                    "not_cleaned",
                ):
                    d["content"] = max(0.0, d["content"] - penalty)
                elif issue.issue_type == "duplicate":
                    d["uniqueness"] = max(0.0, d["uniqueness"] - 30.0)
            ts = now_cn()
            for article_id, s in scores.items():
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
                        article_id,
                        round(s["overall"], 1),
                        round(s["completeness"], 1),
                        round(s["content"], 1),
                        s["link"],
                        round(s["uniqueness"], 1),
                        ts,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ 任务/问题写入
    def _supersede_old_issues(self) -> None:
        """把上一轮遗留的 open 问题标记为 superseded（快照取代）。"""
        conn = self._connect()
        try:
            conn.execute("UPDATE audit_issues SET status='superseded' WHERE status='open'")
            conn.commit()
        finally:
            conn.close()

    def _create_task(self, task_type: str, started: str) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO audit_tasks (task_type, status, started_at, created_at)"
                " VALUES (?, 'running', ?, ?)",
                (task_type, started, started),
            )
            conn.commit()
            lastrowid = cur.lastrowid
            return int(lastrowid) if lastrowid is not None else 0
        finally:
            conn.close()

    def _finish_task(
        self, task_id: int, *, completed_at: str, total_articles: int, issues_found: int
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE audit_tasks SET status='completed', completed_at=?,"
                " total_articles=?, issues_found=? WHERE id=?",
                (completed_at, total_articles, issues_found, task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _write_issues(self, task_id: int, issues: list[AuditIssue]) -> None:
        if not issues:
            return
        conn = self._connect()
        try:
            conn.executemany(
                """INSERT INTO audit_issues
                   (article_id, issue_type, severity, description, details,
                    status, fix_suggestion, created_at)
                   VALUES (?,?,?,?,?, 'open', ?, ?)""",
                [
                    (
                        i.article_id,
                        i.issue_type,
                        i.severity,
                        i.description,
                        json.dumps(i.details, ensure_ascii=False),
                        i.fix_suggestion,
                        now_cn(),
                    )
                    for i in issues
                ],
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ 报告
    def generate_report(self, task_id: int | None = None) -> str:
        """生成 Markdown 审计报告；返回报告文本。"""
        conn = self._connect()
        try:
            issues = conn.execute(
                "SELECT * FROM audit_issues ORDER BY"
                " CASE severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, id"
            ).fetchall()
            lines = ["# 📋 文章质量审计报告", "", f"生成时间：{now_cn()}", ""]
            if task_id:
                lines.append(f"审计任务：#{task_id}")
            total = len(issues)
            lines.append(f"发现问题总数：{total}")
            lines.append("")
            by_type = Counter(str(r["issue_type"]) for r in issues)
            lines.append("## 按类型统计")
            for t, c in by_type.most_common():
                lines.append(f"- {t}: {c}")
            lines.append("")
            by_sev = Counter(str(r["severity"]) for r in issues)
            lines.append("## 按严重程度")
            for s in ("high", "medium", "low"):
                lines.append(f"- {s}: {by_sev.get(s, 0)}")
            lines.append("")
            lines.append("## 高优先级问题（前 20 条）")
            shown = 0
            for r in issues:
                if r["severity"] != "high":
                    continue
                if shown >= 20:
                    break
                lines.append(f"- 🔴 [{r['issue_type']}] 文章#{r['article_id']}：{r['description']}")
                shown += 1
            lines.append("")
            lines.append("## 修复建议")
            lines.append("- 重复内容：保留最新/最完整一篇，其余标记 duplicate")
            lines.append("- 内容污染：调用正文清理器重新清理")
            lines.append("- 缺失摘要/标签：调用 LLM 补充")
            lines.append("- 格式错误：自动清理 HTML 标签和乱码")
            return "\n".join(lines)
        finally:
            conn.close()

    # ------------------------------------------------------------------ 存储
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

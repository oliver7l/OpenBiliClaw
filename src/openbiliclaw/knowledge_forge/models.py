"""Knowledge Forge 数据模型。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

# 北京时间(UTC+8)：articles 及所有新建表的时间字段统一存本地时间字符串
CN_TZ = timezone(timedelta(hours=8))
TIME_FMT = "%Y-%m-%d %H:%M:%S"


def now_cn() -> str:
    """当前北京时间字符串。"""
    return datetime.now(CN_TZ).strftime(TIME_FMT)


@dataclass
class CleanResult:
    """3.0 正文清理结果。"""

    article_id: int
    original_length: int
    cleaned_text: str = ""
    clean_score: float = 0.0
    removed_chars: int = 0
    truncated_at: int | None = None  # 截断位置（字符下标）
    operations: list[dict[str, Any]] = field(default_factory=list)  # 清理日志
    verified: bool = False
    verify_issues: list[str] = field(default_factory=list)

    def add_op(self, op: str, detail: str, chars: int = 0) -> None:
        self.operations.append({"op": op, "detail": detail, "chars": chars})

    def to_log_json(self) -> str:
        import json

        return json.dumps(
            {
                "original_length": self.original_length,
                "cleaned_length": len(self.cleaned_text),
                "removed_chars": self.removed_chars,
                "truncated_at": self.truncated_at,
                "operations": self.operations,
                "ts": now_cn(),
            },
            ensure_ascii=False,
        )


@dataclass
class VerifyResult:
    """3.0.3 清理后质量验证结果。"""

    passed: bool = True
    issues: list[str] = field(default_factory=list)
    title_similarity: float | None = None
    effective_sentences: int = 0

    def fail(self, issue: str) -> None:
        self.passed = False
        self.issues.append(issue)


@dataclass
class SummaryResult:
    """3.1 三层摘要结果。"""

    article_id: int
    detailed: str = ""
    compact: str = ""
    ultra_compact: str = ""
    quality: float | None = None
    provider: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.compact) and not self.error


@dataclass
class Entity:
    """3.2 实体。"""

    name: str
    type: str  # author/topic/concept/organization/person/platform
    description: str = ""
    article_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    id: int | None = None


@dataclass
class AuditIssue:
    """3.5 审计问题。"""

    article_id: int
    issue_type: str  # duplicate/dead_link/missing_summary/too_short/content_contamination/...
    severity: str  # high/medium/low
    description: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    fix_suggestion: str = ""
    status: str = "open"


@dataclass
class ArticleQualityScore:
    """3.5 文章质量分。"""

    article_id: int
    overall_score: float = 0.0
    completeness_score: float = 0.0
    content_score: float = 0.0
    link_score: float = 100.0
    uniqueness_score: float = 100.0
    last_audited_at: str = field(default_factory=now_cn)


def _ts() -> float:
    return time.time()

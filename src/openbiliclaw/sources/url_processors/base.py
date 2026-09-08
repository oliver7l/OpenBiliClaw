"""URL content processor base class and result dataclass.

Inspired by Agent-SaveMark's processor pattern: each platform has a
dedicated processor that extracts structured content from a single URL.
"""

from __future__ import annotations

import enum
import ipaddress
import logging
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 北京时间(UTC+8): articles 表所有时间字段统一存本地时间字符串
CN_TZ = timezone(timedelta(hours=8))

# Internal networks to block (SSRF protection)
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def is_safe_url(url: str) -> bool:
    """Check if URL is safe to fetch (not targeting internal networks).

    Args:
        url: The URL to check.

    Returns:
        True if the URL is safe, False otherwise.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        try:
            addr_info = socket.getaddrinfo(hostname, None)
            for _family, _type, _proto, _canonname, sockaddr in addr_info:
                ip = ipaddress.ip_address(sockaddr[0])
                for network in _BLOCKED_NETWORKS:
                    if ip in network:
                        logger.warning("Blocked internal URL: %s (resolves to %s)", url, ip)
                        return False
        except socket.gaierror:
            return False
        return True
    except Exception:
        return False


class ProcessorStatus(str, enum.Enum):
    """Status of a content extraction."""

    success = "success"
    partial = "partial"
    failed = "failed"


@dataclass
class ProcessorResult:
    """Immutable result from a content processor.

    Attributes:
        title: Extracted article title.
        author: Author/creator name.
        summary: Short summary or description.
        content_text: Full extracted text content.
        published_at: Publication date (ISO format string).
        tags: List of tags or categories.
        source_type: Source platform identifier (e.g., 'zhihu', 'v2ex').
        source_name: Human-readable source name.
        url: Original URL.
        media: List of media URLs (images, videos).
        metadata: Additional platform-specific metadata.
        status: Extraction status.
        error: Error message if extraction failed.
    """

    title: str | None = None
    author: str | None = None
    summary: str | None = None
    content_text: str | None = None
    content_html: str | None = None  # 原始 HTML 快照，用于离线存档
    published_at: str | None = None
    tags: list[str] = field(default_factory=list)
    source_type: str = "generic"
    source_name: str = "Generic URL"
    url: str = ""
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    status: ProcessorStatus = ProcessorStatus.success
    error: str | None = None

    def to_article_dict(self) -> dict[str, Any]:
        """Convert to article dict for database insertion.

        Returns:
            Dictionary with article fields matching the articles table schema.
        """
        import hashlib
        import json
        from datetime import datetime
        content_hash = ""
        if self.content_text:
            content_hash = hashlib.sha256(self.content_text.encode("utf-8")).hexdigest()[:32]

        now = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
        return {
            "source_type": self.source_type,
            "source_name": self.source_name,
            "title": self.title or self.url,
            "url": self.url,
            "author": self.author,
            "summary": self.summary,
            "content_text": self.content_text,
            "published_at": self.published_at,
            "created_at": now,
            "updated_at": now,
            "tags": json.dumps(self.tags, ensure_ascii=False) if self.tags else None,
            "status": "active",
            "body_fetch_attempts": 1,
            "reading_percent": 0.0,
            "favorited": 0,
            "content_hash": content_hash,
        }


class BaseProcessor(ABC):
    """Abstract base class for URL content processors.

    Subclasses declare ``url_patterns`` (regexes) they handle and
    implement ``process()``.

    Attributes:
        url_patterns: List of regex patterns that match URLs this processor handles.
        priority: Matching priority (higher = matched first).
        source_type: Source platform identifier.
        source_name: Human-readable source name.
    """

    url_patterns: list[str] = []
    priority: int = 0
    source_type: str = "generic"
    source_name: str = "Generic URL"

    def __init__(self, *, llm_service: Any = None, browser_cdp_url: str = "") -> None:
        """Initialize processor with optional LLM and browser services.

        Args:
            llm_service: LLM service for AI-powered extraction.
            browser_cdp_url: Chrome DevTools Protocol URL for browser-based extraction.
        """
        self._llm_service = llm_service
        self._browser_cdp_url = browser_cdp_url

    @abstractmethod
    async def process(self, url: str, **kwargs: Any) -> ProcessorResult:
        """Extract content from the given URL.

        Args:
            url: The URL to process.
            **kwargs: Additional processor-specific arguments.

        Returns:
            ProcessorResult with extracted content.
        """
        ...

    def _failed_result(self, url: str, error: str) -> ProcessorResult:
        """Create a failed result.

        Args:
            url: The URL that failed.
            error: Error message.

        Returns:
            ProcessorResult with failed status.
        """
        return ProcessorResult(
            url=url,
            source_type=self.source_type,
            source_name=self.source_name,
            status=ProcessorStatus.failed,
            error=error,
        )

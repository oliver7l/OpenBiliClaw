"""WeChat Official Account (微信公众号) URL processor.

Handles:
- mp.weixin.qq.com/s/{id} — article pages
- mp.weixin.qq.com/s?__biz=... — article pages with query params
"""

from __future__ import annotations

import logging
import re
from typing import Any

from openbiliclaw.sources.url_processors.base import (
    BaseProcessor,
    ProcessorResult,
    ProcessorStatus,
    is_safe_url,
)
from openbiliclaw.sources.url_processors.registry import register_processor

logger = logging.getLogger(__name__)


@register_processor
class WechatProcessor(BaseProcessor):
    """Extract content from WeChat Official Account articles.

    WeChat articles are relatively easy to parse because they use a
    consistent structure with rich_media_content div. No authentication
    required for public articles.
    """

    url_patterns: list[str] = [
        r"mp\.weixin\.qq\.com/s/",
        r"mp\.weixin\.qq\.com/s\?",
    ]
    priority: int = 10
    source_type: str = "wechat"
    source_name: str = "微信公众号"

    async def process(self, url: str, **kwargs: Any) -> ProcessorResult:
        """Extract content from a WeChat article URL.

        Args:
            url: The WeChat article URL.
            **kwargs: Additional arguments.

        Returns:
            ProcessorResult with WeChat article content.
        """
        if not is_safe_url(url):
            return self._failed_result(url, "URL is not safe (internal network)")

        try:
            import requests
            from bs4 import BeautifulSoup

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }

            response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"

            soup = BeautifulSoup(response.text, "lxml")

            # Extract title
            title = None
            title_elem = soup.select_one("#activity-name, h1.rich_media_title, .rich_media_title")
            if title_elem:
                title = title_elem.get_text(strip=True)
            if not title and soup.title:
                title = soup.title.string.strip()

            # Extract author / account name
            author = None
            author_elem = soup.select_one("#js_name, .rich_media_meta_nickname, .profile_nickname")
            if author_elem:
                author = author_elem.get_text(strip=True)

            # Extract publish time
            published_at = None
            time_elem = soup.select_one("#publish_time, .rich_media_meta_list em, #post-date")
            if time_elem:
                time_text = time_elem.get_text(strip=True)
                if time_text:
                    # Try to parse common date formats
                    published_at = self._parse_date(time_text)

            # Extract main content
            content_text = None
            content_elem = soup.select_one("#js_content, .rich_media_content, #page-content")
            if content_elem:
                # Remove unwanted elements
                for tag in content_elem.select("script, style, .qr_code_pc_outer, .rich_media_tool"):
                    tag.decompose()
                content_text = content_elem.get_text(separator="\n", strip=True)
                # Clean up excessive newlines
                content_text = re.sub(r"\n{3,}", "\n\n", content_text).strip()

            # Extract summary/description
            summary = None
            desc_elem = soup.select_one(".rich_media_desc, #js_desc")
            if desc_elem:
                summary = desc_elem.get_text(strip=True)

            # Extract tags (WeChat articles usually don't have explicit tags)
            tags = ["微信公众号"]

            if not title:
                return self._failed_result(url, "Could not extract article title")

            return ProcessorResult(
                title=title,
                author=author,
                summary=summary,
                content_text=content_text,
                published_at=published_at,
                tags=tags,
                source_type=self.source_type,
                source_name=self.source_name,
                url=url,
                metadata={
                    "platform": "wechat",
                },
                status=ProcessorStatus.success if content_text else ProcessorStatus.partial,
            )

        except Exception as e:
            logger.exception("WechatProcessor failed for %s", url)
            return self._failed_result(url, str(e))

    def _parse_date(self, date_text: str) -> str | None:
        """Parse common Chinese date formats to ISO format.

        Args:
            date_text: Date string in Chinese format.

        Returns:
            ISO format date string, or None if parsing fails.
        """
        import re
        from datetime import datetime

        # Try common formats
        patterns = [
            (r"(\d{4})年(\d{1,2})月(\d{1,2})日", "%Y-%m-%d"),
            (r"(\d{4})-(\d{1,2})-(\d{1,2})", "%Y-%m-%d"),
            (r"(\d{4})/(\d{1,2})/(\d{1,2})", "%Y-%m-%d"),
        ]

        for pattern, fmt in patterns:
            match = re.search(pattern, date_text)
            if match:
                try:
                    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
                    return f"{year:04d}-{month:02d}-{day:02d}T00:00:00Z"
                except (ValueError, IndexError):
                    continue

        return None

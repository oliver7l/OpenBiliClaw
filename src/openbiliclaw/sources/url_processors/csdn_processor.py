"""CSDN URL processor — extracts content from CSDN blog articles.

Handles:
- blog.csdn.net/{user}/article/details/{id} — blog articles
- blog.csdn.net/{user}/article/details/{id}?* — blog articles with params
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
class CsdnProcessor(BaseProcessor):
    """Extract content from CSDN blog articles.

    Uses HTML parsing to extract article content. CSDN articles have
    a consistent structure with article content in #content_views div.
    """

    url_patterns: list[str] = [
        r"blog\.csdn\.net/.+/article/details/\d+",
    ]
    priority: int = 10
    source_type: str = "csdn"
    source_name: str = "CSDN"

    async def process(self, url: str, **kwargs: Any) -> ProcessorResult:
        """Extract content from a CSDN article URL.

        Args:
            url: The CSDN article URL.
            **kwargs: Additional arguments.

        Returns:
            ProcessorResult with CSDN article content.

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
            title_elem = soup.select_one("h1.title-article, h1#articleContentId, .article-title")
            if title_elem:
                title = title_elem.get_text(strip=True)
            if not title and soup.title:
                title = soup.title.string.strip()

            # Extract author
            author = None
            author_elem = soup.select_one(".follow-nickName, .user-name, .author-name")
            if author_elem:
                author = author_elem.get_text(strip=True)

            # Extract publish time
            published_at = None
            time_elem = soup.select_one(".time, .article-info-box .time, #publish_time")
            if time_elem:
                time_text = time_elem.get_text(strip=True)
                if time_text:
                    published_at = self._parse_date(time_text)

            # Extract content
            content_text = None
            content_elem = soup.select_one("#content_views, .article-content, #article_content")
            if content_elem:
                for tag in content_elem.select(
                    "script, style, .advertisement, .recommend, .csdn-side-toolbar"
                ):
                    tag.decompose()
                content_text = content_elem.get_text(separator="\n", strip=True)
                content_text = re.sub(r"\n{3,}", "\n\n", content_text).strip()

            # Extract summary
            summary = None
            desc_meta = soup.find("meta", attrs={"name": "description"})
            if desc_meta and desc_meta.get("content"):
                summary = desc_meta["content"].strip()

            # Extract tags
            tags = ["CSDN", "技术博客"]
            tag_elements = soup.select(".article-tags a, .tags-box a, .tag")
            for tag_elem in tag_elements[:5]:
                tag_text = tag_elem.get_text(strip=True)
                if tag_text and tag_text not in tags:
                    tags.append(tag_text)

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
                metadata={"platform": "csdn"},
                status=ProcessorStatus.success if content_text else ProcessorStatus.partial,
            )

        except Exception as e:
            logger.exception("CsdnProcessor failed for %s", url)
            return self._failed_result(url, str(e))

    def _parse_date(self, date_text: str) -> str | None:
        """Parse date string to ISO format."""
        import re

        patterns = [
            (r"(\d{4})-(\d{1,2})-(\d{1,2})", "%Y-%m-%d"),
            (r"(\d{4})年(\d{1,2})月(\d{1,2})日", "%Y-%m-%d"),
            (r"(\d{4})/(\d{1,2})/(\d{1,2})", "%Y-%m-%d"),
        ]
        for pattern, _ in patterns:
            match = re.search(pattern, date_text)
            if match:
                try:
                    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
                    return f"{year:04d}-{month:02d}-{day:02d}T00:00:00Z"
                except (ValueError, IndexError):
                    continue
        return None

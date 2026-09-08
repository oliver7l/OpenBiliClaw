"""36Kr (36氪) URL processor — extracts content from 36Kr articles.

Handles:
- 36kr.com/p/{id}.html — articles
- 36kr.com/p/{id} — articles
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
class Kr36Processor(BaseProcessor):
    """Extract content from 36Kr (36氪) articles.

    Uses HTML parsing to extract article content. 36Kr articles have
    a consistent structure with article content in .article-content div.
    """

    url_patterns: list[str] = [
        r"36kr\.com/p/\d+",
        r"36kr\.com/newsflash/\d+",
    ]
    priority: int = 10
    source_type: str = "36kr"
    source_name: str = "36氪"

    async def process(self, url: str, **kwargs: Any) -> ProcessorResult:
        """Extract content from a 36Kr article URL.

        Args:
            url: The 36Kr article URL.
            **kwargs: Additional arguments.

        Returns:
            ProcessorResult with 36Kr article content.

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
            title_elem = soup.select_one("h1.article-title, h1.title, .article-header h1")
            if title_elem:
                title = title_elem.get_text(strip=True)
            if not title:
                og_title = soup.find("meta", property="og:title")
                if og_title and og_title.get("content"):
                    title = og_title["content"].strip()
            if not title and soup.title:
                title = soup.title.string.strip()

            # Extract author
            author = None
            author_elem = soup.select_one(".author-name, .article-author, .user-name")
            if author_elem:
                author = author_elem.get_text(strip=True)

            # Extract content
            content_text = None
            content_elem = soup.select_one(".article-content, .content, .article-detail-content")
            if content_elem:
                for tag in content_elem.select(
                    "script, style, .advertisement, .recommend, .qrcode"
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
            tags = ["36氪", "科技商业"]
            tag_elements = soup.select(".article-tags a, .tags a, .tag")
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
                tags=tags,
                source_type=self.source_type,
                source_name=self.source_name,
                url=url,
                metadata={"platform": "36kr"},
                status=ProcessorStatus.success if content_text else ProcessorStatus.partial,
            )

        except Exception as e:
            logger.exception("Kr36Processor failed for %s", url)
            return self._failed_result(url, str(e))

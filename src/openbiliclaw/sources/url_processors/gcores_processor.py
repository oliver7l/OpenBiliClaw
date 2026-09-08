"""Gcores (机核) URL processor — extracts content from Gcores articles.

Handles:
- gcores.com/articles/{id} — articles
- www.gcores.com/articles/{id} — articles
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
class GcoresProcessor(BaseProcessor):
    """Extract content from Gcores (机核) articles.

    Uses HTML parsing to extract article content. Gcores articles have
    a consistent structure with article content in .article-content div.
    """

    url_patterns: list[str] = [
        r"gcores\.com/articles/\d+",
        r"gcores\.com/radio/\d+",
    ]
    priority: int = 10
    source_type: str = "gcores"
    source_name: str = "机核"

    async def process(self, url: str, **kwargs: Any) -> ProcessorResult:
        """Extract content from a Gcores article URL.

        Args:
            url: The Gcores article URL.
            **kwargs: Additional arguments.

        Returns:
            ProcessorResult with Gcores article content.

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
            author_elem = soup.select_one(".author-name, .article-author, .user-name, .author a")
            if author_elem:
                author = author_elem.get_text(strip=True)

            # Extract content
            content_text = None
            content_elem = soup.select_one(".article-content, .content, .article-detail")
            if content_elem:
                for tag in content_elem.select("script, style, .advertisement, .recommend"):
                    tag.decompose()
                content_text = content_elem.get_text(separator="\n", strip=True)
                content_text = re.sub(r"\n{3,}", "\n\n", content_text).strip()

            # Extract summary
            summary = None
            desc_meta = soup.find("meta", attrs={"name": "description"})
            if desc_meta and desc_meta.get("content"):
                summary = desc_meta["content"].strip()

            # Extract tags
            tags = ["机核", "游戏文化"]
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
                metadata={"platform": "gcores"},
                status=ProcessorStatus.success if content_text else ProcessorStatus.partial,
            )

        except Exception as e:
            logger.exception("GcoresProcessor failed for %s", url)
            return self._failed_result(url, str(e))

"""Generic URL processor — fallback for any web page.

Uses requests + readability-lxml or BeautifulSoup to extract main content.
This is the lowest-priority processor that handles any URL not matched
by a platform-specific processor.
"""

from __future__ import annotations

import logging
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
class GenericURLProcessor(BaseProcessor):
    """Extract content from any web page using readability + BeautifulSoup.

    This is the fallback processor with the lowest priority. It handles
    any URL that doesn't match a platform-specific processor.
    """

    url_patterns: list[str] = []  # matches nothing - used as fallback
    priority: int = -1
    source_type: str = "web"
    source_name: str = "Web Article"

    async def process(self, url: str, **kwargs: Any) -> ProcessorResult:
        """Extract main content from a generic web page.

        Args:
            url: The URL to process.
            **kwargs: Additional arguments (unused).

        Returns:
            ProcessorResult with extracted content.
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
            if soup.title and soup.title.string:
                title = soup.title.string.strip()
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                title = og_title["content"].strip()

            # Extract author
            author = None
            author_meta = soup.find("meta", attrs={"name": "author"})
            if author_meta and author_meta.get("content"):
                author = author_meta["content"].strip()

            # Extract description/summary
            summary = None
            desc_meta = soup.find("meta", attrs={"name": "description"})
            if desc_meta and desc_meta.get("content"):
                summary = desc_meta["content"].strip()
            og_desc = soup.find("meta", property="og:description")
            if og_desc and og_desc.get("content"):
                summary = og_desc["content"].strip()

            # Extract published time
            published_at = None
            pub_meta = soup.find("meta", property="article:published_time")
            if pub_meta and pub_meta.get("content"):
                published_at = pub_meta["content"].strip()

            # Extract main content
            content_text = self._extract_main_content(soup)

            # Extract tags
            tags = []
            for tag_meta in soup.find_all("meta", property="article:tag"):
                if tag_meta.get("content"):
                    tags.append(tag_meta["content"].strip())

            # Extract source name from domain
            from urllib.parse import urlparse
            domain = urlparse(url).netloc.replace("www.", "")
            source_name = domain.split(".")[0].capitalize() if domain else "Web Article"

            if not title and not content_text:
                return self._failed_result(url, "Could not extract any content")

            return ProcessorResult(
                title=title,
                author=author,
                summary=summary,
                content_text=content_text,
                published_at=published_at,
                tags=tags[:10],
                source_type=self.source_type,
                source_name=source_name,
                url=url,
                status=ProcessorStatus.success if content_text else ProcessorStatus.partial,
            )

        except Exception as e:
            logger.exception("GenericURLProcessor failed for %s", url)
            return self._failed_result(url, str(e))

    def _extract_main_content(self, soup: Any) -> str | None:
        """Extract main article content from BeautifulSoup.

        Tries common content containers, falls back to paragraph extraction.

        Args:
            soup: BeautifulSoup parsed HTML.

        Returns:
            Extracted text content, or None if not found.
        """
        # Remove unwanted elements
        for tag in soup(["script", "style", "nav", "header", "footer",
                          "aside", "iframe", "noscript", "svg", "button"]):
            tag.decompose()

        # Try common content containers
        content_selectors = [
            "article",
            "main",
            '[role="main"]',
            ".article-content",
            ".post-content",
            ".entry-content",
            ".content",
            "#content",
            ".markdown-body",
            ".rich_media_content",  # WeChat
        ]

        for selector in content_selectors:
            element = soup.select_one(selector)
            if element:
                text = element.get_text(separator="\n", strip=True)
                if len(text) > 200:  # Minimum content length
                    return text

        # Fallback: extract all paragraphs
        paragraphs = soup.find_all("p")
        if paragraphs:
            text = "\n\n".join(p.get_text(strip=True) for p in paragraphs
                                if len(p.get_text(strip=True)) > 20)
            if len(text) > 200:
                return text

        return None

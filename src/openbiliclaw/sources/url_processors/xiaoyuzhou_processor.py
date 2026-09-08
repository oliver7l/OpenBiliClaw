"""Xiaoyuzhou (小宇宙) URL processor — extracts content from Xiaoyuzhou podcast episodes.

Handles:
- xiaoyuzhoufm.com/episode/{id} — podcast episodes
- xiaoyuzhoufm.com/podcast/{id} — podcast channels
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
class XiaoyuzhouProcessor(BaseProcessor):
    """Extract content from Xiaoyuzhou (小宇宙) podcast episodes.

    Uses the Xiaoyuzhou web API to fetch episode metadata and show notes.
    Does NOT download audio files (too heavy).
    """

    url_patterns: list[str] = [
        r"xiaoyuzhoufm\.com/episode/\w+",
        r"xiaoyuzhoufm\.com/podcast/\w+",
    ]
    priority: int = 10
    source_type: str = "xiaoyuzhou"
    source_name: str = "小宇宙"

    async def process(self, url: str, **kwargs: Any) -> ProcessorResult:
        """Extract content from a Xiaoyuzhou podcast URL.

        Args:
            url: The Xiaoyuzhou URL to process.
            **kwargs: Additional arguments.

        Returns:
            ProcessorResult with podcast episode content.

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
                "Referer": "https://www.xiaoyuzhoufm.com/",
            }

            response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"

            soup = BeautifulSoup(response.text, "lxml")

            # Extract from meta tags (most reliable for SSR sites)
            title = None
            author = None
            description = None
            published_at = None

            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                title = og_title["content"].strip()

            og_desc = soup.find("meta", property="og:description")
            if og_desc and og_desc.get("content"):
                description = og_desc["content"].strip()

            # Try to extract author from page
            author_elem = soup.select_one(".podcast-title, .author-name, .podcast-name")
            if author_elem:
                author = author_elem.get_text(strip=True)

            # Try to extract show notes / description
            content_text = description
            desc_elem = soup.select_one(".description, .show-notes, .episode-description")
            if desc_elem:
                content_text = desc_elem.get_text(separator="\n", strip=True)

            # Try to extract from __NEXT_DATA__ JSON
            if not title or not content_text:
                next_data = self._extract_from_next_data(soup)
                if next_data:
                    if not title:
                        title = next_data.get("title")
                    if not author:
                        author = next_data.get("podcast", {}).get("title")
                    if not content_text:
                        content_text = next_data.get("description")
                    if not published_at:
                        pub_date = next_data.get("pubDate") or next_data.get("publishAt")
                        if pub_date:
                            published_at = pub_date

            # Extract duration
            duration = None
            duration_elem = soup.select_one(".duration, .time")
            if duration_elem:
                duration = duration_elem.get_text(strip=True)

            # Extract tags
            tags = ["小宇宙", "播客"]
            tag_elements = soup.select(".tag, .category")
            for tag_elem in tag_elements[:5]:
                tag_text = tag_elem.get_text(strip=True)
                if tag_text and tag_text not in tags:
                    tags.append(tag_text)

            if not title:
                return self._failed_result(url, "Could not extract episode title")

            summary = (
                description[:200] + "..." if description and len(description) > 200 else description
            )

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
                    "duration": duration,
                    "is_podcast": True,
                },
                status=ProcessorStatus.success if content_text else ProcessorStatus.partial,
            )

        except Exception as e:
            logger.exception("XiaoyuzhouProcessor failed for %s", url)
            return self._failed_result(url, str(e))

    def _extract_from_next_data(self, soup: Any) -> dict[str, Any] | None:
        """Extract episode data from __NEXT_DATA__ JSON script tag.

        Args:
            soup: BeautifulSoup parsed HTML.

        Returns:
            Episode data dict or None.

        """
        import json

        try:
            script = soup.find("script", id="__NEXT_DATA__")
            if not script or not script.string:
                return None

            data = json.loads(script.string)
            # Navigate to episode data
            props = data.get("props", {})
            page_props = props.get("pageProps", {})

            # Episode data might be in different locations
            episode = page_props.get("episode") or page_props.get("data") or {}
            if episode:
                return {
                    "title": episode.get("title"),
                    "description": episode.get("description") or episode.get("shownote"),
                    "pubDate": episode.get("pubDate") or episode.get("publishAt"),
                    "duration": episode.get("duration"),
                    "podcast": episode.get("podcast", {}),
                }
        except Exception as e:
            logger.debug("Failed to extract from __NEXT_DATA__: %s", e)

        return None

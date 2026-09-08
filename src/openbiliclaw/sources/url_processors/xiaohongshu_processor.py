"""Xiaohongshu (小红书) URL processor — extracts content from Xiaohongshu notes.

Handles:
- xiaohongshu.com/explore/{note_id} — notes
- xhslink.com/{short} — short links
- xiaohongshu.com/discovery/item/{note_id} — notes
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
class XiaohongshuProcessor(BaseProcessor):
    """Extract content from Xiaohongshu (小红书) notes.

    Uses HTML parsing to extract note title, author, description, and tags.
    Xiaohongshu has strong anti-scraping, so this may require cookies
    for some notes. Falls back to generic extraction if API is blocked.
    """

    url_patterns: list[str] = [
        r"xiaohongshu\.com/explore/\w+",
        r"xiaohongshu\.com/discovery/item/\w+",
        r"xhslink\.com/\w+",
    ]
    priority: int = 10
    source_type: str = "xiaohongshu"
    source_name: str = "小红书"

    async def process(self, url: str, **kwargs: Any) -> ProcessorResult:
        """Extract content from a Xiaohongshu note URL.

        Args:
            url: The Xiaohongshu URL to process.
            **kwargs: Additional arguments (cookies can be passed).

        Returns:
            ProcessorResult with Xiaohongshu note content.

        """
        if not is_safe_url(url):
            return self._failed_result(url, "URL is not safe (internal network)")

        cookies = kwargs.get("cookies", {})

        try:
            import requests
            from bs4 import BeautifulSoup

            # Follow short links
            if "xhslink.com" in url:
                url = self._follow_redirect(url)

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://www.xiaohongshu.com/",
            }
            if cookies:
                headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())

            response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"

            soup = BeautifulSoup(response.text, "lxml")

            # Try to extract from __INITIAL_STATE__ JSON (most reliable)
            title, author, content_text, tags, stats = self._extract_from_initial_state(soup)

            # Fallback to meta tags
            if not title:
                og_title = soup.find("meta", property="og:title")
                if og_title and og_title.get("content"):
                    title = og_title["content"].strip()

            if not author:
                og_author = soup.find("meta", attrs={"name": "author"})
                if og_author and og_author.get("content"):
                    author = og_author["content"].strip()

            if not content_text:
                og_desc = soup.find("meta", property="og:description")
                if og_desc and og_desc.get("content"):
                    content_text = og_desc["content"].strip()

            # Extract tags from content
            if not tags and content_text:
                tags = re.findall(r"#(\w+)", content_text)

            if not title:
                return self._failed_result(
                    url, "Could not extract note content (may require login)"
                )

            summary = None
            if stats:
                likes = stats.get("liked_count", 0)
                collects = stats.get("collected_count", 0)
                comments = stats.get("comment_count", 0)
                summary = f"{likes}点赞 · {collects}收藏 · {comments}评论"

            return ProcessorResult(
                title=title,
                author=author,
                summary=summary,
                content_text=content_text,
                tags=["小红书"] + tags[:8],
                source_type=self.source_type,
                source_name=self.source_name,
                url=url,
                metadata=stats or {},
                status=ProcessorStatus.success if content_text else ProcessorStatus.partial,
            )

        except Exception as e:
            logger.exception("XiaohongshuProcessor failed for %s", url)
            return self._failed_result(url, str(e))

    def _follow_redirect(self, url: str) -> str:
        """Follow xhslink.com short link redirect."""
        try:
            import requests

            resp = requests.head(url, allow_redirects=True, timeout=10)
            return resp.url
        except Exception:
            return url

    def _extract_from_initial_state(
        self, soup: Any
    ) -> tuple[str | None, str | None, str | None, list[str], dict[str, Any]]:
        """Extract note data from __INITIAL_STATE__ JSON script tag.

        Args:
            soup: BeautifulSoup parsed HTML.

        Returns:
            Tuple of (title, author, content, tags, stats).

        """
        import json

        title = None
        author = None
        content = None
        tags: list[str] = []
        stats: dict[str, Any] = {}

        try:
            # Find script tag with __INITIAL_STATE__
            for script in soup.find_all("script"):
                script_text = script.string or ""
                if "__INITIAL_STATE__" in script_text or "window.__INITIAL_STATE__" in script_text:
                    # Extract JSON
                    match = re.search(
                        r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});", script_text, re.DOTALL
                    )
                    if not match:
                        match = re.search(
                            r"__INITIAL_STATE__\s*=\s*(\{.*?\})", script_text, re.DOTALL
                        )

                    if match:
                        try:
                            data = json.loads(match.group(1))
                            # Navigate to note data
                            note = data.get("note", {}).get("noteDetailMap", {})
                            if note:
                                first_key = next(iter(note))
                                note_data = note[first_key].get("note", {})
                                title = note_data.get("title")
                                content = note_data.get("desc")
                                author = note_data.get("user", {}).get("nickname")
                                tags = [
                                    t.get("name", "")
                                    for t in note_data.get("tagList", [])
                                    if t.get("name")
                                ]
                                stats = {
                                    "liked_count": note_data.get("interactInfo", {}).get(
                                        "likedCount"
                                    ),
                                    "collected_count": note_data.get("interactInfo", {}).get(
                                        "collectedCount"
                                    ),
                                    "comment_count": note_data.get("interactInfo", {}).get(
                                        "commentCount"
                                    ),
                                    "share_count": note_data.get("interactInfo", {}).get(
                                        "shareCount"
                                    ),
                                }
                        except json.JSONDecodeError:
                            pass
                    break
        except Exception as e:
            logger.debug("Failed to extract from __INITIAL_STATE__: %s", e)

        return title, author, content, tags, stats

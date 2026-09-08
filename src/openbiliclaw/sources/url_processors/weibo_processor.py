"""Weibo (微博) URL processor — extracts content from Weibo posts.

Handles:
- weibo.com/{uid}/{mid} — user posts
- weibo.com/detail/{mid} — post details
- m.weibo.cn/detail/{mid} — mobile post details
- weibo.com/ttarticle/p/show?id=... — articles

Note: Weibo has strong anti-scraping. Cookies may be required for
some posts. This processor works best with authentication cookies.
"""

from __future__ import annotations

import json
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
class WeiboProcessor(BaseProcessor):
    """Extract content from Weibo (微博) posts.

    Uses the Weibo mobile API or HTML parsing to extract post content.
    Cookies may be required for some posts.
    """

    url_patterns: list[str] = [
        r"weibo\.com/\d+/[A-Za-z0-9]+",
        r"weibo\.com/detail/\w+",
        r"m\.weibo\.cn/detail/\w+",
        r"weibo\.com/ttarticle/p/show",
        r"weibo\.com/status/\w+",
    ]
    priority: int = 10
    source_type: str = "weibo"
    source_name: str = "微博"

    async def process(self, url: str, **kwargs: Any) -> ProcessorResult:
        """Extract content from a Weibo post URL.

        Args:
            url: The Weibo post URL.
            **kwargs: Additional arguments (cookies can be passed).

        Returns:
            ProcessorResult with Weibo post content.

        """
        if not is_safe_url(url):
            return self._failed_result(url, "URL is not safe (internal network)")

        cookies = kwargs.get("cookies", {})

        try:
            # Try mobile API first (more reliable)
            result = await self._fetch_via_mobile_api(url, cookies)
            if result and result.status == ProcessorStatus.success:
                return result

            # Fallback to HTML parsing
            return await self._fetch_via_html(url, cookies)

        except Exception as e:
            logger.exception("WeiboProcessor failed for %s", url)
            return self._failed_result(url, str(e))

    async def _fetch_via_mobile_api(
        self, url: str, cookies: dict[str, str]
    ) -> ProcessorResult | None:
        """Fetch Weibo post via mobile API.

        Args:
            url: The Weibo URL.
            cookies: Authentication cookies.

        Returns:
            ProcessorResult or None if API fails.

        """
        try:
            import requests

            # Extract post ID from URL
            mid = self._extract_post_id(url)
            if not mid:
                return None

            # Use mobile API
            api_url = f"https://m.weibo.cn/statuses/show?id={mid}"
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"  # noqa: E501
                ),
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://m.weibo.cn/",
            }
            if cookies:
                headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())

            response = requests.get(api_url, headers=headers, timeout=15)
            if response.status_code != 200:
                return None

            data = response.json()
            if data.get("ok") != 1:
                return None

            status = data.get("data", {})
            text = status.get("text", "")
            # Clean HTML tags from text
            text = re.sub(r"<[^>]+>", "", text)
            text = re.sub(r"&nbsp;", " ", text)
            text = re.sub(r"&amp;", "&", text)

            title = text[:50] + "..." if len(text) > 50 else text
            author = status.get("user", {}).get("screen_name", "")
            created_at = status.get("created_at", "")
            reposts_count = status.get("reposts_count", 0)
            comments_count = status.get("comments_count", 0)
            attitudes_count = status.get("attitudes_count", 0)

            # Parse created_at (Weibo format: "Mon Sep 06 12:00:00 +0800 2026")
            published_at = None
            if created_at:
                try:
                    from datetime import datetime

                    dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
                    published_at = dt.isoformat()
                except (ValueError, TypeError):
                    pass

            return ProcessorResult(
                title=title,
                author=author,
                summary=f"{reposts_count}转发 · {comments_count}评论 · {attitudes_count}赞",
                content_text=text,
                published_at=published_at,
                tags=["微博"],
                source_type=self.source_type,
                source_name=self.source_name,
                url=url,
                metadata={
                    "mid": mid,
                    "reposts_count": reposts_count,
                    "comments_count": comments_count,
                    "attitudes_count": attitudes_count,
                },
                status=ProcessorStatus.success,
            )

        except Exception as e:
            logger.debug("Weibo mobile API failed: %s", e)
            return None

    async def _fetch_via_html(self, url: str, cookies: dict[str, str]) -> ProcessorResult:
        """Fetch Weibo post via HTML parsing (fallback).

        Args:
            url: The Weibo URL.
            cookies: Authentication cookies.

        Returns:
            ProcessorResult with Weibo content.

        """
        import requests
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        if cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())

        response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"

        soup = BeautifulSoup(response.text, "lxml")

        # Try to extract from $render_data script tag
        title = None
        author = None
        content_text = None

        script = soup.find("script", string=re.compile(r"\$render_data"))
        if script and script.string:
            try:
                match = re.search(r"\$render_data\s*=\s*(\[.*?\])\s*;", script.string, re.DOTALL)
                if match:
                    data = json.loads(match.group(1))
                    if data and len(data) > 0:
                        status = data[0].get("status", {})
                        text = status.get("text", "")
                        text = re.sub(r"<[^>]+>", "", text)
                        content_text = text
                        title = text[:50] + "..." if len(text) > 50 else text
                        author = status.get("user", {}).get("screen_name", "")
            except (json.JSONDecodeError, AttributeError) as e:
                logger.debug("Failed to parse $render_data: %s", e)

        # Fallback to meta tags
        if not title:
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                title = og_title["content"].strip()
        if not content_text:
            og_desc = soup.find("meta", property="og:description")
            if og_desc and og_desc.get("content"):
                content_text = og_desc["content"].strip()

        if not title:
            return self._failed_result(
                url, "Could not extract post content (may require login/cookies)"
            )

        return ProcessorResult(
            title=title,
            author=author,
            content_text=content_text,
            tags=["微博"],
            source_type=self.source_type,
            source_name=self.source_name,
            url=url,
            metadata={"platform": "weibo", "note": "May require cookies for full content"},
            status=ProcessorStatus.success if content_text else ProcessorStatus.partial,
        )

    def _extract_post_id(self, url: str) -> str | None:
        """Extract post ID from Weibo URL.

        Args:
            url: Weibo URL.

        Returns:
            Post ID or None.

        """
        patterns = [
            r"weibo\.com/\d+/([A-Za-z0-9]+)",
            r"weibo\.com/detail/(\w+)",
            r"m\.weibo\.cn/detail/(\w+)",
            r"weibo\.com/status/(\w+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

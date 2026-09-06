"""Hupu (虎扑) URL processor — extracts content from Hupu BBS posts.

Handles:
- bbs.hupu.com/{fid}/{tid}.html — forum posts
- bbs.hupu.com/... — other Hupu pages
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
class HupuProcessor(BaseProcessor):
    """Extract content from Hupu (虎扑) BBS posts.

    Uses HTML parsing to extract post title, author, content, and replies.
    Hupu doesn't have a public API, so we parse the HTML directly.
    """

    url_patterns: list[str] = [
        r"bbs\.hupu\.com/\d+/\d+\.html",
        r"bbs\.hupu\.com/.*\.html",
        r"hupu\.com/bbs",
    ]
    priority: int = 10
    source_type: str = "hupu"
    source_name: str = "虎扑"

    async def process(self, url: str, **kwargs: Any) -> ProcessorResult:
        """Extract content from a Hupu post URL.

        Args:
            url: The Hupu URL to process.
            **kwargs: Additional arguments.

        Returns:
            ProcessorResult with Hupu post content.
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
                "Referer": "https://bbs.hupu.com/",
            }

            response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"

            soup = BeautifulSoup(response.text, "lxml")

            # Extract title
            title = None
            title_elem = soup.select_one("h1.post-title, .t-h1, h1")
            if title_elem:
                title = title_elem.get_text(strip=True)
            if not title and soup.title:
                title = soup.title.string.strip()
                # Remove site suffix
                title = re.sub(r"[-_]虎扑.*$", "", title).strip()

            # Extract author
            author = None
            author_elem = soup.select_one(".u-name, .post-author a, .author a")
            if author_elem:
                author = author_elem.get_text(strip=True)

            # Extract main post content
            content_text = None
            content_elem = soup.select_one(".post-content, .quote-content, .t-content")
            if content_elem:
                content_text = content_elem.get_text(separator="\n", strip=True)

            # If no content found, try generic extraction
            if not content_text or len(content_text) < 50:
                # Remove unwanted elements
                for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
                    tag.decompose()
                # Try article or main
                article = soup.select_one("article, .main-content, #container")
                if article:
                    content_text = article.get_text(separator="\n", strip=True)

            # Extract replies (light up count)
            replies = []
            reply_elements = soup.select(".reply-item, .floor-show, .comment-list .comment")
            for reply_elem in reply_elements[:15]:  # Limit to 15 replies
                r_author = reply_elem.select_one(".u-name, .author")
                r_content = reply_elem.select_one(".reply-content, .quote-content, .content")
                r_light = reply_elem.select_one(".light-num, .vote-count")

                if r_content:
                    author_name = r_author.get_text(strip=True) if r_author else "匿名"
                    content = r_content.get_text(strip=True)
                    light = r_light.get_text(strip=True) if r_light else ""
                    if content and len(content) > 5:
                        light_str = f" (亮了 {light})" if light else ""
                        replies.append(f"{author_name}{light_str}: {content}")

            # Combine content
            full_content = content_text or ""
            if replies:
                full_content += "\n\n【亮回复】\n" + "\n".join(replies)

            # Extract tags/node name
            tags = ["虎扑"]
            node_elem = soup.select_one(".b-crumbs a:last-child, .node-name")
            if node_elem:
                node_name = node_elem.get_text(strip=True)
                if node_name and node_name not in ("虎扑", "首页"):
                    tags.append(node_name)

            if not title:
                return self._failed_result(url, "Could not extract title from Hupu page")

            return ProcessorResult(
                title=title,
                author=author,
                summary=f"{len(replies)} 条亮回复" if replies else None,
                content_text=full_content if full_content else None,
                tags=tags,
                source_type=self.source_type,
                source_name=self.source_name,
                url=url,
                metadata={
                    "reply_count": len(replies),
                },
                status=ProcessorStatus.success if full_content else ProcessorStatus.partial,
            )

        except Exception as e:
            logger.exception("HupuProcessor failed for %s", url)
            return self._failed_result(url, str(e))

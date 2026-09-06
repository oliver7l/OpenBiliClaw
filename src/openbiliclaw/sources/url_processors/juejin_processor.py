"""Juejin (掘金) URL processor — extracts content from Juejin articles.

Handles:
- juejin.cn/post/{id} — articles
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
class JuejinProcessor(BaseProcessor):
    """Extract content from Juejin (掘金) articles.

    Uses the Juejin web API to fetch article content. Juejin has a
    public API that returns article details in JSON format.
    """

    url_patterns: list[str] = [
        r"juejin\.cn/post/\d+",
        r"juejin\.cn/editor/\d+",
    ]
    priority: int = 10
    source_type: str = "juejin"
    source_name: str = "掘金"

    async def process(self, url: str, **kwargs: Any) -> ProcessorResult:
        """Extract content from a Juejin article URL.

        Args:
            url: The Juejin article URL.
            **kwargs: Additional arguments.

        Returns:
            ProcessorResult with Juejin article content.
        """
        if not is_safe_url(url):
            return self._failed_result(url, "URL is not safe (internal network)")

        try:
            import requests

            # Extract article ID
            match = re.search(r"juejin\.cn/(?:post|editor)/(\d+)", url)
            if not match:
                return self._failed_result(url, "Could not extract article ID from URL")

            article_id = match.group(1)

            # Fetch article via API
            api_url = "https://api.juejin.cn/content_api/v1/article/detail"
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Content-Type": "application/json",
                "Referer": "https://juejin.cn/",
            }
            payload = {"article_id": article_id}

            response = requests.post(api_url, headers=headers, json=payload, timeout=15)
            response.raise_for_status()
            data = response.json()

            if data.get("err_no") != 0:
                return self._failed_result(url, f"Juejin API error: {data.get('err_msg', 'unknown')}")

            article_info = data.get("data", {}).get("article_info", {})
            author_info = data.get("data", {}).get("author_user_info", {})

            title = article_info.get("title", "")
            author = author_info.get("user_name", "")
            content = article_info.get("mark_content", "") or article_info.get("content", "")
            brief = article_info.get("brief_content", "")
            ctime = article_info.get("ctime")
            view_count = article_info.get("view_count", 0)
            digg_count = article_info.get("digg_count", 0)
            comment_count = article_info.get("comment_count", 0)
            collect_count = article_info.get("collect_count", 0)

            # Extract tags
            tags = ["掘金", "技术"]
            tags_data = data.get("data", {}).get("tags", [])
            for tag in tags_data:
                tag_name = tag.get("tag_name", "")
                if tag_name and tag_name not in tags:
                    tags.append(tag_name)

            # Convert markdown to plain text if needed
            content_text = content
            if content and content.startswith("#"):
                content_text = self._markdown_to_text(content)

            published_at = None
            if ctime:
                from datetime import datetime, timezone
                published_at = datetime.fromtimestamp(int(ctime), tz=timezone.utc).isoformat()

            summary = brief or f"{view_count}阅读 · {digg_count}点赞 · {comment_count}评论"

            return ProcessorResult(
                title=title,
                author=author,
                summary=summary,
                content_text=content_text,
                published_at=published_at,
                tags=tags[:10],
                source_type=self.source_type,
                source_name=self.source_name,
                url=url,
                metadata={
                    "article_id": article_id,
                    "view_count": view_count,
                    "digg_count": digg_count,
                    "comment_count": comment_count,
                    "collect_count": collect_count,
                },
                status=ProcessorStatus.success,
            )

        except Exception as e:
            logger.exception("JuejinProcessor failed for %s", url)
            return self._failed_result(url, str(e))

    def _markdown_to_text(self, markdown: str) -> str:
        """Convert markdown to plain text.

        Args:
            markdown: Markdown content.

        Returns:
            Plain text content.
        """
        text = markdown
        # Remove code blocks
        text = re.sub(r"```[\s\S]*?```", "[代码]", text)
        # Remove headers
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
        # Remove bold/italic
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        text = re.sub(r"\*(.+?)\*", r"\1", text)
        # Remove links
        text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
        # Remove images
        text = re.sub(r"!\[.+?\]\(.+?\)", "[图片]", text)
        # Remove blockquotes
        text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)
        # Clean up
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

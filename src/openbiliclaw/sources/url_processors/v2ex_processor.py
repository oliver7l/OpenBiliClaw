"""V2EX URL processor — extracts content from V2EX topics.

Handles:
- v2ex.com/t/{id} — topic posts with replies
"""

from __future__ import annotations

import logging
import re
from datetime import UTC
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
class V2exProcessor(BaseProcessor):
    """Extract content from V2EX topics.

    Uses the V2EX API (https://www.v2ex.com/api) to fetch topic details
    and replies. No authentication required for public topics.
    """

    url_patterns: list[str] = [
        r"v2ex\.com/t/\d+",
        r"v2ex\.com/t/\d+#",
    ]
    priority: int = 10
    source_type: str = "v2ex"
    source_name: str = "V2EX"

    async def process(self, url: str, **kwargs: Any) -> ProcessorResult:
        """Extract content from a V2EX topic URL.

        Args:
            url: The V2EX URL to process.
            **kwargs: Additional arguments.

        Returns:
            ProcessorResult with V2EX topic content.

        """
        if not is_safe_url(url):
            return self._failed_result(url, "URL is not safe (internal network)")

        try:
            import requests

            # Extract topic ID
            topic_match = re.search(r"v2ex\.com/t/(\d+)", url)
            if not topic_match:
                return self._failed_result(url, "Could not extract topic ID from URL")

            topic_id = topic_match.group(1)

            # Fetch topic details via API
            api_url = f"https://www.v2ex.com/api/topics/show.json?id={topic_id}"
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; OpenBiliClaw/1.0)",
                "Accept": "application/json",
            }
            response = requests.get(api_url, headers=headers, timeout=15)
            response.raise_for_status()
            topics = response.json()

            if not topics:
                return self._failed_result(url, f"Topic {topic_id} not found")

            topic = topics[0]
            title = topic.get("title", "")
            content = topic.get("content", "")
            author = topic.get("member", {}).get("username", "")
            created = topic.get("created")
            replies_count = topic.get("replies", 0)
            node_name = topic.get("node", {}).get("title", "")
            node_id = topic.get("node", {}).get("name", "")

            # Fetch replies
            replies_text = ""
            if replies_count > 0:
                replies_api = f"https://www.v2ex.com/api/replies/show.json?topic_id={topic_id}"
                try:
                    r_resp = requests.get(replies_api, headers=headers, timeout=15)
                    r_resp.raise_for_status()
                    replies = r_resp.json()

                    if replies:
                        replies_text = "\n\n【回复】\n"
                        for i, reply in enumerate(replies[:20], 1):  # Limit to 20 replies
                            r_author = reply.get("member", {}).get("username", "匿名")
                            r_content = reply.get("content", "").strip()
                            r_thanks = reply.get("thanks", 0)
                            if r_content:
                                thanks_str = f" (+{r_thanks})" if r_thanks > 0 else ""
                                replies_text += f"\n{i}. {r_author}{thanks_str}: {r_content}\n"
                except Exception as e:
                    logger.warning("Failed to fetch replies for topic %s: %s", topic_id, e)

            # Combine content
            full_content = content
            if replies_text:
                full_content += replies_text

            published_at = None
            if created:
                from datetime import datetime

                published_at = datetime.fromtimestamp(created, tz=UTC).isoformat()

            tags = ["V2EX", node_name] if node_name else ["V2EX"]

            return ProcessorResult(
                title=title,
                author=author,
                summary=f"{replies_count} 回复 · 节点: {node_name}",
                content_text=full_content,
                published_at=published_at,
                tags=tags,
                source_type=self.source_type,
                source_name=self.source_name,
                url=url,
                metadata={
                    "topic_id": topic_id,
                    "node_id": node_id,
                    "node_name": node_name,
                    "replies_count": replies_count,
                },
                status=ProcessorStatus.success,
            )

        except Exception as e:
            logger.exception("V2exProcessor failed for %s", url)
            return self._failed_result(url, str(e))

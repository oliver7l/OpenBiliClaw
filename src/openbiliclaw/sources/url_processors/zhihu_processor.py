"""Zhihu URL processor — extracts content from Zhihu answers and articles.

Handles:
- zhihu.com/question/{id}/answer/{id} — answers
- zhuanlan.zhihu.com/p/{id} — columns/articles
- zhihu.com/question/{id} — questions (extracts top answer)
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
class ZhihuProcessor(BaseProcessor):
    """Extract content from Zhihu answers and articles.

    Uses the Zhihu web API to fetch structured content. Falls back to
    HTML parsing if API is unavailable.
    """

    url_patterns: list[str] = [
        r"zhihu\.com/question/\d+/answer/\d+",
        r"zhuanlan\.zhihu\.com/p/\d+",
        r"zhihu\.com/question/\d+",
        r"zhihu\.com/p/\d+",
    ]
    priority: int = 10
    source_type: str = "zhihu"
    source_name: str = "知乎"

    async def process(self, url: str, **kwargs: Any) -> ProcessorResult:
        """Extract content from a Zhihu URL.

        Args:
            url: The Zhihu URL to process.
            **kwargs: Additional arguments (cookies can be passed via kwargs).

        Returns:
            ProcessorResult with extracted Zhihu content.

        """
        if not is_safe_url(url):
            return self._failed_result(url, "URL is not safe (internal network)")

        cookies = kwargs.get("cookies", {})

        try:
            # Determine URL type and extract IDs
            answer_match = re.search(r"question/(\d+)/answer/(\d+)", url)
            article_match = re.search(r"zhuanlan\.zhihu\.com/p/(\d+)", url)
            question_match = re.search(r"zhihu\.com/question/(\d+)", url)

            if answer_match:
                return await self._fetch_answer(
                    question_id=answer_match.group(1),
                    answer_id=answer_match.group(2),
                    url=url,
                    cookies=cookies,
                )
            elif article_match:
                return await self._fetch_article(
                    article_id=article_match.group(1),
                    url=url,
                    cookies=cookies,
                )
            elif question_match:
                return await self._fetch_question(
                    question_id=question_match.group(1),
                    url=url,
                    cookies=cookies,
                )
            else:
                return self._failed_result(url, "Unrecognized Zhihu URL format")

        except Exception as e:
            logger.exception("ZhihuProcessor failed for %s", url)
            return self._failed_result(url, str(e))

    async def _fetch_answer(
        self, question_id: str, answer_id: str, url: str, cookies: dict[str, str]
    ) -> ProcessorResult:
        """Fetch a Zhihu answer via API.

        Args:
            question_id: The question ID.
            answer_id: The answer ID.
            url: Original URL.
            cookies: Authentication cookies.

        Returns:
            ProcessorResult with answer content.

        """
        import requests

        api_url = f"https://www.zhihu.com/api/v4/answers/{answer_id}"
        params = {
            "include": (
                "data[*].is_normal,content,voteup_count,comment_count,"
                "created_time,updated_time,author,question"
            ),
        }
        headers = self._get_headers(cookies)

        response = requests.get(api_url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        title = data.get("question", {}).get("title", "")
        author = data.get("author", {}).get("name", "")
        content_html = data.get("content", "")
        content_text = self._html_to_text(content_html)
        created_time = data.get("created_time")
        voteup_count = data.get("voteup_count", 0)
        comment_count = data.get("comment_count", 0)

        published_at = None
        if created_time:
            from datetime import datetime

            published_at = datetime.fromtimestamp(created_time, tz=UTC).isoformat()

        return ProcessorResult(
            title=title,
            author=author,
            summary=f"{voteup_count} 赞同 · {comment_count} 评论",
            content_text=content_text,
            published_at=published_at,
            tags=["知乎", "回答"],
            source_type=self.source_type,
            source_name=self.source_name,
            url=url,
            metadata={
                "question_id": question_id,
                "answer_id": answer_id,
                "voteup_count": voteup_count,
                "comment_count": comment_count,
            },
            status=ProcessorStatus.success,
        )

    async def _fetch_article(
        self, article_id: str, url: str, cookies: dict[str, str]
    ) -> ProcessorResult:
        """Fetch a Zhihu column article via API.

        Args:
            article_id: The article ID.
            url: Original URL.
            cookies: Authentication cookies.

        Returns:
            ProcessorResult with article content.

        """
        import requests

        api_url = f"https://zhuanlan.zhihu.com/api/articles/{article_id}"
        params = {
            "include": "content,voteup_count,comment_count,created,updated,author,title",
        }
        headers = self._get_headers(cookies)

        response = requests.get(api_url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        title = data.get("title", "")
        author = data.get("author", {}).get("name", "")
        content_html = data.get("content", "")
        content_text = self._html_to_text(content_html)
        created = data.get("created")
        voteup_count = data.get("voteup_count", 0)
        comment_count = data.get("comment_count", 0)

        published_at = None
        if created:
            from datetime import datetime

            published_at = datetime.fromtimestamp(created, tz=UTC).isoformat()

        return ProcessorResult(
            title=title,
            author=author,
            summary=f"{voteup_count} 赞同 · {comment_count} 评论",
            content_text=content_text,
            published_at=published_at,
            tags=["知乎", "专栏"],
            source_type=self.source_type,
            source_name=self.source_name,
            url=url,
            metadata={
                "article_id": article_id,
                "voteup_count": voteup_count,
                "comment_count": comment_count,
            },
            status=ProcessorStatus.success,
        )

    async def _fetch_question(
        self, question_id: str, url: str, cookies: dict[str, str]
    ) -> ProcessorResult:
        """Fetch a Zhihu question and its top answer.

        Args:
            question_id: The question ID.
            url: Original URL.
            cookies: Authentication cookies.

        Returns:
            ProcessorResult with question and top answer content.

        """
        import requests

        # Fetch question details
        q_api = f"https://www.zhihu.com/api/v4/questions/{question_id}"
        headers = self._get_headers(cookies)
        q_resp = requests.get(q_api, headers=headers, timeout=15)
        q_resp.raise_for_status()
        q_data = q_resp.json()

        title = q_data.get("title", "")
        detail = q_data.get("detail", "")
        answer_count = q_data.get("answer_count", 0)
        follower_count = q_data.get("follower_count", 0)

        # Fetch top answer
        answers_api = f"https://www.zhihu.com/api/v4/questions/{question_id}/answers"
        params = {
            "include": "data[*].content,voteup_count,author,created_time",
            "limit": 1,
            "sort_by": "default",
        }
        a_resp = requests.get(answers_api, headers=headers, params=params, timeout=15)
        a_resp.raise_for_status()
        a_data = a_resp.json()

        content_text = self._html_to_text(detail)
        author = ""
        if a_data.get("data"):
            top_answer = a_data["data"][0]
            author = top_answer.get("author", {}).get("name", "")
            answer_content = self._html_to_text(top_answer.get("content", ""))
            content_text = (
                f"【问题描述】\n{content_text}\n\n【最高赞回答 by {author}】\n{answer_content}"
            )

        return ProcessorResult(
            title=title,
            author=author,
            summary=f"{answer_count} 回答 · {follower_count} 关注",
            content_text=content_text,
            tags=["知乎", "问题"],
            source_type=self.source_type,
            source_name=self.source_name,
            url=url,
            metadata={
                "question_id": question_id,
                "answer_count": answer_count,
                "follower_count": follower_count,
            },
            status=ProcessorStatus.success,
        )

    def _get_headers(self, cookies: dict[str, str]) -> dict[str, str]:
        """Build request headers with optional cookies.

        Args:
            cookies: Cookie dict.

        Returns:
            Headers dict.

        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.zhihu.com/",
        }
        if cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
        return headers

    def _html_to_text(self, html: str) -> str:
        """Convert HTML content to plain text.

        Args:
            html: HTML content string.

        Returns:
            Plain text content.

        """
        if not html:
            return ""
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "lxml")
            # Replace <br> with newlines
            for br in soup.find_all("br"):
                br.replace_with("\n")
            # Replace <p> with double newlines
            for p in soup.find_all("p"):
                p.insert_after("\n\n")
            text = soup.get_text(separator="\n", strip=True)
            # Clean up excessive newlines
            import re as _re

            text = _re.sub(r"\n{3,}", "\n\n", text)
            return text.strip()
        except Exception:
            # Fallback: simple tag stripping
            import re as _re

            text = _re.sub(r"<[^>]+>", "", html)
            text = _re.sub(r"&nbsp;", " ", text)
            text = _re.sub(r"&amp;", "&", text)
            text = _re.sub(r"&lt;", "<", text)
            text = _re.sub(r"&gt;", ">", text)
            return text.strip()

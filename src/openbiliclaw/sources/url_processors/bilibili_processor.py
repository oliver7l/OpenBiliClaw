"""Bilibili URL processor — extracts content from Bilibili videos and articles.

Handles:
- bilibili.com/video/BV{id} — videos (extracts metadata + subtitles if available)
- bilibili.com/read/cv{id} — articles/columns
- b23.tv/{short} — short links (follows redirect)
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
class BilibiliProcessor(BaseProcessor):
    """Extract content from Bilibili videos and articles.

    Uses the Bilibili web API to fetch video metadata. For videos,
    extracts title, description, author, stats, and subtitles if available.
    Does NOT download video files (too heavy).
    """

    url_patterns: list[str] = [
        r"bilibili\.com/video/BV\w+",
        r"bilibili\.com/video/av\d+",
        r"bilibili\.com/read/cv\d+",
        r"b23\.tv/\w+",
    ]
    priority: int = 10
    source_type: str = "bilibili"
    source_name: str = "哔哩哔哩"

    async def process(self, url: str, **kwargs: Any) -> ProcessorResult:
        """Extract content from a Bilibili URL.

        Args:
            url: The Bilibili URL to process.
            **kwargs: Additional arguments (cookies, SESSDATA can be passed).

        Returns:
            ProcessorResult with Bilibili content.
        """
        if not is_safe_url(url):
            return self._failed_result(url, "URL is not safe (internal network)")

        cookies = kwargs.get("cookies", {})

        try:
            # Follow short links first
            if "b23.tv" in url:
                url = self._follow_redirect(url)

            # Determine URL type
            video_match = re.search(r"video/(BV\w+|av\d+)", url)
            article_match = re.search(r"read/cv(\d+)", url)

            if video_match:
                return await self._fetch_video(
                    video_id=video_match.group(1),
                    url=url,
                    cookies=cookies,
                )
            elif article_match:
                return await self._fetch_article(
                    article_id=article_match.group(1),
                    url=url,
                    cookies=cookies,
                )
            else:
                return self._failed_result(url, "Unrecognized Bilibili URL format")

        except Exception as e:
            logger.exception("BilibiliProcessor failed for %s", url)
            return self._failed_result(url, str(e))

    def _follow_redirect(self, url: str) -> str:
        """Follow b23.tv short link redirect.

        Args:
            url: Short URL.

        Returns:
            Resolved full URL.
        """
        try:
            import requests
            resp = requests.head(url, allow_redirects=True, timeout=10)
            return resp.url
        except Exception:
            return url

    async def _fetch_video(
        self, video_id: str, url: str, cookies: dict[str, str]
    ) -> ProcessorResult:
        """Fetch Bilibili video metadata via API.

        Args:
            video_id: BV or av ID.
            url: Original URL.
            cookies: Authentication cookies.

        Returns:
            ProcessorResult with video metadata and subtitle content.
        """
        import requests

        # Build API URL
        if video_id.startswith("BV"):
            api_url = f"https://api.bilibili.com/x/web-interface/view?bvid={video_id}"
        else:
            aid = video_id.replace("av", "")
            api_url = f"https://api.bilibili.com/x/web-interface/view?aid={aid}"

        headers = self._get_headers(cookies)
        response = requests.get(api_url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()

        if data.get("code") != 0:
            return self._failed_result(url, f"Bilibili API error: {data.get('message', 'unknown')}")

        video_data = data.get("data", {})
        title = video_data.get("title", "")
        desc = video_data.get("desc", "")
        owner = video_data.get("owner", {}).get("name", "")
        stat = video_data.get("stat", {})
        cid = video_data.get("cid", "")
        aid = video_data.get("aid", "")
        bvid = video_data.get("bvid", "")
        pubdate = video_data.get("pubdate")
        duration = video_data.get("duration", 0)
        tags_data = video_data.get("tag", "").split(",") if video_data.get("tag") else []

        # Format stats
        views = self._format_number(stat.get("view", 0))
        likes = self._format_number(stat.get("like", 0))
        coins = self._format_number(stat.get("coin", 0))
        favorites = self._format_number(stat.get("favorite", 0))
        comments = self._format_number(stat.get("reply", 0))

        summary = f"{views}播放 · {likes}点赞 · {coins}投币 · {favorites}收藏 · {comments}评论"

        # Try to fetch subtitles
        content_text = desc
        subtitle_text = self._fetch_subtitles(aid, cid, cookies)
        if subtitle_text:
            content_text = f"【视频简介】\n{desc}\n\n【字幕内容】\n{subtitle_text}"

        published_at = None
        if pubdate:
            from datetime import datetime, timezone
            published_at = datetime.fromtimestamp(pubdate, tz=timezone.utc).isoformat()

        # Format duration
        duration_str = f"{duration // 60}:{duration % 60:02d}" if duration > 0 else ""

        tags = ["B站", "视频"] + tags_data[:5]

        return ProcessorResult(
            title=title,
            author=owner,
            summary=summary,
            content_text=content_text,
            published_at=published_at,
            tags=tags,
            source_type=self.source_type,
            source_name=self.source_name,
            url=url,
            media=[f"https://www.bilibili.com/video/{bvid}"],
            metadata={
                "bvid": bvid,
                "aid": aid,
                "cid": cid,
                "duration": duration_str,
                "views": stat.get("view", 0),
                "likes": stat.get("like", 0),
                "coins": stat.get("coin", 0),
                "favorites": stat.get("favorite", 0),
                "comments": stat.get("reply", 0),
            },
            status=ProcessorStatus.success,
        )

    async def _fetch_article(
        self, article_id: str, url: str, cookies: dict[str, str]
    ) -> ProcessorResult:
        """Fetch Bilibili column article via API.

        Args:
            article_id: Article CV ID.
            url: Original URL.
            cookies: Authentication cookies.

        Returns:
            ProcessorResult with article content.
        """
        import requests

        api_url = f"https://api.bilibili.com/x/article/viewinfo?id={article_id}"
        headers = self._get_headers(cookies)
        response = requests.get(api_url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()

        if data.get("code") != 0:
            return self._failed_result(url, f"Bilibili article API error: {data.get('message')}")

        article_data = data.get("data", {})
        title = article_data.get("title", "")
        author_name = article_data.get("author_name", "")
        content = article_data.get("content", "")
        summary = article_data.get("summary", "")
        publish_time = article_data.get("publish_time")
        stats = article_data.get("stats", {})

        # Convert HTML content to text
        content_text = self._html_to_text(content)

        published_at = None
        if publish_time:
            from datetime import datetime, timezone
            published_at = datetime.fromtimestamp(publish_time, tz=timezone.utc).isoformat()

        views = self._format_number(stats.get("view", 0))
        likes = self._format_number(stats.get("like", 0))

        return ProcessorResult(
            title=title,
            author=author_name,
            summary=summary or f"{views}阅读 · {likes}点赞",
            content_text=content_text,
            published_at=published_at,
            tags=["B站", "专栏"],
            source_type=self.source_type,
            source_name=self.source_name,
            url=url,
            metadata={
                "article_id": article_id,
                "views": stats.get("view", 0),
                "likes": stats.get("like", 0),
            },
            status=ProcessorStatus.success,
        )

    def _fetch_subtitles(self, aid: str, cid: str, cookies: dict[str, str]) -> str | None:
        """Fetch video subtitles if available.

        Args:
            aid: Video AID.
            cid: Video CID.
            cookies: Authentication cookies.

        Returns:
            Subtitle text, or None if not available.
        """
        try:
            import requests

            # Get subtitle list
            player_api = f"https://api.bilibili.com/x/player/v2?aid={aid}&cid={cid}"
            headers = self._get_headers(cookies)
            resp = requests.get(player_api, headers=headers, timeout=10)
            data = resp.json()

            subtitles = data.get("data", {}).get("subtitle", {}).get("subtitles", [])
            if not subtitles:
                return None

            # Fetch first subtitle (prefer Chinese)
            subtitle_url = None
            for sub in subtitles:
                if "zh" in sub.get("lan", "").lower() or "ai" in sub.get("lan", "").lower():
                    subtitle_url = sub.get("subtitle_url")
                    break
            if not subtitle_url and subtitles:
                subtitle_url = subtitles[0].get("subtitle_url")

            if not subtitle_url:
                return None

            # Fetch subtitle content
            if subtitle_url.startswith("//"):
                subtitle_url = "https:" + subtitle_url
            sub_resp = requests.get(subtitle_url, timeout=10)
            sub_data = sub_resp.json()

            # Extract text from subtitle JSON
            lines = []
            for item in sub_data.get("body", []):
                text = item.get("content", "").strip()
                if text:
                    lines.append(text)

            return "\n".join(lines) if lines else None

        except Exception as e:
            logger.debug("Failed to fetch subtitles: %s", e)
            return None

    def _get_headers(self, cookies: dict[str, str]) -> dict[str, str]:
        """Build request headers."""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.bilibili.com/",
        }
        if cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
        return headers

    def _format_number(self, num: int) -> str:
        """Format large numbers for display.

        Args:
            num: Number to format.

        Returns:
            Formatted string (e.g., 1.2万, 3.4亿).
        """
        if num >= 100000000:
            return f"{num / 100000000:.1f}亿"
        elif num >= 10000:
            return f"{num / 10000:.1f}万"
        return str(num)

    def _html_to_text(self, html: str) -> str:
        """Convert HTML to plain text."""
        if not html:
            return ""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            for br in soup.find_all("br"):
                br.replace_with("\n")
            for p in soup.find_all("p"):
                p.insert_after("\n\n")
            text = soup.get_text(separator="\n", strip=True)
            import re as _re
            return _re.sub(r"\n{3,}", "\n\n", text).strip()
        except Exception:
            import re as _re
            return _re.sub(r"<[^>]+>", "", html).strip()

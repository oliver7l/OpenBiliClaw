"""YouTube URL processor — extracts content from YouTube videos.

Handles:
- youtube.com/watch?v={id} — videos
- youtu.be/{id} — short links
- youtube.com/shorts/{id} — shorts
- youtube.com/playlist?list={id} — playlists (extracts first video)
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
class YouTubeProcessor(BaseProcessor):
    """Extract content from YouTube videos.

    Uses yt-dlp to fetch video metadata and subtitles (if available).
    Does NOT download video files (too heavy). Subtitles are preferred
    for content extraction.
    """

    url_patterns: list[str] = [
        r"youtube\.com/watch\?v=",
        r"youtu\.be/[a-zA-Z0-9_-]+",
        r"youtube\.com/shorts/[a-zA-Z0-9_-]+",
        r"youtube\.com/embed/[a-zA-Z0-9_-]+",
    ]
    priority: int = 10
    source_type: str = "youtube"
    source_name: str = "YouTube"

    async def process(self, url: str, **kwargs: Any) -> ProcessorResult:
        """Extract content from a YouTube video URL.

        Args:
            url: The YouTube URL to process.
            **kwargs: Additional arguments.

        Returns:
            ProcessorResult with YouTube video metadata and transcript.

        """
        if not is_safe_url(url):
            return self._failed_result(url, "URL is not safe (internal network)")

        try:
            # Extract video ID
            video_id = self._extract_video_id(url)
            if not video_id:
                return self._failed_result(url, "Could not extract video ID from URL")

            # Use yt-dlp to fetch metadata
            metadata, transcript = self._fetch_with_ytdlp(url)

            if not metadata:
                return self._failed_result(url, "Failed to fetch video metadata with yt-dlp")

            title = metadata.get("title", "")
            author = metadata.get("uploader") or metadata.get("channel", "")
            description = metadata.get("description", "")
            duration = metadata.get("duration", 0)
            view_count = metadata.get("view_count", 0)
            like_count = metadata.get("like_count", 0)
            upload_date = metadata.get("upload_date", "")
            categories = metadata.get("categories", [])
            tags = metadata.get("tags", [])

            # Build content text
            content_text = description
            if transcript:
                content_text = f"【视频简介】\n{description}\n\n【字幕/转录】\n{transcript}"

            # Format stats
            views_str = self._format_number(view_count)
            likes_str = self._format_number(like_count) if like_count else "N/A"
            duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else ""

            summary = f"{views_str}次观看 · {duration_str} · {likes_str}赞"

            # Format upload date
            published_at = None
            if upload_date and len(upload_date) == 8:
                published_at = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}T00:00:00Z"

            # Combine tags
            all_tags = ["YouTube", "视频"] + categories[:3] + tags[:5]
            # Deduplicate while preserving order
            seen = set()
            unique_tags = []
            for t in all_tags:
                if t and t not in seen:
                    seen.add(t)
                    unique_tags.append(t)

            return ProcessorResult(
                title=title,
                author=author,
                summary=summary,
                content_text=content_text,
                published_at=published_at,
                tags=unique_tags[:10],
                source_type=self.source_type,
                source_name=self.source_name,
                url=url,
                media=[f"https://www.youtube.com/watch?v={video_id}"],
                metadata={
                    "video_id": video_id,
                    "duration": duration_str,
                    "view_count": view_count,
                    "like_count": like_count,
                    "has_transcript": bool(transcript),
                },
                status=ProcessorStatus.success,
            )

        except Exception as e:
            logger.exception("YouTubeProcessor failed for %s", url)
            return self._failed_result(url, str(e))

    def _extract_video_id(self, url: str) -> str | None:
        """Extract video ID from various YouTube URL formats.

        Args:
            url: YouTube URL.

        Returns:
            Video ID or None.

        """
        patterns = [
            r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})",
            r"youtube\.com/embed/([a-zA-Z0-9_-]{11})",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def _fetch_with_ytdlp(self, url: str) -> tuple[dict[str, Any] | None, str | None]:
        """Fetch video metadata and transcript using yt-dlp.

        Args:
            url: YouTube URL.

        Returns:
            Tuple of (metadata dict, transcript text or None).

        """
        try:
            import yt_dlp

            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "extract_flat": False,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": ["zh", "en", "zh-Hans", "zh-CN"],
                "subtitlesformat": "srt",
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            if not info:
                return None, None

            metadata = {
                "title": info.get("title"),
                "uploader": info.get("uploader"),
                "channel": info.get("channel"),
                "description": info.get("description", ""),
                "duration": info.get("duration", 0),
                "view_count": info.get("view_count", 0),
                "like_count": info.get("like_count", 0),
                "upload_date": info.get("upload_date", ""),
                "categories": info.get("categories", []),
                "tags": info.get("tags", []),
            }

            # Try to get subtitles from requested_subtitles
            transcript = None
            subtitles = info.get("requested_subtitles") or {}
            if subtitles:
                # Get first available subtitle
                for _lang, sub_info in subtitles.items():
                    sub_url = sub_info.get("url")
                    if sub_url:
                        transcript = self._fetch_subtitle_text(sub_url)
                        if transcript:
                            break

            return metadata, transcript

        except ImportError:
            logger.warning("yt-dlp not installed, falling back to basic metadata")
            return self._fetch_basic_metadata(url), None
        except Exception as e:
            logger.warning("yt-dlp fetch failed: %s", e)
            return None, None

    def _fetch_basic_metadata(self, url: str) -> dict[str, Any] | None:
        """Fetch basic metadata via oEmbed API (no yt-dlp required).

        Args:
            url: YouTube URL.

        Returns:
            Metadata dict or None.

        """
        try:
            import requests

            oembed_url = f"https://www.youtube.com/oembed?url={url}&format=json"
            resp = requests.get(oembed_url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return {
                "title": data.get("title"),
                "uploader": data.get("author_name"),
                "description": "",
                "duration": 0,
                "view_count": 0,
                "like_count": 0,
                "upload_date": "",
                "categories": [],
                "tags": [],
            }
        except Exception:
            return None

    def _fetch_subtitle_text(self, subtitle_url: str) -> str | None:
        """Fetch and parse subtitle file.

        Args:
            subtitle_url: URL to subtitle file (SRT/VTT format).

        Returns:
            Plain text transcript or None.

        """
        try:
            import requests

            resp = requests.get(subtitle_url, timeout=10)
            resp.raise_for_status()
            content = resp.text

            # Parse SRT/VTT: remove timestamps and numbers
            lines = []
            for line in content.split("\n"):
                line = line.strip()
                # Skip empty lines, sequence numbers, and timestamp lines
                if not line or line.isdigit() or "-->" in line:
                    continue
                # Remove VTT header
                if (
                    line.startswith("WEBVTT")
                    or line.startswith("Kind:")
                    or line.startswith("Language:")
                ):
                    continue
                lines.append(line)

            return "\n".join(lines) if lines else None
        except Exception:
            return None

    def _format_number(self, num: int) -> str:
        """Format large numbers for display."""
        if num >= 1000000:
            return f"{num / 1000000:.1f}M"
        elif num >= 1000:
            return f"{num / 1000:.1f}K"
        return str(num)

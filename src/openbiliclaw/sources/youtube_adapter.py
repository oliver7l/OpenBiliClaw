"""YouTube yt-dlp source adapter.

Fetches personalized YouTube feeds (recommended, subscriptions, trending)
using yt-dlp with browser cookies.  yt-dlp must be installed and accessible
(``which yt-dlp``).  The adapter uses ``--cookies-from-browser chrome`` to
piggyback on the browser's YouTube session, so no API key is needed.

Feed types (set via ``recipe.strategy``):
  - ``"recommended"``    — YouTube home feed (/feed/recommended)
  - ``"subscriptions"``  — subscription feed (/feed/subscriptions)
  - ``"trending"``       — trending search (ytsearch10:trending)
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.core.contracts import DiscoveredContent
    from openbiliclaw.sources.protocol import SourceRecipe

logger = logging.getLogger(__name__)

_YT_DLP_CMD = "/opt/homebrew/bin/yt-dlp"

_FEED_URLS: dict[str, str] = {
    "recommended": "https://www.youtube.com/feed/recommended",
    "subscriptions": "https://www.youtube.com/feed/subscriptions",
    "history": "https://www.youtube.com/feed/history",
    "liked": "https://www.youtube.com/playlist?list=LL",
    "watchlater": "https://www.youtube.com/playlist?list=WL",
    "trending": "ytsearch10:trending",
}

_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})")


def _extract_video_id(url: str) -> str | None:
    m = _VIDEO_ID_RE.search(url)
    return m.group(1) if m else None


def _parse_line(line: str) -> dict[str, object] | None:
    """Parse a single yt-dlp --print output line."""
    line = line.strip()
    if not line:
        return None
    parts = line.split("|", 3)
    if len(parts) < 4:
        return None
    title, uploader, view_count_str, url = [p.strip() for p in parts]
    return {
        "title": title,
        "uploader": None if uploader == "NA" else uploader,
        "view_count": int(view_count_str) if view_count_str not in ("NA", "") else None,
        "url": url,
    }


async def _run_ytdlp(url: str, *, timeout: int = 60) -> list[str]:
    """Run yt-dlp and return output lines (async)."""
    cmd = [
        _YT_DLP_CMD,
        "--cookies-from-browser",
        "chrome",
        "--print",
        "%(title)s|%(uploader)s|%(view_count)s|%(webpage_url)s",
        "--flat-playlist",
        "--min-sleep-interval",
        "1",
        "--max-sleep-interval",
        "3",
        url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            logger.warning("yt-dlp exited with code %d: %s", proc.returncode, stderr_text[:200])
            return []
        text = stdout.decode("utf-8", errors="replace")
        return [line for line in text.split("\n") if line.strip()]
    except TimeoutError:
        logger.warning("yt-dlp timed out after %ds for %s", timeout, url)
        return []
    except FileNotFoundError:
        logger.error("yt-dlp not found at %s", _YT_DLP_CMD)
        return []
    except Exception:
        logger.exception("yt-dlp failed for %s", url)
        return []


class YtDlpAdapter:
    """YouTube adapter using yt-dlp for personalized feed fetching."""

    @property
    def source_type(self) -> str:
        return "youtube"

    async def fetch(
        self,
        recipe: SourceRecipe,
        profile: object | None = None,
        limit: int = 20,
    ) -> list[DiscoveredContent]:
        """Fetch YouTube feed using yt-dlp.

        ``recipe.strategy`` selects the feed type (recommended, subscriptions,
        trending, etc.).  Falls back to ``"recommended"`` when unknown.
        """
        strategy = recipe.strategy or "recommended"
        feed_url = _FEED_URLS.get(strategy)
        if feed_url is None:
            logger.warning("Unknown YouTube strategy %r, falling back to recommended", strategy)
            feed_url = _FEED_URLS["recommended"]

        lines = await _run_ytdlp(feed_url)
        if not lines:
            logger.warning("YtDlpAdapter: no data returned for %s/%s", recipe.name, strategy)
            return []

        from openbiliclaw.core.contracts import DiscoveredContent

        items: list[DiscoveredContent] = []
        for line in lines[:limit]:
            parsed = _parse_line(line)
            if parsed is None:
                continue
            title = str(parsed["title"])
            url = str(parsed["url"])
            video_id = _extract_video_id(url)
            if not video_id:
                continue

            items.append(
                DiscoveredContent(
                    content_id=video_id,
                    content_url=url,
                    source_platform="youtube",
                    title=title,
                    description="",
                    author_name=str(parsed["uploader"]) if parsed["uploader"] else "",
                    up_name=str(parsed["uploader"]) if parsed["uploader"] else "",
                    content_type="video",
                    view_count=int(str(parsed.get("view_count", 0) or 0)),
                    topic_key=str(strategy),
                )
            )

        logger.info(
            "YtDlpAdapter: fetched %d/%d items from youtube/%s",
            len(items),
            len(lines),
            strategy,
        )
        return items

"""Content identity normalization for offline evaluation.

Unifies content keys across the three places a piece of content appears:

- ``events.url``            -> ``https://www.bilibili.com/video/BV1xxx``
- ``discovery_candidates.candidate_key`` -> ``bilibili:BV1xxx``
- ``recommendations.bvid``  -> ``BV1xxx`` or bare ``1xxx`` (legacy forms)

The canonical key used throughout the offline eval is ``platform:content_id``
(e.g. ``bilibili:BV1xxx``) — lowercase platform, exact content id.
"""

from __future__ import annotations

import re

_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")

# substring -> canonical platform
_PLATFORM_SUBSTRINGS: list[tuple[str, str]] = [
    ("bilibili.com", "bilibili"),
    ("bilibili", "bilibili"),
    ("xiaohongshu.com", "xiaohongshu"),
    ("xiaohongshu", "xiaohongshu"),
    ("douyin.com", "douyin"),
    ("douyin", "douyin"),
    ("youtube.com", "youtube"),
    ("youtu.be", "youtube"),
    ("zhihu.com", "zhihu"),
    ("x.com", "x"),
    ("twitter.com", "x"),
]


def normalize_bvid(raw: str | None) -> str | None:
    """Extract the canonical BV id from a bvid / url / candidate_key string."""
    if not raw:
        return None
    m = _BV_RE.search(raw)
    return m.group(1) if m else None


def url_to_platform(url: str | None) -> str | None:
    if not url:
        return None
    for needle, platform in _PLATFORM_SUBSTRINGS:
        if needle in url:
            return platform
    return None


def to_content_key(platform: str | None, content_id: str | None) -> str | None:
    if not platform or not content_id:
        return None
    return f"{platform.lower()}:{content_id}"


def content_key_from_url(url: str | None) -> str | None:
    """Map an event URL to a canonical content key.

    Bilibili URLs are resolved through the BV id (stable across URL shapes);
    other platforms fall back to ``platform:<last-path-segment>``.
    """
    if not url:
        return None
    bv = normalize_bvid(url)
    if bv:
        return to_content_key("bilibili", bv)
    platform = url_to_platform(url)
    if not platform:
        return None
    seg = url.rstrip("/").split("/")[-1].split("?")[0].split("#")[0]
    return to_content_key(platform, seg) if seg else None


def key_to_bvid(content_key: str | None) -> str | None:
    """Strip a content key back to its raw id (for DB lookups)."""
    if not content_key or ":" not in content_key:
        return content_key
    return content_key.split(":", 1)[1]

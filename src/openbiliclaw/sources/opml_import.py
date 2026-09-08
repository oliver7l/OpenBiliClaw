"""OPML feed-list import utility.

Reads an OPML 2.0 file (e.g. BestBlogs_RSS_ALL.opml) and extracts RSS/Atom
feed subscriptions.  These can be merged into the project's RSS polling
configuration so that curated feeds are automatically fetched.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any

logger = logging.getLogger(__name__)


def parse_opml(file_path: str) -> list[dict[str, str]]:
    """Parse an OPML 2.0 file and return a list of RSS subscriptions.

    Each entry has ``{"name": …, "url": …}`` shape compatible with
    ``SchedulerConfig.rss_subscriptions``.

    Only entries with a valid ``xmlUrl`` attribute are included.
    """
    tree = ET.parse(file_path)
    root = tree.getroot()

    body = root.find("body")
    if body is None:
        logger.warning("OPML file %s has no <body>", file_path)
        return []

    subscriptions: list[dict[str, str]] = []
    _walk_outlines(body, subscriptions)

    logger.info("OPML import: %d feeds from %s", len(subscriptions), file_path)
    return subscriptions


def _walk_outlines(parent: Any, result: list[dict[str, str]]) -> None:
    """Recursively walk <outline> elements and collect RSS feeds."""
    for outline in parent.findall("outline"):
        xml_url = outline.get("xmlUrl", "") or ""
        if xml_url:
            title = outline.get("title", "") or outline.get("text", "") or ""
            result.append({"name": title.strip(), "url": xml_url.strip()})
        # Nested outlines (OPML supports arbitrary nesting)
        _walk_outlines(outline, result)


def filter_subscriptions(
    subscriptions: list[dict[str, str]],
    *,
    exclude_keywords: list[str] | None = None,
    max_feeds: int = 0,
) -> list[dict[str, str]]:
    """Filter a list of subscriptions.

    Args:
        subscriptions: Input subscription list.
        exclude_keywords: Remove feeds whose name or URL contains any of these.
        max_feeds: If > 0, return only the first N feeds.

    Returns:
        Filtered subscription list.

    """
    result = subscriptions
    if exclude_keywords:
        result = [
            s
            for s in result
            if not any(
                kw.lower() in s["name"].lower() or kw.lower() in s["url"].lower()
                for kw in exclude_keywords
            )
        ]
    if max_feeds > 0:
        result = result[:max_feeds]
    return result


def subscriptions_to_toml(subscriptions: list[dict[str, str]]) -> str:
    """Format subscriptions as TOML ``rss_subscriptions`` entries."""
    lines = ["rss_subscriptions = ["]
    for sub in subscriptions:
        name = sub["name"].replace('"', '\\"')
        url = sub["url"].replace('"', '\\"')
        lines.append(f'    {{name = "{name}", url = "{url}"}},')
    lines.append("]")
    return "\n".join(lines)


# Convenience path for the bundled BestBlogs OPML
_BESTBLOGS_OPML_PATH = (
    "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/rss/BestBlogs_RSS_ALL.opml"
)


def load_bestblogs_subscriptions(
    *,
    exclude_keywords: list[str] | None = None,
    max_feeds: int = 0,
) -> list[dict[str, str]]:
    """Load subscriptions from the bundled BestBlogs OPML file.

    Args:
        exclude_keywords: Remove feeds matching these keywords.
        max_feeds: If > 0, limit to first N feeds.

    Returns:
        List of RSS subscriptions.

    """
    import os

    if not os.path.exists(_BESTBLOGS_OPML_PATH):
        logger.warning("BestBlogs OPML not found at %s", _BESTBLOGS_OPML_PATH)
        return []
    subs = parse_opml(_BESTBLOGS_OPML_PATH)
    return filter_subscriptions(subs, exclude_keywords=exclude_keywords, max_feeds=max_feeds)

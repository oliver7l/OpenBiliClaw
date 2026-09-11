"""豆瓣（Douban）文章 feed 源 adapter。

拉取豆瓣官方 RSS/ATOM feed（个人新评论 / 全站最新评论 / 小组讨论 / 日记），
并归一化为 ``DiscoveredContent`` 供阅读库 / 推荐管线消费。

与 ``RssAdapter`` 的关键区别：豆瓣个人 / 部分小组 feed 需要登录 cookie，
这里用 ``requests.get`` 携带 cookie 取原始 XML 后交给 feedparser 解析。

feed URL 由 ``recipe.config`` 按 ``feed_kind`` 拼：
- ``comment``  → https://douban.com/feed/people/{uid}/
- ``review``   → https://douban.com/feed/review/latest
- ``group``    → https://www.douban.com/feed/group/{group_id}/discussion
- ``diary``    → {rsshub_url}/douban/user/{uid}/status（官方 RSS 有限，走自部署 RSSHub）

diary（及后续 RSSHub 聚合源）URL 的 base 取自 ``[sources.douban].rsshub_url``
（默认 http://127.0.0.1:1200，本地 Docker 自部署 RSSHub），不直接用官方实例。
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.sources.protocol import SourceRecipe

logger = logging.getLogger(__name__)

# 与 rss_adapter 同款独立线程池（feedparser 同步网络抓取，避免占 HTTP 池）
_FEED_PARSE_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = (
    concurrent.futures.ThreadPoolExecutor(
        max_workers=4,
        thread_name_prefix="dbfeed",
    )
)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _feed_url(kind: str, uid: str = "", group_id: str = "", rsshub_url: str = "") -> str:
    """按 feed_kind 拼豆瓣 feed URL。diary 走本地自部署 RSSHub（无则回退官方）。"""
    kind = (kind or "review").strip().lower()
    if kind == "comment" and uid:
        return f"https://douban.com/feed/people/{uid}/"
    if kind == "group" and group_id:
        return f"https://www.douban.com/feed/group/{group_id}/discussion"
    if kind == "diary" and uid:
        base = (rsshub_url or "").strip().rstrip("/") or "http://127.0.0.1:1200"
        # RSSHub 豆瓣用户广播路由：/douban/people/:userid/status
        return f"{base}/douban/people/{uid}/status"
    # 默认：全站最新评论
    return "https://douban.com/feed/review/latest"


class DoubanFeedAdapter:
    """从豆瓣 RSS feed 拉取文章的 source adapter。"""

    def __init__(self, cookie: str = "", rsshub_url: str = "") -> None:
        self._cookie = (cookie or "").strip()
        # 本地自部署 RSSHub base（diary 等聚合源用）；空则 fetch 时回退默认。
        self._rsshub_url = (rsshub_url or "").strip().rstrip("/")

    @property
    def source_type(self) -> str:
        return "douban_feed"

    @property
    def source_name(self) -> str:
        return "豆瓣feed"

    # ── Cookie 字典化（供 requests 使用）────────────────────────
    def _cookie_jar(self) -> dict[str, str] | None:
        """把 cookie 字符串解析成 requests 的 cookie dict。
        格式同浏览器 Netscape（name=value; name2=value2）或 JSON。
        """
        raw = self._cookie
        if not raw:
            return None
        # 尝试 JSON（[{"name":..., "value":...}, ...]）
        if raw.strip().startswith("["):
            try:
                import json

                arr = json.loads(raw)
                out: dict[str, str] = {}
                for obj in arr:
                    n = obj.get("name")
                    v = obj.get("value")
                    if n and v is not None:
                        out[str(n)] = str(v)
                return out or None
            except Exception:  # noqa: BLE001
                logger.debug("豆瓣 cookie 非 JSON，走 Netscape 解析")
        # Netscape：name=value; name2=value2
        out = {}
        for pair in raw.split(";"):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                if k.strip():
                    out[k.strip()] = v.strip()
        return out or None

    def _fetch_xml(self, url: str) -> str:
        """带 cookie 抓取原始 XML。"""
        resp = requests.get(
            url,
            cookies=self._cookie_jar(),
            timeout=20,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/rss+xml, application/atom+xml, text/xml",
            },
        )
        resp.raise_for_status()
        return resp.text

    async def fetch(
        self,
        recipe: SourceRecipe,
        profile: object | None = None,
        limit: int = 20,
    ) -> list[DiscoveredContent]:
        """按 recipe.config 的 feed_kind 拼 URL 抓取并解析。"""
        cfg = getattr(recipe, "config", None) or {}
        feed_kind = str(cfg.get("feed_kind", "review") or "review")
        uid = str(cfg.get("uid", "") or "")
        group_id = str(cfg.get("group_id", "") or "")
        feed_name = recipe.name or cfg.get("name", "") or "豆瓣feed"
        # rsshub_url 优先 recipe.config，其次构造时传入的 rsshub_url。
        rsshub_url = str(cfg.get("rsshub_url", "") or "") or self._rsshub_url
        url = _feed_url(feed_kind, uid=uid, group_id=group_id, rsshub_url=rsshub_url)

        try:
            xml = await asyncio_run_in_executor(self._fetch_xml, url)
        except requests.RequestException as exc:
            logger.warning("DoubanFeedAdapter: 拉取失败 %s: %s", url, exc)
            return []
        except Exception as exc:  # noqa: BLE001
            logger.warning("DoubanFeedAdapter: 拉取异常 %s: %s", url, exc)
            return []

        import feedparser

        feed = feedparser.parse(xml)
        if feed.bozo and not feed.entries:
            logger.warning("DoubanFeedAdapter: feed 解析失败 %s: %s", url, feed.bozo_exception)
            return []

        from openbiliclaw.discovery.engine import DiscoveredContent

        items: list[DiscoveredContent] = []
        for entry in feed.entries[:limit]:
            title = (getattr(entry, "title", "") or "").strip()
            link = (getattr(entry, "link", "") or "").strip()
            if not title or not link:
                continue

            author = ""
            if hasattr(entry, "author") and entry.author:
                author = entry.author.strip()
            elif hasattr(entry, "authors") and entry.authors:
                author = entry.authors[0].get("name", "")

            raw_summary = ""
            if hasattr(entry, "summary") and entry.summary:
                raw_summary = entry.summary.strip()
            elif hasattr(entry, "description") and entry.description:
                raw_summary = entry.description.strip()
            summary = re.sub(r"<[^>]+>", "", raw_summary)[:500] if raw_summary else ""

            published = ""
            if hasattr(entry, "published") and entry.published:
                published = entry.published
            elif hasattr(entry, "updated") and entry.updated:
                published = entry.updated

            content_text = ""
            raw_content = getattr(entry, "content", None)
            if raw_content:
                try:
                    content_text = (raw_content[0].get("value", "") or "").strip()
                except (AttributeError, IndexError, TypeError):
                    content_text = ""
            if not content_text:
                content_text = (getattr(entry, "content_encoded", "") or "").strip()
            if not content_text and raw_summary:
                content_text = raw_summary
            if content_text:
                content_text = re.sub(r"<[^>]+>", " ", content_text)
                content_text = re.sub(r"\s+", " ", content_text).strip()[:20000]

            content_id = f"douban_feed-{abs(hash(link)) & 0xFFFFFFFF:08x}"

            items.append(
                DiscoveredContent(
                    content_id=content_id,
                    content_url=link,
                    source_platform="douban_feed",
                    title=title,
                    description=summary[:300],
                    author_name=author or "豆瓣",
                    discovered_at=published,
                    up_name=feed_name,
                    content_text=content_text,
                )
            )

        logger.info("DoubanFeedAdapter: %d items from %s", len(items), url)
        return items


def asyncio_run_in_executor(fn, *args):
    """在当前事件循环的线程池里跑同步函数。"""
    import asyncio

    loop = asyncio.get_running_loop()
    executor = _FEED_PARSE_EXECUTOR
    if executor is not None:
        return loop.run_in_executor(executor, fn, *args)
    return loop.run_in_executor(None, fn, *args)

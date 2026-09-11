"""豆瓣（Douban）文章 feed 源 adapter。

拉取豆瓣内容并归一化为 ``DiscoveredContent`` 供阅读库 / 推荐管线消费。

- ``comment`` / ``review`` / ``group``：拉豆瓣官方 RSS/ATOM feed（需 cookie 的可
  带 cookie），交给 feedparser 解析。
- ``diary``（用户广播/短评动态）：**直连豆瓣 rexxar JSON 接口**
  （``m.douban.com/rexxar/api/v2/status/user_timeline/{uid}``），需要登录 cookie
  并带精确 ``Referer``——RSSHub 的 status 路由上游未带 Referer 已被豆瓣反爬拦，
  故不走 RSSHub 自部署，改由本 adapter 自取。

feed URL 由 ``recipe.config`` 按 ``feed_kind`` 拼。
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
import time
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
# 移动端 UA + Referer 是豆瓣 rexxar 反爬校验的必需头
_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)
# 每次翻页之间的等待秒数（温和限频，避免触发豆瓣反爬）
_DIARY_PAGE_SLEEP = 1.0


def _feed_url(kind: str, uid: str = "", group_id: str = "") -> str:
    """按 feed_kind 拼豆瓣 RSS feed URL。

    仅 ``comment`` / ``review`` / ``group`` 用 URL 驱动；``diary`` 走 rexxar
    JSON 接口（见 :meth:`DoubanFeedAdapter._fetch_diary`），不在此拼 URL。
    """
    kind = (kind or "review").strip().lower()
    if kind == "comment" and uid:
        return f"https://douban.com/feed/people/{uid}/"
    if kind == "group" and group_id:
        return f"https://www.douban.com/feed/group/{group_id}/discussion"
    # 默认：全站最新评论
    return "https://douban.com/feed/review/latest"


class DoubanFeedAdapter:
    """从豆瓣 RSS feed 拉取文章的 source adapter。"""

    def __init__(self, cookie: str = "") -> None:
        self._cookie = (cookie or "").strip()

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

    def _fetch_diary_raw(self, uid: str) -> list[dict]:
        """直连豆瓣 rexxar 用户时间线接口，返回 status items 列表。

        带移动端 UA + 精确 Referer + 登录 cookie（缺 Referer 会返回
        ``invalid_request_1284`` 被反爬拦截）。分页拉取，页间温和限频。
        """
        start = 0
        per_page = 30
        collected: list[dict] = []
        while True:
            url = f"https://m.douban.com/rexxar/api/v2/status/user_timeline/{uid}"
            params = {"start": start, "count": per_page}
            headers = {
                "User-Agent": _MOBILE_UA,
                "Referer": f"https://m.douban.com/people/{uid}/statuses",
                "Accept": "application/json",
            }
            resp = requests.get(
                url,
                params=params,
                headers=headers,
                cookies=self._cookie_jar(),
                timeout=20,
            )
            resp.raise_for_status()
            try:
                data = resp.json()
            except ValueError:
                logger.warning("DoubanFeedAdapter: diary 响应非 JSON: %s", url)
                break
            page_items = data.get("items") or []
            collected.extend(page_items)
            total = int(data.get("count") or 0)
            start += per_page
            if not page_items or start >= total or len(collected) >= 90:
                break
            time.sleep(_DIARY_PAGE_SLEEP)
        return collected

    def _diary_items(self, raw: list[dict], uid: str, feed_name: str) -> list["DiscoveredContent"]:
        """把 rexxar items 归一化为 DiscoveredContent。"""
        from openbiliclaw.discovery.engine import DiscoveredContent

        out: list[DiscoveredContent] = []
        for it in raw:
            st = (it or {}).get("status") or {}
            if not st:
                continue
            text = (st.get("text") or "").strip()
            if not text:
                continue
            link = (st.get("sharing_url") or "").strip()
            if not link:
                link = f"https://www.douban.com/people/{uid}/statuses"
            author = ((st.get("author") or {}) or {}).get("name") or "豆瓣"
            created = (st.get("create_time") or "").strip()
            # 标题取正文首行（动态通常为短句）
            title = re.split(r"\s+", text)[:12]
            title = " ".join(t for t in title if t)[:60] or "豆瓣动态"
            content_id = f"douban_diary-{abs(hash(link)) & 0xFFFFFFFF:08x}"
            out.append(
                DiscoveredContent(
                    content_id=content_id,
                    content_url=link,
                    source_platform="douban_feed",
                    title=title,
                    description=text[:300],
                    author_name=author,
                    discovered_at=created,
                    up_name=feed_name,
                    content_text=text[:20000],
                )
            )
        return out

    async def _fetch_diary(self, uid: str, feed_name: str, limit: int) -> list["DiscoveredContent"]:
        """拉取并按 limit 截断用户动态。"""
        try:
            raw = await asyncio_run_in_executor(self._fetch_diary_raw, uid)
        except requests.RequestException as exc:
            logger.warning("DoubanFeedAdapter: diary 拉取失败 %s: %s", uid, exc)
            return []
        except Exception as exc:  # noqa: BLE001
            logger.warning("DoubanFeedAdapter: diary 拉取异常 %s: %s", uid, exc)
            return []
        items = self._diary_items(raw, uid, feed_name)
        logger.info("DoubanFeedAdapter: diary %d items for user %s", len(items), uid)
        return items[:limit]

    async def fetch(
        self,
        recipe: SourceRecipe,
        profile: object | None = None,
        limit: int = 20,
    ) -> list[DiscoveredContent]:
        """按 recipe.config 的 feed_kind 抓取并解析。"""
        cfg = getattr(recipe, "config", None) or {}
        feed_kind = str(cfg.get("feed_kind", "review") or "review")
        uid = str(cfg.get("uid", "") or "")
        group_id = str(cfg.get("group_id", "") or "")
        feed_name = recipe.name or cfg.get("name", "") or "豆瓣feed"

        # diary（用户广播）走 rexxar JSON 接口直连，不走 RSS。
        if feed_kind == "diary" and uid:
            return await self._fetch_diary(uid=uid, feed_name=feed_name, limit=limit)

        url = _feed_url(feed_kind, uid=uid, group_id=group_id)

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

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
import random
import re
import time
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from openbiliclaw.core.contracts import DiscoveredContent
    from openbiliclaw.sources.protocol import SourceRecipe

logger = logging.getLogger(__name__)

# 与 rss_adapter 同款独立线程池（feedparser 同步网络抓取，避免占 HTTP 池）
_FEED_PARSE_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = (
    concurrent.futures.ThreadPoolExecutor(
        max_workers=4,
        thread_name_prefix="dbfeed",
    )
)

# 桌面/移动真实 UA 池：每请求轮换，避免固定一个被豆瓣指纹识别（参考同类爬虫项目）。
_UA_POOL = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]
_USER_AGENT = _UA_POOL[0]  # 兼容旧引用
# 每次翻页之间的随机延迟区间（秒）——固定间隔是机器人特征，随机更似真人（参考 douban-takeout）。
_DIARY_SLEEP_MIN = 1.5
_DIARY_SLEEP_MAX = 3.0
# 完整浏览器请求头（缺 Referer / Accept-Language 会被豆瓣直接拦截）
_HEADERS_BASE = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "DNT": "1",
}


def _headers(referer: str) -> dict[str, str]:
    """构造带随机 UA + 指定 Referer 的完整请求头，更接近真人浏览器。"""
    h = dict(_HEADERS_BASE)
    h["User-Agent"] = random.choice(_UA_POOL)
    h["Referer"] = referer
    return h


def _status_time(item: dict) -> str:
    """取 rexxar status item 的 create_time 字符串（缺省空串）。"""
    return ((item or {}).get("status") or {}).get("create_time", "") or ""


def _feed_url(kind: str, uid: str = "", group_id: str = "") -> str:
    """按 feed_kind 拼豆瓣 RSS feed URL。

    ``comment`` / ``review`` / ``group`` 用 RSS URL 驱动；``diary``（广播动态）
    走 rexxar JSON 接口（见 :meth:`DoubanFeedAdapter._fetch_diary`），不在此拼 URL。
    注：笔记(notes)端点 rexxar 1503 + 原生 RSS 会触发反爬，故不做。
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
        """带 cookie 抓取原始 XML（随机 UA + 完整头，更像真人浏览器）。"""
        headers = _headers("https://www.douban.com/")
        headers["Accept"] = "application/rss+xml, application/atom+xml, text/xml, */*;q=0.8"
        resp = requests.get(
            url,
            cookies=self._cookie_jar(),
            timeout=20,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.text

    def _fetch_diary_raw(self, uid: str, since: str = "") -> list[dict]:
        """直连豆瓣 rexxar 用户时间线接口，返回 status items 列表。

        带移动端 UA + 精确 Referer + 登录 cookie（缺 Referer 会返回
        ``invalid_request_1284`` 被反爬拦截）。分页拉取，页间温和限频。

        时间线按新→旧排序；``since``（create_time 字符串）非空时，一旦翻到
        不新于 ``since`` 的条目即停止，实现增量拉取。
        """
        start = 0
        per_page = 30
        collected: list[dict] = []
        while True:
            url = f"https://m.douban.com/rexxar/api/v2/status/user_timeline/{uid}"
            params = {"start": start, "count": per_page}
            headers = _headers(f"https://m.douban.com/people/{uid}/statuses")
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
            total = int(data.get("count") or 0)
            start += per_page
            # 增量截止：若设定了 since，裁掉不新于 since 的条目；一旦被裁即停止翻页。
            if since and page_items:
                kept = [it for it in page_items if _status_time(it) > since]
                collected.extend(kept)
                if len(kept) < len(page_items):
                    break
            else:
                collected.extend(page_items)
            if not page_items or start >= total or len(collected) >= 90:
                break
            time.sleep(random.uniform(_DIARY_SLEEP_MIN, _DIARY_SLEEP_MAX))
        return collected

    def _diary_items(self, raw: list[dict], uid: str, feed_name: str) -> list[DiscoveredContent]:
        """把 rexxar items 归一化为 DiscoveredContent。"""
        from openbiliclaw.core.contracts import DiscoveredContent

        out: list[DiscoveredContent] = []
        for it in raw:
            st = (it or {}).get("status") or {}
            if not st:
                continue
            text = (st.get("text") or "").strip()
            activity = (st.get("activity") or "").strip()
            card = st.get("card") or {}
            card_title = (card.get("title") or "").strip()
            # 正文为空时（转发/徽章/在读等卡片型动态），用「活动 + 卡片标题」组成可读内容。
            if text:
                body = text
            else:
                body = "、".join(p for p in (activity, card_title) if p)
                if not body:
                    continue
            link = (st.get("sharing_url") or "").strip()
            if not link:
                link = (card.get("url") or "").strip()
            if not link:
                link = f"https://www.douban.com/people/{uid}/statuses"
            author = ((st.get("author") or {}) or {}).get("name") or "豆瓣"
            created = (st.get("create_time") or "").strip()
            # 标题取正文首行（动态通常为短句）
            title = re.split(r"\s+", body)[:12]
            title = " ".join(t for t in title if t)[:60] or "豆瓣动态"
            content_id = f"douban_diary-{abs(hash(link)) & 0xFFFFFFFF:08x}"
            out.append(
                DiscoveredContent(
                    content_id=content_id,
                    content_url=link,
                    source_platform="douban_feed",
                    title=title,
                    description=body[:300],
                    author_name=author,
                    discovered_at=created,
                    up_name=feed_name,
                    content_text=body[:20000],
                )
            )
        return out

    async def _fetch_diary(
        self, uid: str, feed_name: str, limit: int, since: str = ""
    ) -> tuple[list[DiscoveredContent], str]:
        """增量拉取用户动态，返回 (items, 最新 create_time 水印)。

        仅拉取 ``create_time > since`` 的新条目；``since`` 空串时拉全量（限 ``limit``）。
        """
        try:
            raw = await asyncio_run_in_executor(self._fetch_diary_raw, uid, since)
        except requests.RequestException as exc:
            logger.warning("DoubanFeedAdapter: diary 拉取失败 %s: %s", uid, exc)
            return [], ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("DoubanFeedAdapter: diary 拉取异常 %s: %s", uid, exc)
            return [], ""
        items = self._diary_items(raw, uid, feed_name)
        restricted = items[:limit] if since else items
        newest = _status_time(raw[0]) if raw else ""
        logger.info(
            "DoubanFeedAdapter: diary %d items for user %s (since=%s)",
            len(restricted),
            uid,
            since or "full",
        )
        return restricted, newest

    async def fetch_diary_since(
        self, uid: str, feed_name: str, since: str = "", limit: int = 30
    ) -> tuple[list[DiscoveredContent], str]:
        """增量抓取用户动态；返回 (items, 最新水印)。供任务层调用。"""
        return await self._fetch_diary(uid=uid, feed_name=feed_name, limit=limit, since=since)

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
            items, _ = await self._fetch_diary(uid=uid, feed_name=feed_name, limit=limit)
            return items

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

        from openbiliclaw.core.contracts import DiscoveredContent

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

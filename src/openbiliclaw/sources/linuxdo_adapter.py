"""Linux.do（Discourse）文章源 adapter。

拉取 linux.do 帖子/列表并归一化为 ``DiscoveredContent`` 供阅读库 / 推荐管线
消费。

- ``topic``：**直连 Discourse topic JSON**（``linux.do/t/{slug}/{id}.json``），
  返回该帖（首帖为正文，正文用 ``raw`` / ``cooked`` 字段，多帖拼成全文）。
- ``latest`` / ``hot``：``/latest.json``、``/hot.json`` 拉板块列表。
- ``search``：``/search.json?q=`` 按关键词搜话题。

走 Discourse 原生 JSON 接口而非 HTML，故在 Cloudflare 拦 HTML 时通常仍可访问
（与豆瓣 feed 直连 rexxar 同理）。登录可见内容需要 cookie：从
``recipe.config.cookie`` 或环境变量（默认 ``OPENBILICLAW_LINUXDO_COOKIE``）
读取，未配置则带空 cookie 拉公开源。

feed URL 由 ``recipe.config`` 按 ``strategy`` 拼。
"""

from __future__ import annotations

import html as _html
import logging
import random
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus, urlparse

import requests

if TYPE_CHECKING:
    from openbiliclaw.core.contracts import DiscoveredContent
    from openbiliclaw.sources.protocol import SourceRecipe

logger = logging.getLogger(__name__)

_BASE = "https://linux.do"

# 桌面/移动真实 UA 池：每请求轮换，避免固定一个被指纹识别（参考豆瓣 feed 适配器）。
_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
)

_TIMEOUT = 15
# 解析 thread 的标题/作者字段，seed 一次即可
_NON_TEXT_HTML = re.compile(r"<(?:script|style|code|pre)[^>]*>.*?</(?:script|style|code|pre)>", re.S | re.I)


def _strip_html(html_text: str) -> str:
    """把 Discourse ``cooked`` 的 HTML 粗略转成纯文本（去掉脚本、标签、多余空白）。"""
    if not html_text:
        return ""
    text = _NON_TEXT_HTML.sub(" ", html_text)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</(?:p|div|li|h\d|blockquote|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    # 折行保留但去首尾/连续空行
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _topic_slug_list(json_data: dict[str, Any]) -> tuple[str, int]:
    """从 topic.json 里提取 (slug, topic_id)。"""
    slug = str(json_data.get("slug") or "")
    topic_id = int(json_data.get("id") or 0)
    if slug:
        return slug, topic_id
    # 回退：从 URL 解析 ``/t/{slug}/{id}``
    raw_url = json_data.get("meta", {}).get("title") or ""
    return slug, topic_id


class LinuxdoAdapter:
    """Linux.do 文章源适配器（直连 Discourse JSON 接口）。

    Recipe config keys:
        yurl: 直接抓的帖子 URL（``https://linux.do/t/<slug>/<id>`` 或含 query）．
        topic_url: 同 ``url`` 的别名。
        topic_id: 直接给帖子 numeric id。
        query: 搜索关键词（仅 ``search`` 策略）。
        cookie: 可选 cookie；缺省回退环境变量。
    """

    def __init__(self, cookie: str = "", cookie_env: str = "OPENBILICLAW_LINUXDO_COOKIE") -> None:
        self._cookie = cookie
        self._cookie_env = cookie_env

    @property
    def source_type(self) -> str:
        return "linuxdo"

    async def fetch(
        self,
        recipe: SourceRecipe,
        profile: Any = None,
        limit: int = 20,
    ) -> list[DiscoveredContent]:
        """Fetch content from linux.do according to *recipe*."""
        strategy = recipe.strategy or "topic"
        config = recipe.config or {}
        cookie = self._resolved_cookie(config)

        try:
            if strategy == "topic":
                items = self._fetch_topic(config, cookie, limit)
            elif strategy in ("latest", "hot"):
                items = self._fetch_list(strategy, config, cookie, limit)
            elif strategy == "search":
                items = self._fetch_search(config, cookie, limit)
            else:
                logger.warning("LinuxdoAdapter: unknown strategy %s", strategy)
                return []
        except Exception:
            logger.exception("LinuxdoAdapter: failed to fetch strategy=%s", strategy)
            return []

        for item in items:
            item.source_platform = "linuxdo"
            item.content_id = item.content_id or _extract_topic_id(item.content_url)
        return items[:limit]

    # ── 策略实现 ───────────────────────────────────────────────

    def _fetch_topic(
        self,
        config: dict[str, Any],
        cookie: str,
        limit: int,
    ) -> list[DiscoveredContent]:
        raw = config.get("url", "") or config.get("topic_url", "") or config.get("yurl", "")
        topic_id = str(config.get("topic_id", "") or "")
        if topic_id.isdigit():
            slug_id = topic_id
        else:
            slug_id = _slug_id_from_url(raw)
            if not slug_id:
                logger.warning("LinuxdoAdapter: no topic url/id for recipe %s", config)
                return []

        data = self._get_json(f"/t/{quote_plus(slug_id, safe='/')}.json", cookie)
        if not data or not data.get("id"):
            return []

        slug, tid = _topic_slug_list(data)
        title = str(data.get("title") or "")
        created_at = str(data.get("created_at") or "")
        tags = list(data.get("tags") or cast_list(data.get("tags")))
        category = _category_name(data)

        posts = (data.get("post_stream") or {}).get("posts") or []
        op = posts[0] if posts else {}
        author = str(op.get("username") or data.get("username") or "")
        op_text = _post_text(op)
        full_text = "\n\n".join(_post_text(p) for p in posts if _post_text(p))

        item = _make_item(
            title=title,
            author=author,
            topic_id=tid,
            slug=slug,
            tags=tags,
            created_at=created_at,
            category=category,
            op_text=op_text,
            full_text=full_text,
            description=str(data.get("excerpt") or ""),
            content_type="thread",
        )
        return [item]

    def _fetch_list(
        self,
        strategy: str,
        config: dict[str, Any],
        cookie: str,
        limit: int,
    ) -> list[DiscoveredContent]:
        path = "/hot.json" if strategy == "hot" else "/latest.json"
        data = self._get_json(path, cookie)
        raw = data.get("topic_list", {}).get("topics") or []
        out: list[DiscoveredContent] = []
        for t in raw:
            if not t or not t.get("id"):
                continue
            slug = str(t.get("slug") or "")
            tid = int(t.get("id") or 0)
            text = _strip_html(str(t.get("excerpt") or ""))
            item = _make_item(
                title=str(t.get("title") or ""),
                author="",
                topic_id=tid,
                slug=slug,
                tags=list(t.get("tags") or []),
                created_at=str(t.get("created_at") or ""),
                category="",
                op_text="",
                full_text=text or str(t.get("title") or ""),
                description=text or str(t.get("title") or ""),
                content_type="thread",
            )
            out.append(item)
        return out

    def _fetch_search(
        self,
        config: dict[str, Any],
        cookie: str,
        limit: int,
    ) -> list[DiscoveredContent]:
        q = str(config.get("query", "") or "")
        if not q:
            return []
        data = self._get_json(f"/search.json?q={quote_plus(q)}", cookie)
        raw = data.get("topics") or []
        out: list[DiscoveredContent] = []
        for t in raw:
            if not t or not t.get("id"):
                continue
            tid = int(t.get("id") or 0)
            item = _make_item(
                title=str(t.get("title") or ""),
                author="",
                topic_id=tid,
                slug=str(t.get("slug") or ""),
                tags=list(t.get("tags") or []),
                created_at=str(t.get("created_at") or ""),
                category="",
                op_text="",
                full_text=str(t.get("blurb") or "") or str(t.get("title") or ""),
                description=str(t.get("blurb") or "") or str(t.get("title") or ""),
                content_type="thread",
            )
            out.append(item)
        return out

    # ── 传输层 ────────────────────────────────────────────────

    def _resolved_cookie(self, config: dict[str, Any]) -> str:
        if config.get("cookie"):
            return str(config["cookie"])
        import os

        cookie = self._cookie or os.environ.get(self._cookie_env, "")
        if not cookie:
            # 回退读项目根 .env（gitignore，不入库）——与豆瓣 feed 一致
            try:
                from pathlib import Path

                env_path = Path(os.getcwd()) / ".env"
                if env_path.exists():
                    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                        raw_line = raw_line.strip()
                        if raw_line.startswith(self._cookie_env + "="):
                            return raw_line.split("=", 1)[1].strip()
            except Exception:
                pass
        return cookie

    def _get_json(self, path: str, cookie: str) -> dict[str, Any]:
        headers: dict[str, str] = {
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        if cookie:
            headers["Cookie"] = cookie
            # Discourse 校验 Referer 的回跳；登录读取时带上本域 Referer 更稳。
            headers["Referer"] = _BASE + "/"
        resp = requests.get(_BASE + path, headers=headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        if "json" not in (resp.headers.get("content-type") or ""):
            raise ValueError("linux.do did not return JSON (Cloudflare/HTML block)")
        return resp.json()


def _post_text(post: dict[str, Any]) -> str:
    raw = str(post.get("raw") or "")
    if raw:
        return raw.strip()
    return _strip_html(str(post.get("cooked") or ""))


def _category_name(data: dict[str, Any]) -> str:
    # topic.json 里 category 需要再查；这里只做粗略回退，非关键字段。
    return ""


def cast_list(value: Any) -> list[str]:
    return value if isinstance(value, list) else [str(value)]


def _make_item(
    *,
    title: str,
    author: str,
    topic_id: int,
    slug: str,
    tags: list[str],
    created_at: str,
    category: str,
    op_text: str,
    full_text: str,
    description: str,
    content_type: str,
) -> "DiscoveredContent":
    from openbiliclaw.core.contracts import DiscoveredContent

    path = f"/t/{slug}/{topic_id}" if slug else f"/t/{topic_id}"
    topic_key = f"linuxdo-{topic_id}"
    return DiscoveredContent(
        title=title,
        author_name=author,
        up_name=author,
        content_id=str(topic_id) if topic_id else "",
        content_url=_BASE + path,
        source_platform="linuxdo",
        content_type=content_type,
        body_text=op_text,
        content_text=full_text or op_text,
        description=description,
        tags=list(tags),
        topic_key=topic_key,
        discovered_at=created_at,
    )


def _slug_id_from_url(raw: str) -> str:
    """从帖子 URL 里解出 Discourse 用的帖子标识。

    优先取数字 id（``/t/<slug>/<id>`` 或 ``/t/<id>``），slug 可能不唯一；
    没有数字 id 时才回退首个 slug 段。
    """
    if not raw:
        return ""
    path = urlparse(raw).path.rstrip("/")
    # 兼容三种真实形态：
    #   /t/<id>                 → <id>
    #   /t/<slug>/<id>          → <id>
    #   /t/<slug>/<id>/<post>   → <id>（末位是楼序号）
    m = re.search(r"/t/(?:[^/]+/)?(\d+)(?:/\d+)?$", path)
    if m:
        return m.group(1)
    m = re.search(r"/t/(?:topic/)?([^/?]+)", path)
    return m.group(1) if m else ""
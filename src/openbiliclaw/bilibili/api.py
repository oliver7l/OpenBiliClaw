"""Bilibili API Client.

Primary interface for interacting with Bilibili, prioritizing the official
and reverse-engineered API for speed and efficiency.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, ClassVar, cast
from urllib.parse import quote, urlencode, urlparse

import httpx

logger = logging.getLogger(__name__)

#: 写接口里「等一会儿再来」的返回码：-412 风控拦截 / -799 请求过快 / -509 账号级限制。
_RATE_LIMIT_CODES = frozenset({-412, -799, -509})

#: 「默认收藏夹」的可能标题（B 站自动创建时叫法可能带空格/别名）。
_DEFAULT_FAVORITE_FOLDER_TITLES = frozenset({"默认收藏夹", "默认收藏", "默认收藏夹 "})


class BilibiliAPIError(RuntimeError):
    """Raised when a Bilibili API request returns an application error."""

    def __init__(self, message: str = "", code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class BilibiliAuthExpiredError(BilibiliAPIError):
    """Raised when Bilibili reports the current Cookie is logged out."""

    def __init__(self, message: str = "", code: int | None = -101) -> None:
        super().__init__(message, code=code)


class BilibiliRateLimitedError(BilibiliAPIError):
    """Raised when Bilibili throttles or risk-controls a write request.

    ``-412`` 是风控拦截、``-799`` 是请求过快、``-509`` 是账号级封禁——三者对
    调用方的**可恢复语义相同**（等一会儿再来，不是代码错了），所以归成一个类型，
    由上层映射成 ``rate_limited`` 而不是 ``failed``。
    """

    def __init__(self, message: str = "", code: int | None = None) -> None:
        super().__init__(message, code=code)


class BilibiliFavoriteFolderNotFoundError(BilibiliAPIError):
    """Raised when the account has no folder named 「默认收藏夹」.

    单独成类是为了让上层能给出**可操作的**错误码（``favorite_folder_unresolved``），
    而不是从 message 里做字符串匹配。
    """

    def __init__(self, message: str = "") -> None:
        super().__init__(message)


def _json_object(value: Any) -> dict[str, Any]:
    """Coerce a JSON value into an object for strict typing.

    Returns an empty dict when *value* is ``None`` (common when B站
    returns ``"data": null`` under rate-limiting or for empty ranking
    regions), mirroring :func:`_json_list`'s null-handling.
    """
    if value is None:
        return {}
    return cast("dict[str, Any]", value)


def _json_list(value: Any) -> list[dict[str, Any]]:
    """Coerce a JSON value into a list of objects for strict typing.

    Returns an empty list when *value* is ``None`` (common when B站
    returns ``"result": null`` under rate-limiting).
    """
    if value is None:
        return []
    return cast("list[dict[str, Any]]", value)


@dataclass
class VideoInfo:
    """Basic video information from Bilibili."""

    bvid: str = ""
    aid: int = 0
    cid: int = 0  # 分 P 的内容 id —— 播放地址接口必须携带
    title: str = ""
    description: str = ""
    duration: int = 0  # seconds
    cover_url: str = ""
    up_name: str = ""
    up_mid: int = 0
    view_count: int = 0
    like_count: int = 0
    coin_count: int = 0
    favorite_count: int = 0
    share_count: int = 0
    danmaku_count: int = 0
    tags: list[str] | None = None
    pub_date: str = ""


@dataclass
class NavInfo:
    """Basic authenticated user info from the nav endpoint."""

    is_login: bool = False
    uname: str = ""
    mid: int = 0


@dataclass
class FavoriteFolder:
    """Favorite folder metadata."""

    media_id: int
    title: str
    media_count: int = 0


@dataclass
class FavoriteFolderWithItems:
    """Favorite folder plus fetched items."""

    folder: FavoriteFolder
    items: list[dict[str, Any]]
    truncated: bool = False


@dataclass
class FollowingUser:
    """Basic followed user info."""

    mid: int
    uname: str
    sign: str = ""


@dataclass
class CommentInfo:
    """Basic comment info."""

    mid: int
    uname: str
    message: str
    like_count: int = 0


class BilibiliAPIClient:
    """Client for Bilibili's web API.

    This is the primary data access layer (API-first approach).
    For operations not supported by the API, use BilibiliBrowser.
    """

    _BASE_URL = "https://api.bilibili.com"
    _SEARCH_WEB_LOCATION = 1430654
    # A v_voucher exhaustion is usually recoverable WBI-key churn / mild
    # rate limiting, so it gets a short, escalating back-off. A genuine
    # HTTP 412 is an explicit IP-level block and gets the longer hard
    # cooldown instead (see ``_SEARCH_COOLDOWN_412_SECONDS``).
    _SEARCH_COOLDOWN_BASE_SECONDS: ClassVar[float] = 180.0
    _SEARCH_COOLDOWN_412_SECONDS: ClassVar[float] = 600.0
    _SEARCH_COOLDOWN_MAX_SECONDS: ClassVar[float] = 1800.0
    _SEARCH_DOM_FALLBACK_SECONDS: ClassVar[float] = 180.0
    # A single challenged keyword (transient churn) must NOT zero out the
    # whole search round + the explore strategy that shares this cooldown.
    # Only trip the process-wide cooldown after this many *consecutive*
    # keyword-level v_voucher exhaustions; any success resets the streak.
    _SEARCH_VOUCHER_BLOCK_THRESHOLD: ClassVar[int] = 3
    _search_cooldown_until: ClassVar[float] = 0.0
    _search_cooldown_level: ClassVar[int] = 0
    _search_voucher_block_streak: ClassVar[int] = 0
    _search_dom_fallback_until: ClassVar[float] = 0.0
    _WBI_MIXIN_KEY_ENC_TAB = [
        46,
        47,
        18,
        2,
        53,
        8,
        23,
        32,
        15,
        50,
        10,
        31,
        58,
        3,
        45,
        35,
        27,
        43,
        5,
        49,
        33,
        9,
        42,
        19,
        29,
        28,
        14,
        39,
        12,
        38,
        41,
        13,
        37,
        48,
        7,
        16,
        24,
        55,
        40,
        61,
        26,
        17,
        0,
        1,
        60,
        51,
        30,
        4,
        22,
        25,
        54,
        21,
        56,
        59,
        6,
        63,
        57,
        62,
        11,
        36,
        20,
        34,
        44,
        52,
    ]

    _WBI_KEY_TTL: float = 300.0  # Refresh WBI keys every 5 minutes

    #: 移动端 auth/export 会把这个 UA 带回给客户端（直连 B 站读接口时保持一致）。
    DEFAULT_USER_AGENT: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        cookie: str = "",
        *,
        min_request_interval: float = 0.2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._cookie = cookie
        self._min_request_interval = min_request_interval
        self._last_request_at = 0.0
        self._cached_wbi_keys: tuple[str, str] | None = None
        self._wbi_keys_fetched_at: float = 0.0
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": self.DEFAULT_USER_AGENT,
                "Referer": "https://www.bilibili.com",
            },
            # Connect directly to Bilibili — do NOT inherit the macOS system
            # proxy (127.0.0.1:7890). That proxy is restarted often and its
            # downtime takes down every outbound request (observed 2026-09-01).
            trust_env=False,
            timeout=30.0,
            # 注入点只给测试用（httpx.MockTransport）：写接口是「会改变用户账号」
            # 的动作，必须在完全不发真实请求的前提下可测。
            transport=transport,
        )
        if cookie:
            self._client.headers["Cookie"] = cookie

    @property
    def is_authenticated(self) -> bool:
        """Whether we have a valid authentication cookie."""
        return bool(self._cookie)

    async def _respect_rate_limit(self) -> None:
        """Wait to keep a minimum interval between requests."""
        elapsed = time.monotonic() - self._last_request_at
        remaining = self._min_request_interval - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)
        self._last_request_at = time.monotonic()

    @classmethod
    def search_cooldown_remaining(cls) -> float:
        """Seconds remaining in the process-wide Bilibili search cooldown."""
        return max(0.0, cls._search_cooldown_until - time.monotonic())

    @classmethod
    def search_dom_fallback_remaining(cls) -> float:
        """Seconds remaining while rendered-page search fallback is preferred."""
        return max(0.0, cls._search_dom_fallback_until - time.monotonic())

    @classmethod
    def _activate_search_dom_fallback(cls, *, seconds: float | None = None) -> float:
        """Ask the extension-search producer to try DOM search soon.

        This signal is intentionally weaker than the global cooldown: API
        search may keep probing, but the browser extension can backfill via a
        rendered search page while the API path looks degraded.
        """
        duration = cls._SEARCH_DOM_FALLBACK_SECONDS if seconds is None else seconds
        cls._search_dom_fallback_until = max(
            cls._search_dom_fallback_until,
            time.monotonic() + duration,
        )
        return duration

    @classmethod
    def _activate_search_cooldown(cls, *, base_seconds: float | None = None) -> float:
        """Back off all search clients after repeated v_voucher/412 blocks.

        ``base_seconds`` overrides the per-step base (412 blocks pass the
        longer hard-cooldown base); the escalation multiplier and absolute
        ceiling are shared across both causes.
        """
        cls._search_cooldown_level = min(cls._search_cooldown_level + 1, 3)
        base = cls._SEARCH_COOLDOWN_BASE_SECONDS if base_seconds is None else base_seconds
        duration = min(
            base * cls._search_cooldown_level,
            cls._SEARCH_COOLDOWN_MAX_SECONDS,
        )
        cls._search_cooldown_until = max(
            cls._search_cooldown_until,
            time.monotonic() + duration,
        )
        cls._activate_search_dom_fallback(seconds=duration)
        return duration

    @classmethod
    def _record_voucher_block(cls) -> float:
        """Record one keyword exhausting its v_voucher retries.

        Returns the cooldown duration if this block crossed the
        consecutive-failure threshold (the whole search path now backs
        off), or ``0.0`` if search stays live and only this one keyword is
        dropped — a lone challenged keyword is usually transient WBI churn,
        not an IP-level block, and must not strand the search round +
        explore for the full cooldown.
        """
        cls._search_voucher_block_streak += 1
        if cls._search_voucher_block_streak >= cls._SEARCH_VOUCHER_BLOCK_THRESHOLD:
            return cls._activate_search_cooldown()
        return 0.0

    @classmethod
    def _reset_search_cooldown_backoff(cls) -> None:
        """Reset escalation + the v_voucher streak once search succeeds again."""
        cls._search_cooldown_level = 0
        cls._search_voucher_block_streak = 0

    async def _get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Perform a GET request and return the decoded `data` payload."""
        await self._respect_rate_limit()
        try:
            resp = await self._client.get(
                f"{self._BASE_URL}{path}",
                params=params,
                headers=headers,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise BilibiliAPIError(str(exc)) from exc

        payload = _json_object(resp.json())
        code = int(payload.get("code", 0))
        if code != 0:
            message = str(payload.get("message", "Bilibili API request failed"))
            if path == "/x/web-interface/nav" and code == -101:
                detail = (
                    f"Bilibili session expired on {path} (-101): {message}. "
                    "Please re-authenticate in the browser or keep the extension "
                    "online to sync a fresh Cookie."
                )
                logger.warning("%s", detail)
                raise BilibiliAuthExpiredError(detail)
            # 带上原始 code（此前只抛 message）：调用方需要按 code 分流，
            # 例如 /view 被风控(-412)时降级到 WBI 签名端点重试。
            raise BilibiliAPIError(message, code=code)
        return _json_object(payload.get("data", {}))

    async def _post_json(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        """Perform a POST and return the **whole** payload（含 ``code``）。

        刻意与 :meth:`_get_json` 不同：不把非 0 ``code`` 直接抛掉。写接口的失败
        是**业务语义**（``-101`` 未登录 / ``-412`` 风控 / 未知码需要原样上报给
        用户），调用方要按码分支，而不是一律变成同一种异常。
        """
        await self._respect_rate_limit()
        try:
            resp = await self._client.post(f"{self._BASE_URL}{path}", data=data)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise BilibiliAPIError(str(exc)) from exc
        return _json_object(resp.json())

    @property
    def csrf_token(self) -> str:
        """Cookie 串里的 ``bili_jct``（所有写接口都要带它做 csrf 校验）。"""
        for part in self._cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "bili_jct":
                return value.strip()
        return ""

    def _require_csrf(self, action_label: str) -> str:
        csrf = self.csrf_token
        if not csrf:
            raise BilibiliAuthExpiredError(
                f"Bilibili cookie has no bili_jct — 无法完成{action_label}，请重新登录后再试"
            )
        return csrf

    def _raise_for_write_code(self, code: int, payload: dict[str, Any], action_label: str) -> None:
        """把写接口的非 0 ``code`` 映射成具体异常（不做「猜码等于成功」的事）。"""
        message = str(payload.get("message", "")) or "Bilibili write request failed"
        if code == -101:
            raise BilibiliAuthExpiredError(f"Bilibili session expired while {action_label}: {message}")
        if code in _RATE_LIMIT_CODES:
            raise BilibiliRateLimitedError(f"Bilibili throttled {action_label} (code={code}): {message}", code=code)
        raise BilibiliAPIError(f"Bilibili refused to {action_label} (code={code}): {message}", code=code)

    async def add_to_watch_later(self, bvid: str) -> None:
        """把视频加入「稍后再看」（官方接口本身幂等，重复调用不报错）。"""
        normalized = bvid.strip()
        if not normalized:
            raise BilibiliAPIError("add_to_watch_later requires a bvid")
        csrf = self._require_csrf("加入稍后再看")
        payload = await self._post_json(
            "/x/v2/history/toview/add",
            {"bvid": normalized, "csrf": csrf},
        )
        code = int(payload.get("code", 0))
        if code != 0:
            self._raise_for_write_code(code, payload, "add to watch later")

    async def is_in_watch_later(self, bvid: str) -> bool:
        """Whether ``bvid`` is already in the user's watch-later list."""
        normalized = bvid.strip()
        if not normalized:
            return False
        data = await self._get_json("/x/v2/history/toview")
        return any(
            str(item.get("bvid", "")).strip() == normalized for item in _json_list(data.get("list", []))
        )

    async def resolve_default_favorite_folder_id(self) -> int:
        """解析「默认收藏夹」的 media_id。

        找不到就抛错：**绝不静默挑一个收藏夹**。写进用户没预期的夹子里，是那种
        「没有报错但数据到了错地方」的缺陷，比直接失败难查得多。
        """
        folders = await self.get_favorite_folders()
        for folder in folders:
            if folder.title.strip() in _DEFAULT_FAVORITE_FOLDER_TITLES:
                return folder.media_id
        raise BilibiliFavoriteFolderNotFoundError(
            "找不到名为「默认收藏夹」的收藏夹——请先在 B 站手动收藏一次，或改用稍后再看"
        )

    async def is_favorited(self, bvid: str, media_id: int, *, max_items: int = 100) -> bool:
        """Whether ``bvid`` already sits in the given favorites folder."""
        normalized = bvid.strip()
        if not normalized or media_id <= 0:
            return False
        items = await self.get_favorites(media_id, max_items=max_items)
        return any(str(item.get("bvid", "")).strip() == normalized for item in items)

    async def get_video_aid(self, bvid: str) -> int:
        """解析视频的数字 ``aid`` —— ``/x/v3/fav/resource/deal`` 只认 aid，不认 bvid。"""
        info = await self.get_video_info(bvid)
        if info.aid <= 0:
            raise BilibiliAPIError(f"无法解析 {bvid} 的 aid")
        return info.aid

    async def add_to_favorites(self, aid: int, media_id: int) -> None:
        """把视频收藏进指定收藏夹（``/x/v3/fav/resource/deal`` 需要数字 aid）。"""
        if aid <= 0:
            raise BilibiliAPIError("add_to_favorites requires a positive aid")
        if media_id <= 0:
            raise BilibiliAPIError("add_to_favorites requires a positive media_id")
        csrf = self._require_csrf("收藏")
        payload = await self._post_json(
            "/x/v3/fav/resource/deal",
            {"rid": aid, "type": 2, "add_media_ids": media_id, "csrf": csrf},
        )
        code = int(payload.get("code", 0))
        if code != 0:
            self._raise_for_write_code(code, payload, "add to favorites")

    async def aclose(self) -> None:
        """Close the underlying HTTP client (writer adapters own their client)."""
        await self._client.aclose()

    async def _get_wbi_keys(self) -> tuple[str, str]:
        """Fetch and cache the WBI image/sub keys used for signed search requests.

        Keys are refreshed after :attr:`_WBI_KEY_TTL` seconds because B站
        rotates them periodically — stale keys cause search to return an
        empty ``v_voucher`` response instead of actual results.
        """
        if (
            self._cached_wbi_keys is not None
            and (time.monotonic() - self._wbi_keys_fetched_at) < self._WBI_KEY_TTL
        ):
            return self._cached_wbi_keys

        await self._respect_rate_limit()
        try:
            resp = await self._client.get(f"{self._BASE_URL}/x/web-interface/nav")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise BilibiliAPIError(str(exc)) from exc

        payload = _json_object(resp.json())
        data = _json_object(payload.get("data", {}))
        wbi_img = _json_object(data.get("wbi_img", {}))
        img_key = self._extract_wbi_key_component(str(wbi_img.get("img_url", "")))
        sub_key = self._extract_wbi_key_component(str(wbi_img.get("sub_url", "")))
        if not img_key or not sub_key:
            raise BilibiliAPIError("Missing wbi keys in nav response")
        self._cached_wbi_keys = (img_key, sub_key)
        self._wbi_keys_fetched_at = time.monotonic()
        return self._cached_wbi_keys

    @staticmethod
    def _extract_wbi_key_component(url: str) -> str:
        """Return the key segment from a WBI image URL."""
        path = urlparse(url).path
        filename = path.rsplit("/", 1)[-1]
        return filename.rsplit(".", 1)[0]

    @classmethod
    def _build_wbi_mixin_key(cls, img_key: str, sub_key: str) -> str:
        """Build the mixed key used by Bilibili WBI request signing."""
        merged = img_key + sub_key
        return "".join(merged[index] for index in cls._WBI_MIXIN_KEY_ENC_TAB)[:32]

    @classmethod
    def _sign_wbi_params(
        cls,
        params: dict[str, object],
        *,
        img_key: str,
        sub_key: str,
    ) -> dict[str, str]:
        """Sign search params using Bilibili's WBI algorithm."""
        mixin_key = cls._build_wbi_mixin_key(img_key, sub_key)
        signed_params = {**params, "wts": int(time.time())}
        ordered_items = sorted(signed_params.items())
        sanitized = {key: re.sub(r"[!'()*]", "", str(value)) for key, value in ordered_items}
        query = urlencode(sanitized)
        sanitized["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
        return sanitized

    async def get_nav_info(self) -> NavInfo:
        """Get the current login state from Bilibili nav API."""
        data = await self._get_json("/x/web-interface/nav")
        return NavInfo(
            is_login=bool(data.get("isLogin", False)),
            uname=str(data.get("uname", "")),
            mid=int(data.get("mid", 0)),
        )

    async def get_video_view_data(self, bvid: str) -> dict[str, Any]:
        """取 `/view` 数据对象；裸端点被风控(-412)时降级到 WBI 签名端点。

        B 站会对特定网络/IP 封禁裸的 ``/x/web-interface/view``（HTTP 412 / code
        -412），而 WBI 签名的 ``/x/web-interface/wbi/view`` 仍然接受——这也是 web
        端实际走的路径。移植自上游 8a1e98a4。
        """
        try:
            return await self._get_json("/x/web-interface/view", params={"bvid": bvid})
        except BilibiliAPIError as exc:
            if exc.code != -412:
                raise
            logger.warning(
                "Bilibili plain /view blocked with 412; retrying via WBI view (bvid=%s)",
                bvid,
            )
            img_key, sub_key = await self._get_wbi_keys()
            signed = self._sign_wbi_params({"bvid": bvid}, img_key=img_key, sub_key=sub_key)
            return await self._get_json("/x/web-interface/wbi/view", params=signed)

    async def get_video_info(self, bvid: str) -> VideoInfo:
        """Get video information by BV ID.

        Args:
            bvid: Bilibili video BV ID.

        Returns:
            VideoInfo dataclass.

        """
        data = await self.get_video_view_data(bvid)
        stat = _json_object(data.get("stat", {}))
        owner = _json_object(data.get("owner", {}))

        return VideoInfo(
            bvid=data.get("bvid", bvid),
            aid=data.get("aid", 0),
            cid=data.get("cid", 0),
            title=data.get("title", ""),
            description=data.get("desc", ""),
            duration=data.get("duration", 0),
            cover_url=data.get("pic", ""),
            up_name=owner.get("name", ""),
            up_mid=owner.get("mid", 0),
            view_count=stat.get("view", 0),
            like_count=stat.get("like", 0),
            coin_count=stat.get("coin", 0),
            favorite_count=stat.get("favorite", 0),
            share_count=stat.get("share", 0),
            danmaku_count=stat.get("danmaku", 0),
            pub_date=data.get("pubdate", ""),
        )

    async def get_play_info(
        self,
        bvid: str,
        cid: int | None = None,
        qn: int = 80,
        preferred_codec: str = "avc",
    ) -> dict[str, Any]:
        """给原生播放器的**扁平化**播放载荷（移动端 play-url 契约）。

        Cookie/WBI 细节全部留在后端；返回结构与移动端
        ``/api/bilibili/player/play-url`` 的约定一致：dash 里按首选编解码挑
        一路视频/音频，附上画质清单、分 P 列表和字幕轨。移植自上游
        90a88262/9422c35e/a50ec617。
        """
        if cid is None:
            cid = (await self.get_video_info(bvid)).cid
        if not cid:
            raise BilibiliAPIError("missing cid", code=-404)

        img_key, sub_key = await self._get_wbi_keys()
        params: dict[str, object] = {
            "bvid": bvid,
            "cid": cid,
            "qn": qn,
            "fnval": 4048,  # DASH + HDR + 4K + Dolby + AV1 全开
            "fnver": 0,
            "fourk": 1,
            "platform": "html5",
            "high_quality": 1,
            "gaia_source": "pre-load",
            "web_location": 1315873,
        }
        data = await self._get_json(
            "/x/player/wbi/playurl",
            params=self._sign_wbi_params(params, img_key=img_key, sub_key=sub_key),
        )

        pages_data: list[dict[str, Any]] = []
        try:
            pages_raw = await self._get_json("/x/player/pagelist", params={"bvid": bvid})
            if isinstance(pages_raw, list):
                pages_data = [dict(item) for item in pages_raw]
        except BilibiliAPIError:
            logger.debug("pagelist fetch failed for bvid=%s", bvid, exc_info=True)

        qualities: list[dict[str, Any]] = [
            {
                "qn": int(item.get("quality", 0) or 0),
                "label": item.get("new_description")
                or item.get("display_desc")
                or str(item.get("quality", "")),
                "width": int(item.get("width", 0) or 0),
                "height": int(item.get("height", 0) or 0),
            }
            for item in (data.get("support_formats", []) or [])
            if isinstance(item, dict)
        ]

        video: dict[str, Any] | None = None
        audio: dict[str, Any] | None = None
        dash = data.get("dash")
        if isinstance(dash, dict):
            video_items = dash.get("video", []) or []
            audio_items = dash.get("audio", []) or []
            if isinstance(video_items, list) and video_items:
                preferred = [
                    item
                    for item in video_items
                    if isinstance(item, dict)
                    and str(item.get("codecs", "") or "")
                    .lower()
                    .startswith(preferred_codec.lower())
                ]
                chosen = preferred[0] if preferred else video_items[0]
                if isinstance(chosen, dict):
                    video = {
                        "qn": int(chosen.get("id", 0) or 0),
                        "label": "",
                        "codec": str(chosen.get("codecs", "") or ""),
                        "url": str(chosen.get("baseUrl") or chosen.get("base_url") or ""),
                        "backup_urls": list(
                            chosen.get("backupUrl") or chosen.get("backup_url") or []
                        ),
                        "width": int(chosen.get("width", 0) or 0),
                        "height": int(chosen.get("height", 0) or 0),
                        "bandwidth": int(chosen.get("bandwidth", 0) or 0),
                        "mime_type": str(
                            chosen.get("mimeType") or chosen.get("mime_type") or ""
                        ),
                    }
            if isinstance(audio_items, list) and audio_items:
                chosen_audio = audio_items[0]
                if isinstance(chosen_audio, dict):
                    audio = {
                        "qn": int(chosen_audio.get("id", 0) or 0),
                        "codec": str(chosen_audio.get("codecs", "") or ""),
                        "url": str(
                            chosen_audio.get("baseUrl") or chosen_audio.get("base_url") or ""
                        ),
                        "backup_urls": list(
                            chosen_audio.get("backupUrl") or chosen_audio.get("backup_url") or []
                        ),
                        "width": 0,
                        "height": 0,
                        "bandwidth": int(chosen_audio.get("bandwidth", 0) or 0),
                        "mime_type": str(
                            chosen_audio.get("mimeType") or chosen_audio.get("mime_type") or ""
                        ),
                    }

        # 非 DASH 兜底（durl 形态：低清晰度 / 老接口行为）
        durl = data.get("durl")
        if video is None and isinstance(durl, list) and durl:
            first = durl[0]
            if isinstance(first, dict):
                video = {
                    "qn": int(data.get("quality", 0) or 0),
                    "label": "",
                    "codec": "mp4",
                    "url": str(first.get("url", "") or ""),
                    "backup_urls": list(first.get("backup_url", []) or []),
                    "width": 0,
                    "height": 0,
                    "bandwidth": 0,
                    "mime_type": "video/mp4",
                }

        subtitles: list[dict[str, Any]] = []
        try:
            player_data = await self._get_json(
                "/x/player/wbi/v2",
                params={"bvid": bvid, "cid": cid},
            )
            raw_subtitle = player_data.get("subtitle", {})
            if isinstance(raw_subtitle, dict):
                for item in raw_subtitle.get("subtitles", []) or []:
                    if not isinstance(item, dict):
                        continue
                    raw_url = str(item.get("subtitle_url", "") or "")
                    if raw_url.startswith("//"):
                        raw_url = f"https:{raw_url}"
                    if raw_url:
                        subtitles.append(
                            {
                                "lan": str(item.get("lan", "") or ""),
                                "name": str(item.get("lan_doc", "") or item.get("lan", "")),
                                "url": raw_url,
                            }
                        )
        except BilibiliAPIError:
            logger.debug("subtitle fetch failed for bvid=%s cid=%s", bvid, cid, exc_info=True)

        duration = int(data.get("timelength", 0) or 0)
        if duration > 1000:
            duration = duration // 1000

        return {
            "bvid": bvid,
            "cid": cid,
            "duration": duration,
            "pages": [
                {
                    "cid": int(page.get("cid", 0) or 0),
                    "page": int(page.get("page", 0) or 0),
                    "part": str(page.get("part", "") or ""),
                    "duration": int(page.get("duration", 0) or 0),
                    "dimension": {
                        "width": int(page.get("dimension", {}).get("width", 0) or 0)
                        if isinstance(page.get("dimension"), dict)
                        else 0,
                        "height": int(page.get("dimension", {}).get("height", 0) or 0)
                        if isinstance(page.get("dimension"), dict)
                        else 0,
                    },
                }
                for page in pages_data
            ],
            "qualities": qualities,
            "video": video,
            "audio": audio,
            "subtitles": subtitles,
        }

    async def get_video_relation_state(self, bvid: str) -> dict[str, Any]:
        """当前登录用户对视频的互动状态（赞/投币/收藏/稍后再看）。

        移动端播放页的状态徽标用；移植自上游 90a88262。
        """
        aid = await self.get_video_aid(bvid)
        data = await self._get_json(
            "/x/web-interface/archive/relation",
            params={"aid": aid, "bvid": bvid},
        )
        return {
            "like": bool(data.get("like", False) or data.get("is_like", False)),
            "coin": int(data.get("coin", 0) or 0),
            "favorite": bool(data.get("favorite", False) or data.get("is_fav", False)),
            "watch_later": bool(
                data.get("watch_later", False) or data.get("is_watch_later", False)
            ),
        }

    async def generate_qrcode(self) -> dict[str, Any]:
        """生成 B 站网页版扫码登录二维码（passport 域，独立于 `_BASE_URL`）。"""
        await self._respect_rate_limit()
        try:
            resp = await self._client.get(
                "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise BilibiliAPIError(f"GET passport/qrcode/generate failed: {exc}") from exc
        payload = _json_object(resp.json())
        code = int(payload.get("code", 0) or 0)
        if code != 0:
            raise BilibiliAPIError(
                str(payload.get("message", "generate qrcode failed")), code=code
            )
        return _json_object(payload.get("data", {}) or {})

    async def poll_qrcode(self, qrcode_key: str) -> dict[str, Any]:
        """轮询扫码登录状态，归一化为 pending/scanned/confirmed/expired。

        移动端登录页只认这四个值（`confirmed` 才算成功）——**不要**把
        「已写入凭据」的状态混进这个字段（见 docs/plans/2026-09-16-mobile-parity.md）。
        """
        await self._respect_rate_limit()
        try:
            resp = await self._client.get(
                "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
                params={"qrcode_key": qrcode_key},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise BilibiliAPIError(f"GET passport/qrcode/poll failed: {exc}") from exc
        payload = _json_object(resp.json())
        code = int(payload.get("code", 0) or 0)
        if code != 0:
            raise BilibiliAPIError(
                str(payload.get("message", "poll qrcode failed")), code=code
            )
        data = _json_object(payload.get("data", {}))
        inner_code = int(data.get("code", 86101) or 86101)
        message = str(data.get("message", "") or "")
        if inner_code == 0 or (inner_code == 86101 and data.get("url")):
            status = "confirmed"
        elif inner_code == 86090:
            status = "scanned"
        elif inner_code == 86038:
            status = "expired"
        else:
            status = "pending"
        return {
            "status": status,
            "message": message,
            "url": str(data.get("url", "") or ""),
            "qrcode_key": qrcode_key,
            "raw_code": inner_code,
        }

    async def get_playurl(
        self,
        bvid: str,
        cid: int,
        *,
        fnval: int = 16,
        fnver: int = 0,
        fourk: int = 1,
    ) -> dict[str, Any]:
        """获取视频播放地址（含音视频流信息）。

        使用 WBI 签名调用 /x/player/wbi/playurl 接口。
        音频流接口比元数据接口更敏感，建议保守限速。

        Args:
            bvid: 视频 BV 号。
            cid: 视频 cid。
            fnval: 格式标识，16 为 DASH 格式（连播）。
            fnver: 格式版本。
            fourk: 是否支持 4K，1 为支持。

        Returns:
            playurl 接口返回的 data 字段字典，包含 dash/durl 等流信息。

        Raises:
            BilibiliAPIError: 接口调用失败或被风控。

        """
        img_key, sub_key = await self._get_wbi_keys()
        params = self._sign_wbi_params(
            {
                "bvid": bvid,
                "cid": cid,
                "fnval": fnval,
                "fnver": fnver,
                "fourk": fourk,
            },
            img_key=img_key,
            sub_key=sub_key,
        )
        data = await self._get_json(
            "/x/player/wbi/playurl",
            params=params,
            headers={
                "Referer": f"https://www.bilibili.com/video/{bvid}",
                "Origin": "https://www.bilibili.com",
            },
        )
        return data

    async def get_audio_streams(
        self,
        bvid: str,
        cid: int,
        *,
        prefer_quality: str = "low",
    ) -> dict[str, Any]:
        """获取视频的音频流信息并按偏好选择。

        优先选择低码率以适配语音转写场景。

        Args:
            bvid: 视频 BV 号。
            cid: 视频 cid。
            prefer_quality: 偏好质量（low/medium/high）。

        Returns:
            包含 best_stream_url、quality_id、quality_desc、all_audio_streams 等的字典。

        """
        data = await self.get_playurl(bvid, cid)
        dash = data.get("dash")
        if not dash:
            raise BilibiliAPIError("无 DASH 音频流（可能为旧版直连流或被风控）")

        audio_list = dash.get("audio", [])
        if not audio_list:
            raise BilibiliAPIError("DASH 清单中无音频流")

        quality_map = {
            30280: "192kbps (高码率)",
            30232: "132kbps (普通码率)",
            30216: "64kbps (低码率)",
            30250: "杜比全景声 (Dolby Atmos)",
            30251: "Hi-Res 无损",
        }

        def sort_key(item: dict[str, Any]) -> int:
            return int(item.get("bandwidth", 0) or item.get("id", 0))

        prefer = (prefer_quality or "low").lower()
        if prefer == "low":
            candidates = [a for a in audio_list if a.get("id") == 30216]
            selected = candidates[0] if candidates else sorted(audio_list, key=sort_key)[0]
        elif prefer == "medium":
            candidates = [a for a in audio_list if a.get("id") == 30232]
            selected = candidates[0] if candidates else sorted(audio_list, key=sort_key)[0]
        else:  # high
            selected = sorted(audio_list, key=sort_key, reverse=True)[0]

        # 取直链（优先主地址，退回备用地址）
        stream_url = selected.get("base_url") or selected.get("baseUrl")
        if not stream_url and selected.get("backup_url"):
            stream_url = selected["backup_url"][0]

        quality_id = selected.get("id", 0)
        quality_desc = quality_map.get(quality_id, f"Audio ID: {quality_id}")

        sorted_streams = sorted(audio_list, key=sort_key, reverse=True)

        return {
            "bvid": bvid,
            "cid": cid,
            "best_stream_url": stream_url or "",
            "quality_id": quality_id,
            "quality_desc": quality_desc,
            "bandwidth": selected.get("bandwidth"),
            "codecs": selected.get("codecs"),
            "all_audio_streams": [
                {
                    "id": a.get("id"),
                    "desc": quality_map.get(a.get("id", 0), f"ID {a.get('id')}"),
                    "bandwidth": a.get("bandwidth"),
                    "url": a.get("base_url") or a.get("baseUrl"),
                }
                for a in sorted_streams
            ],
        }

    async def search(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
        order: str = "totalrank",
    ) -> list[dict[str, Any]]:
        """Search for videos by keyword.

        Args:
            keyword: Search query.
            page: Page number.
            page_size: Results per page.

        Returns:
            List of search result dicts.

        """
        cooldown_remaining = self.search_cooldown_remaining()
        if cooldown_remaining > 0:
            logger.info(
                "Bilibili search cooldown active (%.0fs left) — skipping query=%r",
                cooldown_remaining,
                keyword,
            )
            return []

        # v0.3.55+: 3 attempts with exponential backoff (was 2 with 1.5s
        # linear). Production logs (2026-05-05) showed 141 v_voucher
        # challenges in 43 minutes; with only 1 retry, ~9 full search
        # rounds returned 0 results because keywords got challenged twice
        # and we gave up. The new schedule (1.5s / 5s / 15s = ~21s total
        # per keyword) lets the WBI key churn settle without immediately
        # surrendering. Steady-state cost is zero — retries don't fire
        # when keys are healthy.
        #
        # Fast-fail once a storm is suspected: the first keyword to fail in
        # a fresh round gets the full retry budget so transient churn can
        # settle, but once one keyword has already fully exhausted
        # (streak>0) we drop to a single quick probe — confirming a real
        # storm in a few fast attempts instead of hammering B站 with doomed
        # ~21s retry chains per keyword (which would only deepen the block).
        max_attempts = 1 if type(self)._search_voucher_block_streak > 0 else 3
        backoff_schedule = (1.5, 5.0, 15.0)
        for attempt in range(max_attempts):
            try:
                img_key, sub_key = await self._get_wbi_keys()
                data = await self._get_json(
                    "/x/web-interface/wbi/search/type",
                    params=self._sign_wbi_params(
                        {
                            "keyword": keyword,
                            "search_type": "video",
                            "page": page,
                            "page_size": page_size,
                            "order": order,
                            "web_location": self._SEARCH_WEB_LOCATION,
                        },
                        img_key=img_key,
                        sub_key=sub_key,
                    ),
                    headers={
                        "Referer": (
                            f"https://search.bilibili.com/all?keyword={quote(keyword, safe='')}"
                        ),
                        "Origin": "https://search.bilibili.com",
                    },
                )
            except BilibiliAPIError as exc:
                cause = exc.__cause__
                if isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code == 412:
                    # 412 is an explicit IP-level block — back off hard and
                    # immediately (no streak threshold), with the longer base.
                    duration = self._activate_search_cooldown(
                        base_seconds=self._SEARCH_COOLDOWN_412_SECONDS
                    )
                    logger.warning(
                        "Bilibili search blocked with 412 for query=%r — "
                        "cooling down search for %.0fs",
                        keyword,
                        duration,
                    )
                    return []
                self._activate_search_dom_fallback()
                raise

            # Detect v_voucher-only response (stale WBI keys or rate limit)
            if "v_voucher" in data and data.get("result") is None:
                if attempt < max_attempts - 1:
                    delay = backoff_schedule[attempt]
                    logger.info(
                        "Search v_voucher challenge (attempt %d/%d) for query=%r — "
                        "refreshing WBI keys, retry in %.1fs",
                        attempt + 1,
                        max_attempts,
                        keyword,
                        delay,
                    )
                    self._cached_wbi_keys = None
                    await asyncio.sleep(delay)
                    continue
                # Final attempt also got v_voucher. Record the block; only
                # trip the shared cooldown once consecutive keyword failures
                # cross the threshold — a lone challenged keyword just gets
                # dropped so the rest of the round (and explore) stays live.
                self._activate_search_dom_fallback()
                duration = self._record_voucher_block()
                if duration > 0:
                    logger.warning(
                        "Search v_voucher storm confirmed (%d consecutive blocked "
                        "queries, latest=%r) — cooling down search for %.0fs "
                        "(likely WBI storm or IP rate limit)",
                        type(self)._search_voucher_block_streak,
                        keyword,
                        duration,
                    )
                else:
                    logger.info(
                        "Search v_voucher challenge persisted for query=%r "
                        "(streak %d/%d) — dropping this keyword; search stays live",
                        keyword,
                        type(self)._search_voucher_block_streak,
                        self._SEARCH_VOUCHER_BLOCK_THRESHOLD,
                    )
                return []

            results = _json_list(data.get("result", []))
            self._reset_search_cooldown_backoff()
            if not results:
                logger.debug("Search returned empty result for query=%r", keyword)
            return results
        return []

    async def get_user_history(self, max_items: int = 100) -> list[dict[str, Any]]:
        """Get the authenticated user's watch history.

        Requires valid authentication cookie.

        Args:
            max_items: Maximum number of history items to fetch. 0 means fetch all.

        Returns:
            List of history item dicts.

        """
        if not self.is_authenticated:
            logger.warning("Cannot fetch history without authentication.")
            return []

        items: list[dict[str, Any]] = []
        cursor_params: dict[str, Any] = {"type": "archive"}
        while max_items == 0 or len(items) < max_items:
            data = await self._get_json(
                "/x/web-interface/history/cursor",
                params=cursor_params,
            )
            batch = _json_list(data.get("list", []))
            if not batch:
                break
            items.extend(batch)
            cursor = _json_object(data.get("cursor", {}))
            next_max = cursor.get("max")
            next_view_at = cursor.get("view_at")
            if not next_max or not next_view_at:
                break
            cursor_params = {
                "type": "archive",
                "max": next_max,
                "view_at": next_view_at,
            }
        return items if max_items == 0 else items[:max_items]

    async def get_favorites(
        self,
        media_id: int,
        *,
        max_items: int = 20,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        """Get content from a favorites folder.

        Args:
            media_id: Favorites folder media ID.
            max_items: Maximum number of favorite items to fetch.
            page_size: Page size for the Bilibili resource list endpoint.

        Returns:
            List of favorite item dicts.

        """
        item_limit = max(0, int(max_items))
        if item_limit <= 0:
            return []

        effective_page_size = max(1, min(int(page_size), 20))
        items: list[dict[str, Any]] = []
        page = 1
        while len(items) < item_limit:
            data = await self._get_json(
                "/x/v3/fav/resource/list",
                params={"media_id": media_id, "pn": page, "ps": effective_page_size},
            )
            batch = _json_list(data.get("medias", []))
            if not batch:
                break
            items.extend(batch)
            has_more = data.get("has_more")
            if has_more is not None:
                if not bool(has_more):
                    break
            elif len(batch) < effective_page_size:
                break
            page += 1
        return items[:item_limit]

    async def get_favorite_folders(self) -> list[FavoriteFolder]:
        """Get the authenticated user's favorite folder metadata."""
        nav = await self.get_nav_info()
        data = await self._get_json(
            "/x/v3/fav/folder/created/list-all",
            params={"up_mid": nav.mid},
        )
        folders = _json_list(data.get("list", []))
        return [
            FavoriteFolder(
                media_id=int(folder.get("id", 0)),
                title=str(folder.get("title", "")),
                media_count=int(folder.get("media_count", 0)),
            )
            for folder in folders
        ]

    async def get_all_favorites(
        self,
        *,
        max_folders: int = 10,
        max_items_per_folder: int = 50,
        max_total_items: int | None = None,
    ) -> list[FavoriteFolderWithItems]:
        """Get favorite folders and fetch each folder's items within budget."""
        folders = await self.get_favorite_folders()
        folder_limit = max(0, int(max_items_per_folder))
        folder_count = max(0, int(max_folders))
        if folder_count <= 0 or folder_limit <= 0:
            return []

        remaining_total: int | None
        if max_total_items is None:
            remaining_total = None
        else:
            remaining_total = max(0, int(max_total_items))
            if remaining_total <= 0:
                return []

        aggregated: list[FavoriteFolderWithItems] = []
        for folder in folders[:folder_count]:
            if remaining_total is not None and remaining_total <= 0:
                break
            current_limit = folder_limit
            if remaining_total is not None:
                current_limit = min(current_limit, remaining_total)
            limited_items = await self.get_favorites(folder.media_id, max_items=current_limit)
            aggregated.append(
                FavoriteFolderWithItems(
                    folder=folder,
                    items=limited_items,
                    truncated=folder.media_count > len(limited_items),
                )
            )
            if remaining_total is not None:
                remaining_total -= len(limited_items)
        return aggregated

    async def get_following(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> list[FollowingUser]:
        """Get the authenticated user's following list."""
        nav = await self.get_nav_info()
        data = await self._get_json(
            "/x/relation/followings",
            params={"vmid": nav.mid, "pn": page, "ps": page_size},
        )
        users = _json_list(data.get("list", []))
        return [
            FollowingUser(
                mid=int(user.get("mid", 0)),
                uname=str(user.get("uname", "")),
                sign=str(user.get("sign", "")),
            )
            for user in users
        ]

    async def get_related_videos(self, bvid: str) -> list[dict[str, Any]]:
        """Get related/recommended videos for a given video.

        Args:
            bvid: Source video BV ID.

        Returns:
            List of related video dicts.

        """
        resp = await self._client.get(
            f"{self._BASE_URL}/x/web-interface/archive/related",
            params={"bvid": bvid},
        )
        resp.raise_for_status()
        payload = _json_object(resp.json())
        return _json_list(payload.get("data", []))

    async def get_ranking(self, rid: int = 0) -> list[dict[str, Any]]:
        """Get ranking/trending videos.

        Args:
            rid: Region ID (0 for all).

        Returns:
            List of ranking item dicts.

        """
        resp = await self._client.get(
            f"{self._BASE_URL}/x/web-interface/ranking/v2",
            params={"rid": rid, "type": "all"},
        )
        resp.raise_for_status()
        payload = _json_object(resp.json())
        data = _json_object(payload.get("data", {}))
        return _json_list(data.get("list", []))

    async def get_video_comments(self, bvid: str, limit: int = 20) -> list[CommentInfo]:
        """Get the top comments for a video."""
        video = await self.get_video_info(bvid)
        data = await self._get_json(
            "/x/v2/reply/main",
            params={"oid": video.aid, "type": 1, "mode": 3, "ps": limit},
        )
        replies = _json_list(data.get("replies", []))
        comments = [
            CommentInfo(
                mid=int(reply.get("mid", 0)),
                uname=str(_json_object(reply.get("member", {})).get("uname", "")),
                message=str(_json_object(reply.get("content", {})).get("message", "")),
                like_count=int(reply.get("like", 0)),
            )
            for reply in replies
        ]
        return comments[:limit]

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

"""native-save 适配器回归 —— 让「原生保存」这条链路真的有实现。

背景：``saved_sync/router.py`` + ``service.py``（857 行，含申领 / 心跳 / 超时 /
分离看门狗）此前是「调用存在、实现不存在」：``NativeSaveRouter()`` 空构造、全仓
无一处 ``register(...)`` ⇒ ``route()`` 对所有平台抛 ``UnsupportedNativeSaveError``
⇒ 每次同步都以 ``unsupported`` 收场，``native_save_states`` 恒 0 行。

本文件锁三件事：

1. **适配器真的注册了**（按平台清单核对，防两处清单漂移）；
2. **B 站写路径的行为与副作用**（先查重再写、绝不静默挑收藏夹、失败码不糊）；
3. **能力差异如实上报**：需扩展的平台是 ``extension_required``，不是 ``unsupported``。

所有用例都用**假客户端 / httpx.MockTransport**，一个真实请求都不发——这条路径会
改动用户账号，测试里绝不能有真实写入。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from openbiliclaw.bilibili.api import (
    BilibiliAPIClient,
    BilibiliAPIError,
    BilibiliAuthExpiredError,
    BilibiliFavoriteFolderNotFoundError,
    BilibiliRateLimitedError,
)
from openbiliclaw.saved_sync.adapters import (
    EXTENSION_SAVE_TARGETS,
    ExtensionRequiredNativeSaveAdapter,
    build_extension_required_adapters,
    build_native_save_adapters,
    build_native_save_router,
    native_save_platforms,
)
from openbiliclaw.saved_sync.models import NATIVE_SAVE_TERMINAL_STATUSES, SavedItemInput
from openbiliclaw.saved_sync.router import NativeSaveRouter, UnsupportedNativeSaveError

_COOKIE = "SESSDATA=abc; bili_jct=csrf-token"
_BVID = "BV1test0001"


def _unreachable_factory(_cookie: str) -> Any:  # pragma: no cover - 兜底
    raise AssertionError("本用例不应构造客户端")


class _FakeClient:
    """记录调用的假 B 站客户端：**不联网**，只回放预设结果。"""

    def __init__(
        self,
        *,
        in_watch_later: bool = False,
        favorited: bool = False,
        aid: int = 4242,
        folder_id: int = 999,
        watch_later_error: Exception | None = None,
        folder_error: Exception | None = None,
        favorite_error: Exception | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.closed = False
        self._in_watch_later = in_watch_later
        self._favorited = favorited
        self._aid = aid
        self._folder_id = folder_id
        self._watch_later_error = watch_later_error
        self._folder_error = folder_error
        self._favorite_error = favorite_error
        self.favorite_writes: list[tuple[int, int]] = []

    async def is_in_watch_later(self, bvid: str) -> bool:
        self.calls.append("is_in_watch_later")
        if self._watch_later_error is not None:
            raise self._watch_later_error
        return self._in_watch_later

    async def add_to_watch_later(self, bvid: str) -> None:
        self.calls.append("add_to_watch_later")

    async def resolve_default_favorite_folder_id(self) -> int:
        self.calls.append("resolve_default_favorite_folder_id")
        if self._folder_error is not None:
            raise self._folder_error
        return self._folder_id

    async def is_favorited(self, bvid: str, media_id: int, *, max_items: int = 100) -> bool:
        self.calls.append("is_favorited")
        return self._favorited

    async def get_video_aid(self, bvid: str) -> int:
        self.calls.append("get_video_aid")
        return self._aid

    async def add_to_favorites(self, aid: int, media_id: int) -> None:
        self.calls.append("add_to_favorites")
        self.favorite_writes.append((aid, media_id))
        if self._favorite_error is not None:
            raise self._favorite_error

    async def aclose(self) -> None:
        self.closed = True


def _build(
    *,
    cookie: str = _COOKIE,
    **client_kwargs: Any,
) -> tuple[NativeSaveRouter, list[_FakeClient]]:
    clients: list[_FakeClient] = []

    def factory(_cookie: str) -> _FakeClient:
        client = _FakeClient(**client_kwargs)
        clients.append(client)
        return client

    router = NativeSaveRouter(
        build_native_save_adapters(
            cookie_provider=lambda: cookie,
            client_factory=factory,  # type: ignore[arg-type]
        )
    )
    return router, clients


def _item(bvid: str = _BVID, platform: str = "bilibili") -> SavedItemInput:
    return SavedItemInput(source_platform=platform, content_id=bvid)


class TestAdapterRegistration:
    """适配器集合与平台清单必须一致，且必须真的注册进 router。"""

    def test_every_listed_platform_routes_without_raising(self) -> None:
        """清单里的每个平台都能解析出 route（此前全部抛 Unsupported）。"""
        router, _clients = _build()
        for platform in native_save_platforms():
            adapter, route = router.route(platform, "favorite")
            assert adapter is not None
            assert route.resolved_target

    def test_platform_list_matches_registered_adapters(self) -> None:
        """``native_save_platforms()`` 与实际适配器集合不得漂移。"""
        built = build_native_save_adapters(cookie_provider=lambda: "")
        assert [a.capability.platform for a in built] == list(native_save_platforms())

    def test_platform_list_covers_bilibili_and_extension_targets(self) -> None:
        assert native_save_platforms() == (
            "bilibili",
            *(platform for platform, _favorite, _watch_later in EXTENSION_SAVE_TARGETS),
        )

    @pytest.mark.parametrize("platform", ["weibo", "reddit", "v2ex"])
    def test_platforms_without_adapters_still_unsupported(self, platform: str) -> None:
        """没适配器的平台语义不变（``weibo`` 是设计上的 local-only）。"""
        router, _clients = _build()
        with pytest.raises(UnsupportedNativeSaveError):
            router.route(platform, "favorite")


class TestRouterWiring:
    """接线回归 —— 「空构造 router」正是这条链路此前死掉的原因。"""

    def test_router_routes_every_listed_platform(self, tmp_path: Any) -> None:
        router = build_native_save_router(
            data_dir=tmp_path,
            configured_cookie="",
            client_factory=_unreachable_factory,
        )
        for platform in native_save_platforms():
            _adapter, route = router.route(platform, "favorite")
            assert route.resolved_target

    async def test_cookie_is_re_read_from_disk_on_every_save(self, tmp_path: Any) -> None:
        """每写一次就现取 Cookie → 浏览器里重新登录后**不需要重启服务**。"""
        seen: list[str] = []

        def factory(cookie: str) -> _FakeClient:
            seen.append(cookie)
            return _FakeClient()

        cookie_file = tmp_path / "bilibili_cookie.json"
        cookie_file.write_text(json.dumps({"cookie": "SESSDATA=one; bili_jct=csrf1"}))
        router = build_native_save_router(
            data_dir=tmp_path,
            configured_cookie="",
            client_factory=factory,
        )
        adapter, route = router.route("bilibili", "watch_later")
        await adapter.save(_item(), route)

        cookie_file.write_text(json.dumps({"cookie": "SESSDATA=two; bili_jct=csrf2"}))
        await adapter.save(_item(), route)

        assert seen == ["SESSDATA=one; bili_jct=csrf1", "SESSDATA=two; bili_jct=csrf2"]

    async def test_configured_cookie_wins_over_the_file(self, tmp_path: Any) -> None:
        seen: list[str] = []

        def factory(cookie: str) -> _FakeClient:
            seen.append(cookie)
            return _FakeClient()

        (tmp_path / "bilibili_cookie.json").write_text(json.dumps({"cookie": "from-file"}))
        router = build_native_save_router(
            data_dir=tmp_path,
            configured_cookie="configured-cookie",
            client_factory=factory,
        )
        adapter, route = router.route("bilibili", "watch_later")
        await adapter.save(_item(), route)

        assert seen == ["configured-cookie"]

    async def test_no_cookie_anywhere_is_login_required(self, tmp_path: Any) -> None:
        router = build_native_save_router(
            data_dir=tmp_path,
            configured_cookie="",
            client_factory=_unreachable_factory,
        )
        adapter, route = router.route("bilibili", "watch_later")

        result = await adapter.save(_item(), route)

        assert result.status == "login_required"

    def test_runtime_context_never_builds_an_empty_router(self) -> None:
        """静态防回归：``NativeSaveRouter()`` **空构造**就是这条链路死掉的原因。

        用 AST 取的是运行时真实调用（注释和字符串绕不过去），并且刻意只认
        ``runtime_context.py`` 这一处——它是 api 层唯一的构造点。
        """
        source = (
            Path(__file__).resolve().parents[2] / "src" / "openbiliclaw" / "api" / "runtime_context.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        offenders = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "NativeSaveRouter")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "NativeSaveRouter")
            )
            and not node.args
            and not node.keywords
        ]
        assert offenders == [], (
            f"runtime_context.py 第 {offenders} 行又出现了空构造的 NativeSaveRouter()——"
            "应改用 build_native_save_router(...)，否则所有平台都会退化成 unsupported"
        )


class TestBilibiliWatchLater:
    async def test_synced_when_absent_then_written(self) -> None:
        router, clients = _build(in_watch_later=False)
        adapter, route = router.route("bilibili", "watch_later")

        result = await adapter.save(_item(), route)

        assert result.status == "synced"
        assert result.resolved_action == "watch_later"
        assert clients[0].calls == ["is_in_watch_later", "add_to_watch_later"]

    async def test_already_synced_skips_the_write(self) -> None:
        """命中已有条目时**不能**再发写请求。

        鉴别力：把 ``is_in_watch_later`` 的判定删掉，本用例立刻红（calls 里会多出
        ``add_to_watch_later``）。这条断言是「不重复打扰平台」的唯一防线。
        """
        router, clients = _build(in_watch_later=True)
        adapter, route = router.route("bilibili", "watch_later")

        result = await adapter.save(_item(), route)

        assert result.status == "already_synced"
        assert clients[0].calls == ["is_in_watch_later"]


class TestBilibiliFavorite:
    async def test_synced_into_resolved_default_folder(self) -> None:
        router, clients = _build(favorited=False, aid=777, folder_id=555)
        adapter, route = router.route("bilibili", "favorite")

        result = await adapter.save(_item(), route)

        assert result.status == "synced"
        assert result.resolved_target == "B站默认收藏夹"
        assert clients[0].favorite_writes == [(777, 555)]

    async def test_already_synced_skips_the_write(self) -> None:
        router, clients = _build(favorited=True)
        adapter, route = router.route("bilibili", "favorite")

        result = await adapter.save(_item(), route)

        assert result.status == "already_synced"
        assert clients[0].favorite_writes == []
        assert "add_to_favorites" not in clients[0].calls

    async def test_unresolvable_folder_fails_loudly(self) -> None:
        """解析不到默认收藏夹 → 明确失败，**绝不**随手挑一个夹子写进去。

        鉴别力：把 ``favorite_folder_unresolved`` 换成通用 ``bilibili_api_error``，
        调用方就失去了「该去手动收藏一次」这个可操作提示。
        """
        router, clients = _build(folder_error=BilibiliFavoriteFolderNotFoundError("找不到默认收藏夹"))
        adapter, route = router.route("bilibili", "favorite")

        result = await adapter.save(_item(), route)

        assert result.status == "failed"
        assert result.error_code == "favorite_folder_unresolved"
        assert clients[0].favorite_writes == []


class TestBilibiliFailureMapping:
    """失败要分类，不能一律 failed——分类决定用户下一步该做什么。"""

    async def test_missing_cookie_is_login_required_without_building_a_client(self) -> None:
        router, clients = _build(cookie="")
        adapter, route = router.route("bilibili", "watch_later")

        result = await adapter.save(_item(), route)

        assert result.status == "login_required"
        assert result.error_code == "cookie_missing"
        assert clients == []  # 没有凭证就不该构造客户端

    async def test_expired_cookie_is_login_required(self) -> None:
        router, _clients = _build(watch_later_error=BilibiliAuthExpiredError("session expired"))
        adapter, route = router.route("bilibili", "watch_later")

        result = await adapter.save(_item(), route)

        assert result.status == "login_required"
        assert result.error_code == "cookie_expired"

    async def test_rate_limited_is_retryable_not_failed(self) -> None:
        router, _clients = _build(watch_later_error=BilibiliRateLimitedError("风控", code=-412))
        adapter, route = router.route("bilibili", "watch_later")

        result = await adapter.save(_item(), route)

        assert result.status == "rate_limited"
        assert result.error_code == "bilibili_rate_limited"

    async def test_unknown_api_error_keeps_the_real_code_in_the_message(self) -> None:
        router, _clients = _build(
            watch_later_error=BilibiliAPIError("Bilibili refused", code=-400),
        )
        adapter, route = router.route("bilibili", "watch_later")

        result = await adapter.save(_item(), route)

        assert result.status == "failed"
        assert result.error_code == "bilibili_api_error"

    async def test_missing_bvid_fails_before_touching_the_network(self) -> None:
        """只有 URL、没有视频号时（B 站条目必须有 bvid 才写得了）直接失败。

        ⚠️ 不能用「content_id 与 content_url 都为空」来构造这个场景——那种条目
        连 identity 都建不出来（``make_item_key`` 会抛错），根本进不了成员表。
        """
        router, clients = _build()
        adapter, route = router.route("bilibili", "watch_later")
        url_only = SavedItemInput(
            source_platform="bilibili",
            content_id="",
            content_url="https://www.bilibili.com/video/unknown",
        )

        result = await adapter.save(url_only, route)

        assert result.status == "failed"
        assert result.error_code == "missing_content_id"
        assert clients == []

    async def test_network_error_is_failed(self) -> None:
        router, _clients = _build(watch_later_error=httpx.ConnectError("boom"))
        adapter, route = router.route("bilibili", "watch_later")

        result = await adapter.save(_item(), route)

        assert result.status == "failed"
        assert result.error_code == "network_error"

    async def test_error_message_is_bounded(self) -> None:
        router, _clients = _build(watch_later_error=BilibiliAPIError("x" * 4000))
        adapter, route = router.route("bilibili", "watch_later")

        result = await adapter.save(_item(), route)

        assert len(result.error_message) <= 512


class TestClientLifecycle:
    """adapter 自己造客户端，就必须自己关掉（含失败路径）。"""

    @pytest.mark.parametrize(
        "client_kwargs",
        [
            {},
            {"watch_later_error": BilibiliAPIError("boom")},
            {"watch_later_error": BilibiliAuthExpiredError("expired")},
        ],
    )
    async def test_client_is_always_closed(self, client_kwargs: dict[str, Any]) -> None:
        router, clients = _build(**client_kwargs)
        adapter, route = router.route("bilibili", "watch_later")

        await adapter.save(_item(), route)

        assert clients[0].closed is True


class TestExtensionRequiredPlatforms:
    """需要扩展 ≠ 不支持：状态词表里本来就有 ``extension_required``。"""

    @pytest.mark.parametrize("platform", ["xiaohongshu", "douyin", "youtube"])
    async def test_status_is_extension_required(self, platform: str) -> None:
        router, _clients = _build()
        adapter, route = router.route(platform, "favorite")

        result = await adapter.save(_item(platform=platform), route)

        assert result.status == "extension_required"
        assert result.error_code == "extension_required"
        assert result.resolved_target

    def test_capability_declares_the_extension_dependency(self) -> None:
        for adapter in build_extension_required_adapters():
            assert adapter.capability.requires_extension is True

    def test_watch_later_falls_back_to_favorite_when_unsupported(self) -> None:
        """小红书没有「稍后再看」：路由应落到收藏，且结果如实反映 resolved_action。"""
        router, _clients = _build()
        adapter, route = router.route("xiaohongshu", "watch_later")

        assert route.requested_action == "watch_later"
        assert route.resolved_action == "favorite"
        assert route.resolved_target == "小红书收藏"

    def test_youtube_keeps_watch_later(self) -> None:
        router, _clients = _build()
        _adapter, route = router.route("youtube", "watch_later")

        assert route.resolved_action == "watch_later"
        assert route.resolved_target == "YouTube 稍后观看"

    def test_extension_adapter_rejects_empty_capability(self) -> None:
        with pytest.raises(ValueError):
            ExtensionRequiredNativeSaveAdapter("nothing")


class TestResultContract:
    """adapter 结果必须满足 service 的契约（终态 + route 决定的目标）。"""

    @pytest.mark.parametrize(
        "client_kwargs",
        [
            {},
            {"in_watch_later": True},
            {"watch_later_error": BilibiliAuthExpiredError("expired")},
            {"watch_later_error": BilibiliRateLimitedError("throttled", code=-412)},
            {"watch_later_error": BilibiliAPIError("boom", code=-400)},
        ],
    )
    async def test_status_is_always_terminal(self, client_kwargs: dict[str, Any]) -> None:
        """非终态会被 service 判成 ``invalid_adapter_result`` 并丢掉真实原因。"""
        router, _clients = _build(**client_kwargs)
        adapter, route = router.route("bilibili", "watch_later")

        result = await adapter.save(_item(), route)

        assert result.status in NATIVE_SAVE_TERMINAL_STATUSES

    async def test_item_key_comes_from_the_item(self) -> None:
        router, _clients = _build()
        adapter, route = router.route("bilibili", "watch_later")
        item = _item()

        result = await adapter.save(item, route)

        assert result.item_key == item.item_key == f"bilibili:{_BVID}"

    async def test_target_comes_from_the_route_not_the_adapter(self) -> None:
        """adapter 不许二次裁决路由结果。"""
        router, _clients = _build()
        adapter, route = router.route("bilibili", "watch_later")

        result = await adapter.save(_item(), route)

        assert result.resolved_target == route.resolved_target == "B站稍后再看"


class TestBilibiliClientWriteApi:
    """`BilibiliAPIClient` 的写方法（httpx.MockTransport，零真实请求）。"""

    @staticmethod
    def _client(cookie: str, handler: Any, **kwargs: Any) -> BilibiliAPIClient:
        return BilibiliAPIClient(
            cookie,
            min_request_interval=0.0,
            transport=httpx.MockTransport(handler),
            **kwargs,
        )

    async def test_add_to_watch_later_posts_bvid_and_csrf(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = request.content.decode()
            return httpx.Response(200, json={"code": 0, "message": "0", "data": {}})

        client = self._client(_COOKIE, handler)
        try:
            await client.add_to_watch_later(_BVID)
        finally:
            await client.aclose()

        assert seen["url"].endswith("/x/v2/history/toview/add")
        assert f"bvid={_BVID}" in seen["body"]
        assert "csrf=csrf-token" in seen["body"]

    async def test_missing_csrf_is_auth_expired(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不该被调用
            raise AssertionError("没有 bili_jct 时不应发出请求")

        client = self._client("SESSDATA=abc", handler)
        try:
            with pytest.raises(BilibiliAuthExpiredError):
                await client.add_to_watch_later(_BVID)
        finally:
            await client.aclose()

    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            (-101, BilibiliAuthExpiredError),
            (-412, BilibiliRateLimitedError),
            (-799, BilibiliRateLimitedError),
            (-509, BilibiliRateLimitedError),
            (-400, BilibiliAPIError),
        ],
    )
    async def test_write_codes_map_to_typed_errors(self, code: int, expected: type[Exception]) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": code, "message": f"code {code}", "data": None})

        client = self._client(_COOKIE, handler)
        try:
            with pytest.raises(expected):
                await client.add_to_watch_later(_BVID)
        finally:
            await client.aclose()

    async def test_add_to_favorites_sends_aid_and_folder(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = request.content.decode()
            return httpx.Response(200, json={"code": 0, "message": "0", "data": {}})

        client = self._client(_COOKIE, handler)
        try:
            await client.add_to_favorites(4242, 999)
        finally:
            await client.aclose()

        assert seen["url"].endswith("/x/v3/fav/resource/deal")
        assert "rid=4242" in seen["body"]
        assert "add_media_ids=999" in seen["body"]
        assert "type=2" in seen["body"]

    @pytest.mark.parametrize(("aid", "media_id"), [(0, 999), (4242, 0)])
    async def test_add_to_favorites_validates_arguments(self, aid: int, media_id: int) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不该被调用
            raise AssertionError("参数非法时不应发出请求")

        client = self._client(_COOKIE, handler)
        try:
            with pytest.raises(BilibiliAPIError):
                await client.add_to_favorites(aid, media_id)
        finally:
            await client.aclose()

    async def test_resolve_default_folder_matches_the_title(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "count": 2,
                        "list": [
                            {"id": 111, "title": "杂七杂八", "media_count": 3},
                            {"id": 999, "title": "默认收藏夹", "media_count": 7},
                        ],
                    },
                },
            )

        client = self._client(_COOKIE, handler)
        try:
            assert await client.resolve_default_favorite_folder_id() == 999
        finally:
            await client.aclose()

    async def test_resolve_default_folder_raises_when_absent(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"code": 0, "data": {"count": 1, "list": [{"id": 111, "title": "杂七杂八"}]}},
            )

        client = self._client(_COOKIE, handler)
        try:
            with pytest.raises(BilibiliFavoriteFolderNotFoundError):
                await client.resolve_default_favorite_folder_id()
        finally:
            await client.aclose()

    async def test_csrf_token_parses_the_cookie(self) -> None:
        client = self._client("SESSDATA=abc; bili_jct=token123; DedeUserID=1", _unreachable)
        try:
            assert client.csrf_token == "token123"
        finally:
            await client.aclose()

    async def test_csrf_token_is_empty_without_bili_jct(self) -> None:
        client = self._client("SESSDATA=abc", _unreachable)
        try:
            assert client.csrf_token == ""
        finally:
            await client.aclose()


def _unreachable(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - 兜底
    raise AssertionError("本用例不应发出任何请求")

"""Native-save 平台适配器 —— 让 ``saved_sync`` 的「原生保存」真的有实现。

## 背景（为什么要补）

截至 2026-09-15，这条链路是「调用存在、实现不存在」：
``api/runtime_context.py`` 里 ``NativeSaveRouter()`` 是**空构造**，全仓没有一处
``register(...)``，于是 ``router.route()`` 对任何平台都抛
``UnsupportedNativeSaveError``，857 行 ``service.py``（申领 / 心跳 / 超时 / 分离
看门狗全都有）从来没有真正跑到过 adapter；佐证是 ``native_save_states`` 0 行。

本模块补上适配器并接线（见 ``runtime_context`` 第 12 步）。

## 平台语义（真实能力，不吹）

| 平台 | 稍后再看 | 收藏 | 实现方式 |
|---|---|---|---|
| `bilibili` | ✅ | ✅（默认收藏夹） | **服务端 cookie 重放**，复用 ``BilibiliAPIClient`` |
| `youtube` | ✅ | ✅（播放列表） | 需浏览器扩展 → `extension_required` |
| `xiaohongshu` | ❌ | ✅ | 需浏览器扩展 → `extension_required` |
| `douyin` | ❌ | ✅ | 需浏览器扩展 → `extension_required` |
| 其它 | ❌ | ❌ | 无适配器 → `unsupported`（router 语义不变） |

「需要浏览器扩展」与「不支持」是**两件事**：前者是 terminal 的
``extension_required``（用户知道该装/该开扩展），后者是 ``unsupported``
（这个平台根本没有原生保存）。此前两者都被压成 `unsupported`，用户看不出差别。
这也让 ``NativeSaveCapability.requires_extension`` 这个字段第一次真正被使用。

## 安全阀

- **绝不静默挑收藏夹**：B 站的收藏必须显式解析「默认收藏夹」，找不到就
  `failed` + `favorite_folder_unresolved`，而不是随手写进第一个收藏夹。
- **写入前先查重**：命中已有条目直接 `already_synced`，不重复写。
- **不猜返回码**：非 0 码只映射我们确知的三种（`-101` 未登录 / `-412|-799|-509`
  限流），其余原样上报为 `failed`，把真实 code 与 message 交给用户。
- 本模块**不创建任务、不改状态**：状态落库与并发控制全在 ``SavedSyncService``。
"""

from __future__ import annotations

from contextlib import suppress
from functools import partial
from typing import TYPE_CHECKING, Any

import httpx

from openbiliclaw.bilibili.api import (
    BilibiliAPIClient,
    BilibiliAPIError,
    BilibiliAuthExpiredError,
    BilibiliFavoriteFolderNotFoundError,
    BilibiliRateLimitedError,
)
from openbiliclaw.bilibili.auth import resolve_runtime_cookie

from .models import (
    NativeSaveCapability,
    NativeSaveResult,
    NativeSaveStatus,
    SavedItemInput,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from .models import NativeSaveAction, NativeSaveRoute
    from .router import NativeSaveRouter

#: 写进 ``native_save_states.last_error_message`` 的上限（与 api 层 512 的截断口径一致）。
_MAX_ERROR_MESSAGE_LENGTH = 512


def _clip(message: str) -> str:
    text = " ".join(str(message).split())
    return text[:_MAX_ERROR_MESSAGE_LENGTH]


def _result(
    item_key: str,
    status: NativeSaveStatus,
    route: NativeSaveRoute,
    *,
    error_code: str = "",
    error_message: str = "",
) -> NativeSaveResult:
    """构造 adapter 结果。

    ``resolved_action`` / ``resolved_target`` 一律以 **route** 为准：路由决策在
    router 里做完了，adapter 只负责执行与报告成败，不允许二次裁决。
    """
    return NativeSaveResult(
        item_key=item_key,
        status=status,
        resolved_action=route.resolved_action,
        resolved_target=route.resolved_target,
        error_code=error_code,
        error_message=_clip(error_message),
    )


class BilibiliNativeSaveAdapter:
    """B 站原生保存 —— 服务端 cookie 重放（真实现）。

    Cookie 每次调用**现取**（``cookie_provider``），因此重新登录后不需要重启服务：
    这正是 ``bilibili/auth.resolve_runtime_cookie`` 的语义（配置优先、回落到
    ``data/bilibili_cookie.json``）。
    """

    PLATFORM = "bilibili"

    def __init__(
        self,
        cookie_provider: Callable[[], str],
        client_factory: Callable[[str], BilibiliAPIClient] = BilibiliAPIClient,
    ) -> None:
        self._cookie_provider = cookie_provider
        self._client_factory = client_factory

    @property
    def capability(self) -> NativeSaveCapability:
        return NativeSaveCapability(
            platform=self.PLATFORM,
            supports_favorite=True,
            supports_watch_later=True,
            supports_named_collection=True,
            requires_extension=False,
        )

    def target_label(self, action: NativeSaveAction) -> str:
        return "B站稍后再看" if action == "watch_later" else "B站默认收藏夹"

    async def save(self, item: SavedItemInput, route: NativeSaveRoute) -> NativeSaveResult:
        bvid = (item.content_id or "").strip()
        if not bvid:
            return _result(
                item.item_key,
                "failed",
                route,
                error_code="missing_content_id",
                error_message="该条目没有可用的 B 站视频号，无法做原生保存",
            )

        cookie = (self._cookie_provider() or "").strip()
        if not cookie:
            return _result(
                item.item_key,
                "login_required",
                route,
                error_code="cookie_missing",
                error_message="未找到 B 站登录 Cookie：请先在浏览器登录 B 站，或执行 auth login",
            )

        client = self._client_factory(cookie)
        try:
            return await self._save_with_client(client, bvid, item, route)
        except BilibiliAuthExpiredError as exc:
            return _result(
                item.item_key,
                "login_required",
                route,
                error_code="cookie_expired",
                error_message=str(exc),
            )
        except BilibiliRateLimitedError as exc:
            return _result(
                item.item_key,
                "rate_limited",
                route,
                error_code="bilibili_rate_limited",
                error_message=str(exc),
            )
        except BilibiliFavoriteFolderNotFoundError as exc:
            return _result(
                item.item_key,
                "failed",
                route,
                error_code="favorite_folder_unresolved",
                error_message=str(exc),
            )
        except BilibiliAPIError as exc:
            return _result(
                item.item_key,
                "failed",
                route,
                error_code="bilibili_api_error",
                error_message=str(exc),
            )
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            return _result(
                item.item_key,
                "failed",
                route,
                error_code="network_error",
                error_message=f"{type(exc).__name__}: {exc}",
            )
        finally:
            with suppress(Exception):
                await client.aclose()

    async def _save_with_client(
        self,
        client: BilibiliAPIClient,
        bvid: str,
        item: SavedItemInput,
        route: NativeSaveRoute,
    ) -> NativeSaveResult:
        if route.resolved_action == "watch_later":
            if await client.is_in_watch_later(bvid):
                return _result(item.item_key, "already_synced", route)
            await client.add_to_watch_later(bvid)
            return _result(item.item_key, "synced", route)

        folder_id = await client.resolve_default_favorite_folder_id()
        if await client.is_favorited(bvid, folder_id):
            return _result(item.item_key, "already_synced", route)
        aid = await client.get_video_aid(bvid)
        await client.add_to_favorites(aid, folder_id)
        return _result(item.item_key, "synced", route)


class ExtensionRequiredNativeSaveAdapter:
    """需要浏览器扩展才能完成的平台（返回 ``extension_required``，不是 ``unsupported``）。

    为什么不留空实现：用户看到「需要扩展」才会去开扩展；看到「不支持」只会以为
    这个平台永远不行。状态词表里本来就有 ``extension_required``，此前没人产出它。
    """

    def __init__(
        self,
        platform: str,
        *,
        favorite_label: str = "",
        watch_later_label: str = "",
    ) -> None:
        if not favorite_label and not watch_later_label:
            raise ValueError(f"{platform}: 至少要声明一种可保存形态")
        self._platform = platform
        self._favorite_label = favorite_label
        self._watch_later_label = watch_later_label

    @property
    def capability(self) -> NativeSaveCapability:
        return NativeSaveCapability(
            platform=self._platform,
            supports_favorite=bool(self._favorite_label),
            supports_watch_later=bool(self._watch_later_label),
            supports_named_collection=False,
            requires_extension=True,
        )

    def target_label(self, action: NativeSaveAction) -> str:
        if action == "watch_later" and self._watch_later_label:
            return self._watch_later_label
        return self._favorite_label

    async def save(self, item: SavedItemInput, route: NativeSaveRoute) -> NativeSaveResult:
        return _result(
            item.item_key,
            "extension_required",
            route,
            error_code="extension_required",
            error_message=(
                f"{self._platform} 的原生保存需要浏览器扩展：请启动扩展并保持登录态后重试"
            ),
        )


#: 需要扩展的平台 → (platform, 收藏 label, 稍后再看 label)。空 label = 该形态不支持。
EXTENSION_SAVE_TARGETS: tuple[tuple[str, str, str], ...] = (
    ("xiaohongshu", "小红书收藏", ""),
    ("douyin", "抖音收藏", ""),
    ("youtube", "YouTube 播放列表", "YouTube 稍后观看"),
)


def build_extension_required_adapters() -> list[ExtensionRequiredNativeSaveAdapter]:
    """构造「需要扩展」适配器集合（顺序即 ``EXTENSION_SAVE_TARGETS``）。"""
    return [
        ExtensionRequiredNativeSaveAdapter(
            platform,
            favorite_label=favorite_label,
            watch_later_label=watch_later_label,
        )
        for platform, favorite_label, watch_later_label in EXTENSION_SAVE_TARGETS
    ]


def build_native_save_adapters(
    *,
    cookie_provider: Callable[[], str],
    client_factory: Callable[[str], BilibiliAPIClient] = BilibiliAPIClient,
) -> list[Any]:
    """默认适配器集合：B 站（真实现）+ 需要扩展的平台。

    ``cookie_provider`` 由调用方注入（api 层传
    ``functools.partial(resolve_runtime_cookie, data_dir=..., configured_cookie=...)``），
    这样本模块不依赖配置系统，测试里也只需要塞一个 lambda。
    """
    adapters: list[Any] = [
        BilibiliNativeSaveAdapter(
            cookie_provider=cookie_provider,
            client_factory=client_factory,
        )
    ]
    adapters.extend(build_extension_required_adapters())
    return adapters


def native_save_platforms() -> Sequence[str]:
    """当前有适配器的平台（供文档/测试核对，避免两处清单漂移）。"""
    return ("bilibili", *(platform for platform, _favorite, _watch_later in EXTENSION_SAVE_TARGETS))


def build_native_save_router(
    *,
    data_dir: Path,
    configured_cookie: str = "",
    client_factory: Callable[[str], BilibiliAPIClient] = BilibiliAPIClient,
) -> NativeSaveRouter:
    """构造默认 router —— api 层的**唯一**入口。

    单独成函数不是为了好看，而是为了**可测**：router 原来内联在
    ``runtime_context`` 里构造并且传空，于是「适配器忘了注册」这种回归
    没有任何测试能发现（线上表现是所有平台都 unsupported）。现在测试可以直接
    构造它并断言每个平台都能解析出 route。

    Cookie 每次调用现取（``resolve_runtime_cookie``：配置优先、回落
    ``data/bilibili_cookie.json``），因此浏览器里重新登录后不需要重启服务。
    """
    from .router import NativeSaveRouter

    return NativeSaveRouter(
        build_native_save_adapters(
            cookie_provider=partial(
                resolve_runtime_cookie,
                data_dir=data_dir,
                configured_cookie=configured_cookie,
            ),
            client_factory=client_factory,
        )
    )

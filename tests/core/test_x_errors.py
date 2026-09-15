"""core.x_errors 契约回归 —— 类型身份必须跨 import 路径唯一（K5）。

背景：X 异常原本定义在 ``sources.x_client``，但 ``storage.x_health`` 需要捕获它们
做健康态分类，于是形成 **storage → sources 的反向依赖**（K5）。2026-09-15 把它们
下沉到 ``openbiliclaw.core.x_errors``：抛出方（``sources.x_client``）从 core import
并 re-export，捕获方（``storage.x_health``）直接从 core import。

这里锁的不是「类还在不在」，而是**它们是不是同一个对象**。因为 ``except`` /
``isinstance`` 走的是类型身份：一旦有人把类「复制」一份（或把异常改回定义在
sources 里），health 分类不会报错，只会静默退化成 ``rate_limited`` / ``ok``——
排障时表现为「cookie 过期了但健康面板显示正常」。
"""

from __future__ import annotations

import inspect

import pytest

from openbiliclaw.core import x_errors
from openbiliclaw.sources import x_client
from openbiliclaw.storage.x_health import (
    BLOCKED,
    EXPIRED_COOKIE,
    MISSING_COOKIE,
    RATE_LIMITED,
    health_state_for_error,
)

#: 对外契约的 5 个异常名（抛出方 __all__ 与捕获方 import 的交集）。
_PUBLIC_ERRORS = (
    "XClientError",
    "XMissingCookieError",
    "XAuthError",
    "XBlockedError",
    "XRateLimitError",
)


def _core_class(name: str) -> type[BaseException]:
    cls = getattr(x_errors, name)
    assert isinstance(cls, type)
    return cls


class TestHierarchy:
    """层级形状：一个基类 + 4 个叶子，且基类是 RuntimeError。"""

    def test_base_is_a_runtime_error(self) -> None:
        assert issubclass(x_errors.XClientError, RuntimeError)

    @pytest.mark.parametrize("name", [n for n in _PUBLIC_ERRORS if n != "XClientError"])
    def test_leaves_extend_the_shared_base(self, name: str) -> None:
        assert issubclass(getattr(x_errors, name), x_errors.XClientError)

    @pytest.mark.parametrize("name", [n for n in _PUBLIC_ERRORS if n != "XClientError"])
    def test_leaves_are_siblings_not_a_chain(self, name: str) -> None:
        """四个叶子互不为父子。

        鉴别力：若 ``XAuthError`` 被写成 ``XBlockedError`` 的子类，健康分类里
        ``isinstance(exc, XBlockedError)`` 会提前命中，403 与 401 就被混为一谈。
        """
        others = [n for n in _PUBLIC_ERRORS if n not in {name, "XClientError"}]
        assert not any(issubclass(getattr(x_errors, name), getattr(x_errors, o)) for o in others)


class TestCrossPathIdentity:
    """两条 import 路径必须解析到同一个类对象。"""

    @pytest.mark.parametrize("name", _PUBLIC_ERRORS)
    def test_sources_reexports_the_core_class(self, name: str) -> None:
        assert getattr(x_client, name) is _core_class(name)

    def test_sources_all_lists_every_public_error(self) -> None:
        """``__all__`` 漏一个名字，`from ... import *` 的调用方就会少一个异常类。"""
        assert set(x_client.__all__) == set(_PUBLIC_ERRORS)

    def test_core_defines_exactly_the_public_errors(self) -> None:
        """core.x_errors 里不许出现「没被导出」的野生异常类。"""
        defined = {
            name
            for name, obj in inspect.getmembers(x_errors, inspect.isclass)
            if obj.__module__ == x_errors.__name__ and not name.startswith("_")
        }
        assert defined == set(_PUBLIC_ERRORS)

    def test_except_via_the_old_path_still_catches(self) -> None:
        """抛出方用 core 的类，调用方仍可用 ``x_client.XClientError`` 捕获。"""
        with pytest.raises(x_client.XClientError):
            raise x_errors.XAuthError("401")

    def test_except_via_core_catches_the_sources_object(self) -> None:
        with pytest.raises(x_errors.XBlockedError):
            raise x_client.XBlockedError("403")


class TestHealthClassificationUnchanged:
    """K5 的**真正目的**：捕获方跨层分类仍然准确。

    这是把类型身份和业务后果串起来的一条用例——只有「同一个类对象」成立，
    下面的映射才成立。鉴别力：把异常类搬回 sources 并重新定义一份，本组用例
    会退化成 ``rate_limited`` / ``ok`` 而红。
    """

    @pytest.mark.parametrize(
        ("error_name", "expected"),
        [
            ("XMissingCookieError", MISSING_COOKIE),
            ("XAuthError", EXPIRED_COOKIE),
            ("XBlockedError", BLOCKED),
            ("XRateLimitError", RATE_LIMITED),
        ],
    )
    def test_typed_errors_map_to_their_health_state(self, error_name: str, expected: str) -> None:
        exc = getattr(x_client, error_name)("boom")
        assert health_state_for_error(exc) == expected

    def test_unknown_x_error_backs_off_instead_of_reporting_healthy(self) -> None:
        """未知的 ``XClientError`` 归为限流（宁可按异常处理也不报「健康」）。"""
        assert health_state_for_error(x_errors.XClientError("who knows")) == RATE_LIMITED

    def test_non_x_error_is_not_classified_as_a_source_failure(self) -> None:
        assert health_state_for_error(ValueError("unrelated")) == "ok"

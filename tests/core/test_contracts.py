"""core.contracts 契约回归 —— 派生规则与「落库映射」完整性。

背景：``DiscoveredContent`` / ``DiscoveryStrategy`` 于 2026-09-15 从
``obc_discovery.engine`` 下沉到 ``openbiliclaw.core.contracts``（K6b），
目的是断掉 sources 侧 17 个 adapter 与 discovery 的**同层耦合**（领域→core 合法）。
搬家本身不改变行为，但它让两件「静默失败」的事同时变得容易踩：

1. ``to_cache_kwargs()`` 是 ``DiscoveredContent`` → ``Database.cache_content()``
   的**唯一**映射点，而 ``cache_content`` 内部是
   ``kwargs.get("<列名>", 默认值)`` —— **多传的键被静默忽略、漏传的键静默落默认值**。
   于是「新加一个字段却忘了加进映射」不需要报错，那份数据只是永远进不了库
   （``franchise_key`` 那种字段还会悄悄让 IP 去重失效）。
   这里用「字段集合 == 映射键集合 ∪ 显式未映射白名单」把这个失败模式变成红灯。

2. ``content_text`` **刻意不落** ``content_cache``（正文只进
   ``articles.content_text``）。这条约定此前只存在于代码注释里，重构时最容易被
   「顺手补全」加进去。

两类断言都必须真跑 dataclass 才能得到，grep 源码是查不出来的。
"""

from __future__ import annotations

import inspect
from dataclasses import fields
from typing import Any

import pytest
from obc_discovery.engine import DiscoveredContent as FromDiscoveryEngine

from openbiliclaw.core.contracts import DiscoveredContent, DiscoveryStrategy

# ── 刻意不落 content_cache 的字段（每一项都必须有理由）─────────────
# 增减这个字典等价于修改「哪份数据进推荐池缓存」的契约，请连同
# docs/modules/core.md 的映射表一起改。
_NOT_MAPPED_BY_DESIGN: dict[str, str] = {
    "bvid": "以 content_id 落库（列名 bvid 由调用方传入；两者相等或 content_id 优先）",
    "source_strategy": "落库列名为 source（历史列名），映射里有别名",
    "content_text": "正文只进 articles.content_text，刻意不污染推荐池缓存",
    "score_threshold": "策略内部准入阈值，不是内容属性",
    "pool_expression": "池内文案由 curator 单独写入；cache_content 用 COALESCE 保留既有值",
    "pool_topic_label": "同上",
    "discovered_at": "列由 SQL CURRENT_TIMESTAMP 生成",
    "last_scored_at": "列由 SQL CURRENT_TIMESTAMP 生成",
}

# 映射里出现、但不是 dataclass 字段名的键（别名）。
_ALIAS_KEYS: dict[str, str] = {"source": "source_strategy"}


class _StubStrategy(DiscoveryStrategy):
    """最小可实例化子类（只为实现抽象方法，无业务行为）。"""

    @property
    def name(self) -> str:
        return "stub"

    async def discover(self, profile: Any, limit: int = 20) -> list[DiscoveredContent]:
        del profile, limit
        return []


class TestCacheMappingIsComplete:
    """字段 ↔ 落库键的映射必须显式且完整。"""

    def test_every_dataclass_field_is_mapped_or_explicitly_excluded(self) -> None:
        """新增字段必须二选一：加进 ``to_cache_kwargs()``，或写进白名单。

        鉴别力：这条用例是「静默丢数据」的唯一防线。故意给 dataclass 加一个
        新字段（比如 ``ip_series_key``）而不动映射，本用例会立刻红；而整个测试
        套件在此之前没有任何一条会失败——数据只是不再进库。
        """
        field_names = {f.name for f in fields(DiscoveredContent)}
        mapped = set(DiscoveredContent().to_cache_kwargs())
        alias_sources = set(_ALIAS_KEYS)

        unaccounted = field_names - mapped - alias_sources - set(_NOT_MAPPED_BY_DESIGN)
        assert unaccounted == set(), (
            "DiscoveredContent 新增了字段但没决定它怎么落库：\n"
            f"  未处理：{sorted(unaccounted)}\n"
            "请二选一：加进 to_cache_kwargs()，或加进本文件的 "
            "_NOT_MAPPED_BY_DESIGN 并写明理由。"
        )

    def test_exclusion_allowlist_has_no_stale_entries(self) -> None:
        """白名单不得指向已不存在的字段，否则会掩盖真实的新增。"""
        field_names = {f.name for f in fields(DiscoveredContent)}
        stale = sorted(set(_NOT_MAPPED_BY_DESIGN) - field_names)
        assert stale == [], f"白名单已失效（字段被删或改名），请清理：{stale}"

    def test_alias_keys_are_not_also_field_names(self) -> None:
        """别名键不能同时是字段名，否则上面那条完整性断言会算错。"""
        field_names = {f.name for f in fields(DiscoveredContent)}
        overlap = sorted(set(_ALIAS_KEYS) & field_names)
        assert overlap == [], f"别名键与字段名撞车：{overlap}"

    def test_cache_kwargs_has_no_unknown_keys(self) -> None:
        """多传的键会被 ``cache_content`` 静默丢弃——所以一个都不许有。"""
        field_names = {f.name for f in fields(DiscoveredContent)}
        unknown = sorted(set(DiscoveredContent().to_cache_kwargs()) - field_names - set(_ALIAS_KEYS))
        assert unknown == [], f"to_cache_kwargs() 里有既非字段也非别名的键：{unknown}"

    def test_content_text_is_never_sent_to_content_cache(self) -> None:
        """正文不得进推荐池缓存（会被顺手加进去的那条约定）。"""
        item = DiscoveredContent(bvid="BV1x", content_text="这是一篇很长的正文" * 500)
        assert "content_text" not in item.to_cache_kwargs()

    def test_pool_copy_is_left_to_the_curator(self) -> None:
        """``pool_expression`` / ``pool_topic_label`` 由 curator 写，不由此映射携带。"""
        item = DiscoveredContent(
            bvid="BV1x",
            pool_expression="预置文案",
            pool_topic_label="预置主题",
        )
        kwargs = item.to_cache_kwargs()
        assert "pool_expression" not in kwargs
        assert "pool_topic_label" not in kwargs

    def test_timestamps_are_database_generated(self) -> None:
        item = DiscoveredContent(bvid="BV1x", discovered_at="2026-01-01T00:00:00", last_scored_at="2026-01-01T00:00:00")
        kwargs = item.to_cache_kwargs()
        assert "discovered_at" not in kwargs
        assert "last_scored_at" not in kwargs


class TestCrossPlatformDerivation:
    """``__post_init__`` 的派生分支（补既有用例未覆盖的两条）。"""

    def test_xiaohongshu_url_is_derived_from_content_id(self) -> None:
        """只给 ``content_id`` + 平台，也要能自己拼出可点击 URL。

        鉴别力：删掉 ``__post_init__`` 里小红书那条分支即红。
        """
        item = DiscoveredContent(content_id="64f0abc123", source_platform="xiaohongshu")
        assert item.content_url == "https://www.xiaohongshu.com/explore/64f0abc123"

    def test_xiaohongshu_without_content_id_derives_nothing(self) -> None:
        """只有平台、没有 id 时不许拼出 ``/explore/`` 这种半截链接。"""
        item = DiscoveredContent(source_platform="xiaohongshu")
        assert item.content_url == ""

    def test_explicit_url_wins_over_derivation(self) -> None:
        item = DiscoveredContent(
            content_id="64f0abc123",
            source_platform="xiaohongshu",
            content_url="https://www.xiaohongshu.com/explore/other?xsec_token=AB",
        )
        assert item.content_url == "https://www.xiaohongshu.com/explore/other?xsec_token=AB"

    def test_bilibili_url_uses_bvid_even_when_content_id_differs(self) -> None:
        """B 站的 URL 派生锚点是 ``bvid``，不是被显式覆盖过的 ``content_id``。"""
        item = DiscoveredContent(bvid="BV1abc", content_id="custom")
        assert item.content_url == "https://www.bilibili.com/video/BV1abc"
        assert item.content_id == "custom"

    def test_platform_is_blank_when_nothing_identifies_the_source(self) -> None:
        """不做「默认 bilibili」的臆测：只有 bvid 才代表 B 站。

        落库阶段的兜底在 ``cache_content``（``source_platform`` 默认 bilibili），
        但值对象本身不该替调用方猜平台。
        """
        item = DiscoveredContent(title="无来源信息")
        assert item.source_platform == ""
        assert item.content_id == ""

    def test_derivation_runs_once_and_is_idempotent(self) -> None:
        """``__post_init__`` 的赋值本身不能触发二次派生（例如 URL 被当成显式值）。"""
        item = DiscoveredContent(bvid="BV1abc")
        first = item.content_url
        item.__post_init__()
        assert item.content_url == first
        assert item.content_id == "BV1abc"


class TestSharedTypeIdentity:
    """K6b 下沉后，两个 import 路径必须是**同一个类对象**。"""

    def test_discovery_engine_reexports_the_same_class(self) -> None:
        """``obc_discovery.engine.DiscoveredContent`` 必须是 core 的那个对象。

        鉴别力：若哪天有人「复制」而不是「再导出」，``isinstance`` 判断会在
        跨层传参时静默失效（两个同名类互不认），这类 bug 极难定位。
        """
        assert FromDiscoveryEngine is DiscoveredContent

    def test_strategy_base_is_shared_too(self) -> None:
        from obc_discovery.engine import DiscoveryStrategy as FromEngine

        assert FromEngine is DiscoveryStrategy

    def test_instances_created_from_either_path_share_the_type(self) -> None:
        via_core = DiscoveredContent(bvid="BV1x")
        assert isinstance(via_core, FromDiscoveryEngine)
        assert type(via_core) is FromDiscoveryEngine


class TestDiscoveryStrategyContract:
    """``DiscoveryStrategy`` 的抽象契约（sources 侧 17 个 adapter 的公共基类）。"""

    def test_cannot_instantiate_the_abstract_base(self) -> None:
        with pytest.raises(TypeError):
            DiscoveryStrategy()  # type: ignore[abstract]

    def test_name_is_an_abstract_property_not_a_method(self) -> None:
        """``name`` 必须是 property：调用方写的是 ``strategy.name``，不是 ``name()``。

        鉴别力：把它改成普通抽象方法后，所有 adapter 的 ``strategy.name`` 会
        返回 bound method 而**不报错**，日志与去重键里就会出现
        ``<bound method ...>`` 这种字符串。
        """
        assert isinstance(DiscoveryStrategy.name, property)

    def test_abstract_methods_are_discover_and_name(self) -> None:
        assert DiscoveryStrategy.__abstractmethods__ == frozenset({"discover", "name"})

    def test_discover_is_a_coroutine(self) -> None:
        assert inspect.iscoroutinefunction(DiscoveryStrategy.discover)

    def test_subclass_can_implement_only_the_two_abstracts(self) -> None:
        strategy = _StubStrategy()
        assert strategy.name == "stub"

    def test_backfill_defaults_to_none(self) -> None:
        """默认「不支持回填」——必须显式覆盖才可能返回策略，否则调用方会以为有供给。"""
        assert _StubStrategy().create_backfill_strategy() is None

    def test_missing_abstract_implementation_is_rejected_at_instantiation(self) -> None:
        class _Incomplete(DiscoveryStrategy):
            @property
            def name(self) -> str:
                return "incomplete"

        with pytest.raises(TypeError):
            _Incomplete()  # type: ignore[abstract]

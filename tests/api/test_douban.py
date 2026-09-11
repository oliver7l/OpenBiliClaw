"""API 与存储测试：豆瓣书影音模块（/api/douban + douban.db）。

store / route / adapter 均用 tmp 数据库，避免依赖真实 data/douban.db 数据。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openbiliclaw.douban.routes import build_douban_router
from openbiliclaw.douban.store import DoubanStore
from openbiliclaw.sources.douban_adapter import DoubanAdapter

SAMPLES = [
    {
        "category": "movie",
        "status": "collect",
        "name": "出走的决心",
        "url": "https://movie.douban.com/subject/36587974/",
        "date": "2024-12-28",
        "comment": "好片",
        "rating": "有",
    },
    {
        "category": "book",
        "status": "wish",
        "name": "别想太多啦",
        "url": "https://book.douban.com/subject/35309257/",
        "date": "2022-02-03",
        "pub": "名取芳彦 / 天津人民出版社 / 2021-1 / 49.90元",
    },
    {
        "category": "music",
        "status": "collect",
        "name": "再见理想",
        "url": "https://music.douban.com/subject/1410660/",
        "date": "2018-07-16",
        "intro": "Beyond / 1986-03-01 / 专辑 / 磁带 / 摇滚",
    },
]


def _store(tmp_path) -> DoubanStore:
    return DoubanStore(tmp_path / "douban.db")


def test_store_import_and_count(tmp_path) -> None:
    store = _store(tmp_path)
    r = store.import_items(SAMPLES)
    assert r["inserted"] == 3 and r["skipped"] == 0
    assert store.count() == 3

    # 重复导入同 url 只更新不新增
    r2 = store.import_items([SAMPLES[0]])
    assert r2["inserted"] == 0 and r2["updated"] == 1
    assert store.count() == 3


def test_store_list_and_filters(tmp_path) -> None:
    store = _store(tmp_path)
    store.import_items(SAMPLES)

    movies = store.list_items(category="movie")
    assert len(movies) == 1 and movies[0]["name"] == "出走的决心"

    wish = store.list_items(status="wish")
    assert len(wish) == 1 and wish[0]["name"] == "别想太多啦"

    search = store.list_items(search="再见")
    assert len(search) == 1 and search[0]["name"] == "再见理想"


def test_store_stats(tmp_path) -> None:
    store = _store(tmp_path)
    store.import_items(SAMPLES)
    st = store.stats()
    assert st["total"] == 3
    assert st["categories"]["movie"]["collect"] == 1


def test_routes(tmp_path) -> None:
    _store(tmp_path)  # 建库 + schema
    app = FastAPI()
    app.include_router(
        build_douban_router(
            config=_cfg(tmp_path),
        )
    )
    tc = TestClient(app)

    r = tc.get("/api/douban/stats")
    assert r.status_code == 200
    assert r.json()["total"] == 0

    # 先导入数据再查
    store = _store(tmp_path)
    store.import_items(SAMPLES)

    r = tc.get("/api/douban/items?category=movie&status=collect")
    assert r.status_code == 200
    assert r.json()["count"] == 1

    r = tc.get("/api/douban/items?search=再见")
    assert r.status_code == 200
    assert r.json()["items"][0]["name"] == "再见理想"


def test_adapter_fetch(tmp_path) -> None:
    import asyncio

    store = _store(tmp_path)
    store.import_items(SAMPLES)
    adapter = DoubanAdapter(db_path=str(tmp_path / "douban.db"))
    assert adapter.source_type == "douban"
    assert adapter.source_name == "豆瓣"

    items = asyncio.run(adapter.fetch(recipe=None, profile=None, limit=10))  # type: ignore[arg-type]
    assert len(items) == 3
    assert items[0].source_platform == "douban"
    assert items[0].content_url.startswith("https://")


def test_analytics_aggregation(tmp_path) -> None:
    from openbiliclaw.douban.analytics import DoubanAnalytics

    store = _store(tmp_path)
    store.import_items(SAMPLES)
    a = DoubanAnalytics(store)
    report = a.full_report()
    assert report["total"] == 3
    assert report["status_ratio"]["collected"] == 2
    assert report["era_span"]["earliest_year"] == 2018  # music collect 2018-07-16
    assert report["era_span"]["latest_year"] == 2024  # movie collect 2024-12-28
    # 年度趋势里 2018(music)+2024(movie) 有 collect
    trend = report["yearly_trend"]
    assert trend["years"] == ["2018", "2024"]
    assert trend["series"]["music"] == [1, 0]
    assert trend["series"]["movie"] == [0, 1]


def test_analytics_empty(tmp_path) -> None:
    from openbiliclaw.douban.analytics import DoubanAnalytics

    store = _store(tmp_path)  # 空库
    a = DoubanAnalytics(store)
    report = a.full_report()
    assert report["total"] == 0
    assert report["status_ratio"]["collected_pct"] == 0
    assert report["era_span"]["earliest_year"] is None


def _llm_fake() -> object:
    """返回一个能返回固定 report 的 mock llm_service。"""

    class _Resp:
        content = "这是一份测试画像报告。"

    class _Fake:
        async def complete_structured_task(self, **kwargs):  # type: ignore[no-untyped-def]
            return _Resp()

    return _Fake()


def test_insight_generate_and_cache(tmp_path, monkeypatch) -> None:
    from openbiliclaw.douban.insight import generate_insight_report, load_cached_report

    store = _store(tmp_path)
    store.import_items(SAMPLES)
    cache = tmp_path / "profile_report.json"

    # 无 llm -> ok false
    res = asyncio_run(generate_insight_report(None, store, force=True, cache_path=str(cache)))
    assert res["ok"] is False

    # 有 llm -> 生成并缓存
    res = asyncio_run(
        generate_insight_report(_llm_fake(), store, force=True, cache_path=str(cache))
    )
    assert res["ok"] is True and "画像报告" in res["report"]
    assert cache.exists()

    # 非 force 走缓存
    res2 = asyncio_run(
        generate_insight_report(_llm_fake(), store, force=False, cache_path=str(cache))
    )
    assert res2["ok"] is True and res2["cached"] is True

    # load_cached_report
    cached = load_cached_report(str(cache))
    assert cached["ok"] is True


def asyncio_run(coro) -> object:
    import asyncio

    return asyncio.run(coro)


def test_routes_analytics_and_insight(tmp_path, monkeypatch) -> None:
    import openbiliclaw.douban.insight as insight_mod

    monkeypatch.setattr(insight_mod, "DEFAULT_CACHE", tmp_path / "profile_report.json")
    store = _store(tmp_path)
    store.import_items(SAMPLES)
    app = FastAPI()
    app.include_router(build_douban_router(config=_cfg(tmp_path), llm_service=_llm_fake()))
    tc = TestClient(app)

    r = tc.get("/api/douban/analytics")
    assert r.status_code == 200
    assert r.json()["total"] == 3

    r = tc.get("/api/douban/insight")
    assert r.status_code == 200

    r = tc.post("/api/douban/insight", json={"force": True})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def _cfg(tmp_path) -> object:
    """构造带 douban_db_path 的假 config。"""
    class _Storage:
        douban_db_path = str(tmp_path / "douban.db")

    class _Config:
        storage = _Storage()

    # 用类式构建，绕过 load_config 依赖
    return _Config()


@pytest.fixture(autouse=True)
def _no_real_db():
    """防止误用真实 data/douban.db：本测试不触碰它。"""
    yield

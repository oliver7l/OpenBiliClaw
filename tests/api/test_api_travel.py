"""旅游（✈️ 旅行 tab）API 回归测试。

锁定 2026-09-15 实测出的两个缺陷（详见
``docs/plans/旅游模块梳理与整合方案.md`` §1.2）：

1. **桌面端旅行页线上全 404**：``desktop/index.html`` 的三个子标签调用
   ``/flights`` / ``/overview`` / ``/doc``，这三个端点走 ``TravelConfig.data_path``，
   而该字段默认是空串且 ``config.toml`` 在 .gitignore 内 → ``base=None``
   → 统一 404。**能出数据的另外四个 DB 端点桌面端反而一个都没调。**
2. **DB 路径硬编码**：``/itinerary`` 等 4 个端点各自写死
   ``_project_root()/"data"/"travel.db"``，切数据目录时会静默读错库
   （该假象很难发现——它们会成功返回**别的**库的行程）。

按项目铁律，**端点存在性断言的是「(path, method) 在运行时路由表内」，
而不是「请求非 404」**：后者会被降级门（与 Butler 共用 skybridge 时
整套 API 统一 503）掩盖，零鉴别力。
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.config import TravelConfig, _project_root
from openbiliclaw.travel.routes import _under_root, build_travel_router

TRAVEL_ROUTES = (
    "/api/travel/flights",
    "/api/travel/documents",
    "/api/travel/doc",
    "/api/travel/overview",
    "/api/travel/itinerary",
    "/api/travel/expenses",
    "/api/travel/flights-detail",
    "/api/travel/hotels",
)

_TRIP_SCHEMA = """
CREATE TABLE trips (
    id INTEGER PRIMARY KEY, title TEXT, budget REAL, people_count INTEGER, status TEXT,
    start_date TEXT, end_date TEXT, notes TEXT
);
CREATE TABLE trip_days (
    id INTEGER PRIMARY KEY, trip_id INTEGER, day_number INTEGER, date TEXT,
    title TEXT, description TEXT, transport TEXT, highlights TEXT, notes TEXT
);
CREATE TABLE trip_members (
    id INTEGER PRIMARY KEY, trip_id INTEGER, name TEXT, relation TEXT,
    age INTEGER, notes TEXT
);
CREATE TABLE trip_checklist (
    id INTEGER PRIMARY KEY, trip_id INTEGER, category TEXT, item TEXT,
    owner TEXT, done INTEGER, notes TEXT
);
"""


def _write_trip_db(path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_TRIP_SCHEMA)
        conn.execute(
            "INSERT INTO trips (id, title, budget, people_count, status) VALUES (1, '测试行程', 100, 2, '草稿')"
        )
        conn.execute(
            "INSERT INTO trip_days (id, trip_id, day_number, date, title) VALUES (1, 1, 1, '2026-10-01', '集合日')"
        )
        conn.execute("INSERT INTO trip_members (id, trip_id, name) VALUES (1, 1, '测试成员')")
        conn.execute(
            "INSERT INTO trip_checklist (id, trip_id, category, item, owner, done) "
            "VALUES (1, 1, '证件', '身份证', '爸爸', 1)"
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def app():
    return create_app(memory_manager=object(), database=object(), soul_engine=object())


class TestTravelRouteRegistration:
    """八个端点必须真实注册——按 (path, method) 断言，不靠请求状态码。"""

    @pytest.mark.parametrize("path", TRAVEL_ROUTES)
    def test_route_present(self, app, path: str) -> None:
        table = {
            (r.path, m) for r in app.routes for m in (getattr(r, "methods", set()) or set())
        }
        assert (path, "GET") in table

    def test_no_duplicate_travel_routes(self, app) -> None:
        """重复注册时 Starlette 先注册者胜，后者永不执行。"""
        pairs = [
            (r.path, m)
            for r in app.routes
            if r.path.startswith("/api/travel/")
            for m in (getattr(r, "methods", set()) or set())
        ]
        assert len(pairs) == len(set(pairs)), f"重复的 (path, method): {pairs}"


class TestTravelPathResolution:
    """相对路径按项目根解析，绝对路径原样使用。"""

    def test_relative_resolves_under_project_root(self) -> None:
        assert _under_root("data/travel.db") == _project_root() / "data" / "travel.db"
        assert _under_root("data/travel") == _project_root() / "data" / "travel"

    def test_absolute_untouched(self) -> None:
        assert _under_root("/tmp/whatever/travel.db").as_posix() == "/tmp/whatever/travel.db"

    def test_user_home_expanded(self) -> None:
        assert _under_root("~/x.db") == _under_root("~/x.db").home() / "x.db"


class TestDefaultsKeepPageAlive:
    """config.toml 是 gitignored 的，留空默认值 = 换机就退化成 404。"""

    def test_defaults_are_non_empty(self) -> None:
        assert TravelConfig.data_path, "data_path 留空会让文档类端点全部 404"
        assert TravelConfig.db_path, "db_path 留空会让 /itinerary 等端点 404"

    def test_default_data_dir_is_a_real_subdirectory(self) -> None:
        """留空时 `项目根 / ""` 仍是目录，故只判 is_dir 抓不住——必须同时判
        「不等于项目根」，否则会把整站 assets 当成旅行文档目录扫一遍。"""
        target = _project_root() / TravelConfig.data_path
        assert target.is_dir()
        assert TravelConfig.data_path.strip()
        assert target != _project_root()


class TestDbEndpointsUseConfiguredPath:
    """删掉硬编码后，DB 端点必须真去读配置指定的库——这是最有鉴别力的一条。

    若有人把 ``_project_root()/"data"/"travel.db"`` 写回去，这里会红：
    端点仍能 200（因为项目库确实存在），但返回的是**真实行程的 8 天**，
    而不是测试用例临时库里的 1 天。
    """

    def test_itinerary_reads_the_injected_database(self, tmp_path) -> None:
        db = tmp_path / "nested" / "travel.db"
        db.parent.mkdir(parents=True)
        _write_trip_db(db)

        router = build_travel_router(
            data_path=str(tmp_path),
            db_path=str(db),
            budget_doc="missing.md",
            flights_json="missing.json",
        )
        app = FastAPI()
        app.include_router(router)

        with TestClient(app) as client:
            resp = client.get("/api/travel/itinerary")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["trip"]["title"] == "测试行程"
        assert [d["day_number"] for d in body["days"]] == [1]
        assert [m["name"] for m in body["members"]] == ["测试成员"]
        # 勾过的清单项不能被冲掉
        assert body["checklist"][0]["done"] == 1

    def test_missing_database_is_an_explicit_404(self, tmp_path) -> None:
        """库不存在必须报错，而不是静默返回空数据（原行为是对的，别改成空列表）。"""
        router = build_travel_router(
            data_path=str(tmp_path),
            db_path=str(tmp_path / "absent.db"),
            budget_doc="x.md",
            flights_json="x.json",
        )
        app = FastAPI()
        app.include_router(router)

        with TestClient(app) as client:
            resp = client.get("/api/travel/itinerary")
        assert resp.status_code == 404
        assert "旅行数据库不存在" in resp.json()["detail"]

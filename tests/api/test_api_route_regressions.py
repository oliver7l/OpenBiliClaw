"""路由注册回归测试。

锁定两类曾在 2026-09-13 存活审计中发现的结构性缺陷（详见
``docs/project-audit-2026-09-13.md``）：

- F2：``GET /api/diary/rag/stats`` 曾被 ``app.py`` 里误挂在序列化辅助函数
  ``_serialize_recommendation_items`` 上的装饰器抢先注册而遮蔽（Starlette
  先注册者胜），导致真实实现永不执行、该端点恒 422。
- F3：前端 ``diary-insights.js`` 曾调用后端根本不存在的
  ``/api/diary/people``（同项目 ``diary-people.js`` 用的是正确的
  ``/api/diary/extraction-stats``），属被取代的遗留死代码。

这里用「真实注册顺序」而非静态源码判断，因为重复注册的胜负由注册顺序决定。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.config import Config, save_config

_WEB_DIR = Path(__file__).resolve().parents[2] / "src" / "openbiliclaw" / "web"


def _route_endpoints(app) -> dict[tuple[str, str], list[object]]:
    """聚合 ``(path, method) -> [endpoint, ...]``，顺序即注册顺序（先注册者胜）。"""
    table: dict[tuple[str, str], list[object]] = {}
    for route in app.routes:
        path = getattr(route, "path", "")
        endpoint = getattr(route, "endpoint", None)
        methods = getattr(route, "methods", None) or set()
        if not path or endpoint is None:
            continue
        for method in methods:
            if method == "HEAD":
                continue
            table.setdefault((path, method), []).append(endpoint)
    return table


@pytest.fixture()
def isolated_client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    """把项目根指向临时目录，避免测试读到本机真实数据库。"""
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(Config(data_dir=str(tmp_path / "data")), tmp_path / "config.toml")
    return TestClient(create_app(), raise_server_exceptions=False)


def test_diary_rag_stats_route_not_shadowed() -> None:
    """F2 回归：胜出的 handler 必须是 diary_rag_stats，而非序列化辅助函数。"""
    winners = _route_endpoints(create_app()).get(("/api/diary/rag/stats", "GET"))
    assert winners, "路由 /api/diary/rag/stats 未注册"
    assert winners[0].__name__ == "diary_rag_stats", (
        "被遮蔽：胜出 handler 应为 diary_rag_stats，实际为 "
        f"{winners[0].__name__}（共 {len(winners)} 个重复注册）"
    )


def test_diary_rag_stats_never_422(isolated_client: TestClient) -> None:
    """F2 用户可见症状：端点不得再恒 422（被当作必填 body）。"""
    response = isolated_client.get("/api/diary/rag/stats")
    assert response.status_code != 422
    assert response.status_code in (200, 503)


def test_diary_persons_route_registered() -> None:
    """diary-people.js 依赖的人物列表端点必须存在。"""
    table = _route_endpoints(create_app())
    assert ("/api/diary/persons", "GET") in table


def test_frontend_does_not_call_removed_diary_people_endpoint() -> None:
    """F3 回归：前端资产不得再引用后端不存在的 /api/diary/people。"""
    offenders = sorted(
        str(js.relative_to(_WEB_DIR))
        for js in _WEB_DIR.rglob("*.js")
        if "/api/diary/people" in js.read_text(encoding="utf-8")
    )
    assert offenders == [], f"前端仍引用不存在的 /api/diary/people：{offenders}"

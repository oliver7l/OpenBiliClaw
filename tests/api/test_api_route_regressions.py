"""路由注册回归测试。

锁定两类曾在 2026-09-13 存活审计中发现的结构性缺陷（详见
``docs/project-audit-2026-09-13.md``）：

- F2：``GET /api/diary/rag/stats`` 曾被 ``app.py`` 里误挂在序列化辅助函数
  ``_serialize_recommendation_items`` 上的装饰器抢先注册而遮蔽（Starlette
  先注册者胜），导致真实实现永不执行、该端点恒 422。
- F3：前端 ``diary-insights.js`` 曾调用后端根本不存在的
  ``/api/diary/people``（同项目 ``diary-people.js`` 用的是正确的
  ``/api/diary/extraction-stats``），属被取代的遗留死代码。
- F4（2026-09-14 发现）：``260683c8``（把 ``app.py`` 拆成 11 个路由模块）把
  ``ArticleUpdateIn`` / ``ArticleNoteIn`` 的导入挪进了 ``article_routes.py`` 的
  ``TYPE_CHECKING`` 块。该模块有 ``from __future__ import annotations``，注解
  变成字符串后 FastAPI 无法在模块全局命名空间解析它，于是把 ``payload`` **静默
  降级为 query 参数**（PATCH 不再接收 JSON body），同时 ``/openapi.json`` 恒 500。
  拆分会掩盖这类问题：端点仍「存在」，只是参数位置错了。

这里用「真实注册顺序」而非静态源码判断，因为重复注册的胜负由注册顺序决定；
另用「能否生成 OpenAPI」以及「参数是否被降级」两条断言守住静默降级类缺陷。
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


# ── F4 回归：OpenAPI 契约（静默参数降级 / JSON body 识别） ──────────────────


def test_openapi_generation_succeeds(isolated_client: TestClient) -> None:
    """回归：``create_app().openapi()`` 必须能生成。

    F4 症状是生成时抛 ``PydanticUserError``（未解析的 ForwardRef），使
    ``/api/openapi.json`` 恒 500、Swagger UI 的接口列表不可用。
    """
    schema = isolated_client.app.openapi()
    assert len(schema["paths"]) > 100, "OpenAPI 路径数异常偏少，疑似注册阶段出错"


def test_openapi_body_params_not_degraded_to_query(isolated_client: TestClient) -> None:
    """回归：不得有端点把 body 参数静默降级成 query 参数。

    F4 的具体表现——``PATCH /api/articles/{id}`` 的 ``payload`` 出现在
    ``parameters`` 中且 ``in == "query"``；前端按 JSON body 调用会恒 422，
    而端点看起来「还在」。
    """
    schema = isolated_client.app.openapi()
    offenders = sorted(
        f"{method.upper()} {path}"
        for path, operations in schema["paths"].items()
        for method, operation in operations.items()
        if isinstance(operation, dict)
        and any(
            param.get("name") == "payload" and param.get("in") == "query"
            for param in operation.get("parameters", [])
        )
    )
    assert offenders == [], f"以下端点的 body 参数被降级为 query：{offenders}"


def test_article_patch_declares_json_request_body(isolated_client: TestClient) -> None:
    """F4 回归锁定：``PATCH /api/articles/{id}`` 必须声明 JSON requestBody。"""
    operation = isolated_client.app.openapi()["paths"]["/api/articles/{article_id}"][
        "patch"
    ]
    assert not any(
        param.get("name") == "payload" for param in operation.get("parameters", [])
    ), "payload 不应作为参数出现（它应是 requestBody）"
    ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/ArticleUpdateIn"), ref


def test_interview_prefixed_paths_in_openapi(isolated_client: TestClient) -> None:
    """期 2 契约：面试域三前缀进 OpenAPI，旧前缀别名（双挂载）不进。"""
    paths = set(isolated_client.app.openapi()["paths"])
    for expected in (
        "/api/interview/job/status",
        "/api/interview/study/today",
        "/api/interview/review/stats",
    ):
        assert expected in paths, f"新前缀路径缺失：{expected}"
    for legacy in (
        "/api/interview/status",
        "/api/interview/jobs",
        "/api/interview/today",
        "/api/interview/reviews",
    ):
        assert legacy not in paths, f"旧前缀别名不应进 OpenAPI：{legacy}"

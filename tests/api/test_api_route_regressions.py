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
- F5（2026-09-14 发现，与 F4 同源）：``knowledge_routes.py`` 与
  ``knowledge_forge_routes.py`` 各有一个同名函数 ``knowledge_graph``，路径
  ``/api/knowledge/graph`` 与 ``/api/knowledge-graph`` 规范化后
  （``/``、``-`` 都变成 ``_``）默认 operationId 完全相同
  （``knowledge_graph_api_knowledge_graph_get``）→ OpenAPI 生成客户端代码时
  方法名冲突。此前被 ``/openapi.json`` 恒 500（F4）长期掩盖，F4 修复后才暴露。
  两者是**语义不同的端点**（概念共现图 / 实体-文章图），故显式指定
  ``operation_id`` 加以区分，而非合并。
- F6（2026-09-14 发现，与 F4/F5 同源于 app.py 拆分）：``d5f02792``（K5 批量清理
  「重复」函数）删掉了 ``app.py`` 的 ``POST /api/delight/sent``，理由是它已在
  ``_delight_routes.py`` 里有副本——但那个模块**从未被 ``_route_registry`` 接线**
  （K3 孤儿路由修复清单漏项），于是端点直接消失。同批被误删的
  ``@app.post("/api/delight/respond")`` 装饰器已由 ``9eb97451`` 补回，``/sent``
  当时漏补。调用方（移动 Web ``api.js`` 的 ``markDelightSent``、openclaw 的
  ``_acknowledge_delight``）都是 fire-and-forget 且 ``except: pass``，所以 404
  长期无人发现。「假定某模块已接线」与「删了装饰器但函数体还在」都会让端点**静默
  消失**——本组同时断言路由存在、JSON body 契约，以及客户端引用的 delight 路径
  必须真实存在。

这里用「真实注册顺序」而非静态源码判断，因为重复注册的胜负由注册顺序决定；
另用「能否生成 OpenAPI」「参数是否被降级」「operationId 是否唯一」三条断言
守住静默降级与文档契约类缺陷。
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


# ── F5：OpenAPI operationId 必须唯一 ─────────────────────────────


def test_openapi_operation_ids_are_unique(isolated_client: TestClient) -> None:
    """F5 回归：OpenAPI 的 operationId 必须全局唯一。

    默认 operationId = ``{函数名}_{规范化路径}_{方法}``，而规范化会把路径里的
    ``/`` 与 ``-`` 都变成 ``_``。于是两个**同名函数**即使挂在不同路径
    （如 ``/api/knowledge/graph`` 与 ``/api/knowledge-graph``）也会撞成同一个 ID，
    使客户端代码生成器产出重复方法名或静默丢弃其中一个端点。
    """
    locations: dict[str, list[str]] = {}
    for path, operations in isolated_client.app.openapi()["paths"].items():
        for method, operation in operations.items():
            if not isinstance(operation, dict):
                continue
            oid = operation.get("operationId")
            if oid:
                locations.setdefault(oid, []).append(f"{method.upper()} {path}")
    duplicates = {oid: locs for oid, locs in locations.items() if len(locs) > 1}
    assert duplicates == {}, f"operationId 重复（客户端代码生成会冲突）：{duplicates}"


def test_knowledge_graph_endpoints_have_distinct_operation_ids(
    isolated_client: TestClient,
) -> None:
    """F5 回归锁定：两条 knowledge graph 端点各有可区分的 operationId。

    两者语义不同、数据源不同，故只做 ID 区分而不合并：
    - ``/api/knowledge/graph`` —— 概念共现图谱（``knowledge.knowledge_concepts``）
    - ``/api/knowledge-graph`` —— 实体-文章图谱（``knowledge.entities`` +
      ``article_entities``）
    """
    paths = isolated_client.app.openapi()["paths"]
    assert "/api/knowledge/graph" in paths, "概念共现图谱端点未注册"
    assert "/api/knowledge-graph" in paths, "实体-文章图谱端点未注册"
    concept_id = paths["/api/knowledge/graph"]["get"]["operationId"]
    entity_id = paths["/api/knowledge-graph"]["get"]["operationId"]
    assert concept_id != entity_id, (
        f"两条 knowledge graph 端点 operationId 撞名：{concept_id}"
    )


# ── F6：delight 端点契约（K5 误删 / 孤儿模块从未接线） ────────────

_DELIGHT_ENDPOINTS = (
    ("/api/delight/pending", "GET"),
    ("/api/delight/pending-batch", "GET"),
    ("/api/delight/respond", "POST"),
    ("/api/delight/sent", "POST"),
)


def test_delight_endpoints_registered() -> None:
    """F6 回归：四条 delight 端点必须全部注册（且方法正确）。

    ``POST /api/delight/sent`` 曾被 ``d5f02792`` 删除（假定已随
    ``_delight_routes.py`` 拆分出去），而该模块从未接线，端点因此静默消失。
    断言 (路径, 方法) 组合而非仅路径，可同时锁住方法被改错的情况。

    注意：不能靠「请求返回 404」来锁这条——降级门（LLM 不可用）会在路由之前
    统一返回 503，把 404 完全掩盖（实测该写法在修复前也照样通过）。
    """
    table = _route_endpoints(create_app())
    missing = [pair for pair in _DELIGHT_ENDPOINTS if pair not in table]
    assert missing == [], f"delight 端点未注册：{missing}"


def test_delight_sent_is_in_openapi_with_json_body(isolated_client: TestClient) -> None:
    """F6 回归：``/api/delight/sent`` 必须在 OpenAPI 中且声明 JSON body。

    「在路由表里」不等于契约正常——参数被降级为 query 时端点依然存在（见 F4），
    故同时断言 requestBody 指向 ``DelightAckIn`` 且没有名为 payload 的 query 参数。
    """
    operation = (
        isolated_client.app.openapi()["paths"].get("/api/delight/sent", {}).get("post")
    )
    assert operation is not None, "/api/delight/sent 未进 OpenAPI"
    request_body = operation.get("requestBody")
    assert request_body, "/api/delight/sent 未声明 requestBody（body 可能被降级为 query）"
    ref = request_body["content"]["application/json"]["schema"].get("$ref", "")
    assert ref.endswith("/DelightAckIn"), f"requestBody schema 应为 DelightAckIn，实际 {ref}"
    assert all(param.get("name") != "payload" for param in operation.get("parameters", [])), (
        "payload 被降级成了 query 参数"
    )


def test_delight_clients_reference_existing_endpoints() -> None:
    """F6 泛化断言：客户端引用的 delight 路径必须真实存在。

    与 F3 同类（客户端调用不存在的端点），但按 delight 前缀**自动发现**，避免下次
    端点在别处被改名或误删时又要靠人肉发现。扫描范围限定在两个前端与 openclaw
    集成；刻意不含 ``src/openbiliclaw/api/``，否则装饰器会自我满足该断言。
    """
    import re

    repo_root = Path(__file__).resolve().parents[2]
    pattern = re.compile(r"/api/delight/([A-Za-z0-9_-]+)|[\"'`]/delight/([A-Za-z0-9_-]+)")
    routes = {getattr(route, "path", "") for route in create_app().routes}
    offenders: set[str] = set()
    for root in (_WEB_DIR, repo_root / "src" / "openbiliclaw" / "integrations"):
        for asset in root.rglob("*"):
            if asset.suffix not in {".js", ".py"} or not asset.is_file():
                continue
            text = asset.read_text(encoding="utf-8")
            for match in pattern.finditer(text):
                segment = match.group(1) or match.group(2)
                if f"/api/delight/{segment}" not in routes:
                    offenders.add(
                        f"{asset.relative_to(repo_root)} -> /api/delight/{segment}"
                    )
    assert offenders == set(), f"客户端引用了不存在的 delight 端点：{sorted(offenders)}"

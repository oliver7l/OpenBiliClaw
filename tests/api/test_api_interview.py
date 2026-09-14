"""API tests for the interview module routes.

期 2 URL 分区后 A（岗位备战）的正规前缀是 ``/api/interview/job``；旧前缀
``/api/interview`` 以**双挂载别名**保留（``include_in_schema=False``），
本文件同时覆盖两者，确保兼容层不退化。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openbiliclaw.interview.job.routes import (
    LEGACY_PREFIX,
    PREFIX,
    build_interview_router,
    mount_interview_router,
)
from tests.interview.test_interview_engine import build_kb

if TYPE_CHECKING:
    from pathlib import Path


# 新前缀 ↔ 旧前缀别名 的等价 GET 端点对照表
_LEGACY_GET_PAIRS = [
    (f"{PREFIX}/status", f"{LEGACY_PREFIX}/status"),
    (f"{PREFIX}/jobs", f"{LEGACY_PREFIX}/jobs"),
    (f"{PREFIX}/numbers", f"{LEGACY_PREFIX}/numbers"),
    (f"{PREFIX}/projects", f"{LEGACY_PREFIX}/projects"),
    (f"{PREFIX}/directions", f"{LEGACY_PREFIX}/directions"),
    (f"{PREFIX}/index", f"{LEGACY_PREFIX}/index"),
    (f"{PREFIX}/logs", f"{LEGACY_PREFIX}/logs"),
    (f"{PREFIX}/doctor", f"{LEGACY_PREFIX}/doctor"),
]


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    root = build_kb(tmp_path / "kb")
    app = FastAPI()
    mount_interview_router(app, root=str(root))
    return TestClient(app)


def test_status(client: TestClient) -> None:
    resp = client.get(f"{PREFIX}/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert len(body["jobs"]) == 1
    assert body["number_count"] == 2


def test_jobs_filter(client: TestClient) -> None:
    assert client.get(f"{PREFIX}/jobs").json()["total"] == 1
    assert client.get(f"{PREFIX}/jobs", params={"keyword": "推荐"}).json()["total"] == 1
    assert client.get(f"{PREFIX}/jobs", params={"keyword": "无"}).json()["total"] == 0


def test_search(client: TestClient) -> None:
    resp = client.get(f"{PREFIX}/search", params={"q": "oCPX"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 2
    assert body["items"][0]["snippet"]
    # 空关键词 422
    assert client.get(f"{PREFIX}/search", params={"q": " "}).status_code == 422


def test_numbers_projects_directions(client: TestClient) -> None:
    assert client.get(f"{PREFIX}/numbers").json()["total"] == 2
    assert client.get(f"{PREFIX}/numbers", params={"keyword": "ARPU"}).json()["total"] == 1
    assert client.get(f"{PREFIX}/projects").json()["total"] == 1
    dirs = client.get(f"{PREFIX}/directions").json()["items"]
    assert any(d["direction"] == "推荐系统" for d in dirs)


def test_card(client: TestClient) -> None:
    resp = client.get(f"{PREFIX}/card/测试公司")
    assert resp.status_code == 200
    body = resp.json()
    assert body["job"]["公司"] == "测试公司"
    assert body["projects"][0]["项目名"] == "测试项目A"
    assert client.get(f"{PREFIX}/card/不存在公司").status_code == 404


def test_index_routes(client: TestClient) -> None:
    resp = client.get(f"{PREFIX}/index", params={"keyword": "README"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert client.get(f"{PREFIX}/index", params={"layer": "03"}).json()["total"] == 1
    assert client.get(f"{PREFIX}/index", params={"layer": "99"}).status_code == 422


def test_index_rebuild_and_doctor(client: TestClient) -> None:
    """重建索引 + 健康检查路由。"""
    resp = client.post(f"{PREFIX}/index/rebuild")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 8
    assert body["per_layer"]["02_方向知识库"] >= 2
    # 健康检查 C1-C4 通过
    resp = client.get(f"{PREFIX}/doctor")
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["passed"] is True
    assert [c["id"] for c in doc["checks"]] == ["C1", "C2", "C3", "C4"]
    # full + fix 也正常
    resp = client.get(f"{PREFIX}/doctor", params={"full": "true", "fix": "true"})
    assert resp.status_code == 200
    assert resp.json()["passed"] is True


def test_logs_roundtrip(client: TestClient) -> None:
    assert client.get(f"{PREFIX}/logs").json()["total"] == 1
    resp = client.post(
        f"{PREFIX}/logs",
        json={"company": "测试公司", "round": "二面", "points": "问 AB 实验"},
    )
    assert resp.status_code == 201
    assert resp.json()["id"] == 2
    assert client.get(f"{PREFIX}/logs").json()["total"] == 2
    # 缺字段 422
    assert client.post(f"{PREFIX}/logs", json={"company": "测试公司"}).status_code == 422


def test_scaffold(client: TestClient) -> None:
    resp = client.post(f"{PREFIX}/scaffold", json={"company": "新公司", "role": "数据分析"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "新公司-数据分析-面试准备"
    assert len(body["created"]) == 3
    # 幂等：再次建档 created 为空
    again = client.post(f"{PREFIX}/scaffold", json={"company": "新公司", "role": "数据分析"})
    assert again.status_code == 201
    assert again.json()["created"] == []


def test_unconfigured_returns_404(tmp_path: Path) -> None:
    app = FastAPI()
    mount_interview_router(app, root=str(tmp_path / "missing"))
    client = TestClient(app)
    assert client.get(f"{PREFIX}/jobs").status_code == 404
    assert client.get(f"{PREFIX}/search", params={"q": "x"}).status_code == 404
    # status 本身不要求配置（返回未配置状态）
    body = client.get(f"{PREFIX}/status").json()
    assert body["configured"] is False


# ── 期 2 兼容层回归：旧前缀别名必须继续可用且响应一致 ──────────────────


@pytest.mark.parametrize(("new_path", "legacy_path"), _LEGACY_GET_PAIRS)
def test_legacy_prefix_alias_get_equivalent(
    client: TestClient, new_path: str, legacy_path: str
) -> None:
    """旧前缀 GET 与新前缀返回完全一致（双挂载别名而非重定向）。"""
    resp_new = client.get(new_path)
    resp_old = client.get(legacy_path)
    assert resp_new.status_code == 200, new_path
    assert resp_old.status_code == 200, legacy_path
    assert resp_old.json() == resp_new.json()


def test_legacy_prefix_alias_supports_post_and_404(client: TestClient) -> None:
    """双挂载的关键收益：POST 不经过重定向，带 body 也可用。"""
    resp = client.post(
        f"{LEGACY_PREFIX}/scaffold",
        json={"company": "旧前缀公司", "role": "算法"},
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "旧前缀公司-算法-面试准备"
    # 旧前缀的 404 行为也保持
    assert client.get(f"{LEGACY_PREFIX}/card/不存在公司").status_code == 404


def test_legacy_prefix_absent_from_openapi() -> None:
    """旧前缀是兼容层：不得出现在 OpenAPI 文档里（不应污染对外契约）。"""
    app = FastAPI()
    mount_interview_router(app)
    schema = app.openapi()
    interview_paths = [p for p in schema["paths"] if p.startswith("/api/interview")]
    assert interview_paths, "新前缀端点应出现在 OpenAPI 中"
    assert all(p.startswith(PREFIX) for p in interview_paths), interview_paths
    # operationId 无重复（同一 handler 挂两次不应生成重复 id）
    op_ids = [
        op["operationId"]
        for path_item in schema["paths"].values()
        for op in path_item.values()
        if isinstance(op, dict) and "operationId" in op
    ]
    assert len(op_ids) == len(set(op_ids))


def test_build_interview_router_is_prefix_free() -> None:
    """router 本体不带 prefix，前缀由挂载处决定（双挂载的前提）。"""
    router = build_interview_router()
    assert router.prefix == ""
    assert all(not r.path.startswith("/api/interview") for r in router.routes)

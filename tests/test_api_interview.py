"""API tests for the interview module routes (/api/interview)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openbiliclaw.interview.routes import build_interview_router
from tests.test_interview_engine import build_kb

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    root = build_kb(tmp_path / "kb")
    app = FastAPI()
    app.include_router(build_interview_router(root=str(root)))
    return TestClient(app)


def test_status(client: TestClient) -> None:
    resp = client.get("/api/interview/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert len(body["jobs"]) == 1
    assert body["number_count"] == 2


def test_jobs_filter(client: TestClient) -> None:
    assert client.get("/api/interview/jobs").json()["total"] == 1
    assert client.get("/api/interview/jobs", params={"keyword": "推荐"}).json()["total"] == 1
    assert client.get("/api/interview/jobs", params={"keyword": "无"}).json()["total"] == 0


def test_search(client: TestClient) -> None:
    resp = client.get("/api/interview/search", params={"q": "oCPX"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 2
    assert body["items"][0]["snippet"]
    # 空关键词 422
    assert client.get("/api/interview/search", params={"q": " "}).status_code == 422


def test_numbers_projects_directions(client: TestClient) -> None:
    assert client.get("/api/interview/numbers").json()["total"] == 2
    assert client.get("/api/interview/numbers", params={"keyword": "ARPU"}).json()["total"] == 1
    assert client.get("/api/interview/projects").json()["total"] == 1
    dirs = client.get("/api/interview/directions").json()["items"]
    assert any(d["direction"] == "推荐系统" for d in dirs)


def test_card(client: TestClient) -> None:
    resp = client.get("/api/interview/card/测试公司")
    assert resp.status_code == 200
    body = resp.json()
    assert body["job"]["公司"] == "测试公司"
    assert body["projects"][0]["项目名"] == "测试项目A"
    assert client.get("/api/interview/card/不存在公司").status_code == 404


def test_index_routes(client: TestClient) -> None:
    resp = client.get("/api/interview/index", params={"keyword": "README"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert client.get("/api/interview/index", params={"layer": "03"}).json()["total"] == 1
    assert client.get("/api/interview/index", params={"layer": "99"}).status_code == 422


def test_logs_roundtrip(client: TestClient) -> None:
    assert client.get("/api/interview/logs").json()["total"] == 1
    resp = client.post(
        "/api/interview/logs",
        json={"company": "测试公司", "round": "二面", "points": "问 AB 实验"},
    )
    assert resp.status_code == 201
    assert resp.json()["id"] == 2
    assert client.get("/api/interview/logs").json()["total"] == 2
    # 缺字段 422
    assert client.post("/api/interview/logs", json={"company": "测试公司"}).status_code == 422


def test_scaffold(client: TestClient) -> None:
    resp = client.post("/api/interview/scaffold", json={"company": "新公司", "role": "数据分析"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "新公司-数据分析-面试准备"
    assert len(body["created"]) == 3
    # 幂等：再次建档 created 为空
    again = client.post("/api/interview/scaffold", json={"company": "新公司", "role": "数据分析"})
    assert again.status_code == 201
    assert again.json()["created"] == []


def test_unconfigured_returns_404(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(build_interview_router(root=str(tmp_path / "missing")))
    client = TestClient(app)
    assert client.get("/api/interview/jobs").status_code == 404
    assert client.get("/api/interview/search", params={"q": "x"}).status_code == 404
    # status 本身不要求配置（返回未配置状态）
    body = client.get("/api/interview/status").json()
    assert body["configured"] is False

"""API tests for the ed2k download management routes (/api/ed2k).

后端经本机 `mule` CLI 驱动 MLDonkey（Colima + Docker + 真实 P2P 网络），
无法在单测里真实调用，故对 MuleService 的方法做 monkeypatch，聚焦路由
参数处理、错误传播与响应结构。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openbiliclaw.config import Ed2kConfig, load_config
from openbiliclaw.ed2k import MuleService
from openbiliclaw.ed2k.routes import build_ed2k_router

if TYPE_CHECKING:
    pass


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    config = load_config()
    config.ed2k = Ed2kConfig(mule_path="/fake/mule", download_dir="/tmp/ed2k-out")
    app = FastAPI()
    app.include_router(build_ed2k_router(config=config))

    # 桩掉所有 mule 调用：返回稳定的假数据
    def _fake(method: str, value: object) -> None:
        monkeypatch.setattr(MuleService, method, lambda self, *a, **k: value)

    _fake("net", {"kad_connected": True, "servers": "--- Connected to 2 servers on the Donkey network ---", "kad_raw": "", "servers_raw": ""})
    _fake("search", {"query": "test", "count": 1, "results": [{"id": 3, "name": "Some File.mkv", "size": "1.0G", "size_bytes": 1073741824, "sources": 5, "ed2k": "0" * 32}]})
    _fake("downloads", {"count": 1, "rate": "1.0 MB/s", "downloads": [{"id": 1, "state": "downloading", "name": "dl.mkv", "percent": "42.0%", "size": "1.0G"}]})
    _fake("download", {"requested": [3], "responses": ["Added"]})
    _fake("download_link", {"ok": True, "output": "Added link : ed2k://..."})
    _fake("cancel", {"ok": True, "output": "Cancelled"})
    _fake("commit", {"ok": True, "output": "Committed"})
    _fake("path", "/tmp/ed2k-out/")
    return TestClient(app)


def test_net(client: TestClient) -> None:
    resp = client.get("/api/ed2k/net")
    assert resp.status_code == 200
    assert resp.json()["kad_connected"] is True


def test_search_ok(client: TestClient) -> None:
    resp = client.get("/api/ed2k/search", params={"q": "test"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["results"][0]["id"] == 3


def test_search_missing_query(client: TestClient) -> None:
    resp = client.get("/api/ed2k/search")
    assert resp.status_code == 422


def test_downloads(client: TestClient) -> None:
    resp = client.get("/api/ed2k/downloads")
    assert resp.status_code == 200
    assert resp.json()["downloads"][0]["state"] == "downloading"


def test_download_post(client: TestClient) -> None:
    resp = client.post("/api/ed2k/download", json={"ids": [3]})
    assert resp.status_code == 200
    assert resp.json()["requested"] == [3]


def test_download_empty_ids(client: TestClient) -> None:
    resp = client.post("/api/ed2k/download", json={"ids": []})
    assert resp.status_code == 422


def test_download_link(client: TestClient) -> None:
    resp = client.post(
        "/api/ed2k/download-link",
        json={"link": "ed2k://|file|some.mkv|12345|ABCDEF0123456789ABCDEF0123456789|/"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_download_link_empty(client: TestClient) -> None:
    resp = client.post("/api/ed2k/download-link", json={"link": ""})
    assert resp.status_code == 422


def test_cancel(client: TestClient) -> None:
    resp = client.post("/api/ed2k/cancel", json={"ids": [1]})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_commit(client: TestClient) -> None:
    resp = client.post("/api/ed2k/commit")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_path(client: TestClient) -> None:
    resp = client.get("/api/ed2k/path")
    assert resp.status_code == 200
    assert resp.json()["path"] == "/tmp/ed2k-out/"
"""Route tests for the view-history implicit-feedback endpoints.

Covers /api/view-record (POST), /api/view-dwell (POST) and
/api/view-history (GET), backed by a real Database so the request → storage
round-trip (including the dwell_seconds field) is exercised end to end.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_runtime_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point create_app at an empty project root, independent of the dev machine."""
    from openbiliclaw.config import Config, save_config

    project_root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    cfg = Config()
    cfg.llm.default_provider = "ollama"
    cfg.llm.ollama.model = "llama3"
    save_config(cfg, project_root / "config.toml")


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    db = Database(tmp_path / "api.db")
    db.initialize()
    app = create_app(memory_manager=object(), database=db, soul_engine=object())
    return TestClient(app)


def test_view_record_then_history_roundtrip(client: TestClient) -> None:
    payload: dict[str, Any] = {
        "bvid": "BV1",
        "title": "示例标题",
        "source_platform": "bilibili",
        "topic_group": "科技",
        "content_url": "https://b/1",
        "up_name": "UP",
        "quality_score": 0.7,
        "fit_score": 0.5,
        "dwell_seconds": 88,
    }
    r = client.post("/api/view-record", json=payload)
    assert r.status_code == 200 and r.json() == {"ok": True}

    hist = client.get("/api/view-history", params={"limit": 10}).json()
    assert len(hist) == 1
    row = hist[0]
    assert row["bvid"] == "BV1"
    assert row["topic_group"] == "科技"
    assert row["dwell_seconds"] == pytest.approx(88.0)


def test_view_record_requires_bvid(client: TestClient) -> None:
    assert client.post("/api/view-record", json={"title": "no bvid"}).status_code == 422


def test_view_dwell_updates_latest_view(client: TestClient) -> None:
    client.post("/api/view-record", json={"bvid": "BV1", "topic_group": "生活"})
    r = client.post("/api/view-dwell", json={"bvid": "BV1", "dwell_seconds": 130})
    assert r.status_code == 200 and r.json() == {"ok": True}
    hist = client.get("/api/view-history", params={"limit": 10}).json()
    assert hist[0]["dwell_seconds"] == pytest.approx(130.0)


def test_view_dwell_unknown_bvid_returns_not_ok(client: TestClient) -> None:
    r = client.post("/api/view-dwell", json={"bvid": "missing", "dwell_seconds": 10})
    assert r.status_code == 200 and r.json() == {"ok": False}


def test_view_dwell_rejects_out_of_range(client: TestClient) -> None:
    assert (
        client.post("/api/view-dwell", json={"bvid": "BV1", "dwell_seconds": -1}).status_code == 422
    )
    assert (
        client.post("/api/view-dwell", json={"bvid": "BV1", "dwell_seconds": 999999}).status_code
        == 422
    )

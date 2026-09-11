"""API tests for the reading-library auto-tag backfill and find-similar routes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api import reading_routes
from openbiliclaw.api.app import create_app
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_runtime_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from openbiliclaw.config import Config, save_config

    project_root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    cfg = Config()
    cfg.llm.default_provider = "ollama"
    cfg.llm.ollama.model = "llama3"
    save_config(cfg, project_root / "config.toml")


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "api.db")
    database.initialize()
    return database


@pytest.fixture()
def client(db: Database) -> TestClient:
    app = create_app(memory_manager=object(), database=db, soul_engine=object())
    return TestClient(app)


def test_auto_tag_merges_interest_tags(client: TestClient, db: Database, monkeypatch) -> None:
    # auto-tag 路由直接绑定 `openbiliclaw.api.utils.load_interest_keywords`，
    # 必须 patch reading_routes 的模块全局；patch api.app 是无效的（曾静默不生效）。
    monkeypatch.setattr(reading_routes, "_load_interest_keywords", lambda: [("机器学习", 0.9)])
    art_id = db.upsert_article(
        "zhihu", "知乎", "机器学习实践", "http://a/1", content_text="深入讲解机器学习模型"
    )
    assert json_len(db.get_article(art_id)["tags"]) == 1  # only source tag

    r = client.post("/api/reading/auto-tag", params={"only_sparse": "true", "max_new": "5"})
    assert r.status_code == 200 and r.json()["updated"] == 1

    tags = _tags(db, art_id)
    assert "机器学习" in tags and "知乎" in tags  # merged, source preserved


def test_auto_tag_idempotent(client: TestClient, db: Database, monkeypatch) -> None:
    monkeypatch.setattr(reading_routes, "_load_interest_keywords", lambda: [("机器学习", 0.9)])
    art_id = db.upsert_article("zhihu", "知乎", "机器学习", "http://a/2", content_text="机器学习")
    client.post("/api/reading/auto-tag")
    first = _tags(db, art_id)
    client.post("/api/reading/auto-tag", params={"only_sparse": "false"})
    assert _tags(db, art_id) == first  # second run adds nothing new


def test_auto_tag_no_profile_is_noop(client: TestClient, db: Database, monkeypatch) -> None:
    monkeypatch.setattr(reading_routes, "_load_interest_keywords", lambda: [])
    db.upsert_article("zhihu", "知乎", "某文章", "http://a/3", content_text="正文")
    r = client.post("/api/reading/auto-tag")
    body = r.json()
    assert body["ok"] is True and body["updated"] == 0 and "note" in body


def test_similar_returns_nearest(client: TestClient, db: Database) -> None:
    a = db.upsert_article("zhihu", "知乎", "机器学习入门", "http://a/1")
    b = db.upsert_article("rss", "RSS", "机器学习进阶", "http://a/2")
    db.update_article_tags(a, ["机器学习", "AI"])
    db.update_article_tags(b, ["机器学习", "AI"])
    c = db.upsert_article("rss", "RSS", "烹饪技巧", "http://a/3")
    db.update_article_tags(c, ["美食"])

    r = client.get("/api/reading/similar", params={"id": a, "k": "5"})
    assert r.status_code == 200
    items = r.json()["items"]
    ids = [it["id"] for it in items]
    assert a not in ids  # excludes itself
    assert ids[0] == b  # b shares tags+title tokens → most similar
    assert items[0]["similarity"] > 0


def test_similar_unknown_id_404(client: TestClient) -> None:
    assert client.get("/api/reading/similar", params={"id": 999999}).status_code == 404


def _tags(db: Database, article_id: int) -> list[str]:
    raw = db.get_article(article_id)["tags"]
    return json.loads(raw) if isinstance(raw, str) else list(raw or [])


def json_len(raw) -> int:
    return len(json.loads(raw) if isinstance(raw, str) else raw)

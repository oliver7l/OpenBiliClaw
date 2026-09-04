"""Tests for the Hupu hot-posts feed producer (CLI-based)."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from openbiliclaw.runtime import hupu_feed_producer as hfp

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _make_content_cache(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE content_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bvid TEXT UNIQUE, title TEXT, up_name TEXT, author_name TEXT,
            content_url TEXT, source_platform TEXT, source TEXT,
            content_type TEXT, pool_status TEXT, body_text TEXT,
            discovered_at TEXT)"""
    )


def _sample_posts() -> list[dict]:
    return [
        {
            "rank": 1,
            "id": "642264589",
            "title": "美国六代机F47",
            "url": "https://bbs.hupu.com/642264589.html",
        },
        {
            "rank": 2,
            "id": "642263309",
            "title": "武大雷军",
            "url": "https://bbs.hupu.com/642263309.html",
        },
    ]


def test_to_rows_normalizes_posts() -> None:
    rows = hfp._to_rows(_sample_posts())
    assert len(rows) == 2
    first = rows[0]
    assert first["bvid"] == "642264589"
    assert first["title"] == "美国六代机F47"
    assert first["content_url"] == "https://bbs.hupu.com/642264589.html"
    assert first["source_platform"] == "hupu"
    assert first["source"] == "hupu-hot"
    assert first["content_type"] == "thread"
    assert first["pool_status"] == "fresh"


def test_to_rows_skips_invalid_posts() -> None:
    posts = [
        {"id": "abc", "title": "非数字id", "url": "https://bbs.hupu.com/1.html"},
        {"id": "123", "title": "", "url": "https://bbs.hupu.com/2.html"},
        {"id": "456", "title": "无url"},
        "not-a-dict",
        {"id": "789", "title": "ok", "url": "https://bbs.hupu.com/3.html"},
    ]
    rows = hfp._to_rows(posts)  # type: ignore[arg-type]
    assert [r["bvid"] for r in rows] == ["789"]


def test_insert_rows_is_idempotent(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    _make_content_cache(conn)
    rows = hfp._to_rows(_sample_posts())

    assert hfp._insert_rows(conn, rows) == 2
    conn.commit()
    # Second run inserts nothing (dedupe by bvid via INSERT OR IGNORE).
    assert hfp._insert_rows(conn, rows) == 0
    count = conn.execute("SELECT COUNT(*) FROM content_cache").fetchone()[0]
    assert count == 2
    conn.close()


def test_fetch_hot_parses_json(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(_sample_posts())
    monkeypatch.setattr(hfp.subprocess, "run", lambda *a, **k: _FakeProc(0, payload, ""))
    posts = hfp._fetch_hot(10)
    assert len(posts) == 2
    assert posts[0]["title"] == "美国六代机F47"


def test_fetch_hot_failure_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hfp.subprocess, "run", lambda *a, **k: _FakeProc(1, "", "boom"))
    assert hfp._fetch_hot(10) == []


def test_fetch_hot_missing_binary_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a: object, **k: object) -> None:
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(hfp.subprocess, "run", _raise)
    assert hfp._fetch_hot(10) == []


def test_fetch_hot_bad_json_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hfp.subprocess, "run", lambda *a, **k: _FakeProc(0, "not json", ""))
    assert hfp._fetch_hot(10) == []


class _FakeProc:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

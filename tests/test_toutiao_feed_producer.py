"""Tests for the Toutiao hot-news feed producer (CLI-based)."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from openbiliclaw.runtime import toutiao_feed_producer as tfp

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _make_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE content_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bvid TEXT UNIQUE, title TEXT, up_name TEXT, author_name TEXT,
            content_url TEXT, source_platform TEXT, source TEXT,
            content_type TEXT, pool_status TEXT, body_text TEXT,
            discovered_at TEXT)"""
    )
    conn.execute(
        """CREATE TABLE articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT, source_name TEXT, title TEXT, url TEXT,
            author TEXT, content_text TEXT, published_at TEXT, tags TEXT,
            UNIQUE(url))"""
    )


def _sample_news() -> list[dict]:
    return [
        {
            "rank": 1,
            "title": "普京刚警告完，英军就亮底牌",
            "source": "刘白惜",
            "genre": "article",
            "duration": "",
            "abstract": "有句老话说得好。",
            "date": "2026-09-04",
            "url": "https://www.toutiao.com/group/7681527039983141410/",
        },
        {
            "rank": 2,
            "title": "紧急预警",
            "source": "绚烂花海",
            "genre": "article",
            "duration": "",
            "abstract": "很多人以为家里装好防盗门。",
            "date": "2026-09-04",
            "url": "https://www.toutiao.com/article/707060220726/",
        },
    ]


def test_bvid_from_group_url() -> None:
    assert (
        tfp._bvid_for("https://www.toutiao.com/group/7681527039983141410/") == "7681527039983141410"
    )
    assert tfp._bvid_for("https://www.toutiao.com/a707060220726") == "707060220726"


def test_bvid_falls_back_to_hash_for_unknown_url() -> None:
    bvid = tfp._bvid_for("https://www.toutiao.com/some/other/path")
    assert len(bvid) == 32  # md5 hexdigest


def test_to_rows_normalizes_news() -> None:
    rows = tfp._to_rows(_sample_news())
    assert len(rows) == 2
    first = rows[0]
    assert first["bvid"] == "7681527039983141410"
    assert first["title"] == "普京刚警告完，英军就亮底牌"
    assert first["up_name"] == "刘白惜"
    assert first["body_text"] == "有句老话说得好。"
    assert first["source_platform"] == "toutiao"
    assert first["source"] == "toutiao-hot"
    assert first["content_type"] == "article"


def test_to_rows_skips_invalid_items() -> None:
    news = [
        {"title": "", "url": "https://www.toutiao.com/group/1/"},
        {"title": "无url"},
        "not-a-dict",
        {"title": "ok", "url": "https://www.toutiao.com/group/4/"},
    ]
    rows = tfp._to_rows(news)  # type: ignore[arg-type]
    assert [r["bvid"] for r in rows] == ["4"]


def test_insert_rows_writes_cache_and_articles(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    _make_tables(conn)
    rows = tfp._to_rows(_sample_news())

    cache_ins, art_ins = tfp._insert_rows(conn, rows)
    conn.commit()
    assert cache_ins == 2
    assert art_ins == 2

    # Idempotent: nothing inserted on the second run.
    cache_ins2, art_ins2 = tfp._insert_rows(conn, rows)
    conn.commit()
    assert (cache_ins2, art_ins2) == (0, 0)
    assert conn.execute("SELECT COUNT(*) FROM content_cache").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 2
    conn.close()


def test_fetch_hot_parses_json(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(_sample_news())
    monkeypatch.setattr(tfp.subprocess, "run", lambda *a, **k: _FakeProc(0, payload, ""))
    news = tfp._fetch_hot(10)
    assert len(news) == 2
    assert news[0]["title"] == "普京刚警告完，英军就亮底牌"


def test_fetch_hot_failure_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tfp.subprocess, "run", lambda *a, **k: _FakeProc(1, "", "boom"))
    assert tfp._fetch_hot(10) == []


def test_fetch_hot_missing_binary_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a: object, **k: object) -> None:
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(tfp.subprocess, "run", _raise)
    assert tfp._fetch_hot(10) == []


def test_fetch_hot_bad_json_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tfp.subprocess, "run", lambda *a, **k: _FakeProc(0, "not json", ""))
    assert tfp._fetch_hot(10) == []


class _FakeProc:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

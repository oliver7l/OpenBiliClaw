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


# ---------------------------------------------------------------------------
# 步行街 (Buxingjie) direct HTTP scrape tests
# ---------------------------------------------------------------------------

_SAMPLE_BXJ_HTML = """
<ul>
<li class="bbs-sl-web-post-body"><div class="bbs-sl-web-post-layout">
<div class="post-title"><a href="/642228101.html" class="p-title">测试帖子标题一</a></div>
<div class="post-datum">102 / 32408</div>
<div class="post-auth"><a href="https://my.hupu.com/208223136791539">测试作者A</a></div>
<div class="post-time">09-04 17:52</div>
</div></li>
<li class="bbs-sl-web-post-body"><div class="bbs-sl-web-post-layout">
<div class="post-title"><a href="/642236837.html" class="p-title">测试帖子标题二</a></div>
<div class="post-datum">5 / 1200</div>
<div class="post-auth"><a href="https://my.hupu.com/146574301444784">测试作者B</a></div>
<div class="post-time">09-04 17:50</div>
</div></li>
<li class="bbs-sl-web-post-body"><div class="bbs-sl-web-post-layout">
<div class="post-title"><a href="/641915588.html" class="p-title">万单位测试</a></div>
<div class="post-datum">1.2万 / 50.5万</div>
<div class="post-auth"><a href="https://my.hupu.com/125891782125113">作者C</a></div>
<div class="post-time">昨天</div>
</div></li>
</ul>
"""


def test_parse_bxj_html_extracts_all_fields() -> None:
    posts = hfp._parse_bxj_html(_SAMPLE_BXJ_HTML)
    assert len(posts) == 3

    p0 = posts[0]
    assert p0["id"] == "642228101"
    assert p0["title"] == "测试帖子标题一"
    assert p0["reply_count"] == 102
    assert p0["view_count"] == 32408
    assert p0["author"] == "测试作者A"
    assert p0["author_uid"] == "208223136791539"
    assert p0["post_time"] == "09-04 17:52"
    assert p0["url"] == "https://bbs.hupu.com/642228101.html"


def test_parse_bxj_html_wan_units() -> None:
    posts = hfp._parse_bxj_html(_SAMPLE_BXJ_HTML)
    p2 = posts[2]
    assert p2["reply_count"] == 12000  # 1.2万
    assert p2["view_count"] == 505000  # 50.5万


def test_parse_bxj_html_empty_returns_empty() -> None:
    assert hfp._parse_bxj_html("") == []
    assert hfp._parse_bxj_html("<html><body>no posts</body></html>") == []


def test_parse_int_various_formats() -> None:
    assert hfp._parse_int("102") == 102
    assert hfp._parse_int("1.2万") == 12000
    assert hfp._parse_int("50.5万") == 505000
    assert hfp._parse_int("1,234") == 1234
    assert hfp._parse_int("") == 0
    assert hfp._parse_int("abc") == 0


def test_to_bxj_rows_normalization() -> None:
    posts = [
        {
            "id": "642228101",
            "title": "测试帖子",
            "reply_count": 102,
            "view_count": 32408,
            "author": "测试作者",
            "author_uid": "123",
            "post_time": "09-04 17:52",
            "url": "https://bbs.hupu.com/642228101.html",
        },
        {"id": "642228101", "title": "重复帖子", "url": "https://bbs.hupu.com/642228101.html"},
        {"id": "", "title": "无ID", "url": "https://bbs.hupu.com/x.html"},
    ]
    rows = hfp._to_bxj_rows(posts)
    assert len(rows) == 1  # only one unique valid post
    assert rows[0]["bvid"] == "642228101"
    assert rows[0]["source"] == "hupu-bxj"
    assert rows[0]["up_name"] == "测试作者"
    assert rows[0]["reply_count"] == 102
    assert rows[0]["view_count"] == 32408


def test_bxj_page_url_construction() -> None:
    # Page 1 uses base URL, page 2+ uses -N suffix
    # We can't easily test _fetch_bxj_page directly without network,
    # but we can verify the URL pattern by checking the constant
    assert hfp.BXJ_BASE_URL == "https://bbs.hupu.com/bxj"
    assert hfp.BXJ_POSTS_PER_PAGE == 50

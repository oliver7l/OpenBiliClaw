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


# ---------------------------------------------------------------------------
# Search (虎扑搜索) tests
# ---------------------------------------------------------------------------

_SEARCH_HTML_SAMPLE = (
    '<div class="content-outline">'
    '<div class="content-wrap">'
    '<a class="content-wrap-span" href="https://bbs.hupu.com/641673232.html">'
    "这配置跑<font color='#c01e2f'>python</font>处理数据</a>"
    '<a class="content-wrap-span" href="https://bbs.hupu.com/74">PC区</a>'
    "<span>2026-08-07</span>"
    '<span class="content-wrap-span1">8</span>'
    '<span class="content-wrap-span1">1</span>'
    '<span class="content-wrap-span1">0</span>'
    "</div>"
    '<div class="content-wrap">'
    '<a class="content-wrap-span" href="https://bbs.hupu.com/639691499.html">'
    "Python异步编程入门指南</a>"
    '<a class="content-wrap-span" href="https://bbs.hupu.com/37">步行街</a>'
    "<span>2026-07-15</span>"
    '<span class="content-wrap-span1">42</span>'
    '<span class="content-wrap-span1">15</span>'
    '<span class="content-wrap-span1">3</span>'
    "</div>"
    '<div class="content-wrap">'
    '<a class="content-wrap-span" href="https://bbs.hupu.com/invalid">无数字ID</a>'
    "<span>2026-01-01</span>"
    "</div>"
    "</div>"
)


def test_parse_search_html_extracts_all_fields() -> None:
    posts = hfp._parse_search_html(_SEARCH_HTML_SAMPLE)
    assert len(posts) == 2  # third block has no valid numeric ID

    p1 = posts[0]
    assert p1["id"] == "641673232"
    assert p1["title"] == "这配置跑python处理数据"
    assert p1["forum"] == "PC区"
    assert p1["post_time"] == "2026-08-07"
    assert p1["reply_count"] == 8
    assert p1["recommend_count"] == 1
    assert p1["light_count"] == 0
    assert p1["url"] == "https://bbs.hupu.com/641673232.html"

    p2 = posts[1]
    assert p2["id"] == "639691499"
    assert p2["title"] == "Python异步编程入门指南"
    assert p2["forum"] == "步行街"
    assert p2["reply_count"] == 42
    assert p2["recommend_count"] == 15
    assert p2["light_count"] == 3


def test_parse_search_html_strips_font_highlight_tags() -> None:
    """Keyword highlights (<font color='...'>keyword</font>) must be stripped."""
    html = (
        '<div class="content-wrap">'
        '<a class="content-wrap-span" href="https://bbs.hupu.com/123.html">'
        "学习<font color='#c01e2f'>AI</font>和<font color='#c01e2f'>Python</font>技术</a>"
        '<a class="content-wrap-span" href="https://bbs.hupu.com/1">技术区</a>'
        "<span>2026-01-01</span>"
        '<span class="content-wrap-span1">5</span>'
        '<span class="content-wrap-span1">2</span>'
        '<span class="content-wrap-span1">1</span>'
        "</div>"
    )
    posts = hfp._parse_search_html(html)
    assert len(posts) == 1
    assert posts[0]["title"] == "学习AI和Python技术"
    assert "<font" not in posts[0]["title"]


def test_parse_search_html_empty_returns_empty() -> None:
    assert hfp._parse_search_html("") == []
    assert hfp._parse_search_html("<html><body>no results</body></html>") == []


def test_to_search_rows_normalization() -> None:
    posts = [
        {
            "id": "641673232",
            "title": "测试帖子",
            "forum": "PC区",
            "post_time": "2026-08-07",
            "reply_count": 8,
            "recommend_count": 1,
            "light_count": 0,
            "url": "https://bbs.hupu.com/641673232.html",
            "keyword": "python",
        },
        {"id": "641673232", "title": "重复帖子", "url": "https://bbs.hupu.com/641673232.html"},
        {"id": "", "title": "无ID", "url": "https://bbs.hupu.com/x.html"},
    ]
    rows = hfp._to_search_rows(posts)
    assert len(rows) == 1
    assert rows[0]["bvid"] == "641673232"
    assert rows[0]["source"] == "hupu-search-python"
    assert rows[0]["source_platform"] == "hupu"
    assert rows[0]["content_type"] == "thread"
    assert rows[0]["reply_count"] == 8
    assert rows[0]["forum"] == "PC区"


def test_search_sort_options_valid() -> None:
    assert "general" in hfp.SEARCH_SORT_OPTIONS
    assert "createtime" in hfp.SEARCH_SORT_OPTIONS
    assert "light" in hfp.SEARCH_SORT_OPTIONS
    assert "reply" in hfp.SEARCH_SORT_OPTIONS
    assert hfp.SEARCH_BASE_URL == "https://bbs.hupu.com/search"

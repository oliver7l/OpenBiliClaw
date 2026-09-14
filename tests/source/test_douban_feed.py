"""豆瓣 feed 阅读源测试：adapter（带 cookie、mock 抓取）+ tasks（写库/去重）。

douban feed 网络抓取用 mock，不触网；写库用 tmp db。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from openbiliclaw.sources.douban_feed_adapter import DoubanFeedAdapter, _feed_url

_SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>豆瓣评论</title>
    <link>https://www.douban.com/review/</link>
    <item>
      <title>出走的决心：一次勇敢的出走</title>
      <link>https://movie.douban.com/review/123/</link>
      <author>影迷A</author>
      <pubDate>Mon, 01 Jan 2024 08:00:00 GMT</pubDate>
      <description><![CDATA[<p>这是一篇影评正文，讲述了女性觉醒。</p>]]></description>
    </item>
    <item>
      <title>你好，李焕英</title>
      <link>https://movie.douban.com/review/456/</link>
      <author>影迷B</author>
      <pubDate>Tue, 02 Jan 2024 08:00:00 GMT</pubDate>
      <description><![CDATA[<p>温暖的母女情。</p>]]></description>
    </item>
  </channel>
</rss>
"""


def _recipe(name="豆瓣feed", feed_kind="review", uid="", group_id="") -> object:
    from openbiliclaw.sources.protocol import SourceRecipe

    return SourceRecipe(
        id="t1",
        source_type="douban_feed",
        name=name,
        strategy="feed",
        config={"feed_kind": feed_kind, "uid": uid, "group_id": group_id, "name": name},
    )


def test_feed_url_templates() -> None:
    assert _feed_url("comment", uid="60690917") == "https://douban.com/feed/people/60690917/"
    assert _feed_url("review") == "https://douban.com/feed/review/latest"
    assert _feed_url("group", group_id="beijing") == "https://www.douban.com/feed/group/beijing/discussion"
    # diary 不走 RSS URL（rexxar 直连），回落默认 review
    assert _feed_url("diary", uid="60690917") == "https://douban.com/feed/review/latest"
    # 默认 review
    assert _feed_url("") == "https://douban.com/feed/review/latest"


def test_cookie_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    # JSON 数组格式（浏览器导出）
    a = DoubanFeedAdapter(cookie='[{"name":"ck","value":"yU89"},{"name":"bid","value":"R2wz"}]')
    assert a._cookie_jar() == {"ck": "yU89", "bid": "R2wz"}
    # Netscape 格式
    a2 = DoubanFeedAdapter(cookie="ck=yU89; bid=R2wzGlT0iHw")
    assert a2._cookie_jar() == {"ck": "yU89", "bid": "R2wzGlT0iHw"}
    # 空 cookie
    assert DoubanFeedAdapter()._cookie_jar() is None


def test_fetch_mock_parses_items(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class _FakeResp:
        text = _SAMPLE_XML

        def raise_for_status(self) -> None:
            pass

    def fake_get(url, cookies=None, timeout=None, headers=None):  # type: ignore[no-untyped-def]
        captured["url"] = url
        captured["cookies"] = cookies
        return _FakeResp()

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    adapter = DoubanFeedAdapter(cookie='[{"name":"ck","value":"yU89"}]')
    recipe = _recipe(feed_kind="review")
    items = asyncio.run(adapter.fetch(recipe, limit=5))

    assert captured["url"] == "https://douban.com/feed/review/latest"
    assert captured["cookies"] == {"ck": "yU89"}
    assert len(items) == 2
    assert items[0].title == "出走的决心：一次勇敢的出走"
    assert items[0].content_url == "https://movie.douban.com/review/123/"
    assert items[0].source_platform == "douban_feed"
    assert "影评正文" in items[0].content_text


def test_fetch_cookie_built_from_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class _FakeResp:
        text = _SAMPLE_XML

        def raise_for_status(self) -> None:
            pass

    def fake_get(url, cookies=None, timeout=None, headers=None):  # type: ignore[no-untyped-def]
        captured["url"] = url
        return _FakeResp()

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    adapter = DoubanFeedAdapter(cookie="ck=yU89")
    asyncio.run(adapter.fetch(_recipe(feed_kind="comment", uid="60690917"), limit=5))
    assert captured["url"] == "https://douban.com/feed/people/60690917/"


def test_fetch_diary_uses_rexxar_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    """diary 直连豆瓣 rexxar JSON 接口，带 Referer + cookie，解析 status items。"""
    captured: dict = {}
    _SAMPLE_TIMELINE = {  # noqa: N806
        "count": 2,
        "items": [
            {
                "status": {
                    "text": "看完《出走的决心》，很有感触。",
                    "sharing_url": "https://www.douban.com/people/60690917/status/1/",
                    "create_time": "2026-09-01 10:00:00",
                    "author": {"name": "影迷A"},
                }
            },
            {
                "status": {
                    "text": "读书笔记打卡",
                    "sharing_url": "https://www.douban.com/people/60690917/status/2/",
                    "create_time": "2026-09-02 09:00:00",
                    "author": {"name": "影迷A"},
                }
            },
        ],
    }

    def fake_get(url, cookies=None, params=None, timeout=None, headers=None):  # type: ignore[no-untyped-def]
        captured["url"] = url
        captured["cookies"] = cookies
        captured["headers"] = headers
        resp = type("R", (), {})()
        resp.json = lambda: _SAMPLE_TIMELINE  # type: ignore[attr-defined]
        resp.raise_for_status = lambda: None  # type: ignore[attr-defined]
        return resp

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    adapter = DoubanFeedAdapter(cookie="ck=yU89; dbcl2=\"60690917:x\"")
    items = asyncio.run(adapter.fetch(_recipe(feed_kind="diary", uid="60690917"), limit=5))
    assert captured["url"].startswith(
        "https://m.douban.com/rexxar/api/v2/status/user_timeline/60690917"
    )
    assert captured["headers"].get("Referer", "").startswith(
        "https://m.douban.com/people/60690917"
    )
    assert captured["cookies"] == {"ck": "yU89", "dbcl2": "\"60690917:x\""}
    assert len(items) == 2
    assert items[0].source_platform == "douban_feed"
    assert items[0].content_url == "https://www.douban.com/people/60690917/status/1/"
    assert "出走的决心" in items[0].title


def test_fetch_empty_xml(monkeypatch: pytest.MonkeyPatch) -> None:
    class _EmptyResp:
        text = '<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>'

        def raise_for_status(self) -> None:
            pass

    def fake_get(url, cookies=None, timeout=None, headers=None):  # type: ignore[no-untyped-def]
        return _EmptyResp()

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    adapter = DoubanFeedAdapter()
    items = asyncio.run(adapter.fetch(_recipe(), limit=5))
    assert items == []


def test_fetch_diary_since_filters_old(monkeypatch: pytest.MonkeyPatch) -> None:
    """增量：since 之后的新条目才返回，旧条目被裁掉，且不继续翻页。"""
    calls: list[str] = []
    _TIMELINE = {  # noqa: N806
        "count": 2,
        "items": [
            {
                "status": {
                    "text": "新动态 A",
                    "create_time": "2026-09-10 10:00:00",
                    "sharing_url": "https://www.douban.com/people/1/status/a/",
                    "author": {"name": "T"},
                }
            },
            {
                "status": {
                    "text": "旧动态 B",
                    "create_time": "2026-09-01 09:00:00",
                    "sharing_url": "https://www.douban.com/people/1/status/b/",
                    "author": {"name": "T"},
                }
            },
        ],
    }

    def fake_get(url, cookies=None, params=None, timeout=None, headers=None):  # type: ignore[no-untyped-def]
        calls.append(str(params or {}))
        resp = type("R", (), {})()
        resp.json = lambda: _TIMELINE  # type: ignore[attr-defined]
        resp.raise_for_status = lambda: None  # type: ignore[attr-defined]
        return resp

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    adapter = DoubanFeedAdapter(cookie="ck=yU89")
    items, newest = asyncio.run(
        adapter.fetch_diary_since("1", "T", since="2026-09-05 00:00:00")
    )
    # 只有 create_time > since 的新条目，且只翻一页就停（returned from first page）
    assert [it.title for it in items] == ["新动态 A"]
    assert newest == "2026-09-10 10:00:00"
    assert len(calls) == 1


def test_watermarks_persist(tmp_path: pytest.TempPathFactory) -> None:
    """任务层 watermarks 落盘/读回。"""
    from openbiliclaw.sources import douban_feed_tasks as tasks

    state_file = tmp_path / "douban_feed_state.json"

    class _FakeDB:
        _db_path = state_file

    # 初始为空
    assert tasks._load_watermarks(_FakeDB()) == {}
    # 写入后读回
    tasks._save_watermarks(_FakeDB(), {"diary:1": "2026-09-10 10:00:00"})
    assert tasks._load_watermarks(_FakeDB()) == {"diary:1": "2026-09-10 10:00:00"}


def _fake_db(monkeypatch: pytest.MonkeyPatch) -> object:
    """返回记录 upsert_article 调用的假 db。"""

    class _FakeDB:
        def __init__(self) -> None:
            self.upserted: list[dict] = []
            self.pool: list[dict] = []

        def upsert_article(self, **kw):  # type: ignore[no-untyped-def]
            self.upserted.append(kw)

        def inject_article_to_pool(self, **kw):  # type: ignore[no-untyped-def]
            self.pool.append(kw)

    db = _FakeDB()
    monkeypatch.setattr("openbiliclaw.sources.douban_feed_tasks._DB_WRITE_EXECUTOR", None)
    return db


def test_persist_items_source_type(monkeypatch: pytest.MonkeyPatch) -> None:
    from obc_discovery.engine import DiscoveredContent

    from openbiliclaw.sources.douban_feed_tasks import _persist_items

    db = _fake_db(monkeypatch)
    item = DiscoveredContent(
        content_id="d1",
        content_url="https://movie.douban.com/review/123/",
        source_platform="douban_feed",
        title="测试标题",
        description="摘要",
        author_name="作者",
    )
    n = _persist_items(db, [item], "豆瓣feed")
    assert n == 1
    assert db.upserted[0]["source_type"] == "douban_feed"
    assert db.upserted[0]["url"] == "https://movie.douban.com/review/123/"
    assert db.upserted[0]["title"] == "测试标题"
    assert db.pool[0]["bvid"].startswith("douban_feed-")


def test_persist_items_dedup(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一 url 的条目在 `_persist_items` 层不重复计数（入库去重由 upsert_article 保证）。"""
    from openbiliclaw.sources.douban_feed_tasks import _persist_items

    db = _fake_db(monkeypatch)
    n = _persist_items(db, [], "豆瓣feed")
    assert n == 0

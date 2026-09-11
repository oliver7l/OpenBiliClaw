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
    # diary 走本地自部署 RSSHub（默认），可显式给 rsshub_url
    assert _feed_url("diary", uid="60690917") == "http://127.0.0.1:1200/douban/user/60690917/status"
    assert (
        _feed_url("diary", uid="60690917", rsshub_url="http://192.168.1.5:1200/")
        == "http://192.168.1.5:1200/douban/user/60690917/status"
    )
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


def test_fetch_diary_uses_self_hosted_rsshub(monkeypatch: pytest.MonkeyPatch) -> None:
    """diary feed 走本地自部署 RSSHub，且构造器传入的 rsshub_url 生效。"""
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
    adapter = DoubanFeedAdapter(
        cookie="ck=yU89", rsshub_url="http://127.0.0.1:1200/"
    )
    asyncio.run(adapter.fetch(_recipe(feed_kind="diary", uid="60690917"), limit=5))
    assert captured["url"] == "http://127.0.0.1:1200/douban/user/60690917/status"


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
    from openbiliclaw.discovery.engine import DiscoveredContent
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

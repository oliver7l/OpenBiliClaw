"""Tests for the Douyin recommendation-feed producer (Playwright-based)."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from openbiliclaw.runtime import douyin_feed_producer as dfp

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE content_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bvid TEXT UNIQUE, title TEXT, up_name TEXT, author_name TEXT,
            content_url TEXT, source_platform TEXT, source TEXT,
            content_type TEXT, pool_status TEXT, body_text TEXT,
            like_count INTEGER DEFAULT 0, comment_count INTEGER DEFAULT 0,
            favorite_count INTEGER DEFAULT 0, share_count INTEGER DEFAULT 0,
            discovered_at TEXT)"""
    )


class _FakePage:
    """Minimal stand-in for a Playwright Page object."""

    def __init__(
        self,
        inner_text: str = "",
        evaluate_results: dict[str, Any] | None = None,
    ) -> None:
        self._inner_text = inner_text
        self._evaluate_results = evaluate_results or {}

    def evaluate(self, script: str) -> Any:
        # The producer calls evaluate with two distinct scripts: one for
        # innerText (contains "document.body.innerText") and one for
        # aweme_id (contains "feed-active-video").
        if "document.body.innerText" in script:
            return self._inner_text
        if "feed-active-video" in script:
            return self._evaluate_results.get("aweme_id", "")
        return None


# ---------------------------------------------------------------------------
# _parse_count
# ---------------------------------------------------------------------------


def test_parse_count_pure_number() -> None:
    assert dfp._parse_count("6161") == 6161
    assert dfp._parse_count("0") == 0


def test_parse_count_wan_suffix() -> None:
    assert dfp._parse_count("1.4万") == 14000
    assert dfp._parse_count("547.2万") == 5472000


def test_parse_count_w_suffix() -> None:
    assert dfp._parse_count("2.5w") == 25000


def test_parse_count_invalid_returns_none() -> None:
    assert dfp._parse_count("") is None
    assert dfp._parse_count("not a number") is None
    assert dfp._parse_count("1.5x") is None


# ---------------------------------------------------------------------------
# _parse_video_text
# ---------------------------------------------------------------------------


_SAMPLE_FEED_TEXT = """\
精选
推荐
AI抖音
关注
朋友
我的
直播
放映厅
短剧
搜索

00:20 / 06:37
倍速
智能
清屏
连播
6161
161
1580
235
听抖音
@甘莫
· 5小时前
第2集：小伙偶然捡到一张机票，却因此开启一段人生奇遇
#抖音精选 #了不起的精讲团
合集 · 《被捡到的男人》
已是最新集
1.4万
1762
3281
4116
"""


def test_parse_video_text_extracts_fields() -> None:
    result = dfp._parse_video_text(_SAMPLE_FEED_TEXT)
    assert result is not None
    assert result["author"] == "甘莫"
    assert "小伙偶然捡到一张机票" in result["title"]
    assert result["published_at"] == "5小时前"
    assert "抖音精选" in result["hashtags"]
    assert result["likes"] == 6161
    assert result["comments"] == 161
    assert result["favorites"] == 1580
    assert result["shares"] == 235
    assert result["duration"] == "06:37"


def test_parse_video_text_no_author_returns_none() -> None:
    assert dfp._parse_video_text("just some text without at sign") is None


def test_parse_video_text_handles_missing_counts() -> None:
    text = """\
@测试作者
· 2天前
这是一个测试视频标题
#测试标签
"""
    result = dfp._parse_video_text(text)
    assert result is not None
    assert result["author"] == "测试作者"
    assert result["likes"] == 0
    assert result["comments"] == 0


# ---------------------------------------------------------------------------
# _parse_jingxuan_cards
# ---------------------------------------------------------------------------

_JINGXUAN_SAMPLE = """\
精选
推荐
关注
我的
全部公开课游戏二次元音乐影视美食知识
01:39
2490
潮汕人超爱的生腌梭子蟹，入口真的绝 #美食 #梭子蟹 #广东美食
@美食原产地
 · 8月14日
45:57
8592
【韩路游记】穿越甘南秘境（上集） #甘南旅游 #洛克之路
@韩路有点意思
 · 7月25日
29:36
1.1万
大明王朝81：全身而退 #大明王朝1566
@Lucas真有大劲
 · 8月12日
"""


def test_parse_jingxuan_cards_extracts_multiple() -> None:
    cards = dfp._parse_jingxuan_cards(_JINGXUAN_SAMPLE)
    assert len(cards) == 3
    assert cards[0]["author"] == "美食原产地"
    assert "生腌梭子蟹" in cards[0]["title"]
    assert cards[0]["likes"] == 2490
    assert cards[0]["duration"] == "01:39"
    assert "美食" in cards[0]["hashtags"]
    assert cards[1]["author"] == "韩路有点意思"
    assert cards[1]["likes"] == 8592
    assert cards[2]["likes"] == 11000  # 1.1万


def test_parse_jingxuan_cards_skips_ads_and_live() -> None:
    text = """\
00:30
100
这是一个正常视频标题 #正常
@正常作者
 · 8月1日
广告
豆包工作任务全新改版
立即领取
05:00
200
另一个正常视频 #另一个
@另一个作者
 · 8月2日
直播中
203
船长OVO正在直播
@
船长ovo
"""
    cards = dfp._parse_jingxuan_cards(text)
    authors = [c["author"] for c in cards]
    assert "正常作者" in authors
    assert "另一个作者" in authors
    assert "船长ovo" not in authors


def test_parse_jingxuan_cards_empty_returns_empty() -> None:
    assert dfp._parse_jingxuan_cards("") == []
    assert dfp._parse_jingxuan_cards("no cards here just text") == []


def test_to_rows_jingxuan_source() -> None:
    videos = [
        {
            "bvid": "jx001",
            "author": "测试作者",
            "title": "精选视频标题",
            "hashtags": [],
            "likes": 100,
            "comments": 0,
            "favorites": 0,
            "shares": 0,
            "content_url": dfp.JINGXUAN_URL,
        }
    ]
    rows = dfp._to_rows(videos, source="douyin-jingxuan")
    assert len(rows) == 1
    assert rows[0]["source"] == "douyin-jingxuan"
    assert rows[0]["source_platform"] == "douyin"


# ---------------------------------------------------------------------------
# _is_logged_in
# ---------------------------------------------------------------------------


def test_is_logged_in_true_when_no_login_prompt() -> None:
    page = _FakePage(inner_text="精选\n推荐\n关注\n我的\n一些视频内容")
    assert dfp._is_logged_in(page) is True


def test_is_logged_in_false_when_qr_prompt_visible() -> None:
    page = _FakePage(inner_text="扫码登录\n验证码登录\n密码登录")
    assert dfp._is_logged_in(page) is False


def test_is_logged_in_false_when_standalone_login_button() -> None:
    page = _FakePage(inner_text="精选\n推荐\n登录\n投稿")
    assert dfp._is_logged_in(page) is False


def test_is_logged_in_handles_evaluate_exception() -> None:
    class _BrokenPage:
        def evaluate(self, script: str) -> Any:
            raise RuntimeError("page crashed")

    assert dfp._is_logged_in(_BrokenPage()) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _extract_aweme_id
# ---------------------------------------------------------------------------


def test_extract_aweme_id_from_dom() -> None:
    page = _FakePage(evaluate_results={"aweme_id": "7395123456789012345"})
    assert dfp._extract_aweme_id(page) == "7395123456789012345"


def test_extract_aweme_id_empty_when_not_found() -> None:
    page = _FakePage(evaluate_results={"aweme_id": ""})
    assert dfp._extract_aweme_id(page) == ""


def test_extract_aweme_id_handles_exception() -> None:
    class _BrokenPage:
        def evaluate(self, script: str) -> Any:
            raise RuntimeError("detached")

    assert dfp._extract_aweme_id(_BrokenPage()) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _to_rows
# ---------------------------------------------------------------------------


def _sample_videos() -> list[dict[str, Any]]:
    return [
        {
            "bvid": "7395123456789012345",
            "aweme_id": "7395123456789012345",
            "author": "甘莫",
            "title": "第2集：小伙偶然捡到一张机票",
            "published_at": "5小时前",
            "hashtags": ["抖音精选", "了不起的精讲团"],
            "likes": 6161,
            "comments": 161,
            "favorites": 1580,
            "shares": 235,
            "duration": "06:37",
            "content_url": "https://www.douyin.com/video/7395123456789012345",
        },
        {
            "bvid": "abc123def456",
            "aweme_id": "",
            "author": "测试作者",
            "title": "测试视频标题",
            "published_at": "2天前",
            "hashtags": [],
            "likes": 100,
            "comments": 10,
            "favorites": 5,
            "shares": 2,
            "duration": "01:30",
            "content_url": dfp.RECOMMEND_URL,
        },
    ]


def test_to_rows_normalizes_videos() -> None:
    rows = dfp._to_rows(_sample_videos())
    assert len(rows) == 2
    first = rows[0]
    assert first["bvid"] == "7395123456789012345"
    assert first["title"] == "第2集：小伙偶然捡到一张机票"
    assert first["up_name"] == "甘莫"
    assert first["author_name"] == "甘莫"
    assert first["source_platform"] == "douyin"
    assert first["source"] == "douyin-recommend"
    assert first["content_type"] == "video"
    assert first["like_count"] == 6161
    assert first["comment_count"] == 161
    assert first["favorite_count"] == 1580
    assert first["share_count"] == 235
    assert "#抖音精选" in first["body_text"]


def test_to_rows_skips_invalid_items() -> None:
    videos = [
        {"bvid": "", "author": "a", "title": "t"},
        {"bvid": "1", "author": "", "title": "t"},
        {"bvid": "2", "author": "a", "title": ""},
        "not-a-dict",
        {"bvid": "3", "author": "ok", "title": "valid"},
    ]
    rows = dfp._to_rows(videos)  # type: ignore[arg-type]
    assert [r["bvid"] for r in rows] == ["3"]


def test_to_rows_deduplicates_by_bvid() -> None:
    v = _sample_videos()
    rows = dfp._to_rows(v + v)
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# _insert_rows
# ---------------------------------------------------------------------------


def test_insert_rows_writes_cache(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    _make_tables(conn)
    rows = dfp._to_rows(_sample_videos())

    inserted = dfp._insert_rows(conn, rows)
    conn.commit()
    assert inserted == 2
    assert conn.execute("SELECT COUNT(*) FROM content_cache").fetchone()[0] == 2

    # Idempotent
    inserted2 = dfp._insert_rows(conn, rows)
    conn.commit()
    assert inserted2 == 0
    conn.close()


def test_insert_rows_preserves_interaction_counts(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    _make_tables(conn)
    rows = dfp._to_rows(_sample_videos())
    dfp._insert_rows(conn, rows)
    conn.commit()

    row = conn.execute(
        "SELECT like_count, comment_count, favorite_count, share_count "
        "FROM content_cache WHERE bvid = ?",
        ("7395123456789012345",),
    ).fetchone()
    assert row == (6161, 161, 1580, 235)
    conn.close()

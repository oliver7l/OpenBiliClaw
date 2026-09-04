"""Tests for the reading-library enhancement layer (progress / favorites /
notes / AI summary / stats), the article_finished / article_dismissed event
classification, and the terminal ``hidden`` (block / 不再出现) state."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from openbiliclaw.sources.event_format import classify_event_satisfaction
from openbiliclaw.storage.database import Database


def _make_db() -> tuple[Database, Path]:
    tmp = Path(tempfile.mkdtemp()) / "test.db"
    db = Database(tmp)
    db.initialize()
    db.upsert_article(
        "rss", "观察站", "测试文章标题", "https://example.com/1",
        author="作者A", summary="摘要",
        content_text="正文内容。" * 40,
        tags=["科技", "AI"],
    )
    return db, tmp


def _first_article_id(db: Database) -> int:
    row = db.conn.execute("SELECT id FROM articles LIMIT 1").fetchone()
    assert row is not None
    return int(row["id"])


def test_articles_table_has_new_columns() -> None:
    db, _ = _make_db()
    cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(articles)").fetchall()}
    assert {"reading_percent", "reading_progress", "favorited", "ai_summary"} <= cols


def test_article_notes_table_created() -> None:
    db, _ = _make_db()
    tables = {
        r["name"]
        for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "article_notes" in tables


def test_reading_progress_roundtrip_and_clamp() -> None:
    db, _ = _make_db()
    aid = _first_article_id(db)
    assert db.update_article_reading_progress(aid, percent=42.5, progress="scroll-999")
    art = db.get_article(aid)
    assert art is not None
    assert art["reading_percent"] == pytest.approx(42.5)
    assert art["reading_progress"] == "scroll-999"
    # clamp 到 [0, 100]
    assert db.update_article_reading_progress(aid, percent=250.0)
    assert db.get_article(aid)["reading_percent"] == 100.0
    assert db.update_article_reading_progress(aid, percent=-5.0)
    assert db.get_article(aid)["reading_percent"] == 0.0


def test_favorite_flag_roundtrip() -> None:
    db, _ = _make_db()
    aid = _first_article_id(db)
    assert db.set_article_favorited(aid, True)
    assert db.get_article(aid)["favorited"] == 1
    assert db.set_article_favorited(aid, False)
    assert db.get_article(aid)["favorited"] == 0


def test_note_add_list_delete() -> None:
    db, _ = _make_db()
    aid = _first_article_id(db)
    nid = db.add_article_note(aid, quote="引文", note="想法", color="#f6c")
    assert nid is not None
    notes = db.get_article_notes(aid)
    assert len(notes) == 1
    assert notes[0]["quote"] == "引文"
    assert notes[0]["note"] == "想法"
    assert notes[0]["color"] == "#f6c"
    assert db.delete_article_note(nid)
    assert db.get_article_notes(aid) == []
    # 删除不存在的笔记返回 True（幂等）
    assert db.delete_article_note(99999)


def test_ai_summary_store_and_missing_list() -> None:
    db, _ = _make_db()
    aid = _first_article_id(db)
    summary = json.dumps({"one_liner": "一句话", "points": ["a", "b", "c"]}, ensure_ascii=False)
    assert db.update_article_ai_summary(aid, summary)
    assert json.loads(db.get_article(aid)["ai_summary"])["one_liner"] == "一句话"
    # 已有摘要的行不再出现在待补列表
    missing = db.get_articles_missing_summary(limit=10)
    assert all(m["id"] != aid for m in missing)


def test_reading_stats_shape() -> None:
    db, _ = _make_db()
    aid = _first_article_id(db)
    db.update_article_status(aid, "finished")
    db.add_article_note(aid, quote="q", note="n")
    stats = db.get_article_reading_stats()
    assert stats["by_status"].get("finished", 0) >= 1
    assert stats["notes"] >= 1
    assert stats["by_source"].get("rss", 0) >= 1
    assert isinstance(stats["top_tags"], list)


def test_article_finished_classified_positive() -> None:
    category, reason = classify_event_satisfaction(
        {
            "event_type": "article_finished",
            "url": "https://example.com/1",
            "title": "测试",
            "metadata": {"article_id": 1, "source_type": "rss"},
        }
    )
    assert category == "positive"
    assert reason == "explicit_engagement"


def test_list_articles_includes_new_fields() -> None:
    db, _ = _make_db()
    aid = _first_article_id(db)
    db.update_article_reading_progress(aid, percent=30)
    db.set_article_favorited(aid, True)
    items = db.get_recent_articles(limit=10)
    assert len(items) >= 1
    item = next(i for i in items if i["id"] == aid)
    assert item["reading_percent"] == pytest.approx(30)
    assert item["favorited"] == 1


def test_hidden_article_is_excluded_from_default_views() -> None:
    """屏蔽后：列表 / 计数 / 搜索 / 来源分布都不再出现，但显式过滤仍可查。"""
    db, _ = _make_db()
    aid = _first_article_id(db)
    assert db.update_article_status(aid, "hidden") is True

    assert db.get_recent_articles(limit=10) == []
    assert db.count_articles() == 0
    assert db.search_articles(q="测试文章标题") == []
    facets = db.conn.execute(
        "SELECT COUNT(*) AS n FROM articles "
        "WHERE COALESCE(status, 'unread') != 'hidden'"
    ).fetchone()
    assert int(facets["n"]) == 0

    # 显式按 hidden 过滤仍能定位到，便于将来做「已屏蔽」管理视图。
    hidden = db.get_recent_articles(limit=10, status="hidden")
    assert [row["id"] for row in hidden] == [aid]
    assert db.count_articles(status="hidden") == 1


def test_hidden_survives_resync_of_same_url() -> None:
    """重新抓取同一 URL 不得把已屏蔽文章拉回可见池：upsert 不触碰 status。"""
    db, _ = _make_db()
    aid = _first_article_id(db)
    db.update_article_status(aid, "hidden")

    db.upsert_article(
        "rss", "观察站", "测试文章标题（更新）", "https://example.com/1",
        author="作者A", summary="新摘要", content_text="新正文。" * 40,
        tags=["科技"],
    )
    assert db.get_article(aid)["status"] == "hidden"
    assert db.get_recent_articles(limit=10) == []


def test_article_dismissed_classified_negative() -> None:
    category, reason = classify_event_satisfaction(
        {
            "event_type": "article_dismissed",
            "url": "https://example.com/1",
            "title": "测试",
            "metadata": {"article_id": 1, "source_type": "rss"},
        }
    )
    assert category == "negative"
    assert reason == "explicit_aversion"


def test_blocking_article_suppresses_matching_pool_rows() -> None:
    """屏蔽文章时同步清洗候选池：同一 content_url 的 fresh 行被抑制，
    其它 URL 与已展示的历史行不受影响。"""
    db, _ = _make_db()
    aid = _first_article_id(db)  # url = https://example.com/1
    db.cache_content(
        "BV1P", title="池内同文", source="rss_polling",
        content_url="https://example.com/1",
    )
    db.cache_content(
        "BV2P", title="池内无关", source="rss_polling",
        content_url="https://example.com/other",
    )
    db.cache_content(
        "BV3P", title="池内同文已展示", source="search",
        content_url="https://example.com/1",
    )
    db.conn.execute(
        "UPDATE content_cache SET pool_status = 'shown', "
        "recommended_at = CURRENT_TIMESTAMP WHERE bvid = 'BV3P'"
    )
    db.conn.commit()

    assert db.suppress_pool_rows_by_url("https://example.com/1") == 1
    assert db.suppress_pool_rows_by_url("") == 0

    def status(bvid: str) -> str:
        row = db.conn.execute(
            "SELECT pool_status FROM content_cache WHERE bvid = ?", (bvid,)
        ).fetchone()
        return str(row["pool_status"])

    assert status("BV1P") == "suppressed"
    assert status("BV2P") == "fresh"
    assert status("BV3P") == "shown"  # 历史行保留，不连坐


def test_unblocking_article_revives_suppressed_pool_rows() -> None:
    """恢复（取消屏蔽）把此前连坐抑制的候选放回 fresh。"""
    db, _ = _make_db()
    aid = _first_article_id(db)
    db.cache_content(
        "BV1P", title="池内同文", source="rss_polling",
        content_url="https://example.com/1",
    )
    assert db.suppress_pool_rows_by_url("https://example.com/1") == 1
    assert db.update_article_status(aid, "hidden") is True
    assert db.update_article_status(aid, "unread") is True  # 管理视图「恢复」

    assert db.revive_suppressed_pool_rows_by_url("https://example.com/1") == 1
    row = db.conn.execute(
        "SELECT pool_status FROM content_cache WHERE bvid = 'BV1P'"
    ).fetchone()
    assert row["pool_status"] == "fresh"


def test_daily_reading_summary_counts_today_finished() -> None:
    """每日简报「今日阅读回顾」：当天 finished 计数、来源分布、主题标签。"""
    import datetime

    db, _ = _make_db()
    aid = _first_article_id(db)
    assert db.update_article_status(aid, "finished") is True
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    summary = db.get_daily_reading_summary(day=today)
    assert summary["finished_today"] == 1
    assert summary["by_source"] == {"rss": 1}
    assert "科技" in summary["top_topics"]
    assert "AI" in summary["top_topics"]


def test_daily_reading_summary_excludes_other_days_and_statuses() -> None:
    """非当天 finished 与 unread/hidden 不计入今日回顾。"""
    import datetime

    db, _ = _make_db()
    db.upsert_article(
        "zhihu", "知乎", "昨天的文章", "https://example.com/2",
        author="作者B", tags=["历史"],
    )
    row2 = db.conn.execute(
        "SELECT id FROM articles WHERE url = 'https://example.com/2'"
    ).fetchone()
    aid2 = int(row2["id"])

    assert db.update_article_status(aid2, "finished") is True
    # 第二篇拨回昨天：只统计今天
    yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime(
        "%Y-%m-%d 00:00:00"
    )
    db.conn.execute("UPDATE articles SET updated_at = ? WHERE id = ?", (yesterday, aid2))
    db.conn.commit()
    # 第一篇保持 unread：不计数
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    summary = db.get_daily_reading_summary(day=today)
    assert summary["finished_today"] == 0
    assert summary["by_source"] == {}
    assert summary["top_topics"] == []

    # 回到今天后正常计数
    assert db.update_article_status(aid2, "finished") is True  # 再触发一次更新时间
    summary = db.get_daily_reading_summary(day=today)
    assert summary["finished_today"] == 1
    assert summary["by_source"] == {"zhihu": 1}


def test_daily_brief_endpoint_roundtrip(tmp_path) -> None:
    """端到端：/api/reading/daily-brief 三板块齐活且口径与存储一致。"""
    from fastapi.testclient import TestClient

    from openbiliclaw.api.app import create_app

    db = Database(tmp_path / "api.db")
    db.initialize()
    db.upsert_article(
        "zhihu", "知乎", "今天的文章", "https://example.com/brief",
        author="作者C", tags=["科技", "历史"],
        content_text="正文。" * 20,
    )
    aid = int(db.conn.execute("SELECT id FROM articles").fetchone()["id"])
    assert db.update_article_status(aid, "finished") is True
    db.upsert_article(
        "rss", "观察站", "未读好文", "https://example.com/unread",
        tags=["科技"], content_text="另一篇正文，提到科技。" * 20,
    )

    import datetime
    import tempfile
    from pathlib import Path as _Path

    project_root = _Path(tempfile.mkdtemp()) / "rt"
    import os

    os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(project_root)
    try:
        from openbiliclaw.config import Config, save_config

        save_config(Config(), project_root / "config.toml")
        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)
        resp = client.get("/api/reading/daily-brief")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["date"] == datetime.datetime.now().strftime("%Y-%m-%d")
        # 今日阅读回顾
        assert data["reading"]["finished_today"] == 1
        assert data["reading"]["by_source"] == {"zhihu": 1}
        assert "历史" in data["reading"]["top_topics"]
        # 画像板块：object() 没有 load_cognition_updates，降级为空不报错
        assert data["profile"]["updates"] == []
        # 明日值得看：未读好文按契合度进入榜单
        titles = [it["title"] for it in data["tomorrow"]]
        assert "未读好文" in titles
        picked = next(it for it in data["tomorrow"] if it["title"] == "未读好文")
        assert picked["fit_score"] > 0
    finally:
        os.environ.pop("OPENBILICLAW_PROJECT_ROOT", None)


def test_rule_parse_reading_intent_helpers() -> None:
    """意图解析纯函数：来源/状态/排除剥离 + 排除过滤。"""
    from openbiliclaw.api.app import _apply_reading_exclusions, _rule_parse_reading_intent

    intent = _rule_parse_reading_intent("知乎 北京古建筑 不要营销号")
    assert intent["source_type"] == "zhihu"
    assert intent["exclude"] == ["营销号"]
    assert "北京古建筑" in intent["keywords"]
    assert "营销号" not in intent["keywords"]

    kept = _apply_reading_exclusions(
        [
            {"id": 1, "title": "某营销号水文", "summary": "", "tags": "[]", "author": "x"},
            {"id": 2, "title": "胡同改造纪实", "summary": "", "tags": "[]", "author": "y"},
        ],
        ["营销号"],
    )
    assert [it["id"] for it in kept] == [2]


def test_intent_search_endpoint_rule_fallback(tmp_path) -> None:
    """无 LLM 时端点走规则回退：命中排除后不含被排词，附 intent 回显。"""
    import os
    import tempfile
    from pathlib import Path as _Path

    from fastapi.testclient import TestClient

    from openbiliclaw.api.app import create_app
    from openbiliclaw.config import Config, save_config

    db = Database(tmp_path / "intent.db")
    db.initialize()
    db.upsert_article("zhihu", "知乎", "北京胡同改造纪实", "https://e/z1", tags=["城市更新"])
    db.upsert_article("zhihu", "知乎", "营销号水文", "https://e/z2", tags=["营销号"])
    db.upsert_article("rss", "观察站", "无关文章", "https://e/r1", tags=["科技"])

    project_root = _Path(tempfile.mkdtemp()) / "rt"
    os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(project_root)
    try:
        save_config(Config(), project_root / "config.toml")
        # soul_engine=object() 无 llm_ask → 强制规则回退
        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)
        resp = client.get("/api/reading/intent-search", params={"q": "知乎 胡同 不要营销号"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["intent"]["llm_used"] is False
        assert data["intent"]["source_type"] == "zhihu"
        titles = [it["title"] for it in data["items"]]
        assert "北京胡同改造纪实" in titles
        assert "营销号水文" not in titles
        assert all("营销号" not in t for t in titles)
    finally:
        os.environ.pop("OPENBILICLAW_PROJECT_ROOT", None)

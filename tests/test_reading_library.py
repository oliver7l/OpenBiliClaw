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

"""Tests for the topic (专题) storage layer.

Covers: table creation, CRUD, idempotent item insertion, item listing and
the collected-at stamp refresh. Uses a temp database so the real
openbiliclaw.db is never touched.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from openbiliclaw.storage.database import Database


@pytest.fixture()
def db() -> Database:
    with tempfile.TemporaryDirectory() as td:
        database = Database(os.path.join(td, "topics_test.db"))
        database.initialize()
        yield database


def test_create_and_list_topics(db: Database) -> None:
    db.create_topic(
        name="广告",
        slug="advertising",
        description="计算广告学习库",
        keywords=["计算广告", "广告投放"],
        platforms=["bilibili", "rss"],
    )
    db.create_topic(
        name="去有风的地方",
        slug="youfeng",
        keywords=["去有风的地方"],
        platforms=["bilibili"],
    )
    topics = db.list_topics()
    assert len(topics) == 2
    by_slug = {t["slug"]: t for t in topics}
    assert by_slug["advertising"]["name"] == "广告"
    assert by_slug["advertising"]["item_count"] == 0
    assert "计算广告" in by_slug["advertising"]["keywords"]


def test_duplicate_slug_raises(db: Database) -> None:
    db.create_topic(name="A", slug="same")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_topic(name="B", slug="same")


def test_get_topic_by_slug_and_id(db: Database) -> None:
    tid = db.create_topic(name="广告", slug="advertising")
    assert db.get_topic_by_slug("advertising") is not None
    assert db.get_topic_by_slug("nope") is None
    assert db.get_topic_by_id(tid) is not None
    assert db.get_topic_by_id(9999) is None


def test_add_topic_item_is_idempotent(db: Database) -> None:
    tid = db.create_topic(name="广告", slug="advertising")
    item = {
        "content_key": "bilibili:BV1",
        "title": "计算广告入门",
        "url": "https://www.bilibili.com/video/BV1",
        "source_platform": "bilibili",
        "source_name": "阿宁",
    }
    assert db.add_topic_item(tid, item) is True
    # Same content_key again -> duplicate, not inserted.
    assert db.add_topic_item(tid, item) is False
    # Different key for same topic -> inserted.
    item2 = dict(item, content_key="bilibili:BV2", title="第二个")
    assert db.add_topic_item(tid, item2) is True
    assert db.count_topic_items(tid) == 2


def test_add_topic_item_requires_key_and_title(db: Database) -> None:
    tid = db.create_topic(name="广告", slug="advertising")
    assert db.add_topic_item(tid, {"content_key": "", "title": "无 key"}) is False
    assert db.add_topic_item(tid, {"content_key": "k", "title": ""}) is False
    assert db.count_topic_items(tid) == 0


def test_get_topic_items_newest_first(db: Database) -> None:
    tid = db.create_topic(name="广告", slug="advertising")
    for idx in range(5):
        db.add_topic_item(
            tid,
            {
                "content_key": f"bilibili:BV{idx}",
                "title": f"标题 {idx}",
                "source_platform": "bilibili",
            },
        )
    items = db.get_topic_items(tid, limit=3)
    assert len(items) == 3
    # Newest first (highest id).
    assert items[0]["content_key"] == "bilibili:BV4"
    assert items[2]["content_key"] == "bilibili:BV2"


def test_mark_topic_collected_refreshes_count(db: Database) -> None:
    tid = db.create_topic(name="广告", slug="advertising")
    db.add_topic_item(tid, {"content_key": "k1", "title": "一"})
    db.add_topic_item(tid, {"content_key": "k2", "title": "二"})
    assert db.get_topic_by_id(tid)["item_count"] == 0
    db.mark_topic_collected(tid)
    topic = db.get_topic_by_id(tid)
    assert topic is not None
    assert topic["item_count"] == 2
    assert topic["last_collected_at"] is not None

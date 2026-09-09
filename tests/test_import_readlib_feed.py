"""已读库导入脚本喂画像逻辑的定向测试。

只覆盖 scripts/import_readlib_to_db.feed_profile_event 的新增行为：
- article_finished 事件入库且分类为 (positive, explicit_engagement)
- 同 URL 幂等（重复喂不产生第二条）
- 事件措辞与 API finished 分支共用 format_event_context
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))

import import_readlib_to_db as importlib_script  # noqa: E402

from openbiliclaw.sources.event_format import (  # noqa: E402
    classify_event_satisfaction,
)


def _make_db(tmp_path: Path) -> sqlite3.Connection:
    """建 main(read_archive) + 子库 events(events) 两张库，与库内 schema 对齐。

    events 已拆分到 events.db，经 ATTACH 以 events. 前缀访问，与
    import_readlib_to_db 的生产写入路径一致。
    """
    conn = sqlite3.connect(tmp_path / "feed.db")
    conn.executescript(
        """
        CREATE TABLE read_archive (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT,
            source_name TEXT,
            title TEXT,
            url TEXT UNIQUE,
            author TEXT,
            summary TEXT,
            content_text TEXT,
            published_at TEXT,
            tags TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        """
    )
    events_conn = sqlite3.connect(tmp_path / "feed.events.db")
    events_conn.executescript(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            url TEXT,
            title TEXT,
            context TEXT,
            metadata TEXT,
            inferred_satisfaction TEXT,
            satisfaction_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    events_conn.commit()
    events_conn.close()
    conn.execute(
        "ATTACH DATABASE ? AS events", (str(tmp_path / "feed.events.db"),)
    )
    return conn


def _feed(
    conn: sqlite3.Connection, *, article_id: int = 1, url: str = "local://readlib/示例"
) -> bool:
    cur = conn.cursor()
    return importlib_script.feed_profile_event(
        cur,
        article_id=article_id,
        url=url,
        title="讲透历史叙事",
        platform="知乎",
        author="历史实验室",
        tags=["已读库", "知乎", "历史"],
    )


def test_feed_inserts_positive_finished_event(tmp_path) -> None:
    conn = _make_db(tmp_path)
    assert _feed(conn) is True
    row = conn.execute(
        "SELECT event_type, url, title, context, metadata, "
        "inferred_satisfaction, satisfaction_reason FROM events.events"
    ).fetchone()
    assert row[0] == "article_finished"
    assert row[1] == "local://readlib/示例"
    assert row[2] == "讲透历史叙事"
    # 措辞与 API finished 分支共用 format_event_context
    assert "读完了" in row[3] and "《讲透历史叙事》" in row[3]
    meta = json.loads(row[4])
    assert meta["source_type"] == "read-archive"
    assert meta["article_id"] == 1
    assert meta["signal_strength"] == 0.8
    # 单一分类口径：存储侧分类器对同一事件给出相同结论
    assert (row[5], row[6]) == ("positive", "explicit_engagement")
    assert classify_event_satisfaction(
        {"event_type": row[0], "url": row[1], "title": row[2], "metadata": meta}
    ) == (row[5], row[6])


def test_feed_is_idempotent_per_url(tmp_path) -> None:
    conn = _make_db(tmp_path)
    assert _feed(conn) is True
    assert _feed(conn) is False
    assert conn.execute("SELECT COUNT(*) FROM events.events").fetchone()[0] == 1


def test_feed_different_urls_not_deduped(tmp_path) -> None:
    conn = _make_db(tmp_path)
    assert _feed(conn, url="local://readlib/a") is True
    assert _feed(conn, article_id=2, url="local://readlib/b") is True
    assert conn.execute("SELECT COUNT(*) FROM events.events").fetchone()[0] == 2

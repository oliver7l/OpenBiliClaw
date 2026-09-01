"""Core data-path behaviours: pool cooldown, library shuffle, cognition gating.

Pins the 2026-09-01 rework:

- Recommendation pool: a *shown* or *feedbacked* (non-dislike) row becomes
  servable again after a 24h cooldown; a ``dislike`` row is excluded for
  ever. Fresh rows are always servable.
- Reading library shuffle: ``random_order=True`` scans rowids and samples
  in Python instead of ``ORDER BY RANDOM()`` (which materializes the whole
  table); results must be unique, correct-sized and respect filters.
- Cognition backlog: ``_signal_weighted_selection`` keeps every high-signal
  event and fills the remaining cap slots with the newest low-signal ones.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from openbiliclaw.storage import database as db_mod

# The servable-status predicate introduced with the 24h cooldown rework.
SERVABLE_SQL = f"""
    SELECT bvid FROM content_cache
    WHERE {db_mod._POOL_SERVABLE_STATUS_SQL}
      AND COALESCE(feedback_type, '') != 'dislike'
"""

NOT_RECENT_SQL = f"""
    SELECT bvid FROM content_cache
    WHERE 1=1 {db_mod._POOL_NOT_RECENTLY_RECOMMENDED_SQL}
"""


@pytest.fixture
def tmp_db(tmp_path) -> db_mod.Database:
    db = db_mod.Database(tmp_path / "test.db")
    db.initialize()
    return db


def _insert_pool_row(
    db: db_mod.Database,
    *,
    bvid: str,
    pool_status: str | None = "fresh",
    feedback_type: str | None = None,
    recommended_at: str | None = None,
    feedback_at: str | None = None,
) -> None:
    db.conn.execute(
        """INSERT INTO content_cache (bvid, title, pool_status, feedback_type,
                                      recommended_at, feedback_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (bvid, f"title-{bvid}", pool_status, feedback_type, recommended_at, feedback_at),
    )
    db.conn.commit()


def _servable_bvids(db: db_mod.Database) -> set[str]:
    rows = db.conn.execute(SERVABLE_SQL).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Pool cooldown / dislike exclusion
# ---------------------------------------------------------------------------


def test_fresh_rows_are_always_servable(tmp_db: db_mod.Database) -> None:
    _insert_pool_row(tmp_db, bvid="fresh-1")
    _insert_pool_row(tmp_db, bvid="fresh-2")
    assert _servable_bvids(tmp_db) == {"fresh-1", "fresh-2"}


def test_shown_row_returns_after_24h_cooldown(tmp_db: db_mod.Database) -> None:
    # Shown recently (within 24h) → NOT servable.
    _insert_pool_row(tmp_db, bvid="recent", pool_status="shown", recommended_at="2026-09-01 00:00:00")
    # Shown long ago (beyond 24h) → servable again.
    _insert_pool_row(tmp_db, bvid="old", pool_status="shown", recommended_at="2026-08-01 00:00:00")

    servable = _servable_bvids(tmp_db)
    assert "recent" not in servable
    assert "old" in servable


def test_feedbacked_non_dislike_row_returns_after_cooldown(
    tmp_db: db_mod.Database,
) -> None:
    _insert_pool_row(
        tmp_db, bvid="old-ignored", pool_status="feedbacked",
        feedback_type="ignore", feedback_at="2026-08-01 00:00:00",
    )
    _insert_pool_row(
        tmp_db, bvid="recent-ignored", pool_status="feedbacked",
        feedback_type="ignore", feedback_at="2026-09-01 00:00:00",
    )
    servable = _servable_bvids(tmp_db)
    assert "old-ignored" in servable
    assert "recent-ignored" not in servable


def test_dislike_is_permanently_excluded(tmp_db: db_mod.Database) -> None:
    # Even a very old dislike must never return.
    _insert_pool_row(
        tmp_db, bvid="old-dislike", pool_status="feedbacked",
        feedback_type="dislike", feedback_at="2026-01-01 00:00:00",
    )
    assert "old-dislike" not in _servable_bvids(tmp_db)


def test_recommended_within_24h_is_excluded_from_candidates(
    tmp_db: db_mod.Database,
) -> None:
    """The NOT-RECENTLY-RECOMMENDED guard must exclude rows in recommendations."""
    _insert_pool_row(tmp_db, bvid="rec-now", recommended_at="2026-09-01 00:00:00")
    _insert_pool_row(tmp_db, bvid="rec-old", recommended_at="2026-08-01 00:00:00")
    # Seed the recommendations history table (24h dedup ledger).
    tmp_db.conn.execute(
        "CREATE TABLE IF NOT EXISTS recommendations ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " bvid TEXT, created_at TEXT)"
    )
    tmp_db.conn.execute(
        "INSERT INTO recommendations (bvid, created_at) VALUES (?, ?)",
        ("rec-now", "2026-09-01 00:00:00"),
    )
    tmp_db.conn.execute(
        "INSERT INTO recommendations (bvid, created_at) VALUES (?, ?)",
        ("rec-old", "2026-08-01 00:00:00"),
    )
    tmp_db.conn.commit()

    allowed = {r[0] for r in tmp_db.conn.execute(NOT_RECENT_SQL).fetchall()}
    assert "rec-now" not in allowed
    assert "rec-old" in allowed


# ---------------------------------------------------------------------------
# Reading-library shuffle
# ---------------------------------------------------------------------------


def _insert_article(
    db: db_mod.Database,
    *,
    title: str,
    url: str,
    source_type: str = "web",
    published_at: str = "2026-09-01 00:00:00",
) -> None:
    db.upsert_article(
        source_type=source_type,
        source_name="test-source",
        title=title,
        url=url,
        published_at=published_at,
    )


def test_random_shuffle_returns_unique_rows_within_limit(
    tmp_db: db_mod.Database,
) -> None:
    for i in range(20):
        _insert_article(tmp_db, title=f"article-{i}", url=f"https://x.test/{i}")
    rows = tmp_db.get_recent_articles(limit=10, random_order=True)
    assert len(rows) == 10
    urls = {r["url"] for r in rows}
    assert len(urls) == 10  # no duplicates


def test_random_shuffle_respects_source_filter(tmp_db: db_mod.Database) -> None:
    for i in range(5):
        _insert_article(tmp_db, title=f"bili-{i}", url=f"https://bili.test/{i}", source_type="bilibili")
    for i in range(5):
        _insert_article(tmp_db, title=f"web-{i}", url=f"https://web.test/{i}", source_type="web")
    rows = tmp_db.get_recent_articles(limit=10, source_type="bilibili", random_order=True)
    assert rows
    assert all(r["source_type"] == "bilibili" for r in rows)


def test_random_shuffle_empty_library_returns_empty(tmp_db: db_mod.Database) -> None:
    assert tmp_db.get_recent_articles(limit=10, random_order=True) == []


# ---------------------------------------------------------------------------
# Cognition backlog: high-signal priority
# ---------------------------------------------------------------------------

from openbiliclaw.soul.cognition_cycle import (  # noqa: E402
    CognitionCycle,
    _AWARENESS_BACKLOG_CAP,
    _AWARENESS_LOW_SIGNAL_TYPES,
)

_LOW = list(_AWARENESS_LOW_SIGNAL_TYPES)[0]
_HIGH = "favorite"


def _ev(ev_id: int, event_type: str) -> dict[str, Any]:
    return {"id": ev_id, "event_type": event_type}


def test_high_signal_events_all_kept_within_cap() -> None:
    rows = [_ev(i, _HIGH) for i in range(50)] + [_ev(i + 50, _LOW) for i in range(50)]
    selected = CognitionCycle._signal_weighted_selection(rows)
    high_selected = [r for r in selected if r["event_type"] == _HIGH]
    assert len(high_selected) == 50  # every high-signal event survives
    assert len(selected) <= _AWARENESS_BACKLOG_CAP


def test_low_signal_events_fill_remaining_slots_newest_first() -> None:
    cap = _AWARENESS_BACKLOG_CAP
    rows = [_ev(i, _HIGH) for i in range(5)] + [
        _ev(100 + i, _LOW) for i in range(cap)  # way more low-signal than room
    ]
    selected = CognitionCycle._signal_weighted_selection(rows)
    assert len(selected) == cap
    low_selected = [r for r in selected if r["event_type"] == _LOW]
    # All 5 high survive; the rest are the newest low-signal events (highest ids).
    assert len(low_selected) == cap - 5
    assert low_selected[0]["id"] > low_selected[-1]["id"]  # newest-first


def test_signal_selection_no_duplicates() -> None:
    # Distinct event rows must each survive; identical ids must not duplicate.
    rows = [_ev(1, _HIGH), _ev(2, _LOW), _ev(3, _HIGH)]
    selected = CognitionCycle._signal_weighted_selection(rows)
    ids = [r["id"] for r in selected]
    assert len(ids) == len(set(ids)) == 3

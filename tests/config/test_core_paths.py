"""Core data-path behaviours: pool cooldown, library shuffle, cognition gating.

Pins the v0.3.153 rework (dd09b3d0):

- Recommendation pool: the re-show cooldown was shrunk from 24h to a
  1-second window, so shown / feedbacked (non-dislike) rows recycle almost
  immediately and the recommendations-history dedup guard only blocks
  future-dated rows. A ``dislike`` row is still excluded for ever. Fresh
  rows are always servable.
- Reading library shuffle: ``random_order=True`` scans rowids and samples
  in Python instead of ``ORDER BY RANDOM()`` (which materializes the whole
  table); results must be unique, correct-sized and respect filters.
- Cognition backlog: ``_signal_weighted_selection`` keeps every high-signal
  event and fills the remaining cap slots with the newest low-signal ones.

Timestamps are computed from the clock (never hardcoded) so these tests
cannot rot as the calendar advances.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from openbiliclaw.storage import database as db_mod


def _ts(offset: timedelta = timedelta()) -> str:
    """SQLite-comparable UTC timestamp, ``offset`` from now."""
    return (datetime.now(UTC) + offset).strftime("%Y-%m-%d %H:%M:%S")


# The servable-status predicate (v0.3.153+: 1-second re-show window).
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


def test_shown_row_recycles_within_short_cooldown(tmp_db: db_mod.Database) -> None:
    """v0.3.153+: the 24h cooldown became a 1-second window (dd09b3d0).

    Shown rows — whether exposed a minute ago or three days ago — re-enter
    the rotation immediately; only a *future-dated* exposure (beyond the
    1-second boundary) stays unservable.
    """
    _insert_pool_row(tmp_db, bvid="shown-now", pool_status="shown", recommended_at=_ts())
    _insert_pool_row(
        tmp_db, bvid="shown-recent", pool_status="shown", recommended_at=_ts(timedelta(hours=-1))
    )
    _insert_pool_row(
        tmp_db, bvid="shown-old", pool_status="shown", recommended_at=_ts(timedelta(days=-3))
    )
    _insert_pool_row(
        tmp_db,
        bvid="shown-future",
        pool_status="shown",
        recommended_at=_ts(timedelta(seconds=60)),
    )

    servable = _servable_bvids(tmp_db)
    assert "shown-now" in servable
    assert "shown-recent" in servable
    assert "shown-old" in servable
    assert "shown-future" not in servable


def test_feedbacked_non_dislike_row_recycles_within_short_cooldown(
    tmp_db: db_mod.Database,
) -> None:
    """Non-dislike feedback rows also recycle immediately (1s window)."""
    _insert_pool_row(
        tmp_db,
        bvid="fb-now",
        pool_status="feedbacked",
        feedback_type="ignore",
        feedback_at=_ts(),
    )
    _insert_pool_row(
        tmp_db,
        bvid="fb-old",
        pool_status="feedbacked",
        feedback_type="ignore",
        feedback_at=_ts(timedelta(days=-3)),
    )
    servable = _servable_bvids(tmp_db)
    assert "fb-now" in servable
    assert "fb-old" in servable


def test_dislike_is_permanently_excluded(tmp_db: db_mod.Database) -> None:
    # Even a very old dislike must never return.
    _insert_pool_row(
        tmp_db,
        bvid="old-dislike",
        pool_status="feedbacked",
        feedback_type="dislike",
        feedback_at=_ts(timedelta(days=-365)),
    )
    assert "old-dislike" not in _servable_bvids(tmp_db)


def test_recommendation_history_guard_only_blocks_future_rows(
    tmp_db: db_mod.Database,
) -> None:
    """v0.3.153+: the NOT-RECENTLY-RECOMMENDED guard uses the 1s window too.

    A recommendation created in the past (no matter how recent) no longer
    excludes the item — that is the dd09b3d0 behaviour change that lets
    six-platform feed content flow into recommendations without a 24h
    lockout. Only rows dated beyond the 1-second boundary (i.e. the future)
    are blocked, which pins that the guard still exists and points the
    right way.
    """
    _insert_pool_row(tmp_db, bvid="rec-now", recommended_at=_ts())
    _insert_pool_row(tmp_db, bvid="rec-old", recommended_at=_ts(timedelta(days=-30)))
    _insert_pool_row(tmp_db, bvid="rec-future", recommended_at=_ts(timedelta(seconds=60)))
    # Seed the recommendations history table (the dedup ledger).
    tmp_db.conn.execute(
        "CREATE TABLE IF NOT EXISTS recommendations ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " bvid TEXT, created_at TEXT)"
    )
    for bvid in ("rec-now", "rec-old", "rec-future"):
        if bvid == "rec-old":
            created = _ts(timedelta(days=-30))
        elif bvid == "rec-future":
            created = _ts(timedelta(seconds=60))
        else:
            created = _ts()
        tmp_db.conn.execute(
            "INSERT INTO recommendations (bvid, created_at) VALUES (?, ?)",
            (bvid, created),
        )
    tmp_db.conn.commit()

    allowed = {r[0] for r in tmp_db.conn.execute(NOT_RECENT_SQL).fetchall()}
    assert "rec-now" in allowed
    assert "rec-old" in allowed
    assert "rec-future" not in allowed


# ---------------------------------------------------------------------------
# Reading-library shuffle
# ---------------------------------------------------------------------------


def _insert_article(
    db: db_mod.Database,
    *,
    title: str,
    url: str,
    source_type: str = "web",
    published_at: str | None = None,
) -> None:
    db.upsert_article(
        source_type=source_type,
        source_name="test-source",
        title=title,
        url=url,
        published_at=published_at or _ts(),
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
        _insert_article(
            tmp_db, title=f"bili-{i}", url=f"https://bili.test/{i}", source_type="bilibili"
        )
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
    _AWARENESS_BACKLOG_CAP,
    _AWARENESS_LOW_SIGNAL_TYPES,
    CognitionCycle,
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
        _ev(100 + i, _LOW)
        for i in range(cap)  # way more low-signal than room
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

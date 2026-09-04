"""E1 feedback-loop tests: recommendation click-through consumption state.

Covers the exposure→click loop added in E1:
- ``clicked_at`` column migration is idempotent
- ``mark_recommendations_clicked`` sets presented+clicked and survives re-runs
- ``get_recommendations(exclude_processed=True)`` keeps shown-but-not-clicked
  rows and drops clicked rows
- ``get_clicked_bvids`` returns clicked items for the serve exclusion path
"""

from __future__ import annotations

import os
import tempfile

from openbiliclaw.storage.database import Database


def _make_db(tmpdir: str) -> Database:
    db = Database(os.path.join(tmpdir, "t.db"))
    db.initialize()
    return db


def _insert(db: Database, bvid: str, *, confidence: float = 0.9) -> int:
    db.conn.execute(
        "INSERT INTO recommendations (bvid, presented, confidence) VALUES (?, 0, ?)",
        (bvid, confidence),
    )
    db.conn.commit()
    row = db.conn.execute(
        "SELECT id FROM recommendations WHERE bvid = ?", (bvid,)
    ).fetchone()
    assert row is not None
    return int(row["id"])


def test_clicked_column_migration_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(td)
        db._ensure_recommendation_clicked_column()  # noqa: SLF001
        db._ensure_recommendation_clicked_column()  # noqa: SLF001 — second run must be a no-op
        cols = {
            str(row["name"])
            for row in db.conn.execute("PRAGMA table_info(recommendations)").fetchall()
        }
        assert "clicked_at" in cols


def test_mark_clicked_sets_presented_and_clicked_at() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(td)
        rid = _insert(db, "BVclick1")
        db.mark_recommendations_clicked([rid])
        row = db.conn.execute(
            "SELECT presented, clicked_at FROM recommendations WHERE id = ?", (rid,)
        ).fetchone()
        assert row is not None
        assert int(row["presented"]) == 1
        assert row["clicked_at"] is not None
        # Re-running is idempotent (no constraint violation).
        db.mark_recommendations_clicked([rid])


def test_exclude_processed_keeps_shown_but_drops_clicked() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(td)
        shown = _insert(db, "BVshown1")
        clicked = _insert(db, "BVclicked1")
        db.mark_recommendations_presented([shown])
        db.mark_recommendations_clicked([clicked])

        rows = db.get_recommendations(limit=10, exclude_processed=True)
        bvids = {str(r["bvid"]) for r in rows}
        # presented-only rows stay actionable (exposure != consumption)…
        assert "BVshown1" in bvids
        # …but clicked rows are consumed and excluded.
        assert "BVclicked1" not in bvids


def test_get_clicked_bvids_returns_recent_consumed_items() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(td)
        _insert(db, "BVc1")
        _insert(db, "BVc2")
        _insert(db, "BVplain")
        for bvid in ("BVc1", "BVc2"):
            rid = db.conn.execute(
                "SELECT id FROM recommendations WHERE bvid = ?", (bvid,)
            ).fetchone()
            assert rid is not None
            db.mark_recommendations_clicked([int(rid["id"])])
        clicked = db.get_clicked_bvids()
        assert set(clicked) == {"BVc1", "BVc2"}
        assert "BVplain" not in clicked

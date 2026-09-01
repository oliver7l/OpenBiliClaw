"""Tests for the explore-source admission exemption in delight candidate queries.

Mirrors upstream discovery/admission: explore candidates get a lower
relevance floor (0.58) than the configured admission score (0.60 default)
so cross-circle content stays servable by the delight (surprise) channel.
"""

from __future__ import annotations

from pathlib import Path

from openbiliclaw.storage.database import Database


def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "delight.db")
    db.initialize()
    return db


def _seed(
    db: Database,
    bvid: str,
    *,
    source: str,
    relevance: float,
) -> None:
    db.cache_content(
        bvid=bvid,
        title=bvid,
        relevance_score=relevance,
        source=source,
    )
    db.update_delight_score(
        bvid=bvid,
        delight_score=0.9,
        delight_reason="值得一看",
        delight_hook="好奇心钩子",
    )


def test_explore_candidates_pass_lower_admission_floor(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db, "EXP_058", source="explore", relevance=0.58)
    _seed(db, "EXP_059", source="explore", relevance=0.59)
    _seed(db, "EXP_055", source="explore", relevance=0.55)
    _seed(db, "SRC_059", source="search", relevance=0.59)

    candidates = {row["bvid"] for row in db.get_delight_candidates()}

    # 0.58/0.59 clear the explore floor (0.58); 0.55 stays below it.
    assert {"EXP_058", "EXP_059"} <= candidates
    assert "EXP_055" not in candidates
    # Non-explore sources keep the configured 0.60 floor.
    assert "SRC_059" not in candidates


def test_count_delight_candidates_matches_get_floor(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db, "EXP_058", source="explore", relevance=0.58)
    _seed(db, "SRC_060", source="search", relevance=0.60)

    assert db.count_delight_candidates() == 2
    assert {row["bvid"] for row in db.get_delight_candidates()} == {
        "EXP_058",
        "SRC_060",
    }

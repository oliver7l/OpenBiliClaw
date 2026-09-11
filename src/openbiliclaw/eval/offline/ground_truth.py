"""Ground-truth construction from real user behavior.

Two sides:

- **Positive samples**: strong implicit signals from ``events``
  (favorite / like / article_finished / view) mapped to canonical content keys.
- **Negative pool**: candidate-pool items the user has *not* consumed in the
  evaluation window (the "not consumed" assumption — selection bias is
  disclosed in the report, not silently ignored).

Time slicing: when ``eval_after`` is set, only behavior strictly after that
timestamp counts as positive evidence; this is the M2 leakage guard.
"""

from __future__ import annotations

import sqlite3
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from .content_key import content_key_from_url, to_content_key

# event_type -> (signal weight, is_strong)
_POSITIVE_EVENT_WEIGHTS: dict[str, float] = {
    "favorite": 1.0,
    "like": 1.0,
    "article_finished": 0.8,
    "view": 0.5,
}


@dataclass
class PositiveSample:
    content_key: str
    weight: float
    event_type: str
    ts: str


@dataclass
class CandidateItem:
    """A row from discovery_candidates with everything ranking needs."""

    content_key: str
    bvid: str
    title: str
    topic_group: str
    style_key: str
    relevance_score: float
    candidate_tier: str
    source_platform: str
    content_url: str
    discovered_at: str


def load_positive_samples(
    db_path: str,
    *,
    event_types: tuple[str, ...] = ("favorite", "like", "article_finished", "view"),
    eval_after: str | None = None,
    min_weight: float = 0.5,
) -> dict[str, PositiveSample]:
    """Load positive behavior samples keyed by canonical content key.

    Later timestamps win for the same key (recency). ``eval_after`` applies
    the time-slice guard: only behavior after this instant counts.
    """
    con = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA synchronous=NORMAL")
    # ATTACH 子库，使 events.events / discovery_candidates 查询正常工作
    _db_dir = Path(db_path).parent
    for _alias, _name in [("events", "events.db"), ("discovery", "discovery.db")]:
        with suppress(Exception):
            con.execute(f"ATTACH DATABASE ? AS {_alias}", (str(_db_dir / _name),))
    samples: dict[str, PositiveSample] = {}
    try:
        for ev in event_types:
            if ev not in _POSITIVE_EVENT_WEIGHTS:
                continue
            q = "SELECT url, event_type, created_at FROM events.events WHERE event_type = ?"
            params: list[object] = [ev]
            if eval_after:
                q += " AND created_at > ?"
                params.append(eval_after)
            for row in con.execute(q, params):
                key = content_key_from_url(row["url"])
                if not key:
                    continue
                weight = _POSITIVE_EVENT_WEIGHTS[ev]
                if weight < min_weight:
                    continue
                ts = row["created_at"] or ""
                prev = samples.get(key)
                if prev is None or ts > prev.ts:
                    samples[key] = PositiveSample(
                        content_key=key,
                        weight=weight,
                        event_type=row["event_type"],
                        ts=ts,
                    )
    finally:
        con.close()
    return samples


def load_candidate_pool(
    db_path: str,
    *,
    source_platforms: tuple[str, ...] = ("bilibili",),
) -> list[CandidateItem]:
    """Load the full servable candidate pool as CandidateItems."""
    con = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA synchronous=NORMAL")
    # ATTACH 子库，使 discovery_candidates 查询正常工作
    _db_dir = Path(db_path).parent
    for _alias, _name in [("events", "events.db"), ("discovery", "discovery.db")]:
        with suppress(Exception):
            con.execute(f"ATTACH DATABASE ? AS {_alias}", (str(_db_dir / _name),))
    items: list[CandidateItem] = []
    try:
        q = """
            SELECT candidate_key, bvid, title, topic_group, style_key,
                   relevance_score, candidate_tier, source_platform,
                   content_url, COALESCE(cached_at, last_seen_at, created_at) AS dt
            FROM discovery_candidates
        """
        if source_platforms:
            marks = ",".join("?" for _ in source_platforms)
            q += f" WHERE source_platform IN ({marks})"
        for row in con.execute(q, source_platforms):
            key = row["candidate_key"] or to_content_key(row["source_platform"], row["bvid"])
            if not key:
                continue
            items.append(
                CandidateItem(
                    content_key=key,
                    bvid=row["bvid"] or "",
                    title=row["title"] or "",
                    topic_group=row["topic_group"] or "",
                    style_key=row["style_key"] or "",
                    relevance_score=float(row["relevance_score"] or 0.0),
                    candidate_tier=row["candidate_tier"] or "primary",
                    source_platform=row["source_platform"] or "",
                    content_url=row["content_url"] or "",
                    discovered_at=row["dt"] or "",
                )
            )
    finally:
        con.close()
    return items


def build_negative_pool(
    candidate_pool: list[CandidateItem],
    positive_keys: set[str],
) -> list[CandidateItem]:
    """Candidate items the user has NOT positively consumed (negatives)."""
    return [c for c in candidate_pool if c.content_key not in positive_keys]


def positive_pool_from_candidates(
    candidate_pool: list[CandidateItem],
    positive_samples: dict[str, PositiveSample],
) -> list[tuple[CandidateItem, PositiveSample]]:
    """Candidate items that also appear in the positive sample set.

    Only these can form the positive side of an evaluation unit (we can only
    evaluate ranking quality over items the engine actually has in its pool).
    """
    matched: list[tuple[CandidateItem, PositiveSample]] = []
    for c in candidate_pool:
        s = positive_samples.get(c.content_key)
        if s is not None:
            matched.append((c, s))
    return matched

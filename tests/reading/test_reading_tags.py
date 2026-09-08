"""Tests for deterministic reading-library auto-tagging, similarity and stats.

Pure helpers (``reading.tags``) plus the ``Database`` surfaces the feature
relies on: the weekly/timeline reading stats and the sparse-article scan used
by the auto-tag backfill.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.reading.tags import (
    extract_hashtags,
    generate_tags,
    jaccard,
    merge_tag_lists,
    similarity,
)
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


def test_extract_hashtags_cjk_and_latin_deduped() -> None:
    tags = extract_hashtags("#机器学习 与 #AI 及 #机器学习 再次 #深度-learning")
    assert "机器学习" in tags
    assert "AI" in tags
    assert tags.count("机器学习") == 1  # deduped, order-preserving
    assert not any(t.startswith("#") for t in tags)


def test_merge_tag_lists_order_dedup_cap() -> None:
    merged = merge_tag_lists(["知乎", "AI"], ["ai", "机器", "知乎", "财经"], cap=3)
    assert merged[:2] == ["知乎", "AI"]  # existing first, source preserved
    assert "ai" not in merged  # case-insensitive dedup against existing "AI"
    assert len(merged) == 3


def test_generate_tags_title_hit_outranks_body_hit() -> None:
    kw = [("财经", 0.9), ("机器学习", 0.5)]
    out = generate_tags(
        title="机器学习实战",
        summary="顺便提到财经",
        content_text="",
        interest_keywords=kw,
        existing=[],
    )
    assert out[0] == "机器学习"  # title hit (tier 2) beats high-weight body hit (tier 1)


def test_generate_tags_filters_by_weight_and_min_length() -> None:
    kw = [("量子计算", 0.05), ("猫", 0.9), ("AI", 0.9), ("机器学习", 0.4)]
    out = generate_tags(
        title="AI 与机器学习与猫",
        content_text="量子计算内容",
        interest_keywords=kw,
        existing=[],
        min_weight=0.15,
    )
    assert "量子计算" not in out  # below min_weight
    assert "猫" not in out  # single char, below min keyword length
    assert "AI" in out and "机器学习" in out  # matched, pass both gates


def test_generate_tags_excludes_existing_and_empty_profile() -> None:
    kw = [("机器学习", 0.9)]
    assert generate_tags(title="机器学习", interest_keywords=kw, existing=["机器学习"]) == []
    assert generate_tags(title="机器学习", interest_keywords=[], existing=[]) == []


def test_generate_tags_appends_hashtags_after_keywords() -> None:
    out = generate_tags(
        title="无关键词",
        content_text="见 #热点话题",
        interest_keywords=[("其它", 0.9)],
        existing=[],
    )
    assert "热点话题" in out


def test_jaccard_basic() -> None:
    assert jaccard(set(), set()) == 0.0
    assert jaccard({"a"}, {"a"}) == 1.0
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)


def test_similarity_identical_is_one_and_disjoint_zero() -> None:
    assert similarity("同一标题", ["a", "b"], "同一标题", ["a", "b"]) == pytest.approx(1.0)
    assert similarity("标题甲", ["x"], "完全不同乙", ["y"]) == 0.0


def test_similarity_weights_tags_over_title() -> None:
    same_tags = similarity("标题完全不同啊", ["ml", "ai"], "另一个无关标题", ["ml", "ai"])
    same_title = similarity("机器学习入门", [], "机器学习进阶", ["财经"])
    assert same_tags > same_title  # tag overlap (0.7) dominates title overlap (0.3)


# ---------------------------------------------------------------------------
# Database: weekly + timeline stats, sparse scan for tagging
# ---------------------------------------------------------------------------


def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "r.db")
    db.initialize()
    return db


def _finish_on(db: Database, article_id: int, when: datetime.datetime) -> None:
    db.conn.execute(
        "UPDATE articles SET status='finished', updated_at=? WHERE id=?",
        (when.strftime("%Y-%m-%d %H:%M:%S"), article_id),
    )
    db.conn.commit()


def test_stats_include_week_and_timeline(tmp_path: Path) -> None:
    db = _db(tmp_path)
    now = datetime.datetime.now()
    for i in range(3):
        _finish_on(db, db.upsert_article("zhihu", "知乎", f"文章{i}", f"http://a/{i}"), now)
    # one finished 5 days ago → appears in timeline, may land in a different week
    _finish_on(
        db,
        db.upsert_article("zhihu", "知乎", "旧文", "http://a/old"),
        now - datetime.timedelta(days=5),
    )
    stats = db.get_article_reading_stats()
    assert "by_week" in stats and "timeline" in stats
    assert stats["by_status"].get("finished") == 4
    assert sum(stats["by_week"].values()) == 4
    assert sum(item["count"] for item in stats["timeline"]) == 4


def test_iter_articles_for_tagging_only_sparse(tmp_path: Path) -> None:
    db = _db(tmp_path)
    sparse_id = db.upsert_article(
        "zhihu", "知乎", "光标题", "http://a/sparse"
    )  # tags=[source_name] → 1
    dense_id = db.upsert_article("rss", "某RSS", "已富标签", "http://a/dense")
    db.update_article_tags(dense_id, ["A", "B", "C"])  # 3 tags → not sparse
    sparse = db.iter_articles_for_tagging(only_sparse=True)
    ids = {r["id"] for r in sparse}
    assert sparse_id in ids
    assert dense_id not in ids
    everything = db.iter_articles_for_tagging(only_sparse=False)
    assert {dense_id, sparse_id}.issubset({r["id"] for r in everything})

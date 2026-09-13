"""日记情绪回写与画像情绪基线单元测试。

背景：``EmotionAnalyzer`` 历史上只写 ``diary_emotion_analyses``、从不回写
``diary_entries.mood``，而 ``SelfEvolutionService`` 的 ``emotional_baseline``
只读后者，导致画像情绪维度恒为 ``{"unknown": N}``（本项目存量 925 篇即如此）。

这组用例锁死修复后的三条约定：
1. 分析即回写 —— 分析完 ``diary_entries.mood`` / ``mood_score`` 必须同步；
2. 历史可回填 —— ``backfill_entry_moods()`` 只补 unknown，除非显式 force；
3. 画像优先读分析表 —— 有分析就别再退化成 unknown。
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.diary import DiaryEntryCreate, MoodLevel
from openbiliclaw.diary.emotion import EmotionAnalyzer, mood_level_for_emotion
from openbiliclaw.diary.self_evolution import SelfEvolutionService
from openbiliclaw.diary.store import DiaryStore

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test_diary.db")


@pytest.fixture
def store(tmp_db_path: str) -> DiaryStore:
    s = DiaryStore(db_path=tmp_db_path)
    s.initialize()
    return s


@pytest.fixture
def analyzer(store: DiaryStore) -> EmotionAnalyzer:
    return EmotionAnalyzer(store)


def _create(store: DiaryStore, date: str, content: str, **kwargs: object):
    return store.create_entry(DiaryEntryCreate(entry_date=date, content=content, **kwargs))


# ─── 标签映射 ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("ecstatic", "very_happy"),
        ("happy", "happy"),
        ("content", "happy"),
        ("neutral", "neutral"),
        ("bored", "neutral"),
        ("anxious", "anxious"),
        ("sad", "sad"),
        ("depressed", "very_sad"),
        ("angry", "angry"),
    ],
)
def test_mood_level_for_emotion_maps_known_labels(label: str, expected: str) -> None:
    assert mood_level_for_emotion(label) == expected


def test_mood_level_for_emotion_falls_back_to_neutral() -> None:
    assert mood_level_for_emotion("不存在的标签") == "neutral"


# ─── 分析即回写 ──────────────────────────────────────────────────────


def test_analyze_diary_writes_back_mood(analyzer: EmotionAnalyzer, store: DiaryStore) -> None:
    entry = _create(store, "2026-09-01", "今天很开心，玩得很爽，特别喜欢这种感觉")
    assert entry.mood == MoodLevel.UNKNOWN

    result = analyzer.analyze_diary(entry.id)

    assert result is not None
    assert result.valence > 0
    refreshed = store.get_entry(entry.id)
    assert refreshed.mood != MoodLevel.UNKNOWN
    assert refreshed.mood_score == pytest.approx(round(result.valence, 4))


def test_analyze_diary_is_idempotent(analyzer: EmotionAnalyzer, store: DiaryStore) -> None:
    """反复分析同一篇，效价不应漂移（mood 是输出，不能反向喂回输入）。"""
    entry = _create(store, "2026-09-02", "今天很开心，玩得很爽")

    first = analyzer.analyze_diary(entry.id)
    second = analyzer.analyze_diary(entry.id)
    third = analyzer.analyze_diary(entry.id)

    assert first is not None and second is not None and third is not None
    assert second.valence == pytest.approx(first.valence)
    assert third.valence == pytest.approx(first.valence)


def test_mood_prior_fusion_is_opt_in(analyzer: EmotionAnalyzer, store: DiaryStore) -> None:
    """先验融合默认关闭；显式开启时才把人工 mood 折进效价。"""
    plain = _create(store, "2026-09-03", "今天", mood=MoodLevel.VERY_HAPPY)
    fused_entry = _create(store, "2026-09-04", "今天", mood=MoodLevel.VERY_HAPPY)

    default = analyzer.analyze_diary(plain.id)
    assert default is not None
    assert default.valence == pytest.approx(0.0)

    fused = analyzer.analyze_diary(fused_entry.id, use_mood_prior=True)
    assert fused is not None
    # 文本无情绪词 → valence 0；0 * 0.6 + prior(very_happy)=0.8 * 0.4 = 0.32
    assert fused.valence == pytest.approx(0.32)


# ─── 历史回填 ────────────────────────────────────────────────────────


def test_backfill_only_touches_unknown_by_default(
    analyzer: EmotionAnalyzer, store: DiaryStore, tmp_db_path: str
) -> None:
    a = _create(store, "2026-09-04", "今天很开心")
    b = _create(store, "2026-09-05", "今天很开心")
    analyzer.analyze_all_diaries()

    # 模拟存量：a 没回填过（unknown），b 已有人为标注
    conn = sqlite3.connect(tmp_db_path)
    conn.execute("UPDATE diary_entries SET mood = 'unknown' WHERE id = ?", (a.id,))
    conn.execute("UPDATE diary_entries SET mood = 'happy' WHERE id = ?", (b.id,))
    conn.commit()
    conn.close()

    updated = analyzer.backfill_entry_moods()

    assert updated == 1
    assert store.get_entry(a.id).mood != MoodLevel.UNKNOWN
    assert store.get_entry(b.id).mood == MoodLevel.HAPPY


def test_backfill_force_overwrites_existing(analyzer: EmotionAnalyzer, store: DiaryStore, tmp_db_path: str) -> None:
    a = _create(store, "2026-09-06", "今天很开心")
    b = _create(store, "2026-09-07", "今天很开心")
    analyzer.analyze_all_diaries()

    conn = sqlite3.connect(tmp_db_path)
    conn.execute("UPDATE diary_entries SET mood = 'angry' WHERE id IN (?, ?)", (a.id, b.id))
    conn.commit()
    conn.close()

    updated = analyzer.backfill_entry_moods(only_unknown=False)

    assert updated == 2
    assert store.get_entry(a.id).mood != MoodLevel.ANGRY
    assert store.get_entry(b.id).mood != MoodLevel.ANGRY


def test_backfill_without_any_analysis_returns_zero(analyzer: EmotionAnalyzer, store: DiaryStore) -> None:
    _create(store, "2026-09-08", "今天很开心")
    assert analyzer.backfill_entry_moods() == 0


# ─── 画像情绪基线 ────────────────────────────────────────────────────


def test_emotional_baseline_prefers_analysis_table(analyzer: EmotionAnalyzer, store: DiaryStore) -> None:
    _create(store, "2026-09-01", "今天很开心，很喜欢这种感觉")
    _create(store, "2026-09-02", "今天很开心，很快乐，很满足")
    _create(store, "2026-09-03", "今天很累，很焦虑，烦躁，难受")
    analyzer.analyze_all_diaries()

    profile = SelfEvolutionService(store).update_user_profile("2026-09-11")
    baseline = profile.emotional_baseline

    assert baseline["source"] == "diary_emotion_analyses"
    assert "unknown" not in baseline["mood_distribution"]
    assert baseline["positive_ratio"] > 0
    assert baseline["negative_ratio"] > 0
    ratios = baseline["positive_ratio"] + baseline["negative_ratio"] + baseline["neutral_ratio"]
    assert ratios == pytest.approx(1.0, abs=0.01)


def test_emotional_baseline_falls_back_to_entry_fields(store: DiaryStore) -> None:
    """没有分析结果时，仍能用条目自带的 mood 字段算出基线。"""
    _create(store, "2026-09-01", "今天很开心", mood=MoodLevel.HAPPY)
    _create(store, "2026-09-02", "今天很开心", mood=MoodLevel.HAPPY)
    _create(store, "2026-09-03", "今天很烦", mood=MoodLevel.ANGRY)

    profile = SelfEvolutionService(store).update_user_profile("2026-09-11")
    baseline = profile.emotional_baseline

    assert baseline["source"] == "diary_entries"
    assert baseline["mood_distribution"] == {"happy": 2, "angry": 1}
    assert baseline["dominant_mood"] == "happy"


def test_emotional_baseline_ignores_entries_out_of_scope(analyzer: EmotionAnalyzer, store: DiaryStore) -> None:
    """画像只统计 target_date 及之前的日记。"""
    _create(store, "2026-08-01", "今天很开心，玩得很爽")
    _create(store, "2026-09-01", "今天很开心，玩得很爽")
    analyzer.analyze_all_diaries()

    profile = SelfEvolutionService(store).update_user_profile("2026-08-31")

    assert profile.total_entries_analyzed == 1
    assert sum(profile.emotional_baseline["mood_distribution"].values()) == 1

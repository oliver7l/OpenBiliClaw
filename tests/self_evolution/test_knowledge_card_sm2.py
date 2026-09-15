"""`SM2Scheduler` 回归 —— 间隔重复算法（纯函数，此前零测试）。

`self_evolution/knowledge_card.py` 656 行里唯一**完全确定**的部分就是 SM-2：没有 IO、
没有 LLM、没有时间依赖（只把 `now` 写进 next_review）。它同时是最容易写错又最难看出来
的一块——算错的后果是「复习间隔悄悄变离谱」，界面不报错、只是记忆曲线废掉。

公式（SuperMemo 2）：

- 间隔：`q < 3` → 重置（reps=0, interval=1）；`reps==1` → 1 天；`reps==2` → 6 天；
  `reps>=3` → `ceil(interval × ease_factor)`（用**更新前**的 EF）
- 难度因子：`EF' = EF + (0.1 − (5−q)(0.08 + (5−q)×0.02))`，下限 1.3
- `q` 先夹到 `[0, 5]`
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from openbiliclaw.self_evolution.knowledge_card import KnowledgeCard, SM2Scheduler


def _card(**overrides: object) -> KnowledgeCard:
    base: dict[str, object] = {
        "card_id": "c1",
        "source_article_id": 1,
        "source_title": "文章",
        "source_url": "https://example.com/1",
        "card_type": "qa",
        "front": "问题",
        "back": "答案",
    }
    base.update(overrides)
    return KnowledgeCard(**base)  # type: ignore[arg-type]


@pytest.fixture()
def scheduler() -> SM2Scheduler:
    return SM2Scheduler()


class TestSuccessfulReviews:
    def test_first_three_intervals_follow_the_fixed_ladder(self, scheduler: SM2Scheduler) -> None:
        """第 1/2/3 次成功复习的间隔是 1 → 6 → 6×EF 天。"""
        card = _card()

        scheduler.schedule(card, quality=5)
        assert (card.repetitions, card.interval_days) == (1, 1)

        scheduler.schedule(card, quality=5)
        assert (card.repetitions, card.interval_days) == (2, 6)

        interval_before = card.interval_days
        ease_before = card.ease_factor
        scheduler.schedule(card, quality=5)
        assert card.repetitions == 3
        assert card.interval_days == int(interval_before * ease_before) + 1  # ceil 6*2.6=16

    def test_interval_uses_the_pre_update_ease_factor(self, scheduler: SM2Scheduler) -> None:
        """第 3 次复习用的是**更新前**的 EF。

        鉴别力：把 EF 更新挪到间隔计算之前，本用例立刻红（结果是 6×2.6 还是
        6×2.7 这类 1 天差异——正是那种「肉眼看不出、时间一长就歪」的错）。
        """
        card = _card(repetitions=2, interval_days=6, ease_factor=2.5)

        scheduler.schedule(card, quality=5)

        assert card.interval_days == 15  # ceil(6 × 2.5)，不是 ceil(6 × 2.6)=16
        assert card.ease_factor == pytest.approx(2.6)

    def test_quality_threshold_is_inclusive(self, scheduler: SM2Scheduler) -> None:
        """`q == 3` 算「记住了」（`quality >= 3`）。"""
        card = _card(repetitions=5, interval_days=10, ease_factor=2.5)

        scheduler.schedule(card, quality=3)

        assert card.repetitions == 6
        assert card.interval_days == 25  # ceil(10 × 2.5)


class TestFailedReviews:
    @pytest.mark.parametrize("quality", [0, 1, 2])
    def test_below_threshold_resets_the_ladder(self, scheduler: SM2Scheduler, quality: int) -> None:
        card = _card(repetitions=7, interval_days=42, ease_factor=2.5)

        scheduler.schedule(card, quality=quality)

        assert card.repetitions == 0
        assert card.interval_days == 1

    def test_reset_interval_does_not_survive_the_next_review(self, scheduler: SM2Scheduler) -> None:
        """重置后重新开始：下一次成功复习又回到 1 天。"""
        card = _card(repetitions=7, interval_days=42, ease_factor=2.5)
        scheduler.schedule(card, quality=1)

        scheduler.schedule(card, quality=5)

        assert (card.repetitions, card.interval_days) == (1, 1)


class TestEaseFactor:
    @pytest.mark.parametrize(
        ("quality", "delta"),
        [
            (5, 0.1),  # 满分：+0.1
            (4, 0.0),  # 正好不变
            (3, -0.14),
            (2, -0.32),
            (1, -0.54),
            (0, -0.8),
        ],
    )
    def test_delta_table(self, scheduler: SM2Scheduler, quality: int, delta: float) -> None:
        card = _card(repetitions=1, interval_days=1, ease_factor=2.5)

        scheduler.schedule(card, quality=quality)

        assert card.ease_factor == pytest.approx(2.5 + delta, abs=1e-9)

    def test_floor_is_enforced(self, scheduler: SM2Scheduler) -> None:
        """EF 有下限 1.3：连按 10 次满分失败也压不穿。"""
        card = _card(ease_factor=1.35, repetitions=1)
        for _ in range(10):
            scheduler.schedule(card, quality=0)
        assert card.ease_factor == pytest.approx(SM2Scheduler.MIN_EASE)

    def test_low_ease_keeps_intervals_growing_slowly(self, scheduler: SM2Scheduler) -> None:
        """EF 触底后间隔仍要**增长**（否则卡片会永远黏在复习队列里）。"""
        card = _card(repetitions=3, interval_days=10, ease_factor=1.3)

        scheduler.schedule(card, quality=5)

        assert card.interval_days == 13  # ceil(10 × 1.3)


class TestQualityClamping:
    @pytest.mark.parametrize(("raw", "effective"), [(99, 5), (-7, 0), (6, 5), (-1, 0)])
    def test_out_of_range_quality_is_clamped(
        self, scheduler: SM2Scheduler, raw: int, effective: int
    ) -> None:
        """越界评分按边界处理，不能让 EF 算出 > 2.6 这种没定义的值。"""
        card = _card(repetitions=1, interval_days=1, ease_factor=2.5)
        reference = _card(repetitions=1, interval_days=1, ease_factor=2.5)

        scheduler.schedule(card, quality=raw)
        scheduler.schedule(reference, quality=effective)

        assert card.ease_factor == pytest.approx(reference.ease_factor)


class TestReviewTimestamps:
    def test_next_review_follows_the_interval(self, scheduler: SM2Scheduler) -> None:
        card = _card(repetitions=1, interval_days=1, ease_factor=2.5)

        before = datetime.now()
        scheduler.schedule(card, quality=5)
        after = datetime.now()

        assert datetime.fromisoformat(card.next_review) - before >= timedelta(days=6)
        assert datetime.fromisoformat(card.next_review) - after <= timedelta(days=6)
        assert datetime.fromisoformat(card.last_reviewed) >= before

    def test_timestamps_are_naive_local_time(self, scheduler: SM2Scheduler) -> None:
        """⚠️ 现状（不是设计）：这里写的是**不带时区的本地时间**。

        `reading_schedule.ReadingScheduler` 用的是 `datetime.now(UTC)`（带时区），
        两套混着用会直接炸：`naive - aware` 抛
        `TypeError: can't subtract offset-naive and offset-aware datetimes`。
        本用例把这个事实钉下来，免得以后有人「顺手改成 UTC」时不知道会波及
        已经落库的 naive 旧行；要统一得连迁移一起做。
        """
        card = _card()

        scheduler.schedule(card, quality=5)

        next_review = datetime.fromisoformat(card.next_review)
        assert next_review.tzinfo is None
        assert next_review == next_review.replace(tzinfo=None)

    def test_returns_the_same_object(self, scheduler: SM2Scheduler) -> None:
        card = _card()
        assert scheduler.schedule(card, quality=4) is card

    def test_interval_is_an_int(self, scheduler: SM2Scheduler) -> None:
        """间隔是整天数（`ceil`），不能出现 6.5 天这种值——
        `timedelta(days=...)` 会接受浮点，但落库/比较时立刻变成脏数据。"""
        card = _card(repetitions=3, interval_days=7, ease_factor=2.3)

        scheduler.schedule(card, quality=4)

        assert isinstance(card.interval_days, int)
        assert card.interval_days == 17  # ceil(7 × 2.3) = ceil(16.1)

"""`ReadingScheduler` 回归 —— 顺带钉住两个此前零测试藏住的**真 bug**。

本模块（`self_evolution/reading_schedule.py`，486 行）此前零测试，补测时发现：

1. 🔴 **`_ensure_table` 不可达**：它被写在 `_get_conn()` 的 `return conn` **之后**，
   而 `_get_conn` 又被 `_ensure_table` 调用（挂进去会无限递归）⇒ 全新库上
   `reading_schedule` **永远建不出来**。后果不是报错而是**静默**：
   `register_article` 吞掉 `no such table` 返回 `False`。真实库里表是历史遗留的
   （501 行数据），所以这个洞一直没暴露。
2. 🔴 **`ReadingScheduleItem.from_row` 用了 `sqlite3.Row.get()`**（那是 dict 的方法）
   ⇒ `AttributeError`。三个调用方全中：`review_article` / `get_daily_queue` /
   `get_article_schedule`——也就是「复习」和「今日队列」在生产上是坏的。
3. 🟡 `register_article` 的**返回值与 docstring 相反**：`INSERT OR IGNORE` 撞 UNIQUE
   不抛异常，于是重复注册也返回 `True`。

测试全部用 `tmp_path` 造一对库（`openbiliclaw.db` + 同目录 `content.db`），
因为 `_get_conn` 会 ATTACH 同目录的 `content.db` 去 JOIN `articles`。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from openbiliclaw.self_evolution.reading_schedule import (
    ArticleState,
    ReadingScheduleItem,
    ReadingScheduler,
    ReviewRating,
)

_ARTICLES_DDL = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _make_dbs(tmp_path: Path, article_ids: tuple[int, ...] = (1,)) -> Path:
    """造 `data/` 形状的一对库：主库（调度表由 Scheduler 自己建）+ content.db。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    content = tmp_path / "content.db"
    with sqlite3.connect(content) as conn:
        conn.executescript(_ARTICLES_DDL)
        for article_id in article_ids:
            conn.execute(
                "INSERT INTO articles (id, title, url, source_type) VALUES (?, ?, ?, ?)",
                (article_id, f"文章{article_id}", f"https://example.com/{article_id}", "zhihu"),
            )
    return tmp_path / "openbiliclaw.db"


@pytest.fixture()
def scheduler(tmp_path: Path) -> ReadingScheduler:
    return ReadingScheduler(str(_make_dbs(tmp_path, (1, 2, 3))))


def _raw(db_path: Path, sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    with sqlite3.connect(db_path) as conn:
        return [tuple(row) for row in conn.execute(sql, params).fetchall()]


# ── 1. 建表（真 bug 回归）────────────────────────────────────────


class TestSchemaBootstrap:
    def test_fresh_database_gets_its_own_table(self, tmp_path: Path) -> None:
        """全新库必须能注册文章。

        ⚠️ 这条用例在修复前**必红**（`register_article` 返回 False、表不存在）：
        `_ensure_table` 挂在 `return conn` 之后，是一段不可达代码。
        鉴别力就在于「换个干净库」——用真实 `data/` 跑永远是绿的。
        """
        db_path = _make_dbs(tmp_path, (1,))
        assert _raw(db_path, "SELECT COUNT(*) FROM sqlite_master WHERE name='reading_schedule'") == [
            (0,)
        ]

        sched = ReadingScheduler(str(db_path))

        tables = _raw(db_path, "SELECT name FROM sqlite_master WHERE name='reading_schedule'")
        assert tables == [("reading_schedule",)], "构造时就应该把表建出来"
        assert sched.register_article(1) is True

    def test_indexes_are_created(self, tmp_path: Path) -> None:
        db_path = _make_dbs(tmp_path, (1,))
        ReadingScheduler(str(db_path))
        indexes = {
            row[0]
            for row in _raw(db_path, "SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert {"idx_reading_schedule_next_review", "idx_reading_schedule_state"} <= indexes

    def test_construction_survives_a_broken_database(self, tmp_path: Path) -> None:
        """建表失败不能连累构造（读路径还要能用）。"""
        bad = tmp_path / "not-a-db"
        bad.mkdir()  # 把目录当库文件传进去
        ReadingScheduler(str(bad))  # 不抛异常即可


# ── 2. 注册 ──────────────────────────────────────────────────────


class TestRegister:
    def test_register_reports_duplicates(self, scheduler: ReadingScheduler) -> None:
        """返回值必须反映「这次真的插进去了没有」（docstring 承诺 False=已存在）。"""
        assert scheduler.register_article(1) is True
        assert scheduler.register_article(1) is False

    def test_registered_defaults_and_initial_delay(self, tmp_path: Path) -> None:
        db_path = _make_dbs(tmp_path, (1,))
        sched = ReadingScheduler(str(db_path))
        sched.register_article(1, initial_delay_days=3.0)

        row = _raw(
            db_path,
            "SELECT stability, difficulty, retrievability, state, next_review_at FROM reading_schedule",
        )[0]
        stability, difficulty, retrievability, state, next_review_at = row
        assert (stability, difficulty, retrievability, state) == (1.0, 5.0, 1.0, "new")
        delta = datetime.fromisoformat(str(next_review_at)) - datetime.now(UTC)
        assert timedelta(days=2.9) < delta < timedelta(days=3.1)

    def test_batch_register_skips_already_scheduled(self, tmp_path: Path) -> None:
        db_path = _make_dbs(tmp_path, (1, 2, 3))
        sched = ReadingScheduler(str(db_path))
        assert sched.batch_register_from_articles() == 3
        assert sched.batch_register_from_articles() == 0


# ── 3. FSRS 复核 ────────────────────────────────────────────────


class TestReview:
    @pytest.mark.parametrize(
        ("rating", "expected_stability", "expected_difficulty"),
        [
            (ReviewRating.again, 1.0, 6.0),  # 忘光：稳定性重置为 1.0
            (ReviewRating.hard, 1.2, 5.5),  # ×1.2
            (ReviewRating.good, 1.5, 5.0),  # ×1.5
            (ReviewRating.easy, 2.5, 4.5),  # ×2.5，难度还降 0.5
        ],
    )
    def test_rating_math(
        self,
        tmp_path: Path,
        rating: ReviewRating,
        expected_stability: float,
        expected_difficulty: float,
    ) -> None:
        db_path = _make_dbs(tmp_path, (1,))
        sched = ReadingScheduler(str(db_path))
        sched.register_article(1)

        item = sched.review_article(1, rating)

        assert item is not None
        assert item.stability == pytest.approx(expected_stability)
        assert item.difficulty == pytest.approx(expected_difficulty)
        assert item.reps == 1
        assert item.retrievability == 1.0

    def test_review_reads_back_from_the_database(self, scheduler: ReadingScheduler) -> None:
        """复核结果必须落库，而不是只改内存对象。

        鉴别力：把 `conn.commit()` 删掉，第二次读到的仍是初始值。
        """
        scheduler.register_article(1)
        scheduler.review_article(1, ReviewRating.easy)

        row = _raw(
            scheduler.db_path,
            "SELECT stability, reps, state FROM reading_schedule WHERE article_id=1",
        )[0]
        assert row[0] == pytest.approx(2.5)
        assert row[1] == 1

    def test_lapse_increments_and_resets(self, tmp_path: Path) -> None:
        """连续 easy 积累稳定性后，一次 again 必须把稳定性打回 1.0 并 +1 lapse。"""
        db_path = _make_dbs(tmp_path, (1,))
        sched = ReadingScheduler(str(db_path))
        sched.register_article(1)
        sched.review_article(1, ReviewRating.easy)
        grown = sched.review_article(1, ReviewRating.easy)
        assert grown is not None and grown.stability == pytest.approx(6.25)

        lapsed = sched.review_article(1, ReviewRating.again)

        assert lapsed is not None
        assert lapsed.stability == 1.0
        assert lapsed.lapses == 1
        assert lapsed.state == ArticleState.review.value  # reps 已经 >= 3

    def test_difficulty_is_clamped(self, tmp_path: Path) -> None:
        """难度必须夹在 [1, 10]：连按 10 次 again 也不能冲破上界。"""
        db_path = _make_dbs(tmp_path, (1,))
        sched = ReadingScheduler(str(db_path))
        sched.register_article(1)
        for _ in range(10):
            item = sched.review_article(1, ReviewRating.again)
        assert item is not None and item.difficulty == 10.0

    def test_reading_percent_is_clamped_and_null_keeps_it(self, tmp_path: Path) -> None:
        db_path = _make_dbs(tmp_path, (1,))
        sched = ReadingScheduler(str(db_path))
        sched.register_article(1)

        updated = sched.review_article(1, ReviewRating.good, reading_percent=250.0)
        assert updated is not None and updated.reading_percent == 100.0
        assert updated.last_read_at

        kept = sched.review_article(1, ReviewRating.good)
        assert kept is not None and kept.reading_percent == 100.0

    def test_unknown_article_returns_none(self, scheduler: ReadingScheduler) -> None:
        assert scheduler.review_article(999, ReviewRating.good) is None

    @pytest.mark.parametrize(
        ("reps", "stability", "expected"),
        [
            (0, 1.0, ArticleState.learning.value),  # 复核后 reps=1
            (1, 1.0, ArticleState.learning.value),  # 复核后 reps=2
            (2, 5.0, ArticleState.review.value),  # 复核后 reps=3 → review
            (1, 30.0, ArticleState.mastered.value),  # 稳定性达标优先于 reps
        ],
    )
    def test_state_transitions(
        self,
        tmp_path: Path,
        reps: int,
        stability: float,
        expected: str,
    ) -> None:
        db_path = _make_dbs(tmp_path, (1,))
        sched = ReadingScheduler(str(db_path))
        sched.register_article(1)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE reading_schedule SET reps=?, stability=? WHERE article_id=1",
                (reps, stability),
            )
            conn.commit()

        item = sched.review_article(1, ReviewRating.good)

        assert item is not None
        # 复核会把 reps+1 并按新稳定性重算状态
        assert item.state == expected

    def test_next_review_follows_stability(self, tmp_path: Path) -> None:
        db_path = _make_dbs(tmp_path, (1,))
        sched = ReadingScheduler(str(db_path))
        sched.register_article(1)
        item = sched.review_article(1, ReviewRating.easy)
        assert item is not None
        delta = datetime.fromisoformat(item.next_review_at) - datetime.now(UTC)
        assert timedelta(days=2.4) < delta < timedelta(days=2.6)


# ── 4. 行 → 对象（真 bug 回归）────────────────────────────────────


class TestFromRow:
    def test_row_without_article_columns_does_not_explode(self, tmp_path: Path) -> None:
        """纯 `reading_schedule` 行（没有 JOIN articles）也必须能解析。

        ⚠️ 修复前这里抛 `AttributeError: 'sqlite3.Row' object has no attribute 'get'`
        ——而 `review_article` / `get_daily_queue` / `get_article_schedule` 都走这条路径。
        """
        db_path = _make_dbs(tmp_path, (1,))
        sched = ReadingScheduler(str(db_path))
        sched.register_article(1)

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM reading_schedule").fetchone()
        assert row is not None

        item = ReadingScheduleItem.from_row(row)

        assert item.article_id == 1
        assert item.title == "" and item.url == "" and item.source_type == ""

    def test_joined_row_carries_article_metadata(self, scheduler: ReadingScheduler) -> None:
        scheduler.register_article(2)
        item = scheduler.get_article_schedule(2)
        assert item is not None
        assert item.title == "文章2"
        assert item.url == "https://example.com/2"
        assert item.source_type == "zhihu"

    def test_missing_schedule_returns_none(self, scheduler: ReadingScheduler) -> None:
        assert scheduler.get_article_schedule(1) is None


# ── 5. 今日队列 ─────────────────────────────────────────────────


class TestDailyQueue:
    def test_due_reviews_come_before_new(self, tmp_path: Path) -> None:
        db_path = _make_dbs(tmp_path, (1, 2, 3))
        sched = ReadingScheduler(str(db_path))
        sched.register_article(1)
        sched.register_article(2)
        sched.register_article(3)
        # 1 号已到期（把 next_review_at 挪到过去），2/3 仍是 new
        with sqlite3.connect(str(db_path)) as conn:
            past = (datetime.now(UTC) - timedelta(days=2)).isoformat()
            conn.execute(
                "UPDATE reading_schedule SET next_review_at=?, state='learning' WHERE article_id=1",
                (past,),
            )
            conn.commit()

        queue = sched.get_daily_queue(limit=10, new_count=1)

        assert [item.article_id for item in queue] == [1, 2]
        assert queue[0].title == "文章1"  # JOIN 出来的元数据

    def test_mastered_articles_are_excluded_from_reviews(self, tmp_path: Path) -> None:
        db_path = _make_dbs(tmp_path, (1,))
        sched = ReadingScheduler(str(db_path))
        sched.register_article(1)
        with sqlite3.connect(str(db_path)) as conn:
            past = (datetime.now(UTC) - timedelta(days=2)).isoformat()
            conn.execute(
                "UPDATE reading_schedule SET next_review_at=?, state='mastered' WHERE article_id=1",
                (past,),
            )
            conn.commit()

        assert sched.get_daily_queue(limit=10, new_count=0) == []

    def test_include_new_false_returns_only_reviews(self, tmp_path: Path) -> None:
        db_path = _make_dbs(tmp_path, (1, 2))
        sched = ReadingScheduler(str(db_path))
        sched.register_article(1)
        sched.register_article(2)
        assert sched.get_daily_queue(limit=10, include_new=False) == []

    def test_new_count_is_capped_but_limit_wins(self, tmp_path: Path) -> None:
        db_path = _make_dbs(tmp_path, (1, 2, 3))
        sched = ReadingScheduler(str(db_path))
        for article_id in (1, 2, 3):
            sched.register_article(article_id)

        assert len(sched.get_daily_queue(limit=10, new_count=2)) == 2
        assert len(sched.get_daily_queue(limit=1, new_count=5)) == 1

    def test_stats_counts_by_state(self, tmp_path: Path) -> None:
        db_path = _make_dbs(tmp_path, (1, 2))
        sched = ReadingScheduler(str(db_path))
        sched.register_article(1)
        sched.register_article(2)

        stats = sched.get_stats()

        assert stats["total_articles"] == 2
        assert stats["by_state"] == {"new": 2}
        assert stats["mastered"] == 0
        assert stats["total_reviews"] == 0

    def test_stats_reflect_reviews_and_due_count(self, tmp_path: Path) -> None:
        db_path = _make_dbs(tmp_path, (1,))
        sched = ReadingScheduler(str(db_path))
        sched.register_article(1)
        sched.review_article(1, ReviewRating.again)  # reps=1, lapses=1, 稳定性 1.0

        stats = sched.get_stats()

        assert stats["total_reviews"] == 1
        assert stats["total_lapses"] == 1
        assert ("learning" in stats["by_state"]) or ("review" in stats["by_state"])

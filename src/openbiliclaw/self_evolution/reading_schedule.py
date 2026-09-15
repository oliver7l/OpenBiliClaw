"""智能阅读调度器 — 基于 FSRS 算法的间隔重复阅读管理。

核心功能：
- 每篇文章维护一个"记忆状态"（稳定性、难度、复习次数）
- 根据用户阅读反馈（好/一般/难/忘记）动态调整下次复习时间
- 每日生成"今日阅读队列"：应复习的文章 + 适量新文章
- 阅读进度追踪：记录阅读百分比、最后阅读时间

算法说明（简化版 FSRS）：
- 新文章初始稳定性 = 1 天，难度 = 5
- 反馈"好"：稳定性 ×2.5，难度 -0.5
- 反馈"一般"：稳定性 ×1.5，难度不变
- 反馈"难"：稳定性 ×1.2，难度 +0.5
- 反馈"忘记"：稳定性重置为 1，难度 +1，忘记次数 +1
- 下次复习时间 = 上次复习时间 + 稳定性（天）
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from openbiliclaw.storage.database import open_db_conn

logger = logging.getLogger(__name__)


class ReviewRating(StrEnum):
    """阅读反馈评级。"""

    again = "again"  # 忘记了，需要重新学
    hard = "hard"  # 有点难
    good = "good"  # 一般
    easy = "easy"  # 很简单


class ArticleState(StrEnum):
    """文章学习状态。"""

    new = "new"  # 新文章，未开始阅读
    learning = "learning"  # 学习中
    review = "review"  # 复习中
    mastered = "mastered"  # 已掌握（稳定性 > 30 天）


@dataclass
class ReadingScheduleItem:
    """单篇文章的阅读调度状态。"""

    article_id: int
    title: str = ""
    url: str = ""
    source_type: str = ""
    # FSRS 状态
    stability: float = 1.0  # 记忆稳定性（天）
    difficulty: float = 5.0  # 难度（1-10）
    retrievability: float = 1.0  # 当前可提取性（0-1）
    reps: int = 0  # 复习次数
    lapses: int = 0  # 忘记次数
    state: str = ArticleState.new.value
    last_review_at: str = ""
    next_review_at: str = ""
    # 阅读进度
    reading_percent: float = 0.0
    last_read_at: str = ""
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ReadingScheduleItem:
        """从数据库行创建对象。

        ⚠️ ``sqlite3.Row`` **没有 ``.get()``**（那是 dict 的方法），此前的
        ``row.get("title", "")`` 会让三个调用方全部抛 ``AttributeError``：
        ``review_article`` / ``get_daily_queue`` / ``get_article_schedule``
        ——也就是说「复习」与「今日队列」这两条主路径在生产上是坏的，
        零测试才让它藏了这么久。

        容错是必须的，因为查询有两种形状：纯 ``reading_schedule`` 行，
        以及 JOIN ``articles`` 之后多出 title/url/source_type 的行。
        所以「列在不在」用 ``row.keys()`` 判断，而不是假定它一定在。
        """
        available = set(row.keys())

        def pick(key: str, default: Any) -> Any:
            return row[key] if key in available else default

        return cls(
            article_id=row["article_id"],
            title=pick("title", "") or "",
            url=pick("url", "") or "",
            source_type=pick("source_type", "") or "",
            stability=row["stability"],
            difficulty=row["difficulty"],
            retrievability=row["retrievability"],
            reps=row["reps"],
            lapses=row["lapses"],
            state=row["state"],
            last_review_at=row["last_review_at"] or "",
            next_review_at=row["next_review_at"] or "",
            reading_percent=row["reading_percent"],
            last_read_at=row["last_read_at"] or "",
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "article_id": self.article_id,
            "title": self.title,
            "url": self.url,
            "source_type": self.source_type,
            "stability": round(self.stability, 2),
            "difficulty": round(self.difficulty, 2),
            "retrievability": round(self.retrievability, 4),
            "reps": self.reps,
            "lapses": self.lapses,
            "state": self.state,
            "last_review_at": self.last_review_at,
            "next_review_at": self.next_review_at,
            "reading_percent": self.reading_percent,
            "last_read_at": self.last_read_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ReadingScheduler:
    """智能阅读调度器。

    基于简化版 FSRS 算法，管理文章的阅读复习调度。

    Args:
        db_path: SQLite 数据库路径。

    """

    # 评级对应的稳定性乘数和难度变化
    _RATING_PARAMS = {
        ReviewRating.again: {"stability_mult": 0.3, "difficulty_delta": 1.0, "lapse": True},
        ReviewRating.hard: {"stability_mult": 1.2, "difficulty_delta": 0.5, "lapse": False},
        ReviewRating.good: {"stability_mult": 1.5, "difficulty_delta": 0.0, "lapse": False},
        ReviewRating.easy: {"stability_mult": 2.5, "difficulty_delta": -0.5, "lapse": False},
    }

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        # 建表必须在构造时做一次：``_ensure_table`` 只能挂在 ``__init__`` 上，
        # 不能挂进 ``_get_conn``（它自己会调 ``_get_conn``，挂进去就无限递归）。
        # 此前这句被写在 ``_get_conn`` 的 ``return conn`` **之后**（不可达），
        # 于是全新库上 ``reading_schedule`` 永远建不出来：
        # ``register_article`` 吞掉 "no such table" 静默返回 False、
        # ``review_article`` 直接抛 OperationalError。真实库里表是历史遗留的，
        # 所以这个洞一直没暴露（501 行数据看着一切正常）。
        try:
            self._ensure_table()
        except Exception:
            # 只读库 / 权限不足时不要连累读路径（get_daily_queue 等仍然可用）
            logger.warning("无法确保 reading_schedule 表存在：%s", self.db_path, exc_info=True)

    def _get_conn(self):
        """返回 ATTACH 了 content.db 的连接（v0.4.0+ articles 表迁移）。"""
        conn = open_db_conn(self.db_path)
        conn.row_factory = sqlite3.Row
        from contextlib import suppress as _suppress
        from pathlib import Path as _Path
        _content_path = _Path(str(self.db_path)).with_name('content.db')
        if _content_path.exists():
            with _suppress(Exception):
                conn.execute('ATTACH DATABASE ? AS content', (str(_content_path),))
        return conn

    def _ensure_table(self) -> None:
        """确保 reading_schedule 表存在。"""
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reading_schedule (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    article_id INTEGER NOT NULL UNIQUE,
                    stability REAL DEFAULT 1.0,
                    difficulty REAL DEFAULT 5.0,
                    retrievability REAL DEFAULT 1.0,
                    reps INTEGER DEFAULT 0,
                    lapses INTEGER DEFAULT 0,
                    state TEXT DEFAULT 'new',
                    last_review_at TEXT DEFAULT '',
                    next_review_at TEXT DEFAULT '',
                    reading_percent REAL DEFAULT 0,
                    last_read_at TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_reading_schedule_next_review
                ON reading_schedule(next_review_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_reading_schedule_state
                ON reading_schedule(state)
            """)
            conn.commit()

    def register_article(self, article_id: int, initial_delay_days: float = 1.0) -> bool:
        """注册一篇文章到阅读调度系统。

        Args:
            article_id: 文章 ID。
            initial_delay_days: 首次复习延迟天数（默认 1 天）。

        Returns:
            True 如果注册成功，False 如果已存在。

        """
        now = datetime.now(UTC).isoformat()
        next_review = (datetime.now(UTC) + timedelta(days=initial_delay_days)).isoformat()

        try:
            with self._get_conn() as conn:
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO reading_schedule
                       (article_id, stability, difficulty, retrievability, state,
                        next_review_at, created_at, updated_at)
                       VALUES (?, 1.0, 5.0, 1.0, 'new', ?, ?, ?)""",
                    (article_id, next_review, now, now),
                )
                conn.commit()
                # 返回值必须反映「这次到底插进去没有」：`INSERT OR IGNORE` 撞 UNIQUE
                # 时不会抛异常，只看「没抛」会把重复注册也报成成功（与 docstring
                # 的「False 如果已存在」相反，调用方就无法区分）。
                return cursor.rowcount > 0
        except Exception as e:
            logger.exception("Failed to register article %s: %s", article_id, e)
            return False

    def review_article(
        self, article_id: int, rating: ReviewRating, reading_percent: float | None = None
    ) -> ReadingScheduleItem | None:
        """对一篇文章进行阅读反馈，更新调度状态。

        Args:
            article_id: 文章 ID。
            rating: 阅读反馈评级。
            reading_percent: 本次阅读进度百分比（0-100），如果为 None 则不更新。

        Returns:
            更新后的 ReadingScheduleItem，如果文章不存在则返回 None。

        """
        now = datetime.now(UTC)

        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM reading_schedule WHERE article_id = ?",
                (article_id,),
            ).fetchone()

            if not row:
                logger.warning("Article %s not found in reading schedule", article_id)
                return None

            item = ReadingScheduleItem.from_row(row)
            params = self._RATING_PARAMS[rating]

            # 更新 FSRS 状态
            if params["lapse"]:
                item.lapses += 1
                item.stability = 1.0  # 忘记后重置稳定性
            else:
                item.stability = max(1.0, item.stability * params["stability_mult"])

            item.difficulty = max(1.0, min(10.0, item.difficulty + params["difficulty_delta"]))
            item.reps += 1
            item.retrievability = 1.0  # 刚复习完，可提取性最高

            # 更新状态
            if item.stability >= 30:
                item.state = ArticleState.mastered.value
            elif item.reps >= 3:
                item.state = ArticleState.review.value
            else:
                item.state = ArticleState.learning.value

            # 更新时间
            item.last_review_at = now.isoformat()
            item.next_review_at = (now + timedelta(days=item.stability)).isoformat()

            # 更新阅读进度
            if reading_percent is not None:
                item.reading_percent = max(0.0, min(100.0, reading_percent))
                item.last_read_at = now.isoformat()

            item.updated_at = now.isoformat()

            # 保存到数据库
            conn.execute(
                """UPDATE reading_schedule SET
                       stability=?, difficulty=?, retrievability=?, reps=?, lapses=?,
                       state=?, last_review_at=?, next_review_at=?,
                       reading_percent=?, last_read_at=?, updated_at=?
                   WHERE article_id=?""",
                (
                    item.stability,
                    item.difficulty,
                    item.retrievability,
                    item.reps,
                    item.lapses,
                    item.state,
                    item.last_review_at,
                    item.next_review_at,
                    item.reading_percent,
                    item.last_read_at,
                    item.updated_at,
                    article_id,
                ),
            )
            conn.commit()

            # 补充文章基本信息（title/url/source_type）
            try:
                art_row = conn.execute(
                    "SELECT title, url, source_type FROM articles WHERE id = ?",
                    (article_id,),
                ).fetchone()
                if art_row:
                    item.title = art_row[0] or ""
                    item.url = art_row[1] or ""
                    item.source_type = art_row[2] or ""
            except Exception:
                pass

            logger.info(
                "Reviewed article %s: rating=%s, reps=%d, stability=%.1fd, next=%s",
                article_id,
                rating.value,
                item.reps,
                item.stability,
                item.next_review_at,
            )
            return item

    def get_daily_queue(
        self, limit: int = 20, include_new: bool = True, new_count: int = 5
    ) -> list[ReadingScheduleItem]:
        """获取今日阅读队列。

        包含：
        1. 到期需要复习的文章（next_review_at <= 现在）
        2. 适量新文章（如果 include_new 为 True）

        Args:
            limit: 队列最大数量。
            include_new: 是否包含新文章。
            new_count: 新文章最大数量。

        Returns:
            阅读队列列表，按优先级排序（到期复习优先，然后新文章）。

        """
        now = datetime.now(UTC).isoformat()
        items: list[ReadingScheduleItem] = []

        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row

            # 1. 到期需要复习的文章
            review_rows = conn.execute(
                """SELECT rs.*, a.title, a.url, a.source_type
                   FROM reading_schedule rs
                   JOIN articles a ON rs.article_id = a.id
                   WHERE rs.next_review_at <= ? AND rs.state != 'mastered'
                   ORDER BY rs.next_review_at ASC
                   LIMIT ?""",
                (now, limit),
            ).fetchall()

            for row in review_rows:
                items.append(ReadingScheduleItem.from_row(row))

            # 2. 新文章（如果还有名额）
            if include_new and len(items) < limit:
                remaining = limit - len(items)
                new_limit = min(new_count, remaining)

                new_rows = conn.execute(
                    """SELECT rs.*, a.title, a.url, a.source_type
                       FROM reading_schedule rs
                       JOIN articles a ON rs.article_id = a.id
                       WHERE rs.state = 'new'
                       ORDER BY rs.created_at ASC
                       LIMIT ?""",
                    (new_limit,),
                ).fetchall()

                for row in new_rows:
                    items.append(ReadingScheduleItem.from_row(row))

        return items

    def get_article_schedule(self, article_id: int) -> ReadingScheduleItem | None:
        """获取单篇文章的阅读调度状态。

        Args:
            article_id: 文章 ID。

        Returns:
            ReadingScheduleItem，如果不存在则返回 None。

        """
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT rs.*, a.title, a.url, a.source_type
                   FROM reading_schedule rs
                   JOIN articles a ON rs.article_id = a.id
                   WHERE rs.article_id = ?""",
                (article_id,),
            ).fetchone()

            if not row:
                return None
            return ReadingScheduleItem.from_row(row)

    def get_stats(self) -> dict[str, Any]:
        """获取阅读调度统计信息。

        Returns:
            包含各状态文章数量、今日到期数等统计的字典。

        """
        now = datetime.now(UTC).isoformat()

        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM reading_schedule").fetchone()[0]

            by_state = {}
            for row in conn.execute("SELECT state, COUNT(*) FROM reading_schedule GROUP BY state"):
                by_state[row[0]] = row[1]

            due_today = conn.execute(
                "SELECT COUNT(*) FROM reading_schedule WHERE next_review_at <= ? AND state != 'mastered'",
                (now,),
            ).fetchone()[0]

            mastered = by_state.get("mastered", 0)
            total_reps = conn.execute(
                "SELECT COALESCE(SUM(reps), 0) FROM reading_schedule"
            ).fetchone()[0]
            total_lapses = conn.execute(
                "SELECT COALESCE(SUM(lapses), 0) FROM reading_schedule"
            ).fetchone()[0]
            avg_stability = conn.execute(
                "SELECT COALESCE(AVG(stability), 0) FROM reading_schedule"
            ).fetchone()[0]

        return {
            "total_articles": total,
            "by_state": by_state,
            "due_today": due_today,
            "mastered": mastered,
            "total_reviews": total_reps,
            "total_lapses": total_lapses,
            "avg_stability_days": round(avg_stability, 2),
        }

    def batch_register_from_articles(self, limit: int = 1000) -> int:
        """批量将 articles 表中的文章注册到阅读调度系统。

        只注册尚未在 reading_schedule 表中的文章。

        Args:
            limit: 最大注册数量。

        Returns:
            实际注册的文章数量。

        """
        now = datetime.now(UTC).isoformat()
        next_review = (datetime.now(UTC) + timedelta(days=1)).isoformat()

        with self._get_conn() as conn:
            # 找出尚未注册的文章
            rows = conn.execute(
                """SELECT a.id FROM articles a
                   LEFT JOIN reading_schedule rs ON a.id = rs.article_id
                   WHERE rs.article_id IS NULL
                   ORDER BY a.created_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()

            count = 0
            for row in rows:
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO reading_schedule
                           (article_id, stability, difficulty, retrievability, state,
                            next_review_at, created_at, updated_at)
                           VALUES (?, 1.0, 5.0, 1.0, 'new', ?, ?, ?)""",
                        (row[0], next_review, now, now),
                    )
                    count += 1
                except Exception:
                    continue

            conn.commit()

        logger.info("Batch registered %d articles to reading schedule", count)
        return count

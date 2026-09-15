"""self_evolution 回归测试（2026-09-15，本模块首批单测）。

背景：``self_evolution/`` 有 9,284 行、是**定时任务中枢**，此前零测试——
「聊天分析空转毒化」（loop_engine 构造 ChatAnalysisService 未传 llm_service，
219 个会话被标「已分析」却零产出）正是零覆盖才藏住的。本文件优先锁住：

1. ``SelfEvolutionState``：跨连接持久化 + 属性 round-trip
2. ``ContentFilter``：跨库（content/knowledge/events）筛选与质量排序
3. ``SelfEvolutionLoopEngine._do_chat_analysis``：无 LLM 必须跳过
   （锁住 `5d31f193` 的修复，防止回归成「空转标记」）

全部用 tmp_path 隔离，不碰 ``data/`` 真实库。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from openbiliclaw.self_evolution.loop_engine import (
    ContentFilter,
    SelfEvolutionLoopEngine,
    SelfEvolutionState,
)

# ── SelfEvolutionState ───────────────────────────────────────────


def test_state_roundtrip_across_instances(tmp_path: Path) -> None:
    """状态必须经 sqlite 持久化：换一个实例（模拟重启）还能读到。"""
    db = tmp_path / "state.db"
    s1 = SelfEvolutionState(str(db))
    s1.set("foo", "bar")
    s1.last_processed_article_id = 42
    s1.last_batch_run_at = datetime(2026, 9, 15, 8, 0, 0)

    s2 = SelfEvolutionState(str(db))
    assert s2.get("foo") == "bar"
    assert s2.last_processed_article_id == 42
    assert s2.last_batch_run_at == datetime(2026, 9, 15, 8, 0, 0)


def test_state_defaults_and_garbage_tolerance(tmp_path: Path) -> None:
    """空库给默认值；库里的脏值不得抛异常（定时任务要能自愈）。"""
    db = tmp_path / "state.db"
    s = SelfEvolutionState(str(db))
    assert s.get("missing") is None
    assert s.get("missing", "dft") == "dft"
    assert s.last_processed_article_id == 0
    assert s.last_batch_run_at is None

    s.set("last_processed_article_id", "not-a-number")
    assert s.last_processed_article_id == 0, "脏值应回退到 0 而不是炸掉循环"


# ── ContentFilter（跨库查询）─────────────────────────────────────


@pytest.fixture()
def wired_conn(tmp_path: Path) -> sqlite3.Connection:
    """构造 content / knowledge / events 三个 ATTACH 库的最小 schema。"""
    main = sqlite3.connect(tmp_path / "main.db")
    main.row_factory = sqlite3.Row
    for name in ("content", "knowledge", "events"):
        main.execute(f"ATTACH DATABASE ? AS {name}", (str(tmp_path / f"{name}.db"),))
    main.executescript(
        """
        CREATE TABLE content.articles (
            id INTEGER PRIMARY KEY,
            content_text TEXT,
            favorited INTEGER DEFAULT 0,
            topic_group TEXT,
            source_type TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        );
        CREATE TABLE content.article_tldrs (article_id INTEGER PRIMARY KEY);
        CREATE TABLE knowledge.knowledge_cards (source_article_id INTEGER PRIMARY KEY);
        CREATE TABLE events.events (
            id INTEGER PRIMARY KEY,
            article_id INTEGER,
            event_type TEXT
        );
        """
    )
    main.commit()
    return main


def _article(conn: sqlite3.Connection, aid: int, chars: int = 300, favorited: int = 0) -> None:
    conn.execute(
        "INSERT INTO content.articles (id, content_text, favorited, created_at) VALUES (?, ?, ?, ?)",
        (aid, "x" * chars, favorited, "2026-09-15T00:00:00"),
    )


def test_count_new_articles(wired_conn: sqlite3.Connection) -> None:
    _article(wired_conn, 1)
    _article(wired_conn, 2)
    assert ContentFilter.count_new_articles(wired_conn, since_id=0) == 2
    assert ContentFilter.count_new_articles(wired_conn, since_id=1) == 1


def test_top_candidates_filters_and_prioritises(wired_conn: sqlite3.Connection) -> None:
    """规则过滤 + 质量排序：收藏 > 有互动 > 无互动；太短/已有 TLDR 的排除。"""
    _article(wired_conn, 1)                                   # 普通，300 字
    _article(wired_conn, 2, favorited=1)                      # 收藏 → 最高分
    _article(wired_conn, 3, chars=50)                         # 太短 → 排除
    _article(wired_conn, 4)                                   # 有 view 事件 → 中分
    _article(wired_conn, 5)                                   # 已有 TLDR → 排除
    wired_conn.execute("INSERT INTO content.article_tldrs VALUES (5)")
    _article(wired_conn, 6)                                   # 已有 knowledge card → 排除
    wired_conn.execute("INSERT INTO knowledge.knowledge_cards VALUES (6)")
    wired_conn.executemany(
        "INSERT INTO events.events (article_id, event_type) VALUES (?, ?)",
        [(4, "view"), (2, "favorite")],
    )
    wired_conn.commit()

    ids = ContentFilter.top_candidates(wired_conn, limit=10)

    assert ids == [2, 4, 1], f"质量排序错误：{ids}"
    assert 3 not in ids and 5 not in ids and 6 not in ids, "规则过滤失效"


def test_top_candidates_respects_since_id(wired_conn: sqlite3.Connection) -> None:
    _article(wired_conn, 1)
    _article(wired_conn, 2)

    assert ContentFilter.top_candidates(wired_conn, limit=10, since_id=1) == [2]
    assert ContentFilter.count_unprocessed_articles(wired_conn, since_id=1) == 1


# ── LoopEngine._do_chat_analysis（空转标记回归）──────────────────


async def test_do_chat_analysis_skips_without_llm(tmp_path: Path) -> None:
    """锁住 `5d31f193`：无 LLM 时必须整体跳过，不得构造服务、不得碰任何库。"""
    engine = SelfEvolutionLoopEngine(db_path=str(tmp_path / "se.db"), llm_service=None)

    result = await engine._do_chat_analysis()

    assert result == {"skipped": "no_llm_service"}, (
        "无 LLM 时不应继续执行（旧行为会静默把会话标记成已分析却零产出）"
    )
    assert not list(tmp_path.glob("chat_analysis.db*")), "不应创建任何 chat_analysis 库"

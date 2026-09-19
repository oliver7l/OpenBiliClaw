"""RefillQueue 单元测试：schema / 灌入扫描 / 状态聚合。

用临时 content.db 模拟阅读库文章缺口，验证 fill_gaps 幂等入队、count_missing
实时口径、status 聚合。不触网络 / 浏览器，仅操作独立临时库。
"""

from __future__ import annotations

import sqlite3

import pytest

from openbiliclaw.refill import RefillQueue
from openbiliclaw.storage.database import open_db_conn


@pytest.fixture
def content_db(tmp_path):
    """构造最小 articles 表，返回其路径（且在其中预置缺口数据）。"""
    db_path = tmp_path / "content.db"
    conn = open_db_conn(db_path)
    conn.execute(
        """CREATE TABLE articles (
            url TEXT PRIMARY KEY,
            source_type TEXT,
            source_name TEXT,
            title TEXT,
            content_text TEXT)"""
    )
    rows = [
        # 小红书：3 缺正文
        ("https://xhs.com/1", "xiaohongshu", "小红书", "笔记一", None),
        ("https://xhs.com/2", "xiaohongshu", "小红书", "笔记二", ""),
        ("https://xhs.com/3", "xiaohongshu", "小红书", "", "已有正文不计数"),
        # youtube：2 缺正文
        ("https://yt.com/a", "youtube", "YouTube", "视频A", None),
        ("https://yt.com/b", "youtube", "YouTube", "视频B", "已有正文"),
        # bilibili：1 缺正文
        ("https://bili.com/watch/1", "bilibili", "B站", "稿件", ""),
    ]
    conn.executemany(
        "INSERT INTO articles (url, source_type, source_name, title, content_text)"
        " VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db_path


def test_fill_gaps_only_picks_missing_and_inserts_once(tmp_path, content_db):
    refill_db = tmp_path / "refill.db"
    queue = RefillQueue(refill_db, content_db)
    with queue:
        # 幂等建表
        cols = [r["name"] for r in queue.conn.execute("PRAGMA table_info(refill_queue)")]
        assert "url" in cols and "state" in cols

        first = queue.fill_gaps()
        # 只有带正文的 3 条计数，4 条被排除
        assert first == {"xiaohongshu": 2, "youtube": 1, "bilibili": 1}

        # 幂等：再次灌入不产生新行
        second = queue.fill_gaps()
        assert second == {"xiaohongshu": 0, "youtube": 0, "bilibili": 0}

        rows = queue.conn.execute("SELECT COUNT(*) AS n FROM refill_queue").fetchone()
        assert rows["n"] == 4


def test_fill_gaps_limit_per_source(tmp_path, content_db):
    refill_db = tmp_path / "refill.db"
    with RefillQueue(refill_db, content_db) as queue:
        limited = queue.fill_gaps(limit_per_source=1)
        assert limited["xiaohongshu"] == 1
        # 未受限制的平台仍全量
        assert limited["youtube"] == 1
        rest = queue.fill_gaps()
        assert rest["xiaohongshu"] == 1  # 剩 1 条补上
        assert rest["youtube"] == 0


def test_count_missing_reports_live_articles_gap(tmp_path, content_db):
    refill_db = tmp_path / "refill.db"
    with RefillQueue(refill_db, content_db) as queue:
        missing = queue.count_missing()
        assert missing["xiaohongshu"] == 2
        assert missing["youtube"] == 1
        assert missing["bilibili"] == 1


def test_status_groups_by_source_and_state(tmp_path, content_db):
    refill_db = tmp_path / "refill.db"
    with RefillQueue(refill_db, content_db) as queue:
        queue.fill_gaps()
        # 手动改状态制造聚合样本
        conn = queue.conn
        conn.execute("UPDATE refill_queue SET state='done' WHERE url='https://yt.com/a'")
        conn.execute("UPDATE refill_queue SET state='dropped' WHERE url='https://bili.com/watch/1'")
        conn.commit()

        rows = queue.status()
        by_source = {r.source_type: r for r in rows}
        assert by_source["xiaohongshu"].pending == 2
        assert by_source["xiaohongshu"].total == 2
        assert by_source["youtube"].done == 1  # youtube 仅 1 条缺口，改 done
        assert by_source["youtube"].pending == 0
        assert by_source["bilibili"].dropped == 1
        # 排序：总量最多的平台在前
        assert rows[0].source_type == "xiaohongshu"


def test_sources_filter(tmp_path, content_db):
    refill_db = tmp_path / "refill.db"
    with RefillQueue(refill_db, content_db) as queue:
        filtered = queue.fill_gaps(sources=("youtube",))
        assert filtered == {"youtube": 1}
        assert "xiaohongshu" not in filtered
"""refill_queue 中央队列：建表 / 灌入扫描 / 状态观测。

独立子库 refill.db 承担高频回补状态写，避免与阅读库 content.db 锁竞争
（借鉴 pool.db 独立子库既有决策）。``url`` 唯一 → 跨进程天然去重。

- 去向：从 content.db.articles 中 ``content_text`` 为空的一次性灌入，
  ``INSERT OR IGNORE`` 幂等；去重锚点是 content_text 本身。
- 本模块无网络/浏览器依赖，仅做队列与 DB，通道抓取在后续里程碑接入。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openbiliclaw.storage.database import open_db_conn

_SCHEMA = """
CREATE TABLE IF NOT EXISTS refill_queue (
  id            INTEGER PRIMARY KEY,
  source_type   TEXT NOT NULL,
  url           TEXT NOT NULL UNIQUE,
  title         TEXT DEFAULT '',
  state         TEXT NOT NULL DEFAULT 'pending',
  channel       TEXT,
  attempts      INTEGER DEFAULT 0,
  max_attempts  INTEGER DEFAULT 3,
  last_error    TEXT,
  fetched_len   INTEGER,
  created_at    TEXT,
  updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_refill_pick
  ON refill_queue(state, source_type, updated_at);
"""

# 判定「缺正文」的查询条件；与 design 缺口基线保持一致。
_GAP_CONDITION = "(content_text IS NULL OR length(content_text) = 0)"


@dataclass(frozen=True)
class StatusRow:
    """单来源平台的队列口径统计。"""

    source_type: str
    pending: int = 0
    done: int = 0
    skipped: int = 0
    dropped: int = 0

    @property
    def total(self) -> int:
        return self.pending + self.done + self.skipped + self.dropped


# 队列行字典：scheduler / channel 消费的队项。见 RefillQueue.pending_items。
RefillItem = dict[str, Any]


class RefillQueue:
    """refill_queue 中央队列读写入口。"""

    def __init__(self, db_path: str | Path, content_db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.content_db_path = Path(content_db_path)
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        """懒加载写连接（open_db_conn 已设 WAL / busy_timeout / 兄弟子库 ATTACH）。"""
        if self._conn is None:
            self._conn = open_db_conn(self.db_path)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def __enter__(self) -> RefillQueue:
        self.ensure_schema()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def ensure_schema(self) -> None:
        """幂等建表/索引。"""
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def count_missing(self) -> dict[str, int]:
        """扫描 content.db：各平台当前「缺正文」条数（实时快照，与队列解耦）。

        该值是补抓的真实欠账基线；与队列已入队数并列，可看出「已补但待清队」
        的滞后。返回按缺量降序。
        """
        content = open_db_conn(self.content_db_path)
        try:
            rows = content.execute(
                f"""SELECT source_type, COUNT(*) AS n FROM articles
                    WHERE {_GAP_CONDITION}
                    GROUP BY source_type ORDER BY n DESC"""
            ).fetchall()
            return {str(r["source_type"]): int(r["n"]) for r in rows}
        finally:
            content.close()

    def fill_gaps(
        self,
        *,
        limit_per_source: int | None = None,
        sources: tuple[str, ...] | None = None,
    ) -> dict[str, int]:
        """把 content.db 中「缺正文」的条目增量灌入 refill_queue。

        幂等：以 ``url`` 唯一键 ``INSERT OR IGNORE``，重复调用不重复入队。
        ``limit_per_source``：每个平台本次灌入的行数上限（None 表示不限）。
        ``sources``：限定扫描的平台。返回值：各平台本次实际新入队数。
        """
        content = open_db_conn(self.content_db_path)
        try:
            sql = (
                f"SELECT url, source_type, "
                f"COALESCE(title, '') AS title FROM articles "
                f"WHERE {_GAP_CONDITION}"
            )
            params: tuple[Any, ...] = ()
            if sources:
                placeholders = ",".join("?" for _ in sources)
                sql += f" AND source_type IN ({placeholders})"
                params = tuple(sources)

            with content:
                rows = content.execute(sql, params).fetchall()

            by_source: dict[str, list[tuple[str, str]]] = {}
            for r in rows:
                by_source.setdefault(str(r["source_type"]), []).append(
                    (str(r["url"]), str(r["title"]))
                )

            inserted: dict[str, int] = {}
            for source_type, items in by_source.items():
                if limit_per_source is not None and len(items) > limit_per_source:
                    items = items[:limit_per_source]
                before = self._count_by_source(source_type)
                if items:
                    self.conn.executemany(
                        """INSERT OR IGNORE INTO refill_queue
                           (source_type, url, title, created_at, updated_at)
                           VALUES (?, ?, ?, datetime('now','localtime'),
                                   datetime('now','localtime'))""",
                        [(source_type, url, title) for url, title in items],
                    )
                    self.conn.commit()
                after = self._count_by_source(source_type)
                inserted[source_type] = max(0, after - before)
            return inserted
        finally:
            content.close()

    def _count_by_source(self, source_type: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM refill_queue WHERE source_type = ?",
            (source_type,),
        ).fetchone()
        return int(row["n"]) if row else 0

    def pending_items(
        self,
        *,
        sources: tuple[str, ...] | None = None,
        limit_per_source: int | None = None,
    ) -> dict[str, list[RefillItem]]:
        """取各平台当前 ``pending`` 队项，按 ``ORDER BY RANDOM()`` 打散（避免
        卡单条过期 token 死循环，镜像 ``backfill.py``）。返回 ``{source: [item]}``。

        ``item`` 包含 id/source_type/url/title/attempts/max_attempts/state/channel。
        """
        sql = "SELECT id, source_type, url, title, attempts, max_attempts FROM refill_queue WHERE state = 'pending'"
        params: tuple[Any, ...] = ()
        if sources:
            placeholders = ",".join("?" for _ in sources)
            sql += f" AND source_type IN ({placeholders})"
            params = tuple(sources)
        sql += " ORDER BY RANDOM()"
        rows = self.conn.execute(sql, params).fetchall()
        out: dict[str, list[RefillItem]] = {}
        for r in rows:
            key = str(r["source_type"])
            if limit_per_source is not None and len(out.get(key, [])) >= limit_per_source:
                continue
            out.setdefault(key, []).append(
                {
                    "id": int(r["id"]),
                    "source_type": key,
                    "url": str(r["url"]),
                    "title": str(r["title"]),
                    "attempts": int(r["attempts"]),
                    "max_attempts": int(r["max_attempts"]),
                }
            )
        return out

    def mark_done(self, row_id: int, channel_name: str, fetched_len: int) -> None:
        """队项回补成功：写正文的判定由调用方（scheduler）完成，此处仅收口队列。"""
        self.conn.execute(
            """UPDATE refill_queue SET state='done', channel=?, fetched_len=?,
               last_error=NULL, updated_at=datetime('now','localtime') WHERE id=?""",
            (channel_name, fetched_len, row_id),
        )
        self.conn.commit()

    def mark_attempt(
        self,
        row_id: int,
        channel_name: str,
        max_attempts: int,
        *,
        last_error: str = "",
    ) -> str:
        """回补失败：attempts+1；达到上限置 ``dropped``，否则保持 ``pending``。"""
        row = self.conn.execute("SELECT attempts FROM refill_queue WHERE id=?", (row_id,)).fetchone()
        attempts = (int(row["attempts"]) if row else 0) + 1
        if attempts >= max_attempts:
            self.conn.execute(
                """UPDATE refill_queue SET state='dropped', attempts=?, channel=?,
                   last_error=?, updated_at=datetime('now','localtime') WHERE id=?""",
                (attempts, channel_name, last_error, row_id),
            )
            self.conn.commit()
            return "dropped"
        self.conn.execute(
            """UPDATE refill_queue SET attempts=?, channel=?, last_error=?,
               updated_at=datetime('now','localtime') WHERE id=?""",
            (attempts, channel_name, last_error, row_id),
        )
        self.conn.commit()
        return "pending"

    def mark_skipped(self, row_id: int, channel_name: str, last_error: str = "") -> None:
        """永久不可抓（真无正文，如 YouTube 无字幕）：标 skipped，不消耗重试。"""
        self.conn.execute(
            """UPDATE refill_queue SET state='skipped', channel=?, last_error=?,
               updated_at=datetime('now','localtime') WHERE id=?""",
            (channel_name, last_error, row_id),
        )
        self.conn.commit()

    def reset_by_source(self, source_type: str | None = None, *, target: str = "dropped") -> int:
        """把 ``dropped``（已满重试）队项重置回 ``pending`` 并清零 attempts，
        可限定平台；返回被重置的行数。用于更换通道或配额调整后重启该平台回补。
        """
        sql = "UPDATE refill_queue SET state='pending', attempts=0, last_error=NULL, updated_at=datetime('now','localtime') WHERE state = ?"
        params: tuple[Any, ...] = (target,)
        if source_type:
            sql += " AND source_type = ?"
            params = (target, source_type)
        cursor = self.conn.execute(sql, params)
        self.conn.commit()
        return cursor.rowcount

    def status(self) -> list[StatusRow]:
        """按平台 + 状态聚合队列，返回稳定排序（入队总量最多的平台在前）。"""
        rows = self.conn.execute(
            """SELECT source_type, state, COUNT(*) AS n
               FROM refill_queue GROUP BY source_type, state"""
        ).fetchall()
        acc: dict[str, StatusRow] = {}
        for r in rows:
            st = str(r["source_type"])
            state = str(r["state"])
            n = int(r["n"])
            cur = acc.get(st, StatusRow(source_type=st))
            if state == "pending":
                cur = StatusRow(source_type=st, pending=cur.pending + n, done=cur.done,
                                skipped=cur.skipped, dropped=cur.dropped)
            elif state == "done":
                cur = StatusRow(source_type=st, pending=cur.pending, done=cur.done + n,
                                skipped=cur.skipped, dropped=cur.dropped)
            elif state == "skipped":
                cur = StatusRow(source_type=st, pending=cur.pending, done=cur.done,
                                skipped=cur.skipped + n, dropped=cur.dropped)
            elif state == "dropped":
                cur = StatusRow(source_type=st, pending=cur.pending, done=cur.done,
                                skipped=cur.skipped, dropped=cur.dropped + n)
            acc[st] = cur
        return sorted(acc.values(), key=lambda r: r.total, reverse=True)
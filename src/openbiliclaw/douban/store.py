"""豆瓣书影音持久化存储。

以独立 ``douban.db``（默认 ``data/douban.db``）存放用户从豆瓣抓取的书影音
清单（影视/书/音乐 × 看过/想看/在看）。与原项目主库/推荐池锁域隔离。

每次读写使用独立短连接，遵循项目的 SQLite 连接约束（timeout / check_same_thread
=False / WAL / busy_timeout / synchronous=NORMAL），与 ``media/store.py`` 一致。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS douban_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,        -- movie / book / music
    status TEXT NOT NULL,          -- collect / wish / do
    name TEXT NOT NULL,
    url TEXT DEFAULT '',
    date TEXT DEFAULT '',
    comment TEXT DEFAULT '',
    rating TEXT DEFAULT '',        -- movie/book 星级（有/无）
    pub TEXT DEFAULT '',           -- book 作者/出版社
    intro TEXT DEFAULT '',         -- music 简介
    imported_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_douban_items_cat_status ON douban_items (category, status);
"""


class DoubanStore:
    """豆瓣书影音独立库存储。"""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self._db_path),
            timeout=5.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    # ── 写 ─────────────────────────────────────────────────────
    def import_items(self, items: list[dict]) -> dict:
        """批量导入条目，按 url 去重（同 url 更新，新 url 插入）。

        item 支持字段：category/status/name/url/date/comment/rating/pub/intro。
        返回 {inserted, updated, skipped}。
        """
        inserted = updated = skipped = 0
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._connect() as conn:
            for it in items:
                url = (it.get("url") or "").strip()
                if not url:
                    skipped += 1
                    continue
                cur = conn.execute(
                    "SELECT id FROM douban_items WHERE url=?", (url,)
                ).fetchone()
                vals = (
                    it.get("category", ""),
                    it.get("status", "collect"),
                    it.get("name", ""),
                    url,
                    it.get("date", ""),
                    it.get("comment", ""),
                    it.get("rating", ""),
                    it.get("pub", ""),
                    it.get("intro", ""),
                    now,
                )
                if cur is None:
                    conn.execute(
                        "INSERT INTO douban_items"
                        "(category,status,name,url,date,comment,rating,pub,intro,imported_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        vals,
                    )
                    inserted += 1
                else:
                    conn.execute(
                        "UPDATE douban_items SET category=?,status=?,name=?,url=?,date=?,"
                        "comment=?,rating=?,pub=?,intro=?,imported_at=? WHERE id=?",
                        (*vals, cur["id"]),
                    )
                    updated += 1
            conn.commit()
        return {"inserted": inserted, "updated": updated, "skipped": skipped}

    def clear(self) -> None:
        """清空全部条目（重新导入用）。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM douban_items")
            conn.commit()

    # ── 读 ─────────────────────────────────────────────────────
    def count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM douban_items").fetchone()
        return int(row["c"]) if row else 0

    def list_items(
        self,
        category: str | None = None,
        status: str | None = None,
        search: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict]:
        """按分类/状态/关键词筛选，按导入时间倒序。"""
        where: list[str] = []
        params: list = []  # str | int — sqlite 参数，默认推断可能与搜索字符串混合
        if category:
            where.append("category=?")
            params.append(category)
        if status:
            where.append("status=?")
            params.append(status)
        if search:
            where.append("name LIKE ? OR pub LIKE ? OR intro LIKE ?")
            like = f"%{search}%"
            params.extend([like, like, like])
        sql = "SELECT * FROM douban_items"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY imported_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        """返回 分类×状态 计数概览。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT category, status, COUNT(*) AS c FROM douban_items "
                "GROUP BY category, status"
            ).fetchall()
        cats: dict[str, dict[str, int]] = {}
        total = 0
        for r in rows:
            c, s = str(r["category"]), str(r["status"])
            cats.setdefault(c, {})[s] = int(r["c"])
            total += int(r["c"])
        return {"categories": cats, "total": total}

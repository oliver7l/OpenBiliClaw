"""克隆系统存储层：SQLite 数据持久化。

独立管理克隆站点相关数据表，提供 CRUD、检索、统计等底层操作。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
from typing import TYPE_CHECKING

from .models import CloneSite, CloneSiteCreate, CloneSiteUpdate, CloneStats

if TYPE_CHECKING:
    from ..storage.database import Database

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS clone_sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    source_url TEXT DEFAULT '',
    local_path TEXT NOT NULL,
    description TEXT DEFAULT '',
    category TEXT DEFAULT 'other',
    status TEXT DEFAULT 'cloned'
        CHECK (status IN ('cloned', 'cloning', 'failed', 'moved', 'archived')),
    size_bytes INTEGER DEFAULT 0,
    file_count INTEGER DEFAULT 0,
    tags TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_clone_sites_category ON clone_sites(category);
CREATE INDEX IF NOT EXISTS idx_clone_sites_status ON clone_sites(status);
CREATE INDEX IF NOT EXISTS idx_clone_sites_slug ON clone_sites(slug);

CREATE TABLE IF NOT EXISTS clone_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_clone_tags_count ON clone_tags(count DESC);
"""


class CloneStore:
    """克隆站点数据存储。

    可以传入已有的 Database 实例复用连接，也可以独立使用。
    """

    def __init__(self, database: Database | None = None) -> None:
        self._database = database
        self._thread_local = threading.local()
        self._initialized = False

    @property
    def conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接。"""
        if self._database is not None:
            return self._database.conn
        if not hasattr(self._thread_local, "conn") or self._thread_local.conn is None:
            raise RuntimeError("CloneStore: no database connection available")
        return self._thread_local.conn

    def initialize(self) -> None:
        """初始化克隆站点数据表。"""
        if self._initialized:
            return
        self.conn.executescript(_SCHEMA_SQL)
        self.conn.commit()
        self._initialized = True

    # ── 站点 CRUD ──────────────────────────────────────────────

    def create_site(self, data: CloneSiteCreate) -> CloneSite:
        """创建一条克隆站点记录。"""
        self.initialize()
        now = datetime.now()
        tags_json = json.dumps(data.tags, ensure_ascii=False)
        cursor = self.conn.execute(
            """
            INSERT INTO clone_sites (name, slug, source_url, local_path, description,
                                     category, status, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.name,
                data.slug,
                data.source_url,
                data.local_path,
                data.description,
                data.category,
                data.status.value,
                tags_json,
                now,
                now,
            ),
        )
        self.conn.commit()
        return self._row_to_site(
            {
                "id": cursor.lastrowid,
                "name": data.name,
                "slug": data.slug,
                "source_url": data.source_url,
                "local_path": data.local_path,
                "description": data.description,
                "category": data.category,
                "status": data.status.value,
                "size_bytes": 0,
                "file_count": 0,
                "tags": tags_json,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
        )

    def get_site(self, site_id: int) -> CloneSite | None:
        """根据 ID 获取站点。"""
        self.initialize()
        row = self.conn.execute("SELECT * FROM clone_sites WHERE id = ?", (site_id,)).fetchone()
        return self._row_to_site(row) if row else None

    def get_site_by_slug(self, slug: str) -> CloneSite | None:
        """根据 slug 获取站点。"""
        self.initialize()
        row = self.conn.execute("SELECT * FROM clone_sites WHERE slug = ?", (slug,)).fetchone()
        return self._row_to_site(row) if row else None

    def list_sites(
        self,
        limit: int = 50,
        offset: int = 0,
        category: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        search: str | None = None,
    ) -> tuple[list[CloneSite], int]:
        """列出克隆站点，支持筛选和搜索。"""
        self.initialize()
        conditions = []
        params: list = []

        if category:
            conditions.append("category = ?")
            params.append(category)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if tag:
            conditions.append("tags LIKE ?")
            params.append(f'%"{tag}"%')
        if search:
            conditions.append("(name LIKE ? OR description LIKE ? OR source_url LIKE ?)")
            params.extend([f"%{search}%"] * 3)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        count_row = self.conn.execute(
            f"SELECT COUNT(*) FROM clone_sites {where}", params
        ).fetchone()
        total = int(count_row[0]) if count_row else 0

        rows = self.conn.execute(
            f"SELECT * FROM clone_sites {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

        sites = [self._row_to_site(r) for r in rows]
        return sites, total

    def update_site(self, site_id: int, data: CloneSiteUpdate) -> CloneSite | None:
        """更新站点信息。"""
        self.initialize()
        updates = []
        params: list = []

        for field in ("name", "description", "category", "status"):
            value = getattr(data, field, None)
            if value is not None:
                updates.append(f"{field} = ?")
                params.append(value.value if field == "status" else value)

        if data.tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(data.tags, ensure_ascii=False))

        if not updates:
            return self.get_site(site_id)

        updates.append("updated_at = ?")
        params.append(datetime.now())
        params.append(site_id)

        self.conn.execute(
            f"UPDATE clone_sites SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self.conn.commit()
        return self.get_site(site_id)

    def update_site_size(self, site_id: int, size_bytes: int, file_count: int) -> None:
        """更新站点文件大小和数量。"""
        self.initialize()
        self.conn.execute(
            "UPDATE clone_sites SET size_bytes = ?, file_count = ?, updated_at = ? WHERE id = ?",
            (size_bytes, file_count, datetime.now(), site_id),
        )
        self.conn.commit()

    def delete_site(self, site_id: int) -> bool:
        """删除站点记录。"""
        self.initialize()
        cursor = self.conn.execute("DELETE FROM clone_sites WHERE id = ?", (site_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    # ── 统计 ────────────────────────────────────────────────────

    def get_stats(self) -> CloneStats:
        """获取克隆系统统计信息。"""
        self.initialize()

        total = self.conn.execute("SELECT COUNT(*) FROM clone_sites").fetchone()
        total_sites = int(total[0]) if total else 0

        size = self.conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM clone_sites").fetchone()
        total_size = int(size[0]) if size else 0

        files = self.conn.execute("SELECT COALESCE(SUM(file_count), 0) FROM clone_sites").fetchone()
        total_files = int(files[0]) if files else 0

        cat_rows = self.conn.execute(
            "SELECT category, COUNT(*) FROM clone_sites GROUP BY category ORDER BY COUNT(*) DESC"
        ).fetchall()
        category_dist = {r[0]: int(r[1]) for r in cat_rows}

        status_rows = self.conn.execute(
            "SELECT status, COUNT(*) FROM clone_sites GROUP BY status"
        ).fetchall()
        status_dist = {r[0]: int(r[1]) for r in status_rows}

        tag_count = self.conn.execute("SELECT COUNT(*) FROM clone_tags").fetchone()
        total_tags = int(tag_count[0]) if tag_count else 0

        return CloneStats(
            total_sites=total_sites,
            total_categories=len(category_dist),
            total_tags=total_tags,
            total_size_bytes=total_size,
            total_files=total_files,
            category_distribution=category_dist,
            status_distribution=status_dist,
        )

    # ── 标签管理 ────────────────────────────────────────────────

    def list_tags(self) -> list[dict]:
        """列出所有标签。"""
        self.initialize()
        rows = self.conn.execute("SELECT * FROM clone_tags ORDER BY count DESC").fetchall()
        return [{"id": r[0], "name": r[1], "count": r[2]} for r in rows]

    def _sync_tags(self, tags: list[str]) -> None:
        """同步标签计数。"""
        for tag in tags:
            self.conn.execute(
                "INSERT INTO clone_tags (name, count) VALUES (?, 1) "
                "ON CONFLICT(name) DO UPDATE SET count = count + 1",
                (tag,),
            )

    # ── 内部方法 ────────────────────────────────────────────────

    @staticmethod
    def _row_to_site(row: sqlite3.Row | dict) -> CloneSite:
        if isinstance(row, sqlite3.Row):
            row = dict(row)
        raw_tags = row.get("tags", "[]")
        if isinstance(raw_tags, str):
            tags = json.loads(raw_tags) if raw_tags else []
        elif isinstance(raw_tags, (list, tuple)):
            tags = list(raw_tags)
        else:
            tags = []
        return CloneSite(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            source_url=row.get("source_url", ""),
            local_path=row.get("local_path", ""),
            description=row.get("description", ""),
            category=row.get("category", "other"),
            status=row.get("status", "cloned"),
            size_bytes=row.get("size_bytes", 0),
            file_count=row.get("file_count", 0),
            tags=tags,
            created_at=row.get("created_at", datetime.now()),
            updated_at=row.get("updated_at", datetime.now()),
        )

"""媒体收藏 / 评级持久化。

以独立 ``media_state.db``（默认 ``data/media_state.db``）存放用户对媒体文件
的收藏与评级。与主库/推荐池锁域隔离，避免低频繁的收藏写与推荐流写互相加锁。

按文件绝对路径的 md5 作为主键（文件被移动则视为新条目）。每次读写在独立
短连接上进行，遵循项目的 SQLite 连接约束（timeout / check_same_thread=False /
WAL / busy_timeout / synchronous=NORMAL）。
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS media_items (
    akey      TEXT PRIMARY KEY,          -- md5(绝对路径)
    root      TEXT NOT NULL,             -- 根目录绝对路径
    rel       TEXT NOT NULL,             -- 根目录内相对路径
    name      TEXT NOT NULL DEFAULT '',  -- 文件名（basename，便于展示）
    favorite  INTEGER NOT NULL DEFAULT 0,
    rating    INTEGER NOT NULL DEFAULT 0,  -- 0=未评级, 1-5 星
    updated_at INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_media_items_fav ON media_items (favorite, root);
"""


class MediaStateStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    # ── 连接（每次操作独立短连接，天然线程安全）─────────────────
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

    @staticmethod
    def _key(abs_path: str) -> str:
        return hashlib.md5(abs_path.encode("utf-8", "ignore")).hexdigest()

    def delete_state(self, abs_path: str) -> None:
        """删除一个文件的收藏/评级记录（文件被删除后清理）。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM media_items WHERE akey=?", (self._key(abs_path),))
            conn.commit()

    # ── 读 ─────────────────────────────────────────────────────
    def get_states(self, abs_paths: Iterable[str]) -> dict[str, dict[str, int]]:
        """按绝对路径批量读收藏/评级。返回 ``{abs_path: {favorite, rating}}``。"""
        paths = list(abs_paths)
        if not paths:
            return {}
        keys = [self._key(p) for p in paths]
        with self._connect() as conn:
            placeholders = ",".join("?" * len(keys))
            rows = conn.execute(
                f"SELECT akey, favorite, rating FROM media_items WHERE akey IN ({placeholders})",
                keys,
            ).fetchall()
        by_key = {r["akey"]: {"favorite": int(r["favorite"]), "rating": int(r["rating"])} for r in rows}
        return {p: by_key.get(self._key(p), {"favorite": 0, "rating": 0}) for p in paths}

    def get_state(self, abs_path: str) -> dict[str, int]:
        return self.get_states([abs_path])[abs_path]

    # ── 写 ─────────────────────────────────────────────────────
    def set_state(
        self,
        # 变量名以 abs_ 结尾避免与记忆中的异常名冲突
        abs_path_: str,
        root: str,
        rel: str,
        *,
        favorite: int | None = None,
        rating: int | None = None,
    ) -> dict[str, int]:
        key = self._key(abs_path_)
        name = Path(rel).name
        now = int(__import__("time").time())
        with self._connect() as conn:
            if favorite is None and rating is None:
                # 无字段更新：删除该条（从未有收藏也不报错）
                conn.execute("DELETE FROM media_items WHERE akey=?", (key,))
                conn.commit()
                return {"favorite": 0, "rating": 0}
            cur = conn.execute(
                "SELECT favorite, rating FROM media_items WHERE akey=?", (key,)
            ).fetchone()
            if cur is None:
                fav = 1 if favorite else 0
                rat = rating or 0
                conn.execute(
                    "INSERT INTO media_items(akey,root,rel,name,favorite,rating,updated_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (key, root, rel, name, fav, rat, now),
                )
            else:
                fav = cur["favorite"] if favorite is None else (1 if favorite else 0)
                rat = cur["rating"] if rating is None else (rating or 0)
                conn.execute(
                    "UPDATE media_items SET favorite=?, rating=?, root=?, rel=?, name=?, updated_at=? "
                    "WHERE akey=?",
                    (fav, rat, root, rel, name, now, key),
                )
            conn.commit()
        return {"favorite": fav, "rating": rat}

    # ── 收藏列表 ───────────────────────────────────────────────
    def favorites(self, root: str | None = None) -> list[dict[str, object]]:
        """返回收藏条目列表，按收藏/评级时间倒序。"""
        if root:
            rows: list[sqlite3.Row] = []
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT root, rel, name, favorite, rating FROM media_items "
                    "WHERE favorite=1 AND root=? ORDER BY updated_at DESC",
                    (root,),
                ).fetchall()
        else:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT root, rel, name, favorite, rating FROM media_items "
                    "WHERE favorite=1 ORDER BY updated_at DESC"
                ).fetchall()
        out: list[dict[str, object]] = []
        for r in rows:
            rel = str(r["rel"])
            name = str(r["name"]) or Path(rel).name
            out.append(
                {
                    "root": str(r["root"]),
                    "rel": rel,
                    "name": name,
                    "kind": _kind_by_name(name),
                    "favorite": int(r["favorite"]),
                    "rating": int(r["rating"]),
                }
            )
        return out


def _kind_by_name(name: str) -> str:
    from openbiliclaw.media.service import IMAGE_EXTS

    ext = Path(name).suffix.lower()
    return "image" if ext in IMAGE_EXTS else ("video" if ext else "video")

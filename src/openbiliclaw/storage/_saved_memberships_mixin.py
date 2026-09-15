"""Database mixin: saved memberships (favorites / watch_later).

Contains methods for managing normalized saved list memberships with
native-sync state, including count/get/list/upsert/remove operations.
"""

from __future__ import annotations

from typing import Any


class SavedMembershipsMixin:
    """Database methods for saved memberships (favorites/watch_later)."""

    conn: Any
    _execute_write: Any

    @staticmethod
    def _saved_list_kind(value: str) -> str:
        if value not in {"favorite", "watch_later"}:
            raise ValueError(f"invalid saved list kind: {value}, expected favorite/watch_later")
        return value

    def count_saved_memberships(self, list_kind: str) -> int:
        """Count items in a saved list."""
        normalized_kind = self._saved_list_kind(list_kind)
        self._ensure_fresh_read()
        row = self.conn.execute(
            "SELECT COUNT(*) FROM saved_memberships WHERE list_kind = ?",
            (normalized_kind,),
        ).fetchone()
        return int(row[0] if row is not None else 0)

    def get_saved_membership(self, list_kind: str, item_key: str) -> dict[str, Any] | None:
        """Return one normalized membership with its current native-sync state."""
        normalized_kind = self._saved_list_kind(list_kind)
        self._ensure_fresh_read()
        row = self.conn.execute(
            """
            SELECT
                m.list_kind,
                i.item_key,
                i.source_platform,
                i.content_id,
                i.content_url,
                i.content_type,
                COALESCE(NULLIF(i.title, ''), (
                    SELECT cc.title FROM content_cache cc
                    WHERE cc.bvid = i.content_id OR cc.content_id = i.content_id
                    LIMIT 1
                ), i.title) AS title,
                COALESCE(NULLIF(i.author_name, ''), (
                    SELECT COALESCE(NULLIF(cc.up_name, ''), cc.author_name)
                    FROM content_cache cc
                    WHERE cc.bvid = i.content_id OR cc.content_id = i.content_id
                    LIMIT 1
                ), i.author_name) AS author_name,
                COALESCE(NULLIF(i.cover_url, ''), (
                    SELECT cc.cover_url FROM content_cache cc
                    WHERE cc.bvid = i.content_id OR cc.content_id = i.content_id
                    LIMIT 1
                ), '') AS cover_url,
                i.created_at,
                i.updated_at,
                m.note,
                m.added_at,
                COALESCE(n.requested_action, '') AS requested_action,
                COALESCE(n.resolved_action, '') AS resolved_action,
                COALESCE(n.resolved_target, '') AS resolved_target,
                COALESCE(n.status, 'pending') AS sync_status,
                COALESCE(n.task_id, '') AS sync_task_id,
                COALESCE(n.last_error_code, '') AS last_error_code,
                COALESCE(n.last_error_message, '') AS last_error_message,
                n.last_attempt_at,
                n.synced_at
            FROM saved_memberships AS m
            JOIN saved_items AS i ON i.item_key = m.item_key
            LEFT JOIN native_save_states AS n
                ON n.list_kind = m.list_kind AND n.item_key = m.item_key
            WHERE m.list_kind = ? AND m.item_key = ?
            """,
            (normalized_kind, item_key.strip()),
        ).fetchone()
        return dict(row) if row is not None else None

    def list_saved_memberships(
        self,
        list_kind: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List normalized memberships newest first with native-sync state."""
        normalized_kind = self._saved_list_kind(list_kind)
        self._ensure_fresh_read()
        rows = self.conn.execute(
            """
            SELECT
                m.list_kind,
                i.item_key,
                i.source_platform,
                i.content_id,
                i.content_url,
                i.content_type,
                COALESCE(NULLIF(i.title, ''), (
                    SELECT cc.title FROM content_cache cc
                    WHERE cc.bvid = i.content_id OR cc.content_id = i.content_id
                    LIMIT 1
                ), i.title) AS title,
                COALESCE(NULLIF(i.author_name, ''), (
                    SELECT COALESCE(NULLIF(cc.up_name, ''), cc.author_name)
                    FROM content_cache cc
                    WHERE cc.bvid = i.content_id OR cc.content_id = i.content_id
                    LIMIT 1
                ), i.author_name) AS author_name,
                COALESCE(NULLIF(i.cover_url, ''), (
                    SELECT cc.cover_url FROM content_cache cc
                    WHERE cc.bvid = i.content_id OR cc.content_id = i.content_id
                    LIMIT 1
                ), '') AS cover_url,
                i.created_at,
                i.updated_at,
                m.note,
                m.added_at,
                COALESCE(n.requested_action, '') AS requested_action,
                COALESCE(n.resolved_action, '') AS resolved_action,
                COALESCE(n.resolved_target, '') AS resolved_target,
                COALESCE(n.status, 'pending') AS sync_status,
                COALESCE(n.task_id, '') AS sync_task_id,
                COALESCE(n.last_error_code, '') AS last_error_code,
                COALESCE(n.last_error_message, '') AS last_error_message,
                n.last_attempt_at,
                n.synced_at
            FROM saved_memberships AS m
            JOIN saved_items AS i ON i.item_key = m.item_key
            LEFT JOIN native_save_states AS n
                ON n.list_kind = m.list_kind AND n.item_key = m.item_key
            WHERE m.list_kind = ?
            ORDER BY m.added_at DESC, m.item_key ASC
            LIMIT ? OFFSET ?
            """,
            (normalized_kind, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]

    def upsert_saved_membership(
        self,
        list_kind: str,
        item: Any,  # SavedItemInput
        note: str = "",
    ) -> dict[str, Any]:
        """Atomically upsert an item snapshot and its local list membership."""
        normalized_kind = self._saved_list_kind(list_kind)
        item_key = item.item_key
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO saved_items (
                    item_key, source_platform, content_id, content_url, content_type,
                    title, author_name, cover_url
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_key) DO UPDATE SET
                    source_platform = excluded.source_platform,
                    content_id = excluded.content_id,
                    content_url = excluded.content_url,
                    content_type = excluded.content_type,
                    title = excluded.title,
                    author_name = excluded.author_name,
                    cover_url = excluded.cover_url,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    item_key,
                    item.platform,
                    item.content_id.strip(),
                    item.content_url.strip(),
                    item.content_type.strip() or "video",
                    item.title.strip(),
                    item.author_name.strip(),
                    item.cover_url.strip(),
                ),
            )
            conn.execute(
                """
                INSERT INTO saved_memberships (list_kind, item_key, note)
                VALUES (?, ?, ?)
                ON CONFLICT(list_kind, item_key) DO UPDATE SET
                    note = excluded.note,
                    added_at = CURRENT_TIMESTAMP
                """,
                (normalized_kind, item_key, note),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self._ensure_fresh_read()
        row = self.get_saved_membership(normalized_kind, item_key)
        if row is None:
            raise RuntimeError("saved membership disappeared after upsert")
        return row

    def remove_saved_membership(self, list_kind: str, item_key: str) -> bool:
        """Remove a normalized membership and any matching legacy compatibility row."""
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_key = item_key.strip()
        legacy_table = "favorites" if normalized_kind == "favorite" else "watch_later"
        legacy_bvid = (
            normalized_key.removeprefix("bilibili:")
            if normalized_key.startswith("bilibili:")
            else None
        )
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            removed_snapshot = conn.execute(
                """
                SELECT m.list_kind, i.item_key, i.source_platform,
                       i.content_id, i.content_url, i.content_type,
                       i.title, i.author_name, i.cover_url
                FROM saved_memberships AS m
                JOIN saved_items AS i ON i.item_key = m.item_key
                WHERE m.list_kind = ? AND m.item_key = ?
                """,
                (normalized_kind, normalized_key),
            ).fetchone()
            active_state = conn.execute(
                """
                SELECT task_id
                FROM native_save_states
                WHERE list_kind = ? AND item_key = ?
                  AND status IN ('pending', 'syncing') AND task_id != ''
                """,
                (normalized_kind, normalized_key),
            ).fetchone()
            if active_state is not None:
                conn.execute(
                    """
                    UPDATE native_save_task_items
                    SET status = 'failed', is_live = 0,
                        last_error_code = 'not_saved_locally',
                        last_error_message = 'Item is not saved locally',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND item_key = ? AND is_live = 1
                      AND status IN ('pending', 'syncing')
                    """,
                    (str(active_state["task_id"]), normalized_key),
                )
            cursor = conn.execute(
                "DELETE FROM saved_memberships WHERE list_kind = ? AND item_key = ?",
                (normalized_kind, normalized_key),
            )
            removed = int(cursor.rowcount or 0) > 0
            if removed and removed_snapshot is not None:
                conn.execute(
                    """
                    INSERT INTO saved_item_removals (
                        list_kind, item_key, source_platform, content_id,
                            content_url, content_type, title, author_name, cover_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_kind,
                        normalized_key,
                        str(removed_snapshot["source_platform"] or ""),
                        str(removed_snapshot["content_id"] or ""),
                        str(removed_snapshot["content_url"] or ""),
                        str(removed_snapshot["content_type"] or "video"),
                        str(removed_snapshot["title"] or ""),
                        str(removed_snapshot["author_name"] or ""),
                        str(removed_snapshot["cover_url"] or ""),
                    ),
                )
            conn.execute(
                """
                DELETE FROM saved_item_removals
                WHERE removed_at < datetime('now', '-30 days')
                """,
            )
            # legacy 表只有 bvid 列（无 item_key）——此前按 item_key 删是死代码，
            # 任何 remove 都会在此 OperationalError（2026-09-15 修正）
            if legacy_bvid:
                legacy_cursor = conn.execute(
                    f"DELETE FROM {legacy_table} WHERE bvid = ?",
                    (legacy_bvid,),
                )
            else:
                legacy_cursor = conn.execute(f"DELETE FROM {legacy_table} WHERE 1 = 0")
            removed = removed or int(legacy_cursor.rowcount or 0) > 0
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self._ensure_fresh_read()
        return removed

    # ── Native save state sync ─────────────────────────────────────

    def ensure_native_save_state(
        self,
        list_kind: str,
        item_key: str,
        requested_action: str,
    ) -> dict[str, Any]:
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_key = item_key.strip()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT status, task_id
                FROM native_save_states
                WHERE list_kind = ? AND item_key = ?
                """,
                (normalized_kind, normalized_key),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO native_save_states (
                        list_kind, item_key, requested_action, resolved_action, resolved_target,
                        status, task_id, execution_id, last_error_code, last_error_message
                    ) VALUES (?, ?, ?, ?, ?, 'pending', '', '', '', '')
                    """,
                    (normalized_kind, normalized_key, requested_action, "", ""),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self._ensure_fresh_read()
        row = self.conn.execute(
            """
            SELECT status, task_id FROM native_save_states
            WHERE list_kind = ? AND item_key = ?
            """,
            (normalized_kind, normalized_key),
        ).fetchone()
        return dict(row) if row is not None else {"status": "pending", "task_id": ""}

    def upsert_native_save_state(
        self,
        list_kind: str,
        item_key: str,
        requested_action: str,
        resolved_action: str = "",
        resolved_target: str = "",
        status: str = "pending",
        task_id: str = "",
        execution_id: str = "",
        last_error_code: str = "",
        last_error_message: str = "",
    ) -> None:
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_key = item_key.strip()
        normalized_task_id = task_id.strip()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            membership = conn.execute(
                "SELECT 1 FROM saved_memberships WHERE list_kind = ? AND item_key = ?",
                (normalized_kind, normalized_key),
            ).fetchone()
            if membership is None:
                raise ValueError(
                    f"saved membership does not exist: {normalized_kind}/{normalized_key}"
                )
            conn.execute(
                """
                INSERT INTO native_save_states (
                    list_kind, item_key, requested_action, resolved_action, resolved_target,
                    status, task_id, execution_id, last_error_code, last_error_message,
                    last_attempt_at, synced_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    CASE WHEN ? = 'pending' THEN NULL ELSE CURRENT_TIMESTAMP END,
                    CASE WHEN ? IN ('synced', 'already_synced')
                        THEN CURRENT_TIMESTAMP ELSE NULL END
                )
                ON CONFLICT(list_kind, item_key) DO UPDATE SET
                    requested_action = excluded.requested_action,
                    resolved_action = excluded.resolved_action,
                    resolved_target = excluded.resolved_target,
                    status = excluded.status,
                    task_id = excluded.task_id,
                    execution_id = excluded.execution_id,
                    last_error_code = excluded.last_error_code,
                    last_error_message = excluded.last_error_message,
                    last_attempt_at = CASE
                        WHEN excluded.status = 'pending' THEN native_save_states.last_attempt_at
                        ELSE CURRENT_TIMESTAMP
                    END,
                    synced_at = CASE
                        WHEN excluded.status IN ('synced', 'already_synced')
                            THEN CURRENT_TIMESTAMP
                        ELSE native_save_states.synced_at
                    END
                """,
                (
                    normalized_kind,
                    normalized_key,
                    requested_action,
                    resolved_action,
                    resolved_target,
                    status,
                    normalized_task_id,
                    execution_id,
                    last_error_code,
                    last_error_message,
                    status,
                    status,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self._ensure_fresh_read()

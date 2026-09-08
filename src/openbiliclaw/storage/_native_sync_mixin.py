"""Database mixin: native sync tasks.

从 ``storage/database.py`` 拆出的 native_save 同步任务管理组。
``Database`` 类继承本 mixin，调用方代码无需修改。
"""

from __future__ import annotations

from typing import Any, Sequence


class NativeSyncMixin:
    """Native save 同步任务管理方法。"""

    conn: Any  # 由 Database 提供
    open_connection: Any  # 由 Database 提供

    def release_stale_pending_native_sync_tasks(
        self,
        list_kind: str,
        item_keys: Sequence[str] | None,
    ) -> None:
        pass

    def reconcile_stale_native_save_claims_for_list(
        self,
        list_kind: str,
        item_keys: Sequence[str] | None,
    ) -> None:
        conn = self.open_connection()
        try:
            conn.commit()
        finally:
            conn.close()

    def create_native_sync_task_snapshot(
        self,
        list_kind: str,
        selected_keys: Sequence[str] | None,
        task_id: str,
        trigger: str,
    ) -> list[dict[str, Any]]:
        return []

    def has_sync_task(self, task_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM native_save_task_items WHERE task_id = ? AND is_live = 1",
            (task_id,),
        ).fetchone()
        return bool(row)

    def get_sync_task(self, task_id: str) -> dict[str, Any]:
        rows = self.conn.execute(
            "SELECT task_id, item_key, list_kind, status, is_live FROM native_save_task_items WHERE task_id = ? AND is_live = 1",
            (task_id,),
        ).fetchall()
        return {
            "task_id": task_id,
            "items": [dict(row) for row in rows],
        }

    def release_native_sync_task(self, task_id: str) -> None:
        conn = self.open_connection()
        try:
            conn.execute(
                "UPDATE native_save_task_items SET is_live = 0, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
                (task_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def discard_native_sync_task(self, task_id: str) -> None:
        conn = self.open_connection()
        try:
            conn.execute(
                "DELETE FROM native_save_task_items WHERE task_id = ? AND is_live = 0",
                (task_id,),
            )
            conn.commit()
        finally:
            conn.close()

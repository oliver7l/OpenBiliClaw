"""Database mixin: native save 同步任务的持久化状态机。

从 ``storage/database.py`` 拆出的 native_save 同步任务管理组。
``Database`` 类继承本 mixin，调用方代码无需修改。

表布局（schema 见 ``_schema_mixin``）：
- ``native_save_tasks``      每个任务一行：runner 认领（runner_id）+ 心跳。
- ``native_save_task_items`` 每个任务×条目一行：任务内镜像（is_live 標记有效性）。
- ``native_save_states``     每个条目一行（正本状态机）：status 生命周期
  pending → syncing → synced / failed / unsupported / ...；task_id + execution_id
  记录当前认领方，last_attempt_at 兼作 claim 心跳。

状态机约定（与 ``saved_sync/service.py`` 的调用契约一致）：
- runner 认领：``claim_native_sync_task_runner``（心跳停 90s 视为死亡，可被重新认领）
- 条目认领：``claim_native_save_item``（仅 pending 可认领；execution_id 标识本次执行）
- claim 心跳：``heartbeat_native_save_claim``（刷新 last_attempt_at；返回 0 = 失去认领）
- 完成：``complete_native_save_claim``（仅 execution_id 匹配才生效）
- 僵尸清理：``reconcile_stale_native_save_claims*``（syncing 且 last_attempt_at
  超过阈值 → failed/claim_stale，允许重试）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

# runner 心跳间隔 30s（service 默认），90s 无心跳视为 runner 死亡
_RUNNER_STALE_SECONDS = 90
# claim 心跳 30s + adapter 超时 240s，取 2 倍余量
_CLAIM_STALE_SECONDS = 600


class NativeSyncMixin:
    """Native save 同步任务管理方法。"""

    conn: Any  # 由 Database 提供
    open_connection: Any  # 由 Database 提供
    _ensure_fresh_read: Any  # 由 Database 提供

    # ── runner（任务级）认领与心跳 ─────────────────────────────

    def claim_native_sync_task_runner(self, task_id: str, runner_id: str) -> bool:
        """认领任务的执行权。返回 False 表示已被其他存活 runner 持有。"""
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            # 先回收僵尸 runner（心跳超时）
            conn.execute(
                """
                UPDATE native_save_tasks SET state = 'released'
                WHERE state = 'running'
                  AND (heartbeat_at IS NULL
                       OR heartbeat_at < datetime('now', ?))
                """,
                (f"-{_RUNNER_STALE_SECONDS} seconds",),
            )
            cur = conn.execute(
                """
                UPDATE native_save_tasks
                   SET runner_id = ?, state = 'running', heartbeat_at = CURRENT_TIMESTAMP
                 WHERE task_id = ?
                   AND (state != 'running' OR runner_id = ?)
                """,
                (runner_id, task_id, runner_id),
            )
            if cur.rowcount == 0:
                # 任务行不存在（老数据/零条目批次）→ 直接插入认领
                try:
                    conn.execute(
                        """
                        INSERT INTO native_save_tasks (task_id, runner_id, state, heartbeat_at)
                        VALUES (?, ?, 'running', CURRENT_TIMESTAMP)
                        """,
                        (task_id, runner_id),
                    )
                except Exception:
                    conn.rollback()
                    return False
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return True

    def heartbeat_native_sync_task(self, task_id: str, runner_id: str) -> int:
        """刷新 runner 心跳。返回 0 表示已失去执行权（调用方应中止）。"""
        cur = self.conn.execute(
            """
            UPDATE native_save_tasks SET heartbeat_at = CURRENT_TIMESTAMP
             WHERE task_id = ? AND runner_id = ? AND state = 'running'
            """,
            (task_id, runner_id),
        )
        self.conn.commit()
        return int(getattr(cur, "rowcount", 0) or 0)

    def release_pending_native_sync_task(self, task_id: str, runner_id: str) -> None:
        """runner 结束时调用：释放执行权，未完成的条目镜像退回 pending。"""
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE native_save_tasks SET state = 'released'
                 WHERE task_id = ? AND runner_id = ?
                """,
                (task_id, runner_id),
            )
            # 本任务未完成条目：正本与镜像一起退回 pending（可重试）
            conn.execute(
                """
                UPDATE native_save_states SET status = 'pending', execution_id = ''
                 WHERE task_id = ? AND status = 'syncing'
                """,
                (task_id,),
            )
            conn.execute(
                """
                UPDATE native_save_task_items SET status = 'pending', updated_at = CURRENT_TIMESTAMP
                 WHERE task_id = ? AND status = 'syncing'
                """,
                (task_id,),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def release_native_sync_task(self, task_id: str) -> None:
        conn = self.open_connection()
        try:
            conn.execute(
                "UPDATE native_save_task_items SET is_live = 0, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
                (task_id,),
            )
            conn.execute(
                "UPDATE native_save_tasks SET state = 'released' WHERE task_id = ?",
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
            conn.execute("DELETE FROM native_save_tasks WHERE task_id = ?", (task_id,))
            conn.commit()
        finally:
            conn.close()

    # ── 僵尸清理（reconcile / release stale）───────────────────

    def reconcile_stale_native_save_claims(self, task_id: str) -> None:
        """把本任务中 claim 心跳超时的 syncing 条目标记为 failed（可重试）。"""
        self._reconcile_stale_claims(task_id=task_id)

    def reconcile_stale_native_save_claims_for_list(
        self,
        list_kind: str,
        item_keys: Sequence[str] | None,
    ) -> None:
        """同上，但按 list（可选限定条目键）过滤——建新任务前清理旧残留。"""
        self._reconcile_stale_claims(list_kind=list_kind, item_keys=item_keys)

    def _reconcile_stale_claims(
        self,
        *,
        task_id: str | None = None,
        list_kind: str | None = None,
        item_keys: Sequence[str] | None = None,
    ) -> None:
        conditions = ["s.status = 'syncing'", "s.last_attempt_at IS NOT NULL"]
        params: list[Any] = []
        if task_id is not None:
            conditions.append("s.task_id = ?")
            params.append(task_id)
        if list_kind is not None:
            conditions.append("s.list_kind = ?")
            params.append(list_kind)
        if item_keys:
            keys = [k.strip() for k in item_keys if k and k.strip()]
            if keys:
                conditions.append(
                    f"s.item_key IN ({', '.join('?' for _ in keys)})"
                )
                params.extend(keys)
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                f"""
                UPDATE native_save_states AS s
                   SET status = 'failed',
                       last_error_code = 'claim_stale',
                       last_error_message = 'Native save claim expired without completion'
                 WHERE {' AND '.join(conditions)}
                   AND s.last_attempt_at < datetime('now', ?)
                """,
                (*params, f"-{_CLAIM_STALE_SECONDS} seconds"),
            )
            conn.execute(
                """
                UPDATE native_save_task_items AS t
                   SET status = 'failed', updated_at = CURRENT_TIMESTAMP
                 WHERE t.status = 'syncing'
                   AND EXISTS (
                       SELECT 1 FROM native_save_states s
                        WHERE s.list_kind = t.list_kind AND s.item_key = t.item_key
                          AND s.status = 'failed'
                          AND s.last_error_code = 'claim_stale'
                   )
                """
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def release_stale_pending_native_sync_tasks(
        self,
        list_kind: str,
        item_keys: Sequence[str] | None,
    ) -> None:
        """建新任务前：释放同 list（可选限定条目）上心跳已死的 runner。"""
        conditions = ["t.state = 'running'", "t.heartbeat_at IS NOT NULL"]
        params: list[Any] = []
        sub = "SELECT DISTINCT task_id FROM native_save_task_items WHERE list_kind = ?"
        params.append(list_kind)
        if item_keys:
            keys = [k.strip() for k in item_keys if k and k.strip()]
            if keys:
                sub += f" AND item_key IN ({', '.join('?' for _ in keys)})"
                params.extend(keys)
        conditions.append(f"t.task_id IN ({sub})")
        conn = self.open_connection()
        try:
            conn.execute(
                f"""
                UPDATE native_save_tasks AS t SET state = 'released'
                 WHERE {' AND '.join(conditions)}
                   AND t.heartbeat_at < datetime('now', ?)
                """,
                (*params, f"-{_RUNNER_STALE_SECONDS} seconds"),
            )
            conn.commit()
        finally:
            conn.close()

    def release_stale_pending_native_sync_task(self, task_id: str) -> None:
        """读取任务结果前：若 runner 已死，先释放其执行权。"""
        conn = self.open_connection()
        try:
            conn.execute(
                """
                UPDATE native_save_tasks SET state = 'released'
                 WHERE task_id = ? AND state = 'running'
                   AND (heartbeat_at IS NULL
                        OR heartbeat_at < datetime('now', ?))
                """,
                (task_id, f"-{_RUNNER_STALE_SECONDS} seconds"),
            )
            conn.commit()
        finally:
            conn.close()

    # ── 任务快照与查询 ─────────────────────────────────────────

    def create_native_sync_task_snapshot(
        self,
        list_kind: str,
        selected_keys: Sequence[str] | None,
        task_id: str,
        trigger: str,
    ) -> list[dict[str, Any]]:
        """为新任务落库：任务行 + 条目镜像 + 状态行，返回快照行（供结果重构）。"""
        del trigger  # 预留：审计字段
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO native_save_tasks (task_id, runner_id, state)
                VALUES (?, '', 'pending')
                ON CONFLICT(task_id) DO UPDATE SET state = 'pending'
                """,
                (task_id,),
            )
            key_filter = ""
            params: list[Any] = [list_kind]
            if selected_keys:
                keys = [k.strip() for k in selected_keys if k and k.strip()]
                if keys:
                    key_filter = f" AND m.item_key IN ({', '.join('?' for _ in keys)})"
                    params.extend(keys)
            rows = conn.execute(
                f"""
                SELECT m.item_key, m.list_kind, s.requested_action,
                       s.resolved_action, s.resolved_target, s.status,
                       s.last_error_code, s.last_error_message
                  FROM saved_memberships m
                  LEFT JOIN native_save_states s
                         ON s.list_kind = m.list_kind AND s.item_key = m.item_key
                 WHERE m.list_kind = ?{key_filter}
                """,
                params,
            ).fetchall()
            snapshot: list[dict[str, Any]] = []
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO native_save_states (
                        list_kind, item_key, requested_action, resolved_action, resolved_target,
                        status, task_id, execution_id, last_error_code, last_error_message
                    ) VALUES (?, ?, ?, '', '', 'pending', '', '', '', '')
                    ON CONFLICT(list_kind, item_key) DO NOTHING
                    """,
                    (list_kind, row["item_key"], list_kind),
                )
                conn.execute(
                    """
                    INSERT INTO native_save_task_items (
                        task_id, item_key, list_kind, status, is_live,
                        last_error_code, last_error_message
                    ) VALUES (?, ?, ?, 'pending', 1, '', '')
                    ON CONFLICT(task_id, item_key) DO UPDATE SET
                        is_live = 1, status = 'pending', updated_at = CURRENT_TIMESTAMP
                    """,
                    (task_id, row["item_key"], list_kind),
                )
                snapshot.append(
                    {
                        "item_key": row["item_key"],
                        "requested_action": row["requested_action"] or list_kind,
                        "resolved_action": row["resolved_action"] or "",
                        "resolved_target": row["resolved_target"] or "",
                        "status": "pending",
                        "last_error_code": "",
                        "last_error_message": "",
                        "is_live": 1,
                    }
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self._ensure_fresh_read()
        return snapshot

    def native_sync_task_exists(self, task_id: str) -> bool:
        """任务是否存在（含零条目批次——任务行本身即证据）。"""
        row = self.conn.execute(
            "SELECT 1 FROM native_save_tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return bool(row)

    def list_native_sync_task_items(self, task_id: str) -> list[dict[str, Any]]:
        """任务条目视图（states 为正本，task_items 提供成员资格）。"""
        rows = self.conn.execute(
            """
            SELECT t.item_key,
                   COALESCE(s.status, t.status) AS status,
                   COALESCE(s.requested_action, '') AS requested_action,
                   COALESCE(s.resolved_action, '') AS resolved_action,
                   COALESCE(s.resolved_target, '') AS resolved_target,
                   COALESCE(s.last_error_code, '') AS last_error_code,
                   COALESCE(s.last_error_message, '') AS last_error_message,
                   t.is_live
              FROM native_save_task_items t
              LEFT JOIN native_save_states s
                     ON s.list_kind = t.list_kind AND s.item_key = t.item_key
             WHERE t.task_id = ?
             ORDER BY t.started_at, t.item_key
            """,
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_native_save_states_by_task(self, task_id: str) -> list[dict[str, Any]]:
        """执行视图：任务内活跃条目 + saved_items 的内容元数据（供平台分组）。"""
        rows = self.conn.execute(
            """
            SELECT s.list_kind, s.item_key, s.task_id, s.requested_action, s.status,
                   si.source_platform, si.content_id, si.content_url, si.content_type,
                   si.title, si.author_name, si.cover_url
              FROM native_save_states s
              JOIN native_save_task_items t
                     ON t.list_kind = s.list_kind AND t.item_key = s.item_key
                    AND t.task_id = ? AND t.is_live = 1
              JOIN saved_items si ON si.item_key = s.item_key
             WHERE s.task_id = ?
             ORDER BY si.source_platform, s.item_key
            """,
            (task_id, task_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def has_sync_task(self, task_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM native_save_task_items WHERE task_id = ? AND is_live = 1",
            (task_id,),
        ).fetchone()
        return bool(row)

    def get_sync_task(self, task_id: str) -> dict[str, Any]:
        rows = self.conn.execute(
            "SELECT task_id, item_key, list_kind, status, is_live "
            "FROM native_save_task_items WHERE task_id = ? AND is_live = 1",
            (task_id,),
        ).fetchall()
        return {"task_id": task_id, "items": [dict(r) for r in rows]}

    # ── 条目级认领（执行期）────────────────────────────────────

    def claim_native_save_item(
        self,
        list_kind: str,
        item_key: str,
        task_id: str,
        runner_id: str,
        execution_id: str,
    ) -> bool:
        """认领单条目：pending → syncing。认领失败（非 pending / 失去 runner）返回 False。"""
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            runner = conn.execute(
                "SELECT 1 FROM native_save_tasks WHERE task_id = ? AND runner_id = ? AND state = 'running'",
                (task_id, runner_id),
            ).fetchone()
            if runner is None:
                conn.rollback()
                return False
            cur = conn.execute(
                """
                UPDATE native_save_states
                   SET status = 'syncing', task_id = ?, execution_id = ?,
                       last_attempt_at = CURRENT_TIMESTAMP,
                       last_error_code = '', last_error_message = ''
                 WHERE list_kind = ? AND item_key = ? AND status = 'pending'
                """,
                (task_id, execution_id, list_kind, item_key),
            )
            if cur.rowcount == 0:
                conn.rollback()
                return False
            conn.execute(
                """
                UPDATE native_save_task_items SET status = 'syncing', updated_at = CURRENT_TIMESTAMP
                 WHERE task_id = ? AND item_key = ?
                """,
                (task_id, item_key),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return True

    def update_native_save_claim_route(
        self,
        list_kind: str,
        item_key: str,
        task_id: str,
        execution_id: str,
        *,
        resolved_action: str,
        resolved_target: str,
    ) -> bool:
        """认领仍有效时记录路由决策；认领已丢返回 False（调用方放弃执行）。"""
        cur = self.conn.execute(
            """
            UPDATE native_save_states
               SET resolved_action = ?, resolved_target = ?,
                   last_attempt_at = CURRENT_TIMESTAMP
             WHERE list_kind = ? AND item_key = ?
               AND task_id = ? AND execution_id = ? AND status = 'syncing'
            """,
            (resolved_action, resolved_target, list_kind, item_key, task_id, execution_id),
        )
        self.conn.commit()
        return int(getattr(cur, "rowcount", 0) or 0) > 0

    def heartbeat_native_save_claim(
        self,
        list_kind: str,
        item_key: str,
        task_id: str,
        execution_id: str,
    ) -> int:
        """刷新 claim 心跳（last_attempt_at）。返回 0 = 认领已失效。"""
        cur = self.conn.execute(
            """
            UPDATE native_save_states SET last_attempt_at = CURRENT_TIMESTAMP
             WHERE list_kind = ? AND item_key = ?
               AND task_id = ? AND execution_id = ? AND status = 'syncing'
            """,
            (list_kind, item_key, task_id, execution_id),
        )
        self.conn.commit()
        return int(getattr(cur, "rowcount", 0) or 0)

    def complete_native_save_claim(
        self,
        list_kind: str,
        item_key: str,
        task_id: str,
        execution_id: str,
        *,
        requested_action: str,
        resolved_action: str,
        resolved_target: str,
        status: str,
        last_error_code: str = "",
        last_error_message: str = "",
    ) -> None:
        """写入最终结果。仅当 execution_id 仍匹配（认领未过期）才生效。"""
        del requested_action  # requested_action 在认领时已固定
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE native_save_states
                   SET status = ?, resolved_action = ?, resolved_target = ?,
                       last_error_code = ?, last_error_message = ?,
                       synced_at = CASE
                           WHEN ? IN ('synced', 'already_synced') THEN CURRENT_TIMESTAMP
                           ELSE synced_at END
                 WHERE list_kind = ? AND item_key = ?
                   AND task_id = ? AND execution_id = ? AND status = 'syncing'
                """,
                (
                    status,
                    resolved_action,
                    resolved_target,
                    last_error_code,
                    last_error_message,
                    status,
                    list_kind,
                    item_key,
                    task_id,
                    execution_id,
                ),
            )
            conn.execute(
                """
                UPDATE native_save_task_items SET status = ?, updated_at = CURRENT_TIMESTAMP
                 WHERE task_id = ? AND item_key = ?
                """,
                (status, task_id, item_key),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

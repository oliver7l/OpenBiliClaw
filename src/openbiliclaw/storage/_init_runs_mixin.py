"""Database mixin: init runs (guided GUI initialization).

Contains methods for managing the init_runs table, including status
tracking, single-flight reservation, and boot-time reconciliation.
"""

from __future__ import annotations

from typing import Any


class InitRunsMixin:
    """Database methods for init runs."""

    conn: Any
    _execute_write: Any

    def get_latest_init_run(self) -> dict[str, Any] | None:
        """Return the most recent init run as a dict, or None if none exist."""
        self._ensure_fresh_read()
        row = self.conn.execute(
            "SELECT * FROM init_runs ORDER BY started_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row is not None else None

    def try_reserve_init_starting(self, run_id: str) -> bool:
        """Atomically reserve a new init run in ``starting`` state."""
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT 1 FROM init_runs WHERE status IN ('starting','running') LIMIT 1"
            ).fetchone()
            if active is not None:
                conn.rollback()
                return False
            conn.execute(
                """
                INSERT INTO init_runs (run_id, status, stage, sequence, started_at, updated_at)
                VALUES (?, 'starting', 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(run_id) DO UPDATE SET
                    status='starting', stage=0, sequence=0, partial_success=0,
                    error_reason=NULL, finished_at=NULL, updated_at=CURRENT_TIMESTAMP
                """,
                (run_id,),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def update_init_run(self, run_id: str, **fields: Any) -> None:
        """Update mutable columns of an init run (the single status writer)."""
        allowed = {
            "status",
            "stage",
            "stages_json",
            "partial_success",
            "error_reason",
            "sequence",
            "finished_at",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"update_init_run: unknown columns {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{col} = ?" for col in fields)
        params = [*fields.values(), run_id]
        self._execute_write(
            f"UPDATE init_runs SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE run_id = ?",
            params,
        )

    def reconcile_init_runs_on_boot(self) -> int:
        """Fail any run left ``starting``/``running`` by a crash/restart."""
        cursor = self._execute_write(
            """
            UPDATE init_runs
               SET status = 'failed', error_reason = 'interrupted',
                   finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
             WHERE status IN ('starting','running')
            """
        )
        return cursor.rowcount

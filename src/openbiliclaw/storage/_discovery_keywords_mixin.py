"""Database mixin: discovery keyword store (unified search-keyword planner).

Contains methods for managing the discovery_keywords table, including
schema creation, batch insert, atomic claim, and status transitions
(pending → claimed → executing → used/failed).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


class DiscoveryKeywordsMixin:
    """Database methods for the discovery keyword store."""

    conn: Any
    _execute_write: Any
    _execute_many_write: Any

    def _ensure_discovery_keywords_table(self) -> None:
        """Create the unified search-keyword store + planner single-flight lock."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS discovery_keywords (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                platform          TEXT NOT NULL,
                keyword           TEXT NOT NULL,
                profile_kw_digest TEXT NOT NULL DEFAULT '',
                status            TEXT NOT NULL DEFAULT 'pending',
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                claimed_at        TIMESTAMP,
                executing_at      TIMESTAMP,
                used_at           TIMESTAMP,
                attempts          INTEGER NOT NULL DEFAULT 0,
                yield_count       INTEGER NOT NULL DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_discovery_keywords_inflight
                ON discovery_keywords (platform, keyword, profile_kw_digest)
                WHERE status IN ('pending', 'claimed', 'executing');
            CREATE INDEX IF NOT EXISTS idx_discovery_keywords_status_digest
                ON discovery_keywords (platform, status, profile_kw_digest);
            CREATE INDEX IF NOT EXISTS idx_discovery_keywords_status_used
                ON discovery_keywords (platform, status, used_at);

            CREATE TABLE IF NOT EXISTS discovery_planner_lock (
                lock_name    TEXT PRIMARY KEY,
                owner        TEXT NOT NULL DEFAULT '',
                locked_until TIMESTAMP NOT NULL,
                updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS discovery_keyword_yield (
                keyword_id  INTEGER NOT NULL,
                content_id  TEXT NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (keyword_id, content_id)
            );
        """)

    def insert_pending_keywords(
        self,
        platform: str,
        keywords: Sequence[str],
        profile_kw_digest: str,
    ) -> int:
        """Batch-insert ``pending`` keywords, ignoring in-flight duplicates."""
        platform_key = platform.strip()
        digest = profile_kw_digest.strip()
        seen: set[str] = set()
        rows: list[tuple[str, str, str]] = []
        for raw in keywords:
            word = str(raw).strip()
            if not word or word in seen:
                continue
            seen.add(word)
            rows.append((platform_key, word, digest))
        if not rows:
            return 0
        before = self.conn.total_changes
        self._execute_many_write(
            """
            INSERT OR IGNORE INTO discovery_keywords
                (platform, keyword, profile_kw_digest, status)
            VALUES (?, ?, ?, 'pending')
            """,
            rows,
        )
        return self.conn.total_changes - before

    def count_pending_keywords(self, platform: str, profile_kw_digest: str) -> int:
        """Return how many ``pending`` keywords exist for this digest."""
        self._ensure_fresh_read()
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM discovery_keywords
            WHERE platform = ? AND status = 'pending' AND profile_kw_digest = ?
            """,
            (platform.strip(), profile_kw_digest.strip()),
        ).fetchone()
        return int(row["n"]) if row is not None else 0

    def claim_keywords(self, platform: str, n: int) -> list[dict[str, Any]]:
        """Atomically claim up to ``n`` ``pending`` keywords for a platform."""
        claim_n = max(0, int(n))
        if claim_n <= 0:
            return []
        self._ensure_fresh_read()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            pending = conn.execute(
                """
                SELECT id
                FROM discovery_keywords
                WHERE platform = ? AND status = 'pending'
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (platform.strip(), claim_n),
            ).fetchall()
            if not pending:
                conn.commit()
                return []
            ids = [int(row["id"]) for row in pending]
            placeholders = ", ".join("?" for _ in ids)
            conn.execute(
                f"""
                UPDATE discovery_keywords
                SET status = 'claimed', claimed_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders}) AND status = 'pending'
                """,
                ids,
            )
            claimed = conn.execute(
                f"""
                SELECT *
                FROM discovery_keywords
                WHERE id IN ({placeholders}) AND status = 'claimed'
                ORDER BY claimed_at ASC, id ASC
                """,
                ids,
            ).fetchall()
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()
        return [dict(row) for row in claimed]

    def mark_keyword_executing(self, keyword_id: int) -> None:
        """Move a ``claimed`` keyword to ``executing`` (async fetch enqueued)."""
        self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'executing', executing_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('claimed', 'executing')
            """,
            (int(keyword_id),),
        )

    def mark_keyword_used(self, keyword_id: int) -> None:
        """Mark a keyword ``used`` (terminal — its fetch has completed)."""
        self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'used', used_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('claimed', 'executing')
            """,
            (int(keyword_id),),
        )

    def mark_keyword_failed(self, keyword_id: int) -> int:
        """Mark a keyword ``failed`` and bump ``attempts``."""
        self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'failed',
                attempts = attempts + 1
            WHERE id = ? AND status IN ('claimed', 'executing')
            """,
            (int(keyword_id),),
        )
        row = self.conn.execute(
            "SELECT attempts FROM discovery_keywords WHERE id = ?",
            (int(keyword_id),),
        ).fetchone()
        return int(row["attempts"]) if row is not None else 0

    def rollback_keyword_to_pending(self, keyword_id: int) -> None:
        """Return a ``claimed`` keyword to ``pending`` (budget-rejection rollback)."""
        self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'pending', claimed_at = NULL
            WHERE id = ? AND status = 'claimed'
            """,
            (int(keyword_id),),
        )

    # ── Lease reclaim, history, recycle, expire ───────────────────

    def reclaim_leased_keywords(
        self,
        claim_lease_minutes: float,
        executing_timeout_minutes: float,
    ) -> int:
        """Reclaim leaked in-flight keywords back to ``pending``."""
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        claimed_cutoff = (now - timedelta(minutes=max(0.0, claim_lease_minutes))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        executing_cutoff = (now - timedelta(minutes=max(0.0, executing_timeout_minutes))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        cursor = self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'pending', claimed_at = NULL, executing_at = NULL
            WHERE (status = 'claimed' AND claimed_at IS NOT NULL AND claimed_at <= ?)
               OR (status = 'executing' AND executing_at IS NOT NULL AND executing_at <= ?)
            """,
            (claimed_cutoff, executing_cutoff),
        )
        return int(cursor.rowcount or 0)

    def history_keywords(
        self,
        platform: str,
        window_size: int,
        window_hours: float,
    ) -> list[str]:
        """Return recent in-flight + used keywords for dedup, newest first."""
        from datetime import UTC, datetime, timedelta

        cap = max(0, int(window_size))
        if cap <= 0:
            return []
        self._ensure_fresh_read()
        cutoff = (datetime.now(UTC) - timedelta(hours=max(0.0, window_hours))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        rows = self.conn.execute(
            """
            SELECT keyword
            FROM discovery_keywords
            WHERE platform = ?
              AND status IN ('claimed', 'executing', 'used')
              AND COALESCE(used_at, executing_at, claimed_at, created_at) >= ?
            ORDER BY COALESCE(used_at, executing_at, claimed_at, created_at) DESC, id DESC
            LIMIT ?
            """,
            (platform.strip(), cutoff, cap),
        ).fetchall()
        return [str(row["keyword"]) for row in rows]

    def recycle_oldest_used(
        self,
        platform: str,
        n: int,
        profile_kw_digest: str,
    ) -> int:
        """Recycle the oldest ``used`` keywords back to ``pending``."""
        recycle_n = max(0, int(n))
        if recycle_n <= 0:
            return 0
        digest = profile_kw_digest.strip()
        self._ensure_fresh_read()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            candidates = conn.execute(
                """
                SELECT id, keyword
                FROM discovery_keywords
                WHERE platform = ? AND status = 'used'
                ORDER BY used_at ASC, id ASC
                """,
                (platform.strip(),),
            ).fetchall()
            recycled = 0
            for row in candidates:
                if recycled >= recycle_n:
                    break
                clash = conn.execute(
                    """
                    SELECT 1
                    FROM discovery_keywords
                    WHERE platform = ?
                      AND keyword = ?
                      AND profile_kw_digest = ?
                      AND status IN ('pending', 'claimed', 'executing')
                    LIMIT 1
                    """,
                    (platform.strip(), str(row["keyword"]), digest),
                ).fetchone()
                if clash is not None:
                    continue
                conn.execute(
                    """
                    UPDATE discovery_keywords
                    SET status = 'pending',
                        profile_kw_digest = ?,
                        claimed_at = NULL,
                        executing_at = NULL,
                        used_at = NULL
                    WHERE id = ? AND status = 'used'
                    """,
                    (digest, int(row["id"])),
                )
                recycled += 1
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()
        return recycled

    def expire_pending_by_digest(self, platform: str, current_digest: str) -> int:
        """Expire ``pending`` keywords generated under a stale profile digest."""
        cursor = self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'expired'
            WHERE platform = ? AND status = 'pending' AND profile_kw_digest != ?
            """,
            (platform.strip(), current_digest.strip()),
        )
        return int(cursor.rowcount or 0)

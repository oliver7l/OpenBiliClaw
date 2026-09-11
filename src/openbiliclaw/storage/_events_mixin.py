"""Database mixin: behavioral events.

从 ``storage/database.py`` 拆出的 events 表操作组。
``Database`` 类继承本 mixin，调用方代码无需修改。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime

logger = logging.getLogger(__name__)


class EventsMixin:
    """行为事件记录的读写方法。"""

    conn: Any  # 由 Database 提供
    _execute_write: Any  # 由 Database 提供

    def insert_event(self, event_type: str, **kwargs: Any) -> int:
        """Insert a behavioral event.

        v0.3.23+: ``context`` is now a natural-language string (from
        ``event_format.build_event()``). It's stored as raw text — no
        outer JSON wrapping — so consumers reading via SELECT get back
        the same string they put in. Pre-v0.3.22 callers that passed
        dict-shaped context still work: dicts / lists / other non-string
        values are JSON-encoded for storage so older code paths don't
        suddenly lose data.

        Args:
            event_type: Type of event.
            **kwargs: Additional event fields. ``context`` may be str,
                dict, list, or None.

        Returns:
            Inserted row ID.

        """
        from openbiliclaw.storage._event_classification import classify_event_satisfaction

        raw_context = kwargs.get("context", "")
        if isinstance(raw_context, str):
            context_text = raw_context
        elif raw_context is None:
            context_text = ""
        else:
            # Legacy dict / list payload — JSON-encode for storage.
            context_text = json.dumps(raw_context, ensure_ascii=False)

        metadata_payload = kwargs.get("metadata", {})

        # Single classification owner. Reconstruct the event dict shape
        # the classifier expects (event_type + url + title + metadata).
        # API ingest may set dwell fields at the top level as well; pass
        # those through so the click rules read either location.
        classifier_event: dict[str, Any] = {
            "event_type": event_type,
            "url": kwargs.get("url", ""),
            "title": kwargs.get("title", ""),
            "metadata": metadata_payload if isinstance(metadata_payload, dict) else {},
        }
        for top_level_key in ("watch_seconds", "video_duration_seconds"):
            if top_level_key in kwargs and kwargs[top_level_key] is not None:
                classifier_event[top_level_key] = kwargs[top_level_key]
        inferred_satisfaction, satisfaction_reason = classify_event_satisfaction(classifier_event)

        params = (
            event_type,
            kwargs.get("url", ""),
            kwargs.get("title", ""),
            context_text,
            json.dumps(metadata_payload, ensure_ascii=False),
            inferred_satisfaction,
            satisfaction_reason,
        )
        # 主写：events.db（P2 事件子库，别名 events）。ATTACH 别名恒等于子库
        # 文件名，SQL 以 events.events 显式落在事件子库，与读路径一致。
        lastrowid = 0
        try:
            cursor = self._execute_write(
                "INSERT INTO events.events "
                "(event_type, url, title, context, metadata, "
                " inferred_satisfaction, satisfaction_reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                params,
            )
            lastrowid = cursor.lastrowid or 0
        except Exception as exc:
            logger.warning("events.db 主写失败，事件可能丢失: %s", exc)
        return lastrowid

    def get_recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get recent events.

        Args:
            limit: Maximum number of events.

        Returns:
            List of event dicts.

        """
        cursor = self.conn.execute(
            "SELECT * FROM events.events ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_events(
        self,
        *,
        event_types: list[str] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        keyword: str = "",
        limit: int = 100,
        satisfaction_modes: frozenset[str] | None = None,
        after_event_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Query events with optional filters.

        ``satisfaction_modes`` filters by ``inferred_satisfaction``. When
        the set includes ``"unknown"``, rows with a NULL classification
        (pre-migration legacy rows) are also returned.

        ``after_event_id`` restricts to rows with ``id`` strictly greater
        than the given watermark — used by the cognition cycle to read only
        events not yet folded into awareness. Result order is unchanged
        (newest-first); callers that need chronological order reverse it.
        """
        sql = "SELECT * FROM events.events"
        clauses: list[str] = []
        params: list[Any] = []

        if event_types:
            placeholders = ", ".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(event_types)

        if after_event_id is not None:
            clauses.append("id > ?")
            params.append(after_event_id)

        if start_time is not None:
            clauses.append("created_at >= ?")
            params.append(start_time.isoformat(sep=" "))

        if end_time is not None:
            clauses.append("created_at <= ?")
            params.append(end_time.isoformat(sep=" "))

        if keyword:
            like = f"%{keyword}%"
            clauses.append("(url LIKE ? OR title LIKE ? OR metadata LIKE ?)")
            params.extend([like, like, like])

        if satisfaction_modes is not None:
            modes = list(satisfaction_modes)
            mode_clauses: list[str] = []
            if modes:
                placeholders = ", ".join("?" for _ in modes)
                mode_clauses.append(f"inferred_satisfaction IN ({placeholders})")
                params.extend(modes)
            if "unknown" in satisfaction_modes:
                mode_clauses.append("inferred_satisfaction IS NULL")
            if mode_clauses:
                clauses.append("(" + " OR ".join(mode_clauses) + ")")
            else:
                # Empty modes set explicitly requested → match nothing.
                clauses.append("1 = 0")

        if clauses:
            sql = f"{sql} WHERE {' AND '.join(clauses)}"

        sql = f"{sql} ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        cursor = self.conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def count_events_by_type(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, int]:
        """Count events grouped by event type."""
        sql = "SELECT event_type, COUNT(*) AS count FROM events.events"
        clauses: list[str] = []
        params: list[Any] = []

        if start_time is not None:
            clauses.append("created_at >= ?")
            params.append(start_time.isoformat(sep=" "))

        if end_time is not None:
            clauses.append("created_at <= ?")
            params.append(end_time.isoformat(sep=" "))

        if clauses:
            sql = f"{sql} WHERE {' AND '.join(clauses)}"

        sql = f"{sql} GROUP BY event_type ORDER BY event_type ASC"
        cursor = self.conn.execute(sql, params)
        return {str(row["event_type"]): int(row["count"]) for row in cursor.fetchall()}

    def count_events_by_source_platform(self) -> dict[str, int]:
        """Count behavior events grouped by normalized source platform.

        Uses the ``source_platform`` generated column + index added in
        :meth:`_ensure_event_read_indexes`, so this is an indexed GROUP BY
        (~50ms over 800k+ rows) instead of the fallback in
        ``api.app._count_events_by_source_platform`` which materialized every
        row and parsed each ``metadata`` JSON in Python — ~1s and a full
        event-loop block every time the source-share suggestion endpoint opened.

        Returns platform key -> count, including an ``unknown`` bucket for
        legacy events whose ``metadata`` predates the field.
        """
        cursor = self.conn.execute(
            "SELECT source_platform, COUNT(*) AS n FROM events.events GROUP BY source_platform"
        )
        return {str(row["source_platform"]): int(row["n"]) for row in cursor.fetchall()}

    def get_latest_event_id(self) -> int:
        """Return the latest event primary key."""
        cursor = self.conn.execute("SELECT COALESCE(MAX(id), 0) AS latest_id FROM events.events")
        row = cursor.fetchone()
        return int(row["latest_id"]) if row is not None else 0

    def query_events_since(
        self,
        *,
        after_event_id: int,
        event_types: list[str],
    ) -> list[dict[str, Any]]:
        """Query events newer than a given id for selected event types."""
        if not event_types:
            return []
        placeholders = ", ".join("?" for _ in event_types)
        cursor = self.conn.execute(
            f"""
            SELECT *
            FROM events.events
            WHERE id > ? AND event_type IN ({placeholders})
            ORDER BY id ASC
            """,
            [after_event_id, *event_types],
        )
        return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _decode_event_metadata(row: dict[str, Any]) -> dict[str, Any]:
        import json

        metadata_raw = row.get("metadata", "")
        if isinstance(metadata_raw, str) and metadata_raw:
            try:
                metadata = json.loads(metadata_raw)
            except json.JSONDecodeError:
                metadata = {}
            if isinstance(metadata, dict):
                return metadata
        if isinstance(metadata_raw, dict):
            return metadata_raw
        return {}

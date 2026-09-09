"""Database router for multi-database architecture.

Manages connections to multiple SQLite databases and routes table access
to the correct database. This is the foundation for the database sharding
plan (see docs/database-sharding-plan.md).

Usage::

    router = DatabaseRouter(data_dir)
    conn = router.get_connection("llm_usage")  # returns llm.db connection
    conn.execute("INSERT INTO llm_usage ...")

For cross-database queries::

    main_conn = router.get_db_connection("core")
    router.attach_for_query(main_conn, ["content", "events"])
    main_conn.execute("SELECT * FROM content.articles JOIN events.events ...")
    router.detach_from_query(main_conn, ["content", "events"])
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any

from openbiliclaw.storage.database import LockedConnection, open_db_conn

logger = logging.getLogger(__name__)

# ── Table to database mapping ────────────────────────────────────────
# As tables are migrated to separate databases, add them here.
# Tables not in this map default to the "core" database (main db).
TABLE_TO_DB: dict[str, str] = {
    # P1: llm.db
    "llm_usage": "llm",
    # P2: events.db (not yet migrated, keep in core for now)
    # "events": "events",
    # "push_notifications": "events",
    # "view_history": "events",
    # P3: knowledge_audit.db (not yet migrated)
    # "audit_issues": "knowledge_audit",
    # "audit_tasks": "knowledge_audit",
    # "audit_config": "knowledge_audit",
    # "gap_records": "knowledge_audit",
    # "gap_analysis_tasks": "knowledge_audit",
}

# Database file names
DB_FILES: dict[str, str] = {
    "core": "openbiliclaw.db",
    "llm": "llm.db",
    "events": "events.db",
    "knowledge_audit": "knowledge_audit.db",
    "content": "content.db",
    "discovery": "discovery.db",
    "diary": "diary.db",
    "health": "health.db",
    "knowledge": "knowledge.db",
    "pool": "pool.db",
    "activity": "activity.db",
}


class DatabaseRouter:
    """Manages multiple SQLite database connections and routes table access.

    Each database gets its own connection with WAL mode and sane defaults.
    Connections are cached per database and thread-safe via LockedConnection.
    """

    def __init__(self, data_dir: str | Path):
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._connections: dict[str, LockedConnection] = {}
        self._lock = threading.Lock()
        self._table_to_db = dict(TABLE_TO_DB)

    def get_db_path(self, db_name: str) -> Path:
        """Get the file path for a database."""
        filename = DB_FILES.get(db_name, f"{db_name}.db")
        return self._data_dir / filename

    def get_connection(self, table_name: str) -> LockedConnection:
        """Get the database connection for a given table.

        Tables not in the mapping route to the core database.
        """
        db_name = self._table_to_db.get(table_name, "core")
        return self.get_db_connection(db_name)

    def get_db_connection(self, db_name: str) -> LockedConnection:
        """Get or create a connection for the named database."""
        with self._lock:
            conn = self._connections.get(db_name)
            if conn is None:
                conn = self._create_connection(db_name)
                self._connections[db_name] = conn
            return conn

    def _create_connection(self, db_name: str) -> LockedConnection:
        """Create a new connection with WAL mode and sane defaults."""
        db_path = self.get_db_path(db_name)
        conn = open_db_conn(db_path)
        logger.debug("Created connection to %s (%s)", db_name, db_path)
        return conn

    def attach_for_query(
        self,
        main_conn: sqlite3.Connection,
        db_names: list[str],
        *,
        aliases: dict[str, str] | None = None,
    ) -> None:
        """Attach databases to a connection for cross-database queries.

        Args:
            main_conn: The connection to attach to.
            db_names: List of database names to attach.
            aliases: Optional mapping of db_name -> attach alias.
                Defaults to using db_name as alias.
        """
        for db_name in db_names:
            alias = (aliases or {}).get(db_name, db_name)
            db_path = self.get_db_path(db_name)
            with suppress(sqlite3.OperationalError):
                main_conn.execute(
                    "ATTACH DATABASE ? AS ?",
                    (str(db_path), alias),
                )

    def detach_from_query(
        self,
        main_conn: sqlite3.Connection,
        db_names: list[str],
        *,
        aliases: dict[str, str] | None = None,
    ) -> None:
        """Detach databases from a connection."""
        for db_name in db_names:
            alias = (aliases or {}).get(db_name, db_name)
            with suppress(sqlite3.OperationalError):
                main_conn.execute(f"DETACH DATABASE {alias}")

    def register_table_mapping(self, table_name: str, db_name: str) -> None:
        """Register a table to database mapping at runtime."""
        self._table_to_db[table_name] = db_name
        logger.debug("Registered table %s -> %s", table_name, db_name)

    def get_table_db(self, table_name: str) -> str:
        """Get the database name for a table."""
        return self._table_to_db.get(table_name, "core")

    def list_mapped_tables(self) -> dict[str, str]:
        """Return all table-to-database mappings."""
        return dict(self._table_to_db)

    def close_all(self) -> None:
        """Close all database connections."""
        with self._lock:
            for db_name, conn in self._connections.items():
                with suppress(Exception):
                    conn.close()
                logger.debug("Closed connection to %s", db_name)
            self._connections.clear()

    def checkpoint_all(self) -> None:
        """Run WAL checkpoint on all databases."""
        for db_name in list(self._connections.keys()):
            conn = self._connections.get(db_name)
            if conn is not None:
                with suppress(Exception):
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    logger.debug("Checkpointed %s", db_name)


# ── Global router instance ───────────────────────────────────────────
_global_router: DatabaseRouter | None = None
_global_router_lock = threading.Lock()


def get_global_router(data_dir: str | Path | None = None) -> DatabaseRouter:
    """Get or create the global DatabaseRouter instance.

    Args:
        data_dir: Data directory for database files. Required on first call.
    """
    global _global_router
    with _global_router_lock:
        if _global_router is None:
            if data_dir is None:
                raise RuntimeError(
                    "DatabaseRouter not initialized. Call get_global_router(data_dir) first."
                )
            _global_router = DatabaseRouter(data_dir)
        return _global_router


def reset_global_router() -> None:
    """Reset the global router (for testing)."""
    global _global_router
    with _global_router_lock:
        if _global_router is not None:
            _global_router.close_all()
            _global_router = None

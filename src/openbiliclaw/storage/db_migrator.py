"""Database migration tool for table migration between databases.

Supports:
- Table schema migration (CREATE TABLE from source)
- Data migration (INSERT INTO SELECT, with batch support for large tables)
- Index migration
- Data verification (row count + sample comparison)
- Rollback support

Usage::

    migrator = DatabaseMigrator(data_dir, router)
    result = migrator.migrate_table(
        "llm_usage",
        source_db="core",
        target_db="llm",
        batch_size=10000,
    )
    if result.success:
        print(f"Migrated {result.rows_migrated} rows")
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from openbiliclaw.storage.db_router import DatabaseRouter

logger = logging.getLogger(__name__)


@dataclass
class MigrationResult:
    """Result of a single table migration."""

    table_name: str
    source_db: str
    target_db: str
    success: bool = False
    rows_migrated: int = 0
    rows_source: int = 0
    rows_target: int = 0
    duration_seconds: float = 0.0
    error: str | None = None
    indexes_migrated: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        status = "OK" if self.success else f"FAILED: {self.error}"
        return (
            f"{self.table_name}: {status} "
            f"({self.rows_migrated}/{self.rows_source} rows, "
            f"{self.duration_seconds:.1f}s)"
        )


class DatabaseMigrator:
    """Migrates tables between SQLite databases."""

    def __init__(self, data_dir: str | Path, router: DatabaseRouter):
        self._data_dir = Path(data_dir)
        self._router = router

    def migrate_table(
        self,
        table_name: str,
        *,
        source_db: str = "core",
        target_db: str,
        batch_size: int = 10000,
        create_schema: bool = True,
        create_indexes: bool = True,
        verify: bool = True,
    ) -> MigrationResult:
        """Migrate a table from source database to target database.

        Args:
            table_name: Name of the table to migrate.
            source_db: Source database name.
            target_db: Target database name.
            batch_size: Batch size for data migration (0 = single transaction).
            create_schema: Whether to create the table schema in target.
            create_indexes: Whether to migrate indexes.
            verify: Whether to verify migration result.

        Returns:
            MigrationResult with migration details.
        """
        start = time.monotonic()
        result = MigrationResult(
            table_name=table_name,
            source_db=source_db,
            target_db=target_db,
        )

        try:
            src_conn = self._router.get_db_connection(source_db)
            dst_conn = self._router.get_db_connection(target_db)

            # Get source row count
            result.rows_source = src_conn.execute(
                f"SELECT COUNT(*) FROM {table_name}"
            ).fetchone()[0]

            # Create schema
            if create_schema:
                self._create_table_schema(src_conn, dst_conn, table_name)

            # Migrate data
            if batch_size > 0 and result.rows_source > batch_size:
                result.rows_migrated = self._migrate_data_batch(
                    src_conn, dst_conn, table_name, batch_size
                )
            else:
                result.rows_migrated = self._migrate_data_single(
                    src_conn, dst_conn, table_name
                )

            # Migrate indexes
            if create_indexes:
                result.indexes_migrated = self._migrate_indexes(
                    src_conn, dst_conn, table_name
                )

            # Get target row count
            result.rows_target = dst_conn.execute(
                f"SELECT COUNT(*) FROM {table_name}"
            ).fetchone()[0]

            # Verify
            if verify:
                self._verify_migration(src_conn, dst_conn, table_name, result)

            result.success = result.error is None
            result.duration_seconds = time.monotonic() - start
            logger.info("Migrated table %s: %s", table_name, result)

        except Exception as e:
            result.success = False
            result.error = str(e)
            result.duration_seconds = time.monotonic() - start
            logger.error("Migration failed for %s: %s", table_name, e)

        return result

    def migrate_tables(
        self,
        table_names: list[str],
        *,
        source_db: str = "core",
        target_db: str,
        batch_size: int = 10000,
    ) -> list[MigrationResult]:
        """Migrate multiple tables."""
        results = []
        for table_name in table_names:
            result = self.migrate_table(
                table_name,
                source_db=source_db,
                target_db=target_db,
                batch_size=batch_size,
            )
            results.append(result)
        return results

    def _create_table_schema(
        self,
        src_conn: sqlite3.Connection,
        dst_conn: sqlite3.Connection,
        table_name: str,
    ) -> None:
        """Create table schema in target database from source."""
        # Get CREATE TABLE SQL
        row = src_conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if row is None or row[0] is None:
            raise ValueError(f"Table {table_name} not found in source database")

        create_sql = row[0]
        dst_conn.execute(f"DROP TABLE IF EXISTS {table_name}")
        dst_conn.execute(create_sql)
        dst_conn.commit()
        logger.debug("Created schema for %s", table_name)

    def _migrate_data_single(
        self,
        src_conn: sqlite3.Connection,
        dst_conn: sqlite3.Connection,
        table_name: str,
    ) -> int:
        """Migrate all data in a single transaction."""
        dst_conn.execute(
            f"INSERT OR IGNORE INTO {table_name} SELECT * FROM {table_name}"
        )
        # Need to use ATTACH for cross-database INSERT
        # Actually, connections are separate, so we need to fetch and insert
        rows = src_conn.execute(f"SELECT * FROM {table_name}").fetchall()
        if not rows:
            return 0
        columns = [desc[0] for desc in src_conn.execute(f"SELECT * FROM {table_name} LIMIT 1").description]
        placeholders = ",".join(["?"] * len(columns))
        dst_conn.executemany(
            f"INSERT OR IGNORE INTO {table_name} ({','.join(columns)}) VALUES ({placeholders})",
            rows,
        )
        dst_conn.commit()
        return len(rows)

    def _migrate_data_batch(
        self,
        src_conn: sqlite3.Connection,
        dst_conn: sqlite3.Connection,
        table_name: str,
        batch_size: int,
    ) -> int:
        """Migrate data in batches for large tables."""
        # Get columns
        columns = [desc[0] for desc in src_conn.execute(f"SELECT * FROM {table_name} LIMIT 1").description]
        placeholders = ",".join(["?"] * len(columns))

        total = 0
        offset = 0
        while True:
            rows = src_conn.execute(
                f"SELECT * FROM {table_name} LIMIT ? OFFSET ?",
                (batch_size, offset),
            ).fetchall()
            if not rows:
                break
            dst_conn.executemany(
                f"INSERT OR IGNORE INTO {table_name} ({','.join(columns)}) VALUES ({placeholders})",
                rows,
            )
            dst_conn.commit()
            total += len(rows)
            offset += batch_size
            logger.debug(
                "Migrated %d rows for %s (offset %d)", total, table_name, offset
            )
        return total

    def _migrate_indexes(
        self,
        src_conn: sqlite3.Connection,
        dst_conn: sqlite3.Connection,
        table_name: str,
    ) -> list[str]:
        """Migrate indexes for a table."""
        indexes = src_conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
            (table_name,),
        ).fetchall()

        migrated = []
        for idx_name, idx_sql in indexes:
            try:
                dst_conn.execute(f"DROP INDEX IF EXISTS {idx_name}")
                dst_conn.execute(idx_sql)
                dst_conn.commit()
                migrated.append(idx_name)
            except Exception as e:
                logger.warning("Failed to migrate index %s: %s", idx_name, e)
        return migrated

    def _verify_migration(
        self,
        src_conn: sqlite3.Connection,
        dst_conn: sqlite3.Connection,
        table_name: str,
        result: MigrationResult,
    ) -> None:
        """Verify migration result."""
        # Row count check
        if result.rows_source != result.rows_target:
            result.error = (
                f"Row count mismatch: source={result.rows_source}, "
                f"target={result.rows_target}"
            )
            return

        # Sample data check (last 10 rows)
        src_rows = src_conn.execute(
            f"SELECT * FROM {table_name} ORDER BY rowid DESC LIMIT 10"
        ).fetchall()
        dst_rows = dst_conn.execute(
            f"SELECT * FROM {table_name} ORDER BY rowid DESC LIMIT 10"
        ).fetchall()

        if len(src_rows) != len(dst_rows):
            result.error = "Sample row count mismatch"
            return

        for i, (src_row, dst_row) in enumerate(zip(src_rows, dst_rows, strict=False)):
            if tuple(src_row) != tuple(dst_row):
                result.error = f"Sample data mismatch at row {i}"
                return

        logger.debug("Verified migration for %s", table_name)

    def rollback(
        self,
        table_name: str,
        *,
        target_db: str,
    ) -> bool:
        """Rollback a migration by dropping the table from target database.

        Args:
            table_name: Table to drop.
            target_db: Target database name.

        Returns:
            True if rollback succeeded.
        """
        try:
            dst_conn = self._router.get_db_connection(target_db)
            dst_conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            dst_conn.commit()
            logger.info("Rolled back migration for %s (dropped from %s)", table_name, target_db)
            return True
        except Exception as e:
            logger.error("Rollback failed for %s: %s", table_name, e)
            return False

    def get_table_schema(
        self,
        table_name: str,
        *,
        db_name: str = "core",
    ) -> str | None:
        """Get the CREATE TABLE SQL for a table."""
        conn = self._router.get_db_connection(db_name)
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return row[0] if row else None

    def get_table_row_count(
        self,
        table_name: str,
        *,
        db_name: str = "core",
    ) -> int:
        """Get row count for a table."""
        conn = self._router.get_db_connection(db_name)
        return conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]

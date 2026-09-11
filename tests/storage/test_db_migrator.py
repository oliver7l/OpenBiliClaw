"""Tests for DatabaseMigrator."""

from __future__ import annotations

from pathlib import Path

import pytest

from openbiliclaw.storage.db_migrator import DatabaseMigrator
from openbiliclaw.storage.db_router import DatabaseRouter


@pytest.fixture
def router(tmp_path: Path) -> DatabaseRouter:
    """Create a DatabaseRouter with a temporary data directory."""
    return DatabaseRouter(tmp_path)


@pytest.fixture
def migrator(router: DatabaseRouter) -> DatabaseMigrator:
    """Create a DatabaseMigrator."""
    return DatabaseMigrator(router._data_dir, router)


@pytest.fixture
def source_with_table(router: DatabaseRouter) -> str:
    """Create a source database with a test table."""
    conn = router.get_db_connection("core")
    conn.execute("DROP TABLE IF EXISTS test_table")
    conn.execute(
        "CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT, value REAL)"
    )
    conn.execute("CREATE INDEX idx_test_name ON test_table(name)")
    # Insert 100 rows
    conn.executemany(
        "INSERT INTO test_table (name, value) VALUES (?, ?)",
        [(f"name_{i}", i * 1.5) for i in range(100)],
    )
    conn.commit()
    return "test_table"


class TestDatabaseMigrator:
    """Tests for DatabaseMigrator."""

    def test_migrate_table_success(
        self, migrator: DatabaseMigrator, router: DatabaseRouter, source_with_table: str
    ):
        """migrate_table should successfully migrate a table."""
        result = migrator.migrate_table(
            source_with_table,
            source_db="core",
            target_db="llm",
        )
        assert result.success
        assert result.rows_source == 100
        assert result.rows_migrated == 100
        assert result.rows_target == 100
        assert result.error is None
        router.close_all()

    def test_migrate_table_creates_schema(
        self, migrator: DatabaseMigrator, router: DatabaseRouter, source_with_table: str
    ):
        """migrate_table should create the table schema in target."""
        migrator.migrate_table(
            source_with_table,
            source_db="core",
            target_db="llm",
        )
        dst_conn = router.get_db_connection("llm")
        # Check table exists
        row = dst_conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='test_table'"
        ).fetchone()
        assert row is not None
        assert "test_table" in row[0]
        router.close_all()

    def test_migrate_table_migrates_indexes(
        self, migrator: DatabaseMigrator, router: DatabaseRouter, source_with_table: str
    ):
        """migrate_table should migrate indexes."""
        result = migrator.migrate_table(
            source_with_table,
            source_db="core",
            target_db="llm",
        )
        assert "idx_test_name" in result.indexes_migrated
        dst_conn = router.get_db_connection("llm")
        idx = dst_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_test_name'"
        ).fetchone()
        assert idx is not None
        router.close_all()

    def test_migrate_table_verifies_data(
        self, migrator: DatabaseMigrator, router: DatabaseRouter, source_with_table: str
    ):
        """migrate_table should verify data integrity."""
        result = migrator.migrate_table(
            source_with_table,
            source_db="core",
            target_db="llm",
        )
        assert result.success
        # Verify data in target
        dst_conn = router.get_db_connection("llm")
        count = dst_conn.execute("SELECT COUNT(*) FROM test_table").fetchone()[0]
        assert count == 100
        # Verify specific row
        row = dst_conn.execute(
            "SELECT name, value FROM test_table WHERE id=50"
        ).fetchone()
        assert row[0] == "name_49"  # 0-indexed, id=50 is the 50th row (name_49)
        router.close_all()

    def test_migrate_table_batch(
        self, migrator: DatabaseMigrator, router: DatabaseRouter, source_with_table: str
    ):
        """migrate_table with batch_size should work for large tables."""
        result = migrator.migrate_table(
            source_with_table,
            source_db="core",
            target_db="llm",
            batch_size=30,  # 100 rows in batches of 30
        )
        assert result.success
        assert result.rows_migrated == 100
        router.close_all()

    def test_migrate_table_not_found(
        self, migrator: DatabaseMigrator, router: DatabaseRouter
    ):
        """migrate_table should fail gracefully for non-existent table."""
        result = migrator.migrate_table(
            "nonexistent_table",
            source_db="core",
            target_db="llm",
        )
        assert not result.success
        assert result.error is not None
        router.close_all()

    def test_migrate_tables_multiple(
        self, migrator: DatabaseMigrator, router: DatabaseRouter
    ):
        """migrate_tables should migrate multiple tables."""
        src_conn = router.get_db_connection("core")
        src_conn.execute("CREATE TABLE table1 (id INTEGER)")
        src_conn.execute("INSERT INTO table1 VALUES (1)")
        src_conn.execute("CREATE TABLE table2 (id INTEGER)")
        src_conn.execute("INSERT INTO table2 VALUES (2)")
        src_conn.commit()

        results = migrator.migrate_tables(
            ["table1", "table2"],
            source_db="core",
            target_db="llm",
        )
        assert len(results) == 2
        assert all(r.success for r in results)
        router.close_all()

    def test_rollback(
        self, migrator: DatabaseMigrator, router: DatabaseRouter, source_with_table: str
    ):
        """rollback should drop the table from target database."""
        migrator.migrate_table(
            source_with_table,
            source_db="core",
            target_db="llm",
        )
        # Verify table exists
        dst_conn = router.get_db_connection("llm")
        assert dst_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='test_table'"
        ).fetchone() is not None

        # Rollback
        success = migrator.rollback(source_with_table, target_db="llm")
        assert success

        # Verify table is gone
        assert dst_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='test_table'"
        ).fetchone() is None
        router.close_all()

    def test_get_table_schema(
        self, migrator: DatabaseMigrator, router: DatabaseRouter, source_with_table: str
    ):
        """get_table_schema should return CREATE TABLE SQL."""
        schema = migrator.get_table_schema(source_with_table, db_name="core")
        assert schema is not None
        assert "test_table" in schema
        router.close_all()

    def test_get_table_row_count(
        self, migrator: DatabaseMigrator, router: DatabaseRouter, source_with_table: str
    ):
        """get_table_row_count should return correct count."""
        count = migrator.get_table_row_count(source_with_table, db_name="core")
        assert count == 100
        router.close_all()

    def test_migration_result_str(
        self, migrator: DatabaseMigrator, router: DatabaseRouter, source_with_table: str
    ):
        """MigrationResult.__str__ should be readable."""
        result = migrator.migrate_table(
            source_with_table,
            source_db="core",
            target_db="llm",
        )
        s = str(result)
        assert "test_table" in s
        assert "OK" in s
        router.close_all()

    def test_empty_table_migration(
        self, migrator: DatabaseMigrator, router: DatabaseRouter
    ):
        """Migrating an empty table should succeed."""
        src_conn = router.get_db_connection("core")
        src_conn.execute("CREATE TABLE empty_table (id INTEGER)")
        src_conn.commit()

        result = migrator.migrate_table(
            "empty_table",
            source_db="core",
            target_db="llm",
        )
        assert result.success
        assert result.rows_source == 0
        assert result.rows_migrated == 0
        router.close_all()

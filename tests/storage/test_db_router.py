"""Tests for DatabaseRouter."""

from __future__ import annotations

from pathlib import Path

import pytest

from openbiliclaw.storage.db_router import (
    DatabaseRouter,
    get_global_router,
    reset_global_router,
)


@pytest.fixture
def router(tmp_path: Path) -> DatabaseRouter:
    """Create a DatabaseRouter with a temporary data directory."""
    return DatabaseRouter(tmp_path)


class TestDatabaseRouter:
    """Tests for DatabaseRouter."""

    def test_init_creates_data_dir(self, tmp_path: Path):
        """Router should create data directory if it doesn't exist."""
        data_dir = tmp_path / "data"
        router = DatabaseRouter(data_dir)
        assert data_dir.exists()
        router.close_all()

    def test_get_db_path(self, router: DatabaseRouter):
        """get_db_path should return correct path."""
        assert router.get_db_path("core").name == "openbiliclaw.db"
        assert router.get_db_path("llm").name == "llm.db"
        assert router.get_db_path("custom").name == "custom.db"

    def test_get_db_connection_creates_connection(self, router: DatabaseRouter):
        """get_db_connection should create and cache a connection."""
        conn = router.get_db_connection("llm")
        assert conn is not None
        # Should return same connection on second call
        conn2 = router.get_db_connection("llm")
        assert conn is conn2
        router.close_all()

    def test_get_connection_routes_table(self, router: DatabaseRouter):
        """get_connection should route table to correct database."""
        # llm_usage is mapped to llm db
        conn = router.get_connection("llm_usage")
        assert conn is not None
        # Unknown table routes to core
        conn_core = router.get_connection("unknown_table")
        assert conn_core is not None
        router.close_all()

    def test_get_table_db(self, router: DatabaseRouter):
        """get_table_db should return correct database name."""
        assert router.get_table_db("llm_usage") == "llm"
        assert router.get_table_db("unknown_table") == "core"

    def test_register_table_mapping(self, router: DatabaseRouter):
        """register_table_mapping should add new mapping."""
        router.register_table_mapping("events", "events")
        assert router.get_table_db("events") == "events"
        conn = router.get_connection("events")
        assert conn is not None
        router.close_all()

    def test_list_mapped_tables(self, router: DatabaseRouter):
        """list_mapped_tables should return all mappings."""
        mappings = router.list_mapped_tables()
        assert "llm_usage" in mappings
        assert mappings["llm_usage"] == "llm"

    def test_attach_for_query(self, router: DatabaseRouter):
        """attach_for_query should attach databases."""
        main_conn = router.get_db_connection("core")
        # Create a test table in llm db
        llm_conn = router.get_db_connection("llm")
        llm_conn.execute("CREATE TABLE IF NOT EXISTS test_table (id INTEGER)")
        llm_conn.execute("INSERT INTO test_table VALUES (1)")
        llm_conn.commit()

        # Attach and query
        router.attach_for_query(main_conn, ["llm"])
        row = main_conn.execute("SELECT * FROM llm.test_table").fetchone()
        assert row is not None
        assert row[0] == 1

        router.detach_from_query(main_conn, ["llm"])
        router.close_all()

    def test_close_all(self, router: DatabaseRouter):
        """close_all should close all connections."""
        router.get_db_connection("llm")
        router.get_db_connection("events")
        router.close_all()
        # After close, should create new connections
        conn = router.get_db_connection("llm")
        assert conn is not None
        router.close_all()

    def test_checkpoint_all(self, router: DatabaseRouter):
        """checkpoint_all should run WAL checkpoint."""
        conn = router.get_db_connection("llm")
        conn.execute("CREATE TABLE IF NOT EXISTS test (id INTEGER)")
        conn.execute("INSERT INTO test VALUES (1)")
        conn.commit()
        router.checkpoint_all()
        router.close_all()

    def test_connection_has_wal_mode(self, router: DatabaseRouter):
        """Connections should have WAL mode enabled."""
        conn = router.get_db_connection("llm")
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        router.close_all()


class TestGlobalRouter:
    """Tests for global router singleton."""

    def setup_method(self):
        reset_global_router()

    def teardown_method(self):
        reset_global_router()

    def test_get_global_router_requires_data_dir(self):
        """First call to get_global_router requires data_dir."""
        with pytest.raises(RuntimeError):
            get_global_router()

    def test_get_global_router_creates_singleton(self, tmp_path: Path):
        """get_global_router should return same instance."""
        router1 = get_global_router(tmp_path)
        router2 = get_global_router()
        assert router1 is router2
        router1.close_all()

    def test_reset_global_router(self, tmp_path: Path):
        """reset_global_router should clear the singleton."""
        router1 = get_global_router(tmp_path)
        reset_global_router()
        with pytest.raises(RuntimeError):
            get_global_router()
        router1.close_all()

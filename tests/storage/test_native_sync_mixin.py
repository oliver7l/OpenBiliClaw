"""native_save 同步任务状态机测试（_native_sync_mixin）。

覆盖 saved_sync/service.py 依赖的持久化契约：
runner 认领/心跳/释放、条目认领/路由/完成、僵尸 reconcile、任务快照。
全部 tmp_path 隔离，不碰真实库。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openbiliclaw.saved_sync.models import SavedItemInput
from openbiliclaw.storage.database import Database


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "native_sync.db")
    database.initialize()
    # 前置数据：一个 saved_item + membership（快照与执行视图的 join 依赖）
    item = SavedItemInput(
        source_platform="bilibili",
        content_id="BV1xx",
        content_url="https://www.bilibili.com/video/BV1xx",
        title="测试视频",
        author_name="up主",
    )
    database.upsert_saved_membership("watch_later", item, note="")
    return database


_KEY = SavedItemInput(
    source_platform="bilibili",
    content_id="BV1xx",
    content_url="https://www.bilibili.com/video/BV1xx",
).item_key


def _runner(db: Database, task_id: str, runner_id: str = "runner-1") -> bool:
    return db.claim_native_sync_task_runner(task_id, runner_id)


def _create_snapshot(db: Database, task_id: str = "task-1") -> list[dict]:
    item_key = SavedItemInput(
        source_platform="bilibili",
        content_id="BV1xx",
        content_url="https://www.bilibili.com/video/BV1xx",
    ).item_key
    return db.create_native_sync_task_snapshot("watch_later", [item_key], task_id, "test")


# ── runner 状态机 ─────────────────────────────────────────────


def test_snapshot_creates_task_and_items(db: Database) -> None:
    snapshot = _create_snapshot(db)

    assert len(snapshot) == 1
    assert snapshot[0]["item_key"] == _KEY
    assert snapshot[0]["status"] == "pending"
    assert snapshot[0]["is_live"] == 1
    # 任务行存在（含零条目批次也成立）→ has_sync_task 语义
    assert db.native_sync_task_exists("task-1") is True
    assert db.has_sync_task("task-1") is True


def test_runner_claim_and_heartbeat_ownership(db: Database) -> None:
    _create_snapshot(db)
    assert _runner(db, "task-1", "r1") is True
    # 心跳由持有者刷新 → 非零
    assert db.heartbeat_native_sync_task("task-1", "r1") > 0
    # 其他 runner 心跳 → 0（失去执行权信号）
    assert db.heartbeat_native_sync_task("task-1", "r2") == 0
    # 其他 runner 不能抢正在运行的（心跳未过期）
    assert _runner(db, "task-1", "r2") is False
    # 释放后可被重新认领
    db.release_pending_native_sync_task("task-1", "r1")
    assert _runner(db, "task-1", "r2") is True


def test_item_claim_requires_pending_and_runner(db: Database) -> None:
    _create_snapshot(db)
    # 未认领 runner 时不能认领条目
    assert (
        db.claim_native_save_item("watch_later", _KEY, "task-1", "ghost", "exec-1")
        is False
    )
    assert _runner(db, "task-1", "r1") is True
    assert (
        db.claim_native_save_item("watch_later", _KEY, "task-1", "r1", "exec-1")
        is True
    )
    # 已 syncing 不可重复认领
    assert (
        db.claim_native_save_item("watch_later", _KEY, "task-1", "r1", "exec-2")
        is False
    )


# ── 条目状态机 ────────────────────────────────────────────────


def test_route_and_complete_require_live_claim(db: Database) -> None:
    _create_snapshot(db)
    assert _runner(db, "task-1", "r1") is True
    assert (
        db.claim_native_save_item("watch_later", _KEY, "task-1", "r1", "exec-1")
        is True
    )
    assert (
            db.update_native_save_claim_route(
                "watch_later",
                _KEY,
                "task-1",
                "exec-1",
            resolved_action="watch_later",
            resolved_target="https://target",
        )
        is True
    )
    # 错误 execution_id 的路由更新必须失败
    assert (
        db.update_native_save_claim_route(
            "watch_later",
            _KEY,
            "task-1",
            "exec-999",
            resolved_action="watch_later",
            resolved_target="https://target",
        )
        is False
    )
    # 完成：状态落库、claim 心跳随后失效
    db.complete_native_save_claim(
        "watch_later",
        _KEY,
        "task-1",
        "exec-1",
        requested_action="watch_later",
        resolved_action="watch_later",
        resolved_target="https://target",
        status="synced",
    )
    items = db.list_native_sync_task_items("task-1")
    assert items[0]["status"] == "synced"
    assert items[0]["resolved_target"] == "https://target"
    # claim 已完成 → 心跳返回 0
    assert db.heartbeat_native_save_claim("watch_later", _KEY, "task-1", "exec-1") == 0


def test_list_states_by_task_joins_saved_items(db: Database) -> None:
    _create_snapshot(db)
    assert _runner(db, "task-1", "r1") is True
    assert (
        db.claim_native_save_item("watch_later", _KEY, "task-1", "r1", "exec-1")
        is True
    )
    rows = db.list_native_save_states_by_task("task-1")
    assert len(rows) == 1
    row = rows[0]
    # _item_from_row 需要的字段全部就位
    for field in (
        "source_platform",
        "content_id",
        "content_url",
        "content_type",
        "title",
        "author_name",
        "cover_url",
    ):
        assert field in row, f"缺少字段 {field}"
    assert row["source_platform"] == "bilibili"
    assert row["status"] == "syncing"


def test_stale_claim_reconciled_to_failed(db: Database) -> None:
    _create_snapshot(db)
    assert _runner(db, "task-1", "r1") is True
    assert (
        db.claim_native_save_item("watch_later", _KEY, "task-1", "r1", "exec-1")
        is True
    )
    # 人为把 claim 心跳拨老（超过 600s 阈值）
    db.conn.execute(
        """
        UPDATE native_save_states SET last_attempt_at = datetime('now', '-700 seconds')
         WHERE item_key = ?
        """,
        (_KEY,),
    )
    db.conn.commit()
    db.reconcile_stale_native_save_claims("task-1")
    items = db.list_native_sync_task_items("task-1")
    assert items[0]["status"] == "failed"
    assert items[0]["last_error_code"] == "claim_stale"


def test_release_stale_pending_releases_items(db: Database) -> None:
    _create_snapshot(db)
    assert _runner(db, "task-1", "r1") is True
    assert (
        db.claim_native_save_item("watch_later", _KEY, "task-1", "r1", "exec-1")
        is True
    )
    db.release_pending_native_sync_task("task-1", "r1")
    # syncing 镜像退回 pending（可重试）
    items = db.list_native_sync_task_items("task-1")
    assert items[0]["status"] == "pending"
    # runner 已释放 → 原认领者心跳失效
    assert db.heartbeat_native_sync_task("task-1", "r1") == 0

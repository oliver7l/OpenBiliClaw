"""统一 DB 连接入口 — K7 收口的规范位置（refactor-plan 阶段 3）。

所有"主库（可选 ATTACH pool.db 子库）"的标准连接从这里拿；
``runtime/_db.py`` 的 producer 连接族委托到这里。行为与原实现完全一致。

分层：runtime / api / cli / 领域模块 → storage（基础设施）合法；
storage 不反向依赖任何上层模块。
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

from openbiliclaw.storage.database import open_db_conn

__all__ = ["connect_main", "connect_plain"]


def connect_main(db_path: str | Path, *, pool_attach: bool = True) -> sqlite3.Connection:
    """连接主库，默认 ATTACH 推荐流子库 pool.db（无前缀 content_cache 落到子库）。

    这是大多数 producer / 服务端路径的标准连接方式：db_path 指向主库
    openbiliclaw.db，同目录下的 pool.db 作为 pool schema ATTACH 进来。
    """
    conn = open_db_conn(str(db_path))
    if pool_attach:
        with contextlib.suppress(sqlite3.OperationalError):
            conn.execute(
                "ATTACH DATABASE ? AS pool",
                (str(Path(db_path).with_name("pool.db")),),
            )
    return conn


def connect_plain(db_path: str | Path) -> sqlite3.Connection:
    """连接单个 sqlite 文件（不 ATTACH 任何子库）。"""
    return open_db_conn(str(db_path))

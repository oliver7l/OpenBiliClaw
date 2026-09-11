"""Runtime 数据库连接公共函数。

各 platform producer 之前重复定义了 ``_obc_connect``，这里统一收口。
K7：标准连接的唯一实现在 ``storage.connection``，本模块只做 producer
视角的薄封装（inbox 子库是 runtime 概念，保留在这里）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from openbiliclaw.storage.connection import connect_main, connect_plain


def connect_main_with_pool(db_path: str | Path):
    """连接主库并 ATTACH 推荐流子库 pool.db。委托 storage.connection.connect_main。"""
    return connect_main(db_path)


def connect_pool(db_path: str | Path):
    """直接连接 pool.db（favorites producer 传入的已是 pool.db 路径）。"""
    return connect_plain(db_path)


def connect_inbox(platform: str, data_dir: str | Path = "data") -> sqlite3.Connection:
    """连接 platform 的 inbox 子库（用于 producer 写入，避免并发锁总库）。

    子库位置: data/inbox/<platform>.db，自动创建 content_cache 表。
    合并器定期将 inbox 数据合并到 pool.db。
    """
    from openbiliclaw.runtime.inbox_db import connect_inbox as _connect_inbox
    return _connect_inbox(platform, data_dir)

"""Runtime 数据库连接公共函数。

各 platform producer 之前重复定义了 ``_obc_connect``，这里统一收口。
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path


def connect_main_with_pool(db_path: str | Path) -> sqlite3.Connection:
    """连接主库并 ATTACH 推荐流子库 pool.db（无前缀 content_cache 落到子库）。

    这是大多数 *_producer.py 使用的标准连接方式：db_path 指向主库
    openbiliclaw.db，同目录下的 pool.db 作为 pool schema ATTACH 进来。
    """
    conn = sqlite3.connect(str(db_path))
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute(
            "ATTACH DATABASE ? AS pool",
            (str(Path(db_path).with_name("pool.db")),),
        )
    return conn


def connect_pool(db_path: str | Path) -> sqlite3.Connection:
    """直接连接 pool.db（favorites producer 用）。

    favorites producer 传入的 db_path 已经是 pool.db 的路径，
    不需要再 ATTACH。
    """
    return sqlite3.connect(str(db_path))

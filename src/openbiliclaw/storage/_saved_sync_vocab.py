"""Native sync 状态词表 — storage 侧唯一事实。

DDL 的 CHECK 约束归 storage 所有，词表也归 storage 所有（K5：
消除 storage→saved_sync 反向依赖）。领域层 ``saved_sync.models``
从这里 import 并 re-export，api 侧既有 import 路径不变。
"""

from __future__ import annotations

from typing import Literal

NativeSaveStatus = Literal[
    "pending",
    "syncing",
    "synced",
    "already_synced",
    "login_required",
    "unsupported",
    "rate_limited",
    "extension_required",
    "failed",
]

NATIVE_SAVE_TERMINAL_STATUSES: frozenset[NativeSaveStatus] = frozenset(
    {
        "synced",
        "already_synced",
        "login_required",
        "unsupported",
        "rate_limited",
        "extension_required",
        "failed",
    }
)
NATIVE_SAVE_STATUSES: frozenset[NativeSaveStatus] = frozenset(
    {"pending", "syncing", *NATIVE_SAVE_TERMINAL_STATUSES}
)

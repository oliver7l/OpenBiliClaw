"""X (Twitter) 客户端异常类 — 中性共享定义。

原本定义在 ``sources.x_client``，但 ``storage.x_health`` 需要捕获它们做
健康态分类，形成 storage→sources 反向依赖（K5）。移到 core 后：
- ``sources.x_client`` 从这里 import 并 re-export（抛出方）
- ``storage.x_health`` 直接从这里 import（捕获方）
类型身份唯一，既有 ``except XAuthError`` 语义不变。
"""

from __future__ import annotations


class XClientError(RuntimeError):
    """Base class for all X client failures."""


class XMissingCookieError(XClientError):
    """No usable cookie (``auth_token`` and/or ``ct0`` missing).

    Raised lazily on first use — before any ``twitter_cli`` import — so the
    disabled / unconfigured path never touches the X dependency.
    """


class XAuthError(XClientError):
    """Authentication failed (HTTP 401 / ``AuthenticationError``) — cookie expired."""


class XBlockedError(XClientError):
    """Request blocked (HTTP 403) — account/region/endpoint forbidden."""


class XRateLimitError(XClientError):
    """Rate limited (HTTP 429) — back off and retry later."""

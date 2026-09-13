"""全局 pytest 配置：跨文件测试隔离夹具。

这里只放"所有测试都需要"的环境隔离逻辑，不放业务 fixture。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _ensure_event_loop() -> Iterator[None]:
    """保证每个测试开始时，当前线程都有可用的 event loop。

    动机：``asyncio.run()`` 在结束时会把当前线程的 current event loop 置为
    ``None`` 并关闭它。tests/cli 大量使用 ``asyncio.run()``，而 tests/diary
    等模块在**同步**上下文里调用 ``asyncio.ensure_future()``（见
    ``diary/service.py`` 中 create_entry 的自动 embedding / 相似日记后台
    任务），后者依赖隐式的 current loop。于是出现典型的"单跑全绿、合跑
    必挂"：先跑的 cli 把 loop 清空，后跑的 diary 拿不到 loop 抛
    RuntimeError。

    之所以在**测试开始时**补建、而不是在结束时还原：``asyncio.run()`` 的
    清理发生在它自身的 finally 里，fixture 无法拦截，只能被动补救。
    """
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_event_loop()
    except RuntimeError:
        loop = None

    if loop is None or loop.is_closed():
        asyncio.set_event_loop(asyncio.new_event_loop())

    yield

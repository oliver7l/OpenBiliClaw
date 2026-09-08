"""Compatibility stub — 实现统一收敛到 ``obc_soul.consolidator``（阶段 1 K2 收口）。

原 ``src/openbiliclaw/soul/consolidator.py`` 全量实现已删除；本 stub 通过模块别名把
``openbiliclaw.soul.consolidator`` 指向 obc-soul 实现：新旧两条 import 路径拿到**同一**
模块对象，类身份唯一（K2 的类身份分裂即此），且
``monkeypatch.setattr(模块对象, ...)`` 的补丁语义与删除前完全一致。
"""

import sys as _sys

import obc_soul.consolidator as _impl

_sys.modules[__name__] = _impl

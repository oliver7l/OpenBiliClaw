"""兼容垫片：本模块已迁移至 ``openbiliclaw.interview.study.routes``。

期 3「代码分层」把 B（题目研习）的路由从 ``api/`` 收回包内；
本文件仅为旧导入路径保活，下一个大版本摘除。
"""

from __future__ import annotations

from openbiliclaw.interview.study.routes import register_interview_routes, router

__all__ = ["register_interview_routes", "router"]

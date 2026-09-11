"""ed2k / Kad 下载管理模块。

通过本机 ``mule`` CLI 驱动 MLDonkey（Colima + Docker 容器），提供
``/api/ed2k*`` 后端接口搜索、下载、管理 ed2k 文件，落地目录见 ``[ed2k]``。
"""

from __future__ import annotations

from openbiliclaw.ed2k.service import MuleService

__all__ = ["MuleService"]

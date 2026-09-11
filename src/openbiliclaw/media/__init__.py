"""本地媒体浏览模块。

浏览用户在 ``[media] roots`` 配置的本地媒体目录（视频 + 图片），
提供 ``/media`` 独立页（图片灯箱 + 视频播放）与 ``/api/media*`` 后端接口。
"""

from __future__ import annotations

from openbiliclaw.media.service import MediaService

__all__ = ["MediaService"]

"""乐仔时间线相册（Lezai Timeline Album）。

把「乐仔的照片」（夸克网盘源库，5206 张按拍摄年月归档的原始时间线）
作为独立子系统发布进 OpenBiliClaw：

- 数据真值源仍在外部源库（原图、缩略图、HEIC 预览都在那里），本模块
  **只读不写**——要改照片就改源库；
- 与 ``openbiliclaw.lezai`` 是**两个不同的源库**：lezai 是人脸识别后
  分类的「乐仔相片库」（2576 张，发布分析平台页），album 是未识别的
  原始时间线（5206 张，只做浏览）；
- 原图体积大（16GB），**不**同步进包内，由 ``/album/full`` 直接挂源库；
  只有页面与 PWA 图标这类轻量资产落在包内 ``web/album/``。

对外入口：``GET /album/``（移动优先相册页，纳入密码门禁）。
"""

from __future__ import annotations

from openbiliclaw.album.paths import (
    heic_preview_dir,
    original_photos_dir,
    source_library_dir,
    thumbs_dir,
    web_assets_dir,
)

__all__ = [
    "heic_preview_dir",
    "original_photos_dir",
    "source_library_dir",
    "thumbs_dir",
    "web_assets_dir",
]

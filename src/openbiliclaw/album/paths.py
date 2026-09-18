"""乐仔时间线相册（album）路径锚点。

与 ``openbiliclaw.lezai`` 的分工——两者是**不同的源库**，别混：

- ``lezai`` —— 人脸识别 + 幼儿园/家庭分类的「乐仔相片库」（2576 张），
  对外发布成长分析平台页 ``/lezai/``；
- ``album`` —— 按拍摄年月归档的「乐仔的照片」（5206 张原始时间线），
  只做浏览（缩略图墙 + 原图查看），不做识别与分类。

铁律对齐：项目内路径一律走显式锚点，不写 CWD 相对的数据路径（换个启动
目录就会静默读写到另一个库）。
源库在项目内 08_乐仔相册/（2026-09 自 030-夸克网盘迁入），可用环境变量 ``OBC_ALBUM_SOURCE_DIR``
覆盖（换盘/换机时不用改代码）。
"""

from __future__ import annotations

import os
from pathlib import Path

# album 包位于 src/openbiliclaw/album/，向上两级即 src/openbiliclaw/
_PACKAGE_DIR = Path(__file__).resolve().parent.parent

# 源库默认位置（外部真值源；原图按月目录、缩略图、HEIC 预览都在这里）
_DEFAULT_SOURCE_DIR = Path(
    "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/08_乐仔相册/乐仔的照片"
)

_SOURCE_DIR_ENV = "OBC_ALBUM_SOURCE_DIR"


def source_library_dir() -> Path:
    """返回时间线相册源目录（真值源，外部于本项目）。"""
    env = os.environ.get(_SOURCE_DIR_ENV, "").strip()
    if env:
        return Path(env).expanduser()
    return _DEFAULT_SOURCE_DIR


def web_assets_dir() -> Path:
    """返回包内页面目录（``/album`` 挂载源，轻量资产随包入 git）。"""
    return _PACKAGE_DIR / "web" / "album"


def thumbs_dir() -> Path:
    """返回源库缩略图目录（``/album/thumbs`` 挂载源，5206 张 / 219MB）。"""
    return source_library_dir() / "_thumbs"


def heic_preview_dir() -> Path:
    """返回源库 HEIC→JPEG 预览目录（``/album/heic`` 挂载源）。

    安卓浏览器的 WebView/Chrome 不支持 HEIC，源库侧已用 macOS ``sips``
    预转换 750 张；页面构建时把 ``.heic`` 条目的原图指向这里。
    """
    return source_library_dir() / "_heic_jpg"


def original_photos_dir() -> Path:
    """返回源库根（``/album/full`` 挂载源，原图按月目录存放）。"""
    return source_library_dir()

"""乐仔模块路径锚点。

铁律对齐：项目内路径一律走显式锚点，不用 ``Path("data/...")`` 相对猜测。
- 包内页面资产：``src/openbiliclaw/web/lezai/``（随包分发，thumbs 子目录
  是个人照片数据，已在 .gitignore 排除——与 ``web/clone/sites/`` 同款先例）；
- 外部源库：夸克网盘「乐仔相片库」目录，可用环境变量
  ``OBC_LEZAI_SOURCE_DIR`` 覆盖（换盘/换机时不用改代码）。
"""

from __future__ import annotations

import os
from pathlib import Path

# lezai 包位于 src/openbiliclaw/lezai/，向上两级即 src/openbiliclaw/
_PACKAGE_DIR = Path(__file__).resolve().parent.parent

# 源库默认位置（外部真值源；照片识别模型、分类目录、缩略图都在这里）
_DEFAULT_SOURCE_DIR = Path(
    "/Volumes/固态硬盘1T/002-探索项目/030-夸克网盘/乐仔相片库"
)

_SOURCE_DIR_ENV = "OBC_LEZAI_SOURCE_DIR"


def source_library_dir() -> Path:
    """返回乐仔相片库源目录（真值源，外部于本项目）。"""
    env = os.environ.get(_SOURCE_DIR_ENV, "").strip()
    if env:
        return Path(env).expanduser()
    return _DEFAULT_SOURCE_DIR


def web_assets_dir() -> Path:
    """返回包内静态页目录（/lezai 挂载源）。"""
    return _PACKAGE_DIR / "web" / "lezai"


def thumbs_dir() -> Path:
    """返回包内缩略图目录（/lezai/thumbs 挂载源）。"""
    return web_assets_dir() / "thumbs"

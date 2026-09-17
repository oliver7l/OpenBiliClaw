"""乐仔成长相册（Lezai Growth Album）。

把「乐仔相片库」（人脸识别 + 幼儿园/家庭分类 + 成长分析平台）作为独立
子系统集成进 OpenBiliClaw：

- 数据真值源仍在外部源库（夸克网盘「乐仔相片库」，识别人脸模型、分类
  目录、缩略图都在那里）；
- 本模块只负责两件事：
  1) 路径锚点（源库 / 页面资产 / 缩略图目录）；
  2) 把轻量资产（index.html + thumbs + 数据文件）同步进包内
     ``web/lezai/``，由 ``/lezai`` 静态挂载对外提供；
- 原图目录（01_幼儿园 / 02_家庭生活 / 03_待确认）体积大，**不**同步进
  包内，仅保留在源库。

对外入口：``GET /lezai/``（成长分析平台页）。
"""

from __future__ import annotations

from openbiliclaw.lezai.paths import (
    source_library_dir,
    thumbs_dir,
    web_assets_dir,
)

__all__ = ["source_library_dir", "thumbs_dir", "web_assets_dir"]

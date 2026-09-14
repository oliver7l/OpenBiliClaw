"""面试模块内部路径锚点。

避免各文件散落 ``Path(__file__).resolve().parents[N]`` —— 文件一旦被挪进
``job/`` / ``study/`` / ``review/`` 子包，``parents[N]`` 的 N 就会静默错位，
进而把 ``data/*.db`` 写到错误目录。

``_paths.py`` 固定在 ``src/openbiliclaw/interview/``，故 ``parents[3]`` 恒为项目根，
与引用者所在层级无关。
"""

from __future__ import annotations

from pathlib import Path

#: 项目根目录（含 pyproject.toml / config.toml）
PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]

__all__ = ["PROJECT_ROOT"]

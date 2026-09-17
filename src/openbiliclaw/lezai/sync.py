"""把外部源库的轻量资产同步进包内 ``web/lezai/`` 静态页目录。

设计要点：
- **幂等**：按「相对路径 + 文件大小」比对，内容没变就不重拷；
- **默认 dry-run**：只打印计划不落盘，``--apply`` 才真正写入（与
  ``scripts/travel/build_travel_db.py`` 同款约定）；
- **范围收窄**：只同步 index.html、缩略图和数据文件；原图目录
  （01_幼儿园 / 02_家庭生活 / 03_待确认）不同步——体积大且源库才是真值源；
- **缩略图垃圾**：源库已删除但包内仍存在的缩略图默认只报告，``--prune``
  才删除（照片数据，宁可多报不可误删）。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from openbiliclaw.lezai.paths import source_library_dir, thumbs_dir, web_assets_dir

# 顶层轻量文件（相对源库根）；清单/报告是个人内容但仓库本身私有，随包同步
FILE_ASSETS: tuple[str, ...] = (
    "index.html",
    "data.json",
    "platform_data.json",
    "activities.json",
    "companions.json",
    "README.md",
    "清单.csv",
    "分析报告.md",
    "分析报告-数据表.md",
    "同伴接触表-top15.jpg",
)

THUMBS_SUBDIR = "thumbs"


@dataclass
class SyncPlan:
    """一次同步的动作清单（dry-run 与 apply 共用同一份计划）。"""

    copy_files: list[tuple[Path, Path]] = field(default_factory=list)
    copy_thumbs: list[tuple[Path, Path]] = field(default_factory=list)
    stale_thumbs: list[Path] = field(default_factory=list)  # 包内有、源库没有
    missing_source: list[str] = field(default_factory=list)  # 源库缺的资产

    @property
    def has_actions(self) -> bool:
        return bool(self.copy_files or self.copy_thumbs or self.stale_thumbs)


def build_plan(source: Path | None = None) -> SyncPlan:
    """比对源库与包内目录，生成同步计划（不写任何文件）。"""
    src_root = source or source_library_dir()
    dst_root = web_assets_dir()
    src_thumbs = src_root / THUMBS_SUBDIR
    dst_thumbs = thumbs_dir()

    plan = SyncPlan()

    # ── 顶层轻量文件 ──
    for rel in FILE_ASSETS:
        src = src_root / rel
        dst = dst_root / rel
        if not src.is_file():
            plan.missing_source.append(rel)
            continue
        if not dst.is_file() or dst.stat().st_size != src.stat().st_size:
            plan.copy_files.append((src, dst))

    # ── 缩略图 ──
    if not src_thumbs.is_dir():
        plan.missing_source.append(THUMBS_SUBDIR + "/")
        return plan

    src_map = {p.name: p for p in src_thumbs.iterdir() if p.is_file()}
    dst_map = (
        {p.name: p for p in dst_thumbs.iterdir() if p.is_file()}
        if dst_thumbs.is_dir()
        else {}
    )
    for name, src in src_map.items():
        dst = dst_thumbs / name
        if name not in dst_map or dst.stat().st_size != src.stat().st_size:
            plan.copy_thumbs.append((src, dst))
    plan.stale_thumbs = [dst_map[name] for name in dst_map if name not in src_map]
    return plan


def apply_plan(
    plan: SyncPlan, *, prune: bool = False, source: Path | None = None
) -> dict[str, int]:
    """执行同步计划：拷贝文件/缩略图，可选清理孤儿缩略图。"""
    dst_root = web_assets_dir()
    dst_root.mkdir(parents=True, exist_ok=True)
    thumbs_dst = thumbs_dir()
    thumbs_dst.mkdir(parents=True, exist_ok=True)

    counts = {"files": 0, "thumbs": 0, "pruned": 0}
    for src, dst in plan.copy_files:
        shutil.copy2(src, dst)
        counts["files"] += 1
    for src, dst in plan.copy_thumbs:
        shutil.copy2(src, dst)
        counts["thumbs"] += 1
    if prune:
        for dst in plan.stale_thumbs:
            dst.unlink(missing_ok=True)
            counts["pruned"] += 1
    return counts

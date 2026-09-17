"""乐仔相片库同步 CLI（默认 dry-run）。

用法::

    python -m openbiliclaw.lezai            # dry-run，打印同步计划
    python -m openbiliclaw.lezai --apply    # 执行同步
    python -m openbiliclaw.lezai --apply --prune   # 同时清理源库已删除的缩略图
    OBC_LEZAI_SOURCE_DIR=/path/to/乐仔相片库 python -m openbiliclaw.lezai
"""

from __future__ import annotations

import argparse
import sys

from openbiliclaw.lezai.paths import source_library_dir
from openbiliclaw.lezai.sync import SyncPlan, apply_plan, build_plan


def _print_plan(plan: SyncPlan) -> None:
    print(f"源库: {source_library_dir()}")
    if plan.missing_source:
        print(f"⚠ 源库缺失资产: {', '.join(plan.missing_source)}")
    if not plan.has_actions:
        print("✓ 包内已是最新，无需同步")
        return
    print(f"待拷顶层文件: {len(plan.copy_files)}")
    for src, _dst in plan.copy_files:
        print(f"  + {src.name}")
    print(f"待拷缩略图: {len(plan.copy_thumbs)}")
    print(f"包内孤儿缩略图(源库已无): {len(plan.stale_thumbs)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="乐仔相片库 → OpenBiliClaw 静态页同步")
    parser.add_argument("--apply", action="store_true", help="真正执行同步（默认 dry-run）")
    parser.add_argument(
        "--prune", action="store_true", help="删除包内源库已不存在的缩略图（需配合 --apply）"
    )
    args = parser.parse_args(argv)

    plan = build_plan()
    _print_plan(plan)
    if not args.apply:
        print("\n[dry-run] 未写入任何文件；加 --apply 执行")
        return 0
    counts = apply_plan(plan, prune=args.prune)
    print(
        f"\n✓ 同步完成: 文件 {counts['files']}，缩略图 {counts['thumbs']}，"
        f"清理 {counts['pruned']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

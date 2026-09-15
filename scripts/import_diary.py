#!/usr/bin/env python
"""日记多来源导入 CLI。

用法：
    # 1) 先看有哪些文件夹（苹果备忘录）
    .venv/bin/python scripts/import_diary.py --source apple --list-folders

    # 2) 预演：只统计不写库
    .venv/bin/python scripts/import_diary.py --source apple --folder 每日记录 --dry-run

    # 3) 落库
    .venv/bin/python scripts/import_diary.py --source apple --folder 每日记录 --apply

    # 有道云 / WPS（需先放好 Cookie，见 data/cookies/README.md）
    .venv/bin/python scripts/import_diary.py --source youdao --dry-run

设计要点：
* 默认 ``--dry-run``（安全优先），必须显式 ``--apply`` 才写库；
* 去重键 = 日期 + 正文前 50 字（与历史导入一致，见 diary/sources/upsert.py）；
* 权限/Cookie 问题一律非 0 退出并打印可执行的处理指引。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from openbiliclaw.diary.service import DiaryService  # noqa: E402
from openbiliclaw.diary.sources import SOURCE_NAMES, get_source, import_notes  # noqa: E402

DEFAULT_DB = PROJECT_ROOT / "data" / "diary.db"


def _print(msg: str) -> None:
    print(msg, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="import_diary",
        description="把苹果备忘录 / 有道云笔记 / WPS 笔记里的日记导入 diary.db（幂等）",
    )
    parser.add_argument("--source", required=True, choices=SOURCE_NAMES, help="来源")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="目标 diary.db 路径")
    parser.add_argument("--since", default=None, help="只导入该日期（YYYY-MM-DD）之后的条目")
    parser.add_argument("--folder", default=None, help="只导入指定文件夹（苹果备忘录 / 有道的目录名）")
    parser.add_argument(
        "--mode",
        choices=("dry-run", "apply"),
        default="dry-run",
        help="dry-run=只统计不写库（默认）；apply=真正落库",
    )
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少条（调试用）")
    parser.add_argument("--tag", action="append", default=None, help="额外附加标签，可重复")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出统计结果")
    parser.add_argument("--list-folders", action="store_true", help="仅列出可用文件夹后退出（苹果备忘录）")
    return parser


def _build_source(args: argparse.Namespace) -> object:
    kwargs: dict[str, object] = {}
    if args.folder:
        kwargs["folder"] = args.folder
    return get_source(args.source, **kwargs)


def _list_folders(args: argparse.Namespace) -> int:
    from openbiliclaw.diary.sources.apple_notes import AppleNotesSource, connect, query_folder_paths

    src = AppleNotesSource()
    if not isinstance(src, AppleNotesSource):
        _print("仅苹果备忘录支持 --list-folders")
        return 2
    conn = connect(src.database)
    try:
        counts: dict[str, int] = {}
        for row in src._iter_rows(conn):  # noqa: SLF001 - CLI 诊断用途
            paths = query_folder_paths(conn)
            path = paths.get(row["ZFOLDER"], "Notes") if row["ZFOLDER"] else "Notes"
            counts[path] = counts.get(path, 0) + 1
        _print(f"可用文件夹（{len(counts)} 个）：")
        for path, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            _print(f"  {count:5d}  {path}")
    finally:
        conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_folders:
        try:
            return _list_folders(args)
        except Exception as exc:  # noqa: BLE001 - 顶层错误统一翻译
            _print(f"✗ {exc}")
            return 3

    try:
        source = _build_source(args)
    except ValueError as exc:
        _print(f"✗ {exc}")
        return 2

    notes = source.fetch(since=args.since)  # type: ignore[attr-defined]
    if args.limit:
        limited = []
        for index, note in enumerate(notes):
            if index >= args.limit:
                break
            limited.append(note)
        notes = iter(limited)

    dry_run = args.mode != "apply"
    try:
        service = DiaryService(db_path=str(args.db))
        stats = import_notes(
            service,
            notes,
            source=source.name,  # type: ignore[attr-defined]
            dry_run=dry_run,
            extra_tags=args.tag,
        )
    except Exception as exc:  # noqa: BLE001 - 含 NotesAccessError / CookieMissingError
        _print(f"✗ 导入失败：{exc}")
        return 3

    if args.json:
        _print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2))
        return 0

    mode_label = "预演（未写库）" if dry_run else "已落库"
    _print(f"来源：{stats.source}  模式：{mode_label}")
    _print(f"  扫描 {stats.total} 条 | 新增 {stats.imported} | 跳过重复 {stats.skipped} | 失败 {stats.failed}")
    if stats.undated:
        _print(f"  ⚠ 其中 {stats.undated} 条未识别出日期，已用默认日期兜底")
    for sample in stats.imported_samples:
        _print(f"    + {sample}")
    for sample in stats.skipped_samples:
        _print(f"    = {sample}")
    if stats.errors:
        _print("  错误样本：")
        for err in stats.errors[:5]:
            _print(f"    ! {err}")
    if dry_run and stats.imported:
        _print("\n确认无误后加 --apply 真正写入。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

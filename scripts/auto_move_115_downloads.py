#!/usr/bin/env python3
"""把 ~/Downloads 里「最近下载完成」的 115 视频搬移到目标目录。

规则（避免误搬无关文件）：
- 只处理「最近 RECENT_HOURS(48h) 内出现/被修改」的顶层条目；
- 忽略隐藏文件、隐藏目录，以及仍在下载的临时片
  （*.115chrome_*、*.cfg、*.crdownload、*.part、*.tmp …）；
- 文件：须为视频扩展名，且「稳定 STABILITY_SECONDS(120s) 没有再写入」才搬；
- 目录：须「含至少一个视频文件」且「整目录下载完成」（目录内无任一文件
  在 STABILITY 内被修改、无任何临时片、目录自身也稳定），整个目录一起搬；
- 目标已存在同名 → 自动加序号 ("xxx (1).mp4")。

用法：
  python auto_move_115_downloads.py --dry-run     # 只打印会搬哪些
  python auto_move_115_downloads.py               # 实际搬运（幂等，可反复运行）
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Iterator

VIDEO_EXTS = frozenset(
    {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".flv", ".wmv", ".ts", ".m2ts"}
)
# 下载临时片后缀 / 文件名片段（115 浏览器插件下载中为 *.115chrome_<ver>）
# 顶层需整体忽略的条目名（如 Telegram 单独的目标目录，避免串源）
_SKIP_NAMES = frozenset({"telegram desktop", "web"})
_PARTIAL_SUFFIXES = (
    ".crdownload",
    ".part",
    ".partial",
    ".tmp",
    ".temp",
    ".download",
    ".aria2",
    ".downloading",
    ".!complete",
    ".cfg",
)
_PARTIAL_MARKERS = (".115chrome_", ".uc!", "crum.")


def _is_partial(name: str) -> bool:
    low = name.lower()
    if low.endswith(_PARTIAL_SUFFIXES):
        return True
    return any(marker in low for marker in _PARTIAL_MARKERS)


def _is_video(name: str) -> bool:
    return Path(name).suffix.lower() in VIDEO_EXTS


def _file_stable(path: Path, now: float, stability: float) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    return st.st_size > 0 and (now - st.st_mtime) >= stability


def _is_recent(path: Path, now: float, recent_hours: float) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    return (now - st.st_mtime) <= recent_hours * 3600


def _tree_iter_files(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            if fname.startswith("."):
                continue
            yield Path(dirpath, fname)


def _tree_has_video(root: Path) -> bool:
    return any(_is_video(p.name) for p in _tree_iter_files(root))


def _dir_complete(root: Path, now: float, stability: float) -> bool:
    """整目录下载完成判定：无临时片、无近期被修改的文件、目录自身稳定。"""
    if not _is_video_major(root):
        return False
    try:
        if (now - root.stat().st_mtime) < stability:
            return False
    except OSError:
        return False
    for p in _tree_iter_files(root):
        if _is_partial(p.name):
            return False
        try:
            st = p.stat()
        except OSError:
            return False
        if st.st_size == 0 or (now - st.st_mtime) < stability:
            return False
    return True


def _is_video_major(root: Path) -> bool:
    """目录是否为「视频型下载」：含至少一个视频文件。"""
    return _tree_has_video(root)


def _unique_dest(dest_dir: Path, name: str) -> Path:
    cand = dest_dir / name
    if not cand.exists():
        return cand
    p = Path(name)
    stem, ext = p.stem, p.suffix
    i = 1
    while True:
        cand = dest_dir / f"{stem} ({i}){ext}"
        if not cand.exists():
            return cand
        i += 1


def _move(src: Path, dest_dir: Path, dry: bool, log) -> bool:
    target = _unique_dest(dest_dir, src.name)
    if dry:
        log.info("[dry-run] 将搬运 %s 下的一项 -> %s", src.parent, dest_dir)
        return True
    try:
        shutil.move(str(src), str(target))
        log.info("已搬运 %s 下的一项 -> %s", src.parent, dest_dir)
        return True
    except Exception:  # noqa: BLE001
        log.error("搬运失败（%s 下的一项，已跳过）", src.parent)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="/Users/imac/Downloads")
    parser.add_argument(
        "--dest", default="/Volumes/固态硬盘1T/009-暂存内容/115网盘下载"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--recent-hours", type=float, default=48.0)
    parser.add_argument("--stability", type=float, default=120.0)
    parser.add_argument(
        "--log",
        default=str(Path(__file__).resolve().parent.parent / "logs" / "115_download_mover.log"),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(args.log, encoding="utf-8")],
    )
    log = logging.getLogger("move115")
    if args.dry_run:
        log = logging.getLogger("move115-dry")
        logging.getLogger().handlers = [logging.StreamHandler(sys.stdout)]

    src_dir = Path(args.source).expanduser()
    dest_dir = Path(args.dest).expanduser()
    if not dest_dir.is_dir():
        print(f"目标目录不存在: {dest_dir}", file=sys.stderr)
        return 2
    if not src_dir.is_dir():
        print(f"源目录不存在: {src_dir}", file=sys.stderr)
        return 2

    now = time.time()
    moved_files = 0
    moved_dirs = 0

    for entry in sorted(os.scandir(src_dir), key=lambda e: e.name):
        name = entry.name
        if name.startswith(".") or _is_partial(name):
            continue
        if name.lower() in _SKIP_NAMES:
            continue
        if not _is_recent(Path(entry.path), now, args.recent_hours):
            continue
        if entry.is_dir(follow_symlinks=False):
            d = Path(entry.path)
            if _dir_complete(d, now, args.stability):
                if _move(d, dest_dir, args.dry_run, log):
                    moved_dirs += 1
            else:
                log.info("跳过目录（未完成/无视频/不满足稳定）")
        elif entry.is_file(follow_symlinks=False):
            f = Path(entry.path)
            if not _is_video(name) or not _file_stable(f, now, args.stability):
                continue
            if _move(f, dest_dir, args.dry_run, log):
                moved_files += 1

    log.info(
        "本轮完成：文件 %d 个，目录 %d 个%s",
        moved_files,
        moved_dirs,
        "（dry-run，未实际移动）" if args.dry_run else "",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
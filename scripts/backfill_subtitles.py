#!/usr/bin/env python3
"""字幕补齐脚本 — 用 yt-dlp 为 B站/YouTube 视频补齐字幕。

特点：
- 用 yt-dlp 下载官方字幕和自动生成字幕
- 支持 B站和 YouTube
- 慢速处理：每篇间隔 5-10 秒，避免风控
- 优先级：收藏的 > 阅读率高的 > 最近的 > 其他
- 断点续传：记录已处理的视频 ID
- 自动解析 SRT/VTT 字幕为纯文本，更新 articles.content_text
- 字幕保存到 article_snapshots 表（fetch_source='subtitle'）

用法：
    python scripts/backfill_subtitles.py --platform bilibili --batch-size 100
    python scripts/backfill_subtitles.py --platform youtube --batch-size 100
    python scripts/backfill_subtitles.py --status  # 查看进度
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/tmp/subtitle_backfill.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("subtitle_backfill")

# 数据库路径
DB_PATH = PROJECT_ROOT / "data" / "openbiliclaw.db"

# 进度文件（按平台分开）
PROGRESS_FILE_TEMPLATE = "/tmp/subtitle_backfill_{platform}_progress.json"
PROGRESS_FILE = Path("/tmp/subtitle_backfill_progress.json")  # 默认，会在 main 里覆盖


def load_progress() -> dict[str, Any]:
    """加载进度。"""
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "processed_ids": [],
        "success_ids": [],
        "failed_ids": [],
        "no_subtitle_ids": [],
        "total_processed": 0,
        "total_success": 0,
        "total_failed": 0,
        "total_no_subtitle": 0,
        "start_time": None,
        "last_update": None,
    }


def save_progress(progress: dict[str, Any]) -> None:
    """保存进度。"""
    progress["last_update"] = datetime.now(timezone.utc).isoformat()
    if len(progress["processed_ids"]) > 20000:
        progress["processed_ids"] = progress["processed_ids"][-20000:]
    try:
        with open(PROGRESS_FILE, "w") as f:
            json.dump(progress, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Failed to save progress: %s", e)


def get_videos_to_process(platform: str, batch_size: int,
                           only_favorited: bool = False,
                           exclude_ids: list[int] | None = None) -> list[dict[str, Any]]:
    """获取需要补齐字幕的视频列表。

    Args:
        platform: 平台（bilibili/youtube）
        batch_size: 每批数量
        only_favorited: 只处理收藏的
        exclude_ids: 要排除的已处理 ID 列表
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        conditions = [
            "source_type = ?",
            "url LIKE 'http%'",
            "(content_text = '' OR content_text IS NULL OR length(content_text) < 100)",
        ]
        params: list[Any] = [platform]

        if only_favorited:
            conditions.append("favorited = 1")

        # 排除已处理的 ID（最多排除 2000 个，避免 SQL 过长）
        if exclude_ids:
            exclude_list = exclude_ids[-2000:]
            placeholders = ",".join(["?"] * len(exclude_list))
            conditions.append(f"id NOT IN ({placeholders})")
            params.extend(exclude_list)

        where_clause = " AND ".join(conditions)

        query = f"""
            SELECT id, title, url, author, summary, published_at,
                   favorited, reading_percent, created_at
            FROM articles
            WHERE {where_clause}
            ORDER BY
                favorited DESC,
                reading_percent DESC,
                created_at DESC
            LIMIT ?
        """
        params.append(batch_size)

        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def parse_srt_to_text(srt_content: str) -> str:
    """将 SRT 字幕转换为纯文本。"""
    # 移除时间轴和序号
    lines = srt_content.split("\n")
    text_lines = []
    for line in lines:
        line = line.strip()
        # 跳过空行、序号、时间轴
        if not line:
            continue
        if re.match(r"^\d+$", line):
            continue
        if "-->" in line:
            continue
        # 移除 HTML 标签
        line = re.sub(r"<[^>]+>", "", line)
        text_lines.append(line)

    # 合并重复的行（自动字幕常见）
    merged = []
    prev = ""
    for line in text_lines:
        if line != prev:
            merged.append(line)
        prev = line

    return " ".join(merged)


def parse_vtt_to_text(vtt_content: str) -> str:
    """将 VTT 字幕转换为纯文本。"""
    lines = vtt_content.split("\n")
    text_lines = []
    skip_header = True

    for line in lines:
        line = line.strip()
        if skip_header:
            if line.startswith("WEBVTT") or not line:
                continue
            skip_header = False
        if not line:
            continue
        if "-->" in line:
            continue
        if re.match(r"^\d+$", line):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"\{[^}]+\}", "", line)
        text_lines.append(line)

    return " ".join(text_lines)


def check_has_subtitles(url: str, platform: str, use_browser_cookies: bool = True) -> bool:
    """先用 yt-dlp --list-subs 检查是否有字幕，避免浪费时间下载无字幕视频。"""
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--list-subs",
        "--no-warnings",
        "--quiet",
        "--no-check-certificates",
    ]

    if use_browser_cookies:
        cmd.extend(["--cookies-from-browser", "chrome"])

    # YouTube 不用代理（用户网络可直接访问，代理 IP 反而被 YouTube 封禁）

    cmd.append(url)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=45,
        )
        output = result.stdout + result.stderr
        # 检查输出中是否有字幕信息
        has_subs = ("Available subtitles" in output or
                    "Available automatic captions" in output or
                    "Language" in output)
        # 明确说没有字幕
        if "no subtitles" in output.lower() or "has no subtitles" in output.lower():
            return False
        return has_subs
    except Exception:
        # 检查失败时默认尝试下载（可能是网络问题）
        return True


def download_subtitles(url: str, platform: str, use_browser_cookies: bool = True,
                        max_retries: int = 3) -> tuple[str | None, str | None]:
    """用 yt-dlp 下载字幕，带重试机制。

    SSL 错误是间歇性的，重试通常能解决。

    Returns:
        (subtitle_text, subtitle_format) 元组，失败返回 (None, None)
    """
    for attempt in range(max_retries):
        if attempt > 0:
            wait_time = 5 * attempt
            logger.debug("重试第 %d/%d 次，等待 %d 秒...", attempt + 1, max_retries, wait_time)
            time.sleep(wait_time)

        result = _download_subtitles_once(url, platform, use_browser_cookies)
        if result[0] is not None:
            return result
        # 失败继续重试（除非是明确无字幕）

    return None, None


def _download_subtitles_once(url: str, platform: str,
                               use_browser_cookies: bool = True) -> tuple[str | None, str | None]:
    """单次下载字幕（不带重试）。"""
    tmpdir = tempfile.mkdtemp(prefix="subtitle_")
    output_template = os.path.join(tmpdir, "subtitle.%(ext)s")

    # 构建 yt-dlp 命令
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", "zh,en,zh-Hans,zh-CN,en-US",
        "--sub-format", "srt/vtt",
        "-o", output_template,
        "--no-warnings",
        "--quiet",
        "--retries", "3",
        "--fragment-retries", "3",
        "--no-check-certificates",
    ]

    # 使用浏览器 cookies
    if use_browser_cookies:
        cmd.extend(["--cookies-from-browser", "chrome"])

    # YouTube 不用代理（用户网络可直接访问，代理 IP 反而被 YouTube 封禁）

    cmd.append(url)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=90,
        )

        # 查找下载的字幕文件
        subtitle_files = list(Path(tmpdir).glob("subtitle*"))
        subtitle_files = [f for f in subtitle_files if f.suffix in (".srt", ".vtt")]

        if not subtitle_files:
            return None, None

        # 优先选择中文字幕
        zh_files = [f for f in subtitle_files if "zh" in f.name.lower()]
        if zh_files:
            subtitle_file = zh_files[0]
        else:
            subtitle_file = subtitle_files[0]

        # 读取字幕内容
        content = subtitle_file.read_text(encoding="utf-8", errors="ignore")

        # 解析为纯文本
        if subtitle_file.suffix == ".srt":
            text = parse_srt_to_text(content)
            fmt = "srt"
        else:
            text = parse_vtt_to_text(content)
            fmt = "vtt"

        # 清理临时文件
        for f in Path(tmpdir).glob("*"):
            f.unlink()
        Path(tmpdir).rmdir()

        if len(text) < 20:
            return None, None

        return text, fmt

    except subprocess.TimeoutExpired:
        logger.debug("yt-dlp timeout for %s", url)
        return None, None
    except Exception as e:
        logger.debug("yt-dlp failed for %s: %s", url, e)
        return None, None
    finally:
        # 确保清理临时目录
        try:
            for f in Path(tmpdir).glob("*"):
                f.unlink()
            Path(tmpdir).rmdir()
        except Exception:
            pass


def save_subtitle(article_id: int, url: str, subtitle_text: str,
                   subtitle_format: str) -> bool:
    """保存字幕后更新文章。"""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            now = datetime.now(timezone.utc).isoformat()

            # 更新 articles.content_text
            conn.execute(
                "UPDATE articles SET content_text = ?, updated_at = ? WHERE id = ?",
                (subtitle_text[:500000], now, article_id),
            )

            # 保存到 article_snapshots 表
            conn.execute(
                """INSERT INTO article_snapshots
                   (article_id, url, content_html, content_text, fetch_source, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (article_id, url, "", subtitle_text[:200000],
                 f"subtitle_{subtitle_format}", now),
            )

            conn.commit()
            return True
    except Exception as e:
        logger.warning("Failed to save subtitle for article %s: %s", article_id, e)
        return False


def print_status() -> None:
    """打印当前进度状态。"""
    progress = load_progress()

    with sqlite3.connect(DB_PATH) as conn:
        for platform in ["bilibili", "youtube"]:
            total = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE source_type = ?", (platform,)
            ).fetchone()[0]
            with_subtitle = conn.execute(
                """SELECT COUNT(*) FROM articles
                   WHERE source_type = ? AND content_text != ''
                   AND length(content_text) > 100""",
                (platform,),
            ).fetchone()[0]
            print(f"\n=== {platform} ===")
            print(f"  总数: {total}")
            print(f"  有字幕/内容: {with_subtitle} ({with_subtitle/total*100:.1f}%)")
            print(f"  无字幕: {total - with_subtitle}")

    print("\n" + "=" * 60)
    print("本次运行进度")
    print("=" * 60)
    print(f"已处理: {progress['total_processed']}")
    print(f"  成功: {progress['total_success']}")
    print(f"  无字幕: {progress['total_no_subtitle']}")
    print(f"  失败: {progress['total_failed']}")
    if progress["start_time"]:
        elapsed = time.time() - datetime.fromisoformat(progress["start_time"]).timestamp()
        print(f"运行时间: {elapsed/3600:.1f} 小时")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="字幕补齐脚本")
    parser.add_argument("--platform", type=str, required=True,
                        choices=["bilibili", "youtube"], help="平台")
    parser.add_argument("--batch-size", type=int, default=100, help="每批处理数量")
    parser.add_argument("--delay-min", type=float, default=5.0, help="最小延迟（秒）")
    parser.add_argument("--delay-max", type=float, default=10.0, help="最大延迟（秒）")
    parser.add_argument("--only-favorited", action="store_true", help="只处理收藏的视频")
    parser.add_argument("--status", action="store_true", help="查看进度状态")
    parser.add_argument("--max-runs", type=int, default=0, help="最大运行批次数（0=无限）")
    args = parser.parse_args()

    # 按平台设置进度文件
    global PROGRESS_FILE
    PROGRESS_FILE = Path(PROGRESS_FILE_TEMPLATE.format(platform=args.platform))

    if args.status:
        print_status()
        return

    logger.info("=" * 60)
    logger.info("字幕补齐任务启动 — 平台: %s", args.platform)
    logger.info("批次大小: %d, 延迟: %.1f-%.1f秒",
                args.batch_size, args.delay_min, args.delay_max)
    logger.info("=" * 60)

    progress = load_progress()
    if not progress["start_time"]:
        progress["start_time"] = datetime.now(timezone.utc).isoformat()

    run_count = 0
    consecutive_empty_batches = 0  # 连续无新视频的批次数

    try:
        while True:
            run_count += 1
            if args.max_runs > 0 and run_count > args.max_runs:
                logger.info("达到最大运行批次数，退出")
                break

            logger.info("--- 第 %d 批 ---", run_count)

            videos = get_videos_to_process(
                platform=args.platform,
                batch_size=args.batch_size,
                only_favorited=args.only_favorited,
                exclude_ids=progress["processed_ids"],
            )

            if not videos:
                logger.info("没有更多需要处理的视频，任务完成！")
                break

            # 过滤掉已处理的视频
            new_videos = [v for v in videos if v["id"] not in progress["processed_ids"]]

            if not new_videos:
                consecutive_empty_batches += 1
                logger.info("本批 %d 个视频全部已处理，连续 %d 批无新视频",
                           len(videos), consecutive_empty_batches)
                if consecutive_empty_batches >= 5:
                    logger.info("连续 5 批无新视频，任务完成！")
                    break
                # 快速继续下一批（不延迟）
                continue

            consecutive_empty_batches = 0
            logger.info("获取到 %d 个视频待处理（%d 个新视频）", len(videos), len(new_videos))

            for i, video in enumerate(new_videos, 1):
                article_id = video["id"]
                url = video["url"]
                title = video["title"][:50] if video["title"] else "无标题"

                # 跳过已处理的
                if article_id in progress["processed_ids"]:
                    continue

                logger.info("[%d/%d] 处理: %s (id=%d)", i, len(videos), title, article_id)

                # 先检查是否有字幕（避免浪费时间下载无字幕视频）
                has_subs = check_has_subtitles(url, args.platform)
                if not has_subs:
                    progress["total_no_subtitle"] += 1
                    progress["no_subtitle_ids"].append(article_id)
                    progress["total_processed"] += 1
                    progress["processed_ids"].append(article_id)
                    logger.info("  - 无字幕可用（跳过下载）")
                    if progress["total_processed"] % 10 == 0:
                        save_progress(progress)
                    delay = random.uniform(args.delay_min, args.delay_max)
                    time.sleep(delay)
                    continue

                # 下载字幕（带重试）
                subtitle_text, subtitle_format = download_subtitles(url, args.platform, max_retries=3)

                if subtitle_text:
                    success = save_subtitle(article_id, url, subtitle_text, subtitle_format)
                    if success:
                        progress["total_success"] += 1
                        progress["success_ids"].append(article_id)
                        logger.info("  ✓ 字幕保存成功 (%d 字符, 格式: %s)",
                                   len(subtitle_text), subtitle_format)
                    else:
                        progress["total_failed"] += 1
                        progress["failed_ids"].append(article_id)
                        logger.warning("  ✗ 字幕保存失败")
                else:
                    progress["total_no_subtitle"] += 1
                    progress["no_subtitle_ids"].append(article_id)
                    logger.info("  - 无字幕可用")

                progress["total_processed"] += 1
                progress["processed_ids"].append(article_id)

                # 每处理 10 个保存一次进度
                if progress["total_processed"] % 10 == 0:
                    save_progress(progress)

                # 延迟
                delay = random.uniform(args.delay_min, args.delay_max)
                time.sleep(delay)

            save_progress(progress)
            logger.info("第 %d 批完成，累计: 成功=%d, 无字幕=%d, 失败=%d",
                       run_count, progress["total_success"],
                       progress["total_no_subtitle"], progress["total_failed"])

    except KeyboardInterrupt:
        logger.info("用户中断，保存进度...")
        save_progress(progress)
    except Exception as e:
        logger.exception("任务异常: %s", e)
        save_progress(progress)
    finally:
        save_progress(progress)
        logger.info("任务结束")
        print_status()


if __name__ == "__main__":
    main()

"""统一 Producer 调度器

按顺序运行各个 platform producer，避免 15+ 进程同时写 SQLite 导致 database is locked。
每个 producer 运行一次后退出，全部完成后 sleep 到下一轮。

用法:
    python -m openbiliclaw.runtime.producer_scheduler --interval 6
    python -m openbiliclaw.runtime.producer_scheduler --once  # 只运行一轮
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Producer 定义: (名称, 脚本路径, 额外参数, 超时秒数)
# feed producer 默认 loop，用 timeout 限制单次运行时间
# favorites producer 支持 --loop，不加就是 run once
PRODUCERS: list[tuple[str, str, list[str], int]] = [
    # Feed producers (timeout 限制单次运行)
    ("bili-feed", "bilibili_producer.py", [], 600),
    ("douyin-feed", "douyin_producer.py", ["--interval", "1"], 900),
    ("xhs-feed", "xhs_producer.py", [], 600),
    ("zhihu-feed", "zhihu_producer.py", [], 600),
    ("youtube-feed", "youtube_producer.py", [], 600),
    ("xiaoyuzhou-feed", "xiaoyuzhou_feed_producer.py", [], 300),
    ("v2ex-api", "v2ex_producer.py", ["--mode", "api"], 300),
    ("v2ex-rss", "v2ex_producer.py", ["--mode", "rss"], 300),
    ("hupu-feed", "hupu_feed_producer.py", [], 300),
    ("toutiao-feed", "toutiao_feed_producer.py", [], 300),
    # Favorites producers (run once, 不加 --loop)
    ("bili-favorites", "bilibili_favorites_producer.py", ["--all"], 600),
    ("xhs-favorites", "xhs_favorites_producer.py", [], 600),
    ("zhihu-favorites", "zhihu_favorites_producer.py", [], 600),
    ("xiaoyuzhou-favorites", "xiaoyuzhou_favorites_producer.py", ["--all"], 600),
    ("x-favorites", "x_favorites_producer.py", ["--all"], 600),
]

RUNTIME_DIR = Path(__file__).parent


def run_producer(name: str, script: str, extra_args: list[str], timeout: int) -> bool:
    """运行单个 producer，返回是否成功"""
    script_path = RUNTIME_DIR / script
    cmd = [sys.executable, str(script_path), *extra_args]
    logger.info("[%s] starting (timeout=%ds)...", name, timeout)
    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(RUNTIME_DIR.parent.parent.parent),  # 项目根目录
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed = time.time() - start
        if result.returncode == 0:
            logger.info("[%s] done in %.0fs", name, elapsed)
            return True
        logger.warning("[%s] exit code %d in %.0fs", name, result.returncode, elapsed)
        # 输出最后几行 stderr 便于排查
        if result.stderr:
            last_lines = result.stderr.strip().splitlines()[-5:]
            for line in last_lines:
                logger.warning("[%s] stderr: %s", name, line)
        return False
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        logger.info("[%s] timeout after %.0fs (normal for loop-mode)", name, elapsed)
        return True
    except Exception as e:
        logger.error("[%s] failed: %s", name, e)
        return False


def run_all() -> dict[str, bool]:
    """按顺序运行所有 producer"""
    results: dict[str, bool] = {}
    for name, script, extra_args, timeout in PRODUCERS:
        results[name] = run_producer(name, script, extra_args, timeout)
        # producer 之间短暂间隔，让数据库有时间处理
        time.sleep(2)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified producer scheduler")
    parser.add_argument("--interval", type=int, default=6, help="Loop interval in hours (default: 6)")
    parser.add_argument("--once", action="store_true", help="Run only one cycle then exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    logger.info("producer scheduler started (interval=%dh, once=%s)", args.interval, args.once)

    while True:
        cycle_start = time.time()
        logger.info("=== cycle start ===")
        results = run_all()
        success = sum(1 for v in results.values() if v)
        total = len(results)
        elapsed = time.time() - cycle_start
        logger.info("=== cycle done: %d/%d success in %.0fs ===", success, total, elapsed)

        if args.once:
            break

        sleep_seconds = args.interval * 3600
        logger.info("sleeping %dh until next cycle", args.interval)
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()

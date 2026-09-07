#!/usr/bin/env python3
"""慢速快照补齐脚本 — 后台运行，为阅读库中的文章补齐 HTML 快照。

特点：
- 慢速处理：每篇间隔 5-10 秒随机，避免触发风控
- 优先级：收藏的 > 阅读率高的 > 最近的 > 其他
- 平台策略：优先处理无需登录的平台，跳过视频平台
- 断点续传：记录已处理的文章 ID，重启后继续
- 错误处理：失败的文章跳过，不阻塞后续处理
- 日志记录：详细记录处理进度和结果

用法：
    python scripts/backfill_snapshots.py --batch-size 100 --delay-min 5 --delay-max 10
    python scripts/backfill_snapshots.py --platform v2ex --batch-size 50
    python scripts/backfill_snapshots.py --status  # 查看进度
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sqlite3
import sys
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
        logging.FileHandler("/tmp/snapshot_backfill.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("snapshot_backfill")

# 数据库路径
DB_PATH = PROJECT_ROOT / "data" / "openbiliclaw.db"

# 进度文件
PROGRESS_FILE = Path("/tmp/snapshot_backfill_progress.json")

# 平台策略
PLATFORM_STRATEGY = {
    # 无需登录，可以直接抓取
    "v2ex": {"enabled": False, "need_login": False, "priority": 0, "reason": "Cloudflare 保护，跳过"},
    "web": {"enabled": True, "need_login": False, "priority": 1},
    "sspai": {"enabled": True, "need_login": False, "priority": 2},
    "juejin": {"enabled": True, "need_login": False, "priority": 2},
    "36kr": {"enabled": True, "need_login": False, "priority": 2},
    "huxiu": {"enabled": True, "need_login": False, "priority": 2},
    "csdn": {"enabled": True, "need_login": False, "priority": 3},
    "jianshu": {"enabled": True, "need_login": False, "priority": 3},
    "douban": {"enabled": True, "need_login": False, "priority": 3},
    "gcores": {"enabled": True, "need_login": False, "priority": 3},
    # 需要登录，但可以用 cookies
    "zhihu": {"enabled": True, "need_login": True, "priority": 2},
    "xiaohongshu": {"enabled": True, "need_login": True, "priority": 3},
    "weibo": {"enabled": True, "need_login": True, "priority": 3},
    "wechat": {"enabled": True, "need_login": False, "priority": 2},
    # 视频平台，跳过（有字幕就够了）
    "youtube": {"enabled": False, "need_login": False, "priority": 0, "reason": "视频平台，跳过"},
    "bilibili": {"enabled": False, "need_login": False, "priority": 0, "reason": "视频平台，跳过"},
    "douyin": {"enabled": False, "need_login": False, "priority": 0, "reason": "视频平台，跳过"},
    "xiaoyuzhou": {"enabled": False, "need_login": False, "priority": 0, "reason": "播客平台，跳过"},
    # 其他
    "rss": {"enabled": False, "need_login": False, "priority": 0, "reason": "RSS 内容，跳过"},
    "hupu": {"enabled": True, "need_login": False, "priority": 3},
}

# User-Agent 列表（轮换使用，避免风控）
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


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
        "failed_ids": [],
        "skipped_ids": [],
        "total_processed": 0,
        "total_success": 0,
        "total_failed": 0,
        "total_skipped": 0,
        "start_time": None,
        "last_update": None,
    }


def save_progress(progress: dict[str, Any]) -> None:
    """保存进度。"""
    progress["last_update"] = datetime.now(timezone.utc).isoformat()
    # 只保留最近的 10000 个 ID，避免文件过大
    if len(progress["processed_ids"]) > 10000:
        progress["processed_ids"] = progress["processed_ids"][-10000:]
    try:
        with open(PROGRESS_FILE, "w") as f:
            json.dump(progress, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Failed to save progress: %s", e)


def get_articles_to_process(batch_size: int, platform: str | None = None,
                            only_favorited: bool = False) -> list[dict[str, Any]]:
    """获取需要处理的文章列表。

    优先级：收藏的 > 阅读率高的 > 最近的 > 其他
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        conditions = [
            "a.url LIKE 'http%'",
            "a.content_text != ''",
            "a.content_text IS NOT NULL",
        ]
        params: list[Any] = []

        # 排除已有快照的文章
        conditions.append("""
            a.id NOT IN (SELECT DISTINCT article_id FROM article_snapshots)
        """)

        # 平台过滤
        if platform:
            conditions.append("a.source_type = ?")
            params.append(platform)
        else:
            # 只处理启用的平台
            enabled_platforms = [p for p, s in PLATFORM_STRATEGY.items() if s["enabled"]]
            placeholders = ",".join(["?"] * len(enabled_platforms))
            conditions.append(f"a.source_type IN ({placeholders})")
            params.extend(enabled_platforms)

        # 只处理收藏的
        if only_favorited:
            conditions.append("a.favorited = 1")

        where_clause = " AND ".join(conditions)

        # 按优先级排序：收藏 > 阅读率 > 最近
        query = f"""
            SELECT a.id, a.title, a.url, a.source_type, a.source_name,
                   a.author, a.summary, a.content_text, a.published_at,
                   a.favorited, a.reading_percent, a.created_at
            FROM articles a
            WHERE {where_clause}
            ORDER BY
                a.favorited DESC,
                a.reading_percent DESC,
                a.created_at DESC
            LIMIT ?
        """
        params.append(batch_size)

        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def fetch_snapshot(url: str, source_type: str, cookies: dict[str, str] | None = None) -> tuple[str | None, str | None]:
    """抓取文章 HTML 快照。

    Returns:
        (content_html, content_text) 元组，失败返回 (None, None)
    """
    try:
        import requests

        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }

        # 添加 cookies
        if cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
            headers["Cookie"] = cookie_str

        # 设置代理（如果环境变量中有）
        proxies = {}
        import os
        if os.environ.get("http_proxy"):
            proxies["http"] = os.environ["http_proxy"]
        if os.environ.get("https_proxy"):
            proxies["https"] = os.environ["https_proxy"]

        response = requests.get(
            url,
            headers=headers,
            timeout=20,
            allow_redirects=True,
            proxies=proxies if proxies else None,
        )
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"

        content_html = response.text

        # 简单提取纯文本（用 BeautifulSoup）
        content_text = ""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content_html, "lxml")
            # 移除不需要的标签
            for tag in soup(["script", "style", "nav", "header", "footer", "aside", "iframe"]):
                tag.decompose()
            content_text = soup.get_text(separator="\n", strip=True)
        except Exception:
            pass

        return content_html, content_text

    except Exception as e:
        logger.debug("Failed to fetch %s: %s", url, e)
        return None, None


def save_snapshot(article_id: int, url: str, content_html: str,
                  content_text: str, fetch_source: str = "backfill") -> bool:
    """保存快照到数据库。"""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """INSERT INTO article_snapshots
                   (article_id, url, content_html, content_text, fetch_source, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (article_id, url, content_html[:500000], content_text[:200000], fetch_source, now),
            )
            conn.commit()
            return True
    except Exception as e:
        logger.warning("Failed to save snapshot for article %s: %s", article_id, e)
        return False


def load_cookies() -> dict[str, str]:
    """从配置文件加载 cookies。"""
    cookies = {}
    try:
        config_path = PROJECT_ROOT / "config.toml"
        if config_path.exists():
            import tomllib
            with open(config_path, "rb") as f:
                config = tomllib.load(f)
            # 尝试从配置中读取 cookies
            if "zhihu" in config and "cookies" in config["zhihu"]:
                cookies.update(config["zhihu"]["cookies"])
            if "xiaohongshu" in config and "cookies" in config["xiaohongshu"]:
                cookies.update(config["xiaohongshu"]["cookies"])
    except Exception as e:
        logger.debug("Failed to load cookies: %s", e)
    return cookies


def print_status() -> None:
    """打印当前进度状态。"""
    progress = load_progress()

    with sqlite3.connect(DB_PATH) as conn:
        total = conn.execute("SELECT COUNT(*) FROM articles WHERE url LIKE 'http%'").fetchone()[0]
        with_snapshot = conn.execute("SELECT COUNT(DISTINCT article_id) FROM article_snapshots").fetchone()[0]

    print("=" * 60)
    print("快照补齐进度")
    print("=" * 60)
    print(f"总文章数: {total}")
    print(f"已有快照: {with_snapshot} ({with_snapshot/total*100:.1f}%)")
    print(f"待处理: {total - with_snapshot}")
    print("-" * 60)
    print(f"本次运行处理: {progress['total_processed']}")
    print(f"  成功: {progress['total_success']}")
    print(f"  失败: {progress['total_failed']}")
    print(f"  跳过: {progress['total_skipped']}")
    if progress["start_time"]:
        elapsed = time.time() - datetime.fromisoformat(progress["start_time"]).timestamp()
        print(f"运行时间: {elapsed/3600:.1f} 小时")
        if progress["total_processed"] > 0:
            avg_time = elapsed / progress["total_processed"]
            print(f"平均每篇: {avg_time:.1f} 秒")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="慢速快照补齐脚本")
    parser.add_argument("--batch-size", type=int, default=100, help="每批处理数量")
    parser.add_argument("--delay-min", type=float, default=5.0, help="最小延迟（秒）")
    parser.add_argument("--delay-max", type=float, default=10.0, help="最大延迟（秒）")
    parser.add_argument("--platform", type=str, default=None, help="只处理指定平台")
    parser.add_argument("--only-favorited", action="store_true", help="只处理收藏的文章")
    parser.add_argument("--status", action="store_true", help="查看进度状态")
    parser.add_argument("--max-runs", type=int, default=0, help="最大运行批次数（0=无限）")
    args = parser.parse_args()

    if args.status:
        print_status()
        return

    logger.info("=" * 60)
    logger.info("快照补齐任务启动")
    logger.info("批次大小: %d, 延迟: %.1f-%.1f秒", args.batch_size, args.delay_min, args.delay_max)
    if args.platform:
        logger.info("平台过滤: %s", args.platform)
    if args.only_favorited:
        logger.info("只处理收藏的文章")
    logger.info("=" * 60)

    # 加载进度
    progress = load_progress()
    if not progress["start_time"]:
        progress["start_time"] = datetime.now(timezone.utc).isoformat()

    # 加载 cookies
    cookies = load_cookies()
    if cookies:
        logger.info("已加载 %d 个 cookies", len(cookies))

    run_count = 0

    try:
        while True:
            run_count += 1
            if args.max_runs > 0 and run_count > args.max_runs:
                logger.info("达到最大运行批次数，退出")
                break

            logger.info("--- 第 %d 批 ---", run_count)

            # 获取需要处理的文章
            articles = get_articles_to_process(
                batch_size=args.batch_size,
                platform=args.platform,
                only_favorited=args.only_favorited,
            )

            if not articles:
                logger.info("没有更多需要处理的文章，任务完成！")
                break

            logger.info("获取到 %d 篇文章待处理", len(articles))

            for i, article in enumerate(articles, 1):
                article_id = article["id"]
                url = article["url"]
                source_type = article["source_type"]
                title = article["title"][:50] if article["title"] else "无标题"

                # 检查平台策略
                strategy = PLATFORM_STRATEGY.get(source_type, {"enabled": True, "need_login": False})
                if not strategy["enabled"]:
                    logger.debug("[%d/%d] 跳过 %s (平台策略: %s)",
                                i, len(articles), source_type, strategy.get("reason", "禁用"))
                    progress["total_skipped"] += 1
                    progress["skipped_ids"].append(article_id)
                    continue

                # 需要登录但没有 cookies 的平台，跳过
                if strategy["need_login"] and not cookies:
                    logger.debug("[%d/%d] 跳过 %s (需要登录但无 cookies)", i, len(articles), source_type)
                    progress["total_skipped"] += 1
                    progress["skipped_ids"].append(article_id)
                    continue

                logger.info("[%d/%d] 处理: [%s] %s (id=%d)",
                           i, len(articles), source_type, title, article_id)

                # 抓取快照
                content_html, content_text = fetch_snapshot(url, source_type, cookies)

                if content_html:
                    # 保存快照
                    success = save_snapshot(article_id, url, content_html, content_text)
                    if success:
                        progress["total_success"] += 1
                        logger.info("  ✓ 快照保存成功 (HTML: %d bytes, 文本: %d bytes)",
                                   len(content_html), len(content_text or ""))
                    else:
                        progress["total_failed"] += 1
                        progress["failed_ids"].append(article_id)
                        logger.warning("  ✗ 快照保存失败")
                else:
                    progress["total_failed"] += 1
                    progress["failed_ids"].append(article_id)
                    logger.warning("  ✗ 抓取失败")

                progress["total_processed"] += 1
                progress["processed_ids"].append(article_id)

                # 每处理 10 篇保存一次进度
                if progress["total_processed"] % 10 == 0:
                    save_progress(progress)
                    logger.info("进度已保存 (已处理: %d, 成功: %d)",
                               progress["total_processed"], progress["total_success"])

                # 延迟，避免风控
                delay = random.uniform(args.delay_min, args.delay_max)
                time.sleep(delay)

            # 每批结束后保存进度
            save_progress(progress)
            logger.info("第 %d 批完成，累计处理: %d (成功: %d, 失败: %d, 跳过: %d)",
                       run_count, progress["total_processed"],
                       progress["total_success"], progress["total_failed"],
                       progress["total_skipped"])

    except KeyboardInterrupt:
        logger.info("用户中断，保存进度...")
        save_progress(progress)
        print_status()
    except Exception as e:
        logger.exception("任务异常: %s", e)
        save_progress(progress)
    finally:
        save_progress(progress)
        logger.info("任务结束")
        print_status()


if __name__ == "__main__":
    main()

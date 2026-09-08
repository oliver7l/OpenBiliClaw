"""克隆引擎：从 URL 克隆网站。

支持通过 wget 或 httrack 将远程网站克隆到本地目录。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _is_tool_available(name: str) -> bool:
    """检查系统命令是否可用。"""
    return shutil.which(name) is not None


def clone_website(
    url: str,
    output_dir: str | Path,
    depth: int = 1,
    timeout: int = 300,
) -> dict:
    """克隆一个网站到本地目录。

    优先使用 wget，若不可用则尝试 httrack。

    Args:
        url: 要克隆的网站 URL
        output_dir: 输出目录
        depth: 递归深度
        timeout: 超时时间（秒）

    Returns:
        dict: {success, message, size_bytes, file_count}
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    start = time.time()

    if _is_tool_available("wget"):
        return _clone_with_wget(url, output_path, depth, timeout)
    elif _is_tool_available("httrack"):
        return _clone_with_httrack(url, output_path, depth, timeout)
    else:
        return _clone_with_playwright(url, output_path, timeout)


def _clone_with_wget(
    url: str,
    output_dir: Path,
    depth: int = 1,
    timeout: int = 300,
) -> dict:
    """使用 wget 克隆网站。"""
    logger.info("使用 wget 克隆 %s -> %s", url, output_dir)

    try:
        result = subprocess.run(
            [
                "wget",
                "--mirror",
                "--convert-links",
                "--adjust-extension",
                "--page-requisites",
                "--no-parent",
                "--no-check-certificate",
                f"--level={depth}",
                f"--directory-prefix={output_dir}",
                "--timeout=30",
                "--tries=3",
                "--wait=1",
                "--random-wait",
                "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "克隆超时", "size_bytes": 0, "file_count": 0}
    except FileNotFoundError:
        return {"success": False, "message": "wget 未安装", "size_bytes": 0, "file_count": 0}

    if result.returncode not in (0, 4, 8):
        logger.warning("wget 返回非零状态 %d: %s", result.returncode, result.stderr[:500])
        return {"success": False, "message": f"wget 失败: {result.stderr[:200]}", "size_bytes": 0, "file_count": 0}

    return _count_output(output_dir)


def _clone_with_httrack(
    url: str,
    output_dir: Path,
    depth: int = 1,
    timeout: int = 300,
) -> dict:
    """使用 httrack 克隆网站。"""
    logger.info("使用 httrack 克隆 %s -> %s", url, output_dir)

    try:
        result = subprocess.run(
            [
                "httrack",
                url,
                "-O", str(output_dir),
                f"-r{depth}",
                "--disable-security-limits",
                "-v",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "克隆超时", "size_bytes": 0, "file_count": 0}
    except FileNotFoundError:
        return {"success": False, "message": "httrack 未安装", "size_bytes": 0, "file_count": 0}

    if result.returncode != 0:
        return {"success": False, "message": f"httrack 失败: {result.stderr[:200]}", "size_bytes": 0, "file_count": 0}

    return _count_output(output_dir)


def _clone_with_playwright(
    url: str,
    output_dir: Path,
    timeout: int = 300,
) -> dict:
    """备用方案：使用 playwright 保存页面（简单单页克隆）。"""
    logger.info("使用 playwright 保存 %s", url)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"success": False, "message": "未安装 playwright，请安装 wget 或 httrack", "size_bytes": 0, "file_count": 0}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(url, timeout=timeout * 1000, wait_until="networkidle")
            html = page.content()
            browser.close()

        output_path = output_dir / "index.html"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")

        return _count_output(output_dir)
    except Exception as exc:
        return {"success": False, "message": f"playwright 克隆失败: {exc}", "size_bytes": 0, "file_count": 0}


def _count_output(directory: Path) -> dict:
    """统计输出目录的文件大小和数量。"""
    total_size = 0
    total_files = 0
    for f in directory.rglob("*"):
        if f.is_file():
            total_size += f.stat().st_size
            total_files += 1
    return {
        "success": True,
        "message": "克隆完成",
        "size_bytes": total_size,
        "file_count": total_files,
    }
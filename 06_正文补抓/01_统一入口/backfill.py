#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补正文全家桶 · 统一入口

所有"给文章补正文"的通道都从这里走，一个命令一个通道，便于跟踪和自动化引用。
详细任务清单、缺口统计、变更日志见同目录《任务总览.md》。

用法:
    python3 backfill.py status            # 全家桶体检（缺口/成功率/红灯）
    python3 backfill.py status --quiet    # 静默模式：无警报时不输出（供自动化判断）
    python3 backfill.py unified [N]       # 统一补抓（知乎/YouTube/B站/其他），默认 100 条
    python3 backfill.py getnote [N]       # 得到大脑通道（配额 1000/天，频率不受限），默认 45
    python3 backfill.py xtoken [args...]  # 小红书 token 桥接（自己的 Cookie，必须极低频！）
    python3 backfill.py fetch <url>       # 单条抓取（调试/手工补篇）

频率铁律（Aaron 2026-09-18 定）:
    - 小红书桥接走自己的 Cookie → 极低频（每小时 1 条，captcha 硬停）
    - 得到大脑是配额型 → 不受频率限制，撑满 1000/天即可
    - 其余通道走每日一轮即可
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CLAW_ROOT = Path(__file__).resolve().parents[2]          # OpenBiliClaw/
EXEC_SCRIPTS = Path(__file__).resolve().parents[1] / "02_执行脚本"
CONTENT_LIB = CLAW_ROOT / "scripts" / "content_library"

USAGE = __doc__


def _run(cmd: list[str], env_extra: dict | None = None, cwd: Path = CLAW_ROOT) -> int:
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run([str(c) for c in cmd], env=env, cwd=str(cwd)).returncode


def cmd_status(args: list[str]) -> int:
    return _run([sys.executable, CONTENT_LIB / "backfill_status.py", *args])


def cmd_unified(args: list[str]) -> int:
    limit = args[0] if args else "100"
    return _run([sys.executable, CONTENT_LIB / "refill_library_bodies_v2.py", limit])


def cmd_getnote(args: list[str]) -> int:
    limit = args[0] if args else "45"
    env_extra = {"PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")}
    return _run([sys.executable, EXEC_SCRIPTS / "refill_via_getnote.py", "--limit", limit],
                env_extra=env_extra)


def cmd_xtoken(args: list[str]) -> int:
    """小红书 token 桥接。默认 1 条/轮；args 原样透传（支持 --dry-run/--reset 等）。"""
    return _run([sys.executable, EXEC_SCRIPTS / "backfill_xhs_tokens.py", *args])


def cmd_fetch(args: list[str]) -> int:
    if not args:
        print("用法: backfill.py fetch <url>")
        return 2
    return _run([sys.executable, CONTENT_LIB / "fetch_hub.py", *args])


COMMANDS = {
    "status": cmd_status,
    "unified": cmd_unified,
    "getnote": cmd_getnote,
    "xtoken": cmd_xtoken,
    "fetch": cmd_fetch,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(USAGE)
        return 2
    return COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())

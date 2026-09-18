#!/usr/bin/env python3
"""合并早报（08:00）：日记晨间简报 + 求职简报 + 今日刷题 + 阅读库日报 + 复盘材料包。

依次运行五个脚本，去掉各自的 JSON 末行，用分隔线拼成一条可直接发送的文本。
「复盘材料包」仅在近两天有未复盘面试 / 口述提到面试时出现（草稿文件在
data/daily_snapshots/review_drafts/，自动化需据此生成复盘草稿）。

用法：
    .venv/bin/python scripts/daily/morning_digest.py
    .venv/bin/python scripts/daily/morning_digest.py --job-days 3 --loops 3 --top 5

输出：合并后的早报文本 + 最后一行 JSON（各段条数/是否有失败），供自动化判断。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "bin" / "python"
SCRIPT_DIR = ROOT / "scripts" / "daily"

SECTIONS = (
    ("diary", "diary_morning.py", ["--loops", "{loops}"]),
    ("job", "job_brief.py", ["--days", "{job_days}"]),
    ("quiz", "quiz_daily.py", []),
    ("reading", "reading_daily.py", ["--top", "{top}"]),
    ("review", "review_digest.py", []),
)


def run_section(script: str, extra: list[str]) -> tuple[bool, str]:
    """跑单个脚本，返回 (是否成功, 去掉 JSON 末行后的文本)。"""
    cmd = [str(PY), str(SCRIPT_DIR / script), *extra]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=180)
    lines = (proc.stdout or "").rstrip().splitlines()
    # 末行是给机器读的 JSON，早报里不需要
    if lines and lines[-1].lstrip().startswith("{"):
        try:
            json.loads(lines[-1])
            lines = lines[:-1]
        except ValueError:
            pass
    text = "\n".join(lines).strip()
    ok = proc.returncode == 0 and bool(text)
    if not ok:
        err = (proc.stderr or "").strip().splitlines()
        text = f"⚠️ {script} 运行异常：{err[-1][:200] if err else f'退出码 {proc.returncode}'}"
    return ok, text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-days", type=int, default=3)
    parser.add_argument("--loops", type=int, default=3)
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args()

    values = {"job_days": args.job_days, "loops": args.loops, "top": args.top}
    blocks, failed = [], []
    for key, script, tpl in SECTIONS:
        extra = [t.format(**values) for t in tpl]
        ok, text = run_section(script, extra)
        if not ok:
            failed.append(key)
        if not text:
            continue  # 空段（如复盘材料包当天无内容）直接跳过
        blocks.append(text)

    separator = "\n" + "─" * 24 + "\n"
    print("☀️ 早报 · " + __import__("datetime").date.today().isoformat())
    print(separator.join(blocks))

    print("\n" + json.dumps({
        "sections": [s[0] for s in SECTIONS],
        "failed": failed,
        "ok": not failed,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

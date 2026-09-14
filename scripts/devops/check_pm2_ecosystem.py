#!/usr/bin/env python3
"""校验 ``ecosystem.config.json`` 与 pm2 实际运行拓扑是否一致。

背景（2026-09-14）
------------------
pm2 实跑 21 个进程，而 ``ecosystem.config.json`` 只声明 9 个；更糟的是 19 个
python producer 的**启动写法完全不对**：

* 声明（错）: ``script = ./src/.../xxx_producer.py`` + ``interpreter = .venv/bin/python3``
* 实跑（对）: ``script = /abs/.venv/bin/python3`` + ``args = ["src/.../xxx_producer.py", ...]``

后果：换机 / pm2 重装 / ``pm2 resurrect`` 之后，这批采集进程**无法被恢复**，
或按错误写法恢复后全部起不来。声明已按实跑重建，本脚本防止再次漂移。

用法
----
    python scripts/devops/check_pm2_ecosystem.py             # 声明 vs pm2 实跑
    python scripts/devops/check_pm2_ecosystem.py --offline   # 仅自洽检查（无需 pm2）

退出码：0 = 一致；1 = 有差异（差异明细打印到 stdout）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ECOSYSTEM = ROOT / "ecosystem.config.json"

# 只关心本项目托管的进程；pm2 里可能还有别的项目的 app。
NAME_PREFIX = "openbiliclaw"
NAME_EXTRA = ("pool-feed-api",)


def wanted(name: str) -> bool:
    return name.startswith(NAME_PREFIX) or name in NAME_EXTRA


def load_apps() -> list[dict]:
    data = json.loads(ECOSYSTEM.read_text(encoding="utf-8"))
    return list(data.get("apps", []))


def check_self_consistency(apps: list[dict]) -> list[str]:
    """离线可跑的检查：声明自身是否自洽。"""
    problems: list[str] = []

    names = [a.get("name", "") for a in apps]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        problems.append(f"声明内重复的进程名: {sorted(dupes)}")

    for app in apps:
        name = app.get("name", "<无名>")
        cwd = app.get("cwd")
        if not cwd:
            problems.append(f"[{name}] 缺 cwd")
            continue
        if not Path(cwd).is_dir():
            problems.append(f"[{name}] cwd 不存在: {cwd}")

        script = app.get("script")
        if not script:
            problems.append(f"[{name}] 缺 script")
            continue
        if not Path(script).is_file():
            problems.append(f"[{name}] script 不存在: {script}")

        interp = app.get("interpreter")
        args = app.get("args") or []

        if interp == "none":
            # python producer：可执行文件是 venv python，脚本走 args
            if not args:
                problems.append(f"[{name}] interpreter=none 但没有 args（脚本路径应放 args[0]）")
            else:
                target = Path(cwd) / args[0]
                if not target.is_file():
                    problems.append(f"[{name}] args[0] 指向的文件不存在: {target}")
        elif interp == "bash":
            # shell 包装脚本：本体即入口，不应有 args
            if args:
                problems.append(f"[{name}] interpreter=bash 却带了 args: {args}")
        else:
            problems.append(
                f"[{name}] interpreter 应为 'none'(python) 或 'bash'(shell)，实际 {interp!r}"
            )

    return problems


def pm2_snapshot() -> list[dict] | None:
    if shutil.which("pm2") is None:
        return None
    proc = subprocess.run(["pm2", "jlist"], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    rows = []
    for entry in json.loads(proc.stdout):
        name = entry.get("name", "")
        if not wanted(name):
            continue
        env = entry.get("pm2_env", {})
        rows.append(
            {
                "name": name,
                "script": env.get("pm_exec_path", ""),
                "args": list(env.get("args") or []),
                "interpreter": env.get("exec_interpreter", ""),
                "cwd": env.get("pm_cwd", ""),
                "status": env.get("status", ""),
            }
        )
    return rows


def compare(declared: list[dict], actual: list[dict]) -> list[str]:
    """把声明与 pm2 实跑逐项比对。"""
    problems: list[str] = []

    dmap = {a["name"]: a for a in declared if wanted(a.get("name", ""))}
    amap = {a["name"]: a for a in actual}

    only_declared = sorted(set(dmap) - set(amap))
    only_actual = sorted(set(amap) - set(dmap))
    if only_declared:
        problems.append(f"声明有、但 pm2 没跑（废弃残留？）: {only_declared}")
    if only_actual:
        problems.append(f"pm2 在跑、但声明缺失（换机将丢失）: {only_actual}")

    for name in sorted(set(dmap) & set(amap)):
        d, a = dmap[name], amap[name]

        if d.get("script") != a["script"]:
            problems.append(f"[{name}] script 不一致\n    声明: {d.get('script')}\n    实跑: {a['script']}")

        d_args = list(d.get("args") or [])
        if d_args != a["args"]:
            problems.append(f"[{name}] args 不一致\n    声明: {d_args}\n    实跑: {a['args']}")

        d_interp = d.get("interpreter")
        if d_interp != a["interpreter"]:
            problems.append(
                f"[{name}] interpreter 不一致\n    声明: {d_interp!r}\n    实跑: {a['interpreter']!r}"
            )

        d_cwd = d.get("cwd")
        if d_cwd and d_cwd != a["cwd"]:
            problems.append(f"[{name}] cwd 不一致\n    声明: {d_cwd}\n    实跑: {a['cwd']}")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--offline",
        action="store_true",
        help="只做声明自洽检查，不查询 pm2（CI / 无 pm2 环境可用）",
    )
    args = parser.parse_args()

    apps = load_apps()
    print(f"ecosystem.config.json: {len(apps)} 个 app")

    problems = check_self_consistency(apps)
    if problems:
        print("\n✗ 自洽检查未通过：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("✓ 自洽检查通过（路径存在、写法一致、无重名）")

    if args.offline:
        return 0

    actual = pm2_snapshot()
    if actual is None:
        print("\n! 无法获取 pm2 快照（未安装 pm2 或命令失败）")
        print("  在部署机上运行本脚本，或加 --offline 只做自洽检查。")
        return 1

    print(f"pm2 实跑: {len(actual)} 个进程")

    diffs = compare(apps, actual)
    if diffs:
        print("\n✗ 声明与实跑不一致：")
        for p in diffs:
            print(f"  - {p}")
        print("\n若实跑是对的 → 更新 ecosystem.config.json；若声明是对的 → 重启对应进程。")
        return 1

    print("✓ 声明与 pm2 实跑完全一致")

    stopped = [a["name"] for a in actual if a["status"] != "online"]
    if stopped:
        print(f"\n! 注意：以下进程不是 online 状态: {stopped}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

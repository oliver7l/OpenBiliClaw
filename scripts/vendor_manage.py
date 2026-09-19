#!/usr/bin/env python3
"""二创/GitHub 下载项目统一管理工具（vendor_manage）。

本仓库内嵌了一批「从 GitHub/B站 下载、你改过」的二创项目（如 二创/*、tools/* 等）。
它们各自保留**独立 .git**（自己的仓库/历史），本工具统一扫描、登记、体检它们的状态，
避免：嵌套 .git 在主仓产生 gitlink 污染、改没改/remote 在哪理不清。

约定见 docs/vendor-policy.md：
  - 二创项目放顶层自己的目录（如 二创/<name>/），保留独立 .git；
  - 主仓 .gitignore 排除其整目录（防 gitlink）；
  - 在 vendor-registry.json 登记 {path, upstream, remote, note} 作为单一事实来源。

用法：
  python3 scripts/vendor_manage.py scan             # 全仓扫描嵌套 git 仓库并体检
  python3 scripts/vendor_manage.py register <path>  # 登记（--upstream/--note）
  python3 scripts/vendor_manage.py list             # 展示注册表（叠加 scan 状态）
  python3 scripts/vendor_manage.py status <path>    # 单仓体检
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "vendor-registry.json"

# 扫描时按目录名剪枝（不进入、也不当作嵌套仓库）。
_SKIP_DIRS = {"node_modules", ".venv", "venv", "dist", "build", "__pycache__", "target", ".git", "images", "Pods"}
# 顶层「探索/内容/运行时」目录，不是二创仓库，整棵跳过。
_SKIP_TOP = {"data", "logs", "images", "dist", "notes", "__pycache__", "docs", "migrations"}


def _git(path: Path, *args: str) -> tuple[str, int]:
    try:
        r = subprocess.run(["git", "-C", str(path), *args],
                           capture_output=True, text=True, timeout=20)
        return r.stdout.strip(), r.returncode
    except Exception:  # noqa: BLE001
        return "", -1


def _is_repo(p: Path) -> bool:
    return (p / ".git").exists()


def scan_repos() -> list[Path]:
    """返回所有嵌套 git 仓库的相对路径（按目录序，去重）。"""
    found: list[Path] = []
    for dirpath, dirnames, _filenames in os.walk(str(ROOT)):
        d = Path(dirpath)
        dirnames[:] = [n for n in dirnames if n not in _SKIP_DIRS]
        rel = d.relative_to(ROOT)
        if rel.parts and rel.parts[0] in _SKIP_TOP:
            dirnames[:] = []
            continue
        if rel.parts and _is_repo(d):
            found.append(rel)
            dirnames[:] = []  # 它自己是个仓库，不再深入
    return sorted(set(found), key=lambda p: str(p))


def repo_info(p: Path) -> dict:
    rel = p.as_posix()
    dirty_out, _ = _git(p, "status", "--porcelain")
    log_out, _ = _git(p, "log", "--oneline")
    remote, _ = _git(p, "remote", "get-url", "origin")
    commits = len([l for l in log_out.splitlines() if l.strip()])
    return {
        "path": rel,
        "dirty": bool(dirty_out),
        "dirty_files": len([l for l in dirty_out.splitlines() if l.strip()]),
        "commits": commits,
        "remote": remote,
    }


def load_registry() -> dict:
    if REGISTRY.exists():
        try:
            return json.loads(REGISTRY.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def save_registry(reg: dict) -> None:
    REGISTRY.write_text(
        json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def cmd_scan() -> int:
    repos = scan_repos()
    reg = load_registry()
    print(f"扫描到 {len(repos)} 个嵌套 git 仓库：")
    print(f"{'路径':<42}{'改动':<6}{'提交':<6}{'remote'}")
    for p in repos:
        info = repo_info(p)
        dirty = f"*{info['dirty_files']}" if info["dirty"] else "-"
        tracked = "R" if info["path"] in reg else " "
        print(f"{('R' if info['path'] in reg else ' ')} {info['path']:<40}{dirty:<6}"
              f"{info['commits']:<6}{(info['remote'] or '-')[:50]}")
    return 0


def cmd_status(path: str) -> int:
    p = ROOT / path
    if not p.exists() or not _is_repo(p):
        print(f"{path}: 不是嵌套 git 仓库")
        return 1
    info = repo_info(p)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


def cmd_register(path: str, upstream: str, note: str) -> int:
    p = ROOT / path
    if not p.exists() or not _is_repo(p):
        print(f"{path}: 不是嵌套 git 仓库，无法登记")
        return 1
    reg = load_registry()
    reg[path] = {
        "upstream": upstream,
        "note": note,
        "remote": repo_info(p).get("remote", ""),
        "updated": __import__("datetime").date.today().isoformat(),
    }
    if note and not upstream:
        reg[path]["note"] = note
    save_registry(reg)
    print(f"已登记 {path} → vendor-registry.json")
    return 0


def cmd_list() -> int:
    reg = load_registry()
    if not reg:
        print("（注册表为空。先 `scan` 看有哪些，再 `register <path>` 登记。）")
        return 0
    found = {p.as_posix() for p in scan_repos()}
    print(f"注册表 {len(reg)} 项：")
    for key, meta in reg.items():
        present = "✓" if key in found else "✗(路径缺失)"
        print(f"  [{present}] {key}")
        print(f"      upstream: {meta.get('upstream') or '-'}")
        if meta.get("note"):
            print(f"      note    : {meta['note']}")
    return 0


def cmd_bulk() -> int:
    """把全仓扫描到的所有嵌套 git 仓库登记进注册表（保留已有 upstream/note，不覆盖）。"""
    reg = load_registry()
    repos = scan_repos()
    added = 0
    for p in repos:
        key = p.as_posix()
        existing = reg.get(key, {})
        if isinstance(existing, str):  # 兼容旧字符串
            existing = {}
        info = repo_info(p)
        if key not in reg or isinstance(reg[key], str):
            reg[key] = {
                "upstream": existing.get("upstream", ""),
                "remote": info.get("remote", ""),
                "note": existing.get("note", ""),
                "updated": __import__("datetime").date.today().isoformat(),
            }
            added += 1
    # 去掉占位的顶层 "note" 说明键（它是文档注释，不是项目）
    reg.pop("note", None)
    save_registry(reg)
    print(f"已批量登记 {added} 个（共 {len(reg)} 项）到 vendor-registry.json")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] not in ("scan", "register", "list", "status", "bulk"):
        print(__doc__)
        return 2
    cmd = args[0]
    if cmd == "scan":
        return cmd_scan()
    if cmd == "list":
        return cmd_list()
    if cmd == "bulk":
        return cmd_bulk()
    if cmd == "status":
        if len(args) < 2:
            print("usage: vendor_manage.py status <path>")
            return 2
        return cmd_status(args[1])
    if cmd == "register":
        if len(args) < 2:
            print("usage: vendor_manage.py register <path> [--upstream URL] [--note ...]")
            return 2
        path = args[1]
        upstream, note = "", ""
        rest = args[2:]
        if "--upstream" in rest:
            upstream = rest[rest.index("--upstream") + 1]
        if "--note" in rest:
            note = " ".join(rest[rest.index("--note") + 1:])
        return cmd_register(path, upstream, note)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
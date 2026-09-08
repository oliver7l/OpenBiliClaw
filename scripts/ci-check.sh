#!/usr/bin/env bash
# 提交前本地回归护栏 —— 与 .github/workflows/ci.yml 的 test job 对齐。
# 用法：scripts/ci-check.sh
# 任意一步失败即退出非 0，阻断提交。
set -euo pipefail

cd "$(dirname "$0")/.."

PY=.venv/bin
if [ ! -x "$PY/python" ]; then
    echo "!! 未找到 $PY/python，请先创建虚拟环境（uv sync 或 pip install -e \".[dev]\"）" >&2
    exit 1
fi

echo "==> [1/3] ruff check src/ tests/"
"$PY/ruff" check src/ tests/

echo "==> [2/3] mypy src/"
"$PY/mypy" src/

echo "==> [3/3] pytest（--continue-on-collection-errors，与基线口径一致）"
"$PY/python" -m pytest -q --continue-on-collection-errors

echo ""
echo "✅ ci-check 全部通过"

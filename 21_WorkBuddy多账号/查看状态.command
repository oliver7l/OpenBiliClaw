#!/bin/zsh
# 查看状态.command — 只读体检：当前账号 / 任务分布 / 通道矩阵（不写任何东西）

DIR="$(cd "$(dirname "$0")" && pwd)"
PY="/Users/imac/.workbuddy/binaries/python/versions/3.13.12/bin/python3"

echo "════════ 任务 ════════"
"$PY" "$DIR/scripts/wb_sync_automations.py" status
echo
echo "════════ 通道 ════════"
"$PY" "$DIR/scripts/wb_sync_channels.py" status
echo
echo "════════ 待同步变更（plan 预览，不执行）════════"
"$PY" "$DIR/scripts/wb_sync_automations.py" plan 2>&1 | grep -E "◆" | grep -v "+0 新增 / ~0 覆盖 / -0 删除" || echo "  任务已一致"
"$PY" "$DIR/scripts/wb_sync_channels.py" plan 2>&1 | grep -E "需统一" || echo "  通道已一致"
echo
echo "（要执行同步请双击 同步定时任务.command）"

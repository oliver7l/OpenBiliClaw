#!/bin/zsh
# 同步定时任务.command — 双击运行，把全账号定时任务同步成同一份
# 流程：预览变更 → 确认 → 退出 WorkBuddy（如在运行）→ 写库 → 重启 WorkBuddy

set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="/Users/imac/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
SCRIPT="$DIR/scripts/wb_sync_automations.py"

echo "══════════════════════════════════════════"
echo "  WorkBuddy 全账号定时任务同步"
echo "══════════════════════════════════════════"
"$PY" "$SCRIPT" status
echo
"$PY" "$SCRIPT" plan > /tmp/wb_sync_plan.txt 2>&1
head -40 /tmp/wb_sync_plan.txt
echo "  …（完整计划见 /tmp/wb_sync_plan.txt）"
echo
printf "确认执行同步？(y/N) "
read -r ans
if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
  echo "已取消。"; exit 0
fi

# 检测并退出 WorkBuddy（写库安全前提）
if pgrep -f "WorkBuddy.app" > /dev/null 2>&1; then
  echo "检测到 WorkBuddy 正在运行，请求退出…"
  osascript -e 'tell application "WorkBuddy" to quit' 2>/dev/null
  for i in {1..15}; do
    pgrep -f "WorkBuddy.app" > /dev/null 2>&1 || break
    sleep 1
  done
  if pgrep -f "WorkBuddy.app" > /dev/null 2>&1; then
    echo "✗ WorkBuddy 未能退出，中止（可手动退出后重试）。"
    exit 2
  fi
  echo "  ✓ 已退出"
  RELAUNCH=1
else
  RELAUNCH=0
fi

"$PY" "$SCRIPT" apply
RC=$?

# 消息通道统一（per-user 通道一致化 + 全局层通道铺进账号层）
echo
echo "── 消息通道统一 ──"
"$PY" "$DIR/scripts/wb_sync_channels.py" apply --include-global --force

if [[ $RELAUNCH -eq 1 ]]; then
  echo "重新启动 WorkBuddy…"
  open -a WorkBuddy
fi
echo "完成。"
exit $RC

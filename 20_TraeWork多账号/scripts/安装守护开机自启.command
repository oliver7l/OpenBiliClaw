#!/bin/zsh
# 把「登录态自动捕获守护」（twa_watch.py）注册为 macOS LaunchAgent：
#   - 开机登录后自动启动，终端关了也活着
#   - 进程崩溃/被杀后 launchd 自动拉起（KeepAlive）
# 重复运行 = 幂等更新；--remove 卸载。
#
# 前置：twa_watch.py 每 15s 扫描所有 TRAE SOLO CN* 副本，谁的登录态比档案仓新
#        就自动捕获入库（copy2 保 mtime ⇒ 天然幂等）。

LABEL="cn.traework.autocapture"
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="/Users/imac/.workbuddy/binaries/python/envs/default/bin/python"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG="$HOME/Library/Application Support/cn.traework.assistant/twa_watch.launchd.log"
UID_N="$(id -u)"

if [[ "$1" == "--remove" ]]; then
  launchctl bootout "gui/${UID_N}/${LABEL}" 2>/dev/null
  rm -f "$PLIST"
  echo "已卸载守护自启（${LABEL}）；正在跑的实例可用 twa_watch.py --stop 停掉。"
  exit 0
fi

[[ -x "$PY" ]] || { echo "找不到 python: $PY"; exit 1; }
[[ -f "$DIR/twa_watch.py" ]] || { echo "找不到 $DIR/twa_watch.py"; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents" "$(dirname "$LOG")"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PY}</string>
        <string>${DIR}/twa_watch.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOG}</string>
    <key>StandardErrorPath</key>
    <string>${LOG}</string>
</dict>
</plist>
EOF

# 先卸旧的再装（幂等）
launchctl bootout "gui/${UID_N}/${LABEL}" 2>/dev/null
launchctl bootstrap "gui/${UID_N}" "$PLIST" 2>/dev/null || launchctl load "$PLIST"

sleep 1
if pgrep -f "twa_watch.py" >/dev/null 2>&1; then
  echo "✅ 守护已注册并运行：登录自启、崩溃自动拉起、关终端不影响。"
  echo "   日志: $LOG"
else
  echo "⚠️ plist 已写入但进程未起来，看日志排查: $LOG"
fi
echo "卸载: 双击本脚本无效果，请执行  $0 --remove"

#!/bin/zsh
# 新账号「登录后收尾」一键脚本
#
# 用在哪：某个账号你刚在 TRAE SOLO CN（任一副本）里登录过，但 TraeWorkAssistant
#        的列表里没它 / 切不到它 / 签不了到。
# 干什么：① 把本机所有客户端副本的登录态捕获成档案（切换靠它）
#        ② 把新账号并进 accounts.json、并把已有账号的 token 刷新成客户端那份
#        ③ 打印结果（档案数、新账号是否出现）
# 说明：全程只读写 ~/Library/Application Support/cn.traework.assistant/，
#      动 accounts.json 前会自动备份，账号条目写前有结构自检。
cd "$(dirname "$0")" || exit 1
PY=/Users/imac/.workbuddy/binaries/python/envs/default/bin/python

echo "=============================================="
echo " 新账号收尾：捕获登录态 → 入库 → 校验"
echo "=============================================="
echo

# 助手 App 正在运行时可能用它内存里的旧账号列表覆盖 accounts.json，先提醒
if pgrep -f "TraeWorkAssistant.app/Contents/MacOS/traework-assistant" >/dev/null; then
  echo "⚠️  TraeWorkAssistant 正在运行。若它稍后写回账号列表，可能覆盖本次改动。"
  printf "    现在退出它吗？(y = 退出后继续 / 回车 = 直接继续) "
  read -r ans
  if [[ "$ans" == "y" || "$ans" == "Y" ]]; then
    osascript -e 'quit app "TraeWorkAssistant"' 2>/dev/null
    sleep 2
    echo "    → 已请求退出"
  fi
  echo
fi

echo "① 捕获各客户端副本的登录态 → 档案"
env -u PYTHONHOME -u PYTHONPATH "$PY" twa_switch_account.py capture
echo

echo "② 新账号入库 + 刷新已有账号 token（已备份）"
env -u PYTHONHOME -u PYTHONPATH "$PY" twa_scan_accounts.py --inject --update
echo

echo "③ 当前状态"
env -u PYTHONHOME -u PYTHONPATH "$PY" twa_switch_account.py status
echo

echo "----------------------------------------------"
echo "期望结果：档案数与 accounts.json 里的账号数一致；"
echo "        新登录的账号出现在档案列表里（切换就能选到它）。"
echo "接着打开 TRAE SOLO CN，在 TraeWorkAssistant 里点签到试一次。"
echo
printf "按回车关闭这个窗口…"
read -r _

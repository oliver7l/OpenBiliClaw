#!/bin/zsh
# 开关「登录态自动捕获守护」——以后任何账号只要在本机任一副本里登录过，15 秒内自动进档案仓
#
# 为什么值得开：客户端 storage.json 同时只存一个账号，登下一个就把上一个顶掉；
# 不当时捕获，那次登录态就永久丢了（账号在 accounts.json 里，但切不到、签不了）。
cd "$(dirname "$0")" || exit 1
PY=/Users/imac/.workbuddy/binaries/python/envs/default/bin/python
run() { env -u PYTHONHOME -u PYTHONPATH "$PY" twa_watch.py "$@"; }

if run --status | grep -q "运行中"; then
  echo "守护正在运行："
  run --status
  echo
  printf "要停掉它吗？(y = 停止 / 回车 = 保持运行) "
  read -r ans
  if [[ "$ans" == "y" || "$ans" == "Y" ]]; then
    run --stop
  fi
else
  echo "启动守护（后台常驻，登录即入库）…"
  nohup env -u PYTHONHOME -u PYTHONPATH "$PY" twa_watch.py >/dev/null 2>&1 &
  disown
  sleep 2
  run --status
fi
echo
printf "按回车关闭…"; read -r _

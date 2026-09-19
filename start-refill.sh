#!/bin/bash
# openbiliclaw-refill PM2 启动包装（M4 收口调度入口）
# 用法：pm2 start start-refill.sh --name openbiliclaw-refill \
#          --cron-restart "5 */2 * * *" --autorestart false
#
# 说明：
# - 显式 unset PYTHONHOME/PYTHONPATH，避免 Python init 报 "No module named
#   'encodings'"（dev-guide §6.1 已记录此坑）。
# - 剥离继承的系统代理（如 127.0.0.1:58753），避免劫持子进程出口；refill 各通道
#   需要代理时自行设置（如 ytdlp 走 YT_PROXY_POOL / PROXY）。
# - 跑一轮 `openbiliclaw refill schedule`（按 [refill].quota 每平台每轮配额，
#   默认随机憩志防风控）后退出，等下一次 cron_restart 再拉起。
cd "$(dirname "$0")" || exit 1
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy FTP_PROXY ftp_proxy
exec env -u PYTHONHOME -u PYTHONPATH .venv/bin/openbiliclaw refill schedule
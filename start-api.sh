#!/bin/bash
# OpenBiliClaw API startup script — sources douyin cookie from companion file
cd "$(dirname "$0")" || exit 1
if [ -f douyin-cookie ]; then
    export OPENBILICLAW_DOUYIN_COOKIE
    OPENBILICLAW_DOUYIN_COOKIE=$(cat douyin-cookie)
fi
export PYTHONHOME=""
export PYTHONPATH=""
# Strip any inherited HTTP proxy env vars. The configured endpoints
# (sensenova / deepseek / bilibili) are all reachable directly; a stale
# local proxy port carried over from the PM2 parent would silently kill
# every outbound request (observed 2026-09-01: all providers + bilibili
# "connect failed" while shell-level direct calls worked).
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy FTP_PROXY ftp_proxy
exec .venv/bin/openbiliclaw serve-api --host 0.0.0.0 --port 8420

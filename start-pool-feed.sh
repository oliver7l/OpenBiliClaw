#!/bin/bash
# 独立推荐流浏览服务：只读 pool.db，:8421。
# 与主 api (:8420) 进程隔离，主进程的采集/LLM/冷算不再影响推荐流秒开秒换。
set -e
cd "$(dirname "$0")"
exec .venv/bin/uvicorn openbiliclaw.api.pool_feed_app:app --host 0.0.0.0 --port 8421 --log-level warning

#!/bin/bash
# xhs-backfill PM2 启动包装：显式 unset PYTHONHOME/PYTHONPATH，避免 Python
# init 时因污染环境报 "No module named 'encodings'"（project_memory 已记录此坑）
exec env -u PYTHONHOME -u PYTHONPATH \
    /Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/.venv/bin/python \
    /Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/16_浏览器自动化/backfill.py \
    --source xiaohongshu --n 1 --pacing 8 --jitter 30
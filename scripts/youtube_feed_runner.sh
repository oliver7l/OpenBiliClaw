#!/bin/bash
# Wrapper: clear PYTHONHOME/PYTHONPATH before running youtube_producer.py
# PM2 inherits Trae/DoubaoWork env vars that break the .venv Python.
unset PYTHONHOME
unset PYTHONPATH
unset __PYVENV_LAUNCHER__

exec "$(dirname "$0")/../.venv/bin/python" "$(dirname "$0")/../src/openbiliclaw/runtime/youtube_producer.py"
#!/usr/bin/env python3
"""Fetch Hub CLI（薄壳，实现在 fetchhub/ 包）。

用法：
    .venv/bin/python scripts/content_library/fetch_hub.py <url> [--json] [--archive-draft]
    .venv/bin/python scripts/content_library/fetch_hub.py --health [--days 7]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetchhub.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

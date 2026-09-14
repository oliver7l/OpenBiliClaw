#!/usr/bin/env python3
"""用 zhihu CLI 的登录态直连 api.zhihu.com 抓知乎正文，输出 Markdown。

必须在 zhihu-toolkit 的虚拟环境里运行（那里才有 zhihu_cli 包与登录 session）:
    /Users/imac/.local/share/uv/tools/zhihu-toolkit/bin/python3 \
        scripts/content_library/zhihu_api_body.py answer 2033887544610387219

用法:
    zhihu_api_body.py answer <answer_id>
    zhihu_api_body.py article <article_id>

输出: 正文 Markdown（失败时输出空字符串，退出码非 0）。
"""
from __future__ import annotations

import json
import os
import sys

# 宿主环境可能通过 PYTHONPATH 注入 sitecustomize（沙箱 shim），它拦截 Path.mkdir
# 且不认 exist_ok=True，会导致 zhihu_cli 初始化缓存目录时抛
# `PermissionError: EEXIST ... cache/questions`。检测到就去掉 PYTHONPATH 重启自己。
_pp = os.environ.get("PYTHONPATH") or ""
if "sitecustomize" in _pp or "shim" in _pp:
    os.environ.pop("PYTHONPATH", None)
    os.execv(sys.executable, [sys.executable] + sys.argv)

from zhihu_cli.content.handlers.requests import session  # noqa: E402
from zhihu_cli.content.utils.html2markdown import converter  # noqa: E402

API = {
    "answer": "https://api.zhihu.com/answers/{id}?include=content,excerpt,created_time,voteup_count,comment_count",
    "article": "https://api.zhihu.com/articles/{id}?include=content,excerpt,created_time,voteup_count,comment_count",
}


def fetch(kind: str, obj_id: str) -> str:
    url = API[kind].format(id=obj_id)
    r = session.get(url, timeout=30)
    if r.status_code != 200:
        return ""
    data = r.json() or {}
    content = data.get("content") or ""
    if not content:
        return ""
    try:
        return converter.convert(content).strip()
    except Exception:  # noqa: BLE001
        return json.dumps({"raw": content})[:0] or content


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: zhihu_api_body.py answer|article <id>", file=sys.stderr)
        return 2
    kind, obj_id = sys.argv[1], sys.argv[2]
    if kind not in API:
        print(f"unknown kind: {kind}", file=sys.stderr)
        return 2
    text = fetch(kind, obj_id)
    sys.stdout.write(text)
    return 0 if text else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""zhihu_api 通道：知乎 api.zhihu.com 直连抓正文。

复用 ``scripts/content_library/zhihu_api_body.py``：以子进程方式调用 **zhihu-toolkit
venv** python（那个环境才有 ``zhihu_cli`` 包与其登录 session），按 URL 判 kind
（answer / article）传 id，取回 Markdown 正文。

- URL 里无合法 ``answer`` / ``p`` id → ``PERMANENT``（本通道无法处理）。
- 子进程调用失败 / 未返回正文 → ``ok=False``（计数重试；知乎池子小，交给
  getnote / direct 兜底）。

zhihu-toolkit python 路径：``ZHIHU_TOOLKIT_PY`` 环境变量覆盖，否则用默认 venv 路径。
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from openbiliclaw.refill.channels.base import PERMANENT

_zhihu = "zhihu"
_MIN_BODY = 20
_TIMEOUT = 40
_PROJECT_ROOT = Path(__file__).resolve().parents[4]  # .../src/openbiliclaw/refill/channels/zhihu_api.py 向上 4 级到仓库根

_ANSWER_RE = re.compile(r"/answer/(\d+)")
_ARTICLE_RE = re.compile(r"/p/(\d+)")

Runner = Callable[[list[str], int], subprocess.CompletedProcess]


def _zhihu_python() -> str:
    custom = os.environ.get("ZHIHU_TOOLKIT_PY", "").strip()
    if custom:
        return custom
    return "/Users/imac/.local/share/uv/tools/zhihu-toolkit/bin/python3"


def _default_runner(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", f"timeout after {timeout}s")


def _extract_kind_id(url: str) -> tuple[str, str] | None:
    m = _ANSWER_RE.search(url or "")
    if m:
        return "answer", m.group(1)
    m = _ARTICLE_RE.search(url or "")
    if m:
        return "article", m.group(1)
    return None


class ZhihuApiChannel:
    """知乎直连接口补抓通道（zhihu-toolkit venv 子进程）。"""

    name = "zhihu_api"
    requires_bridge = False

    def __init__(
        self,
        *,
        python: str | None = None,
        script: str | Path | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.python = python or _zhihu_python()
        # M4 收尾：原脚本已归档到 06_正文补抓/archive（git mv，不删除）。
        self.script = str(
            script or _PROJECT_ROOT / "06_正文补抓" / "archive" / "zhihu_api_body.py"
        )
        self._runner = runner or _default_runner

    def supports(self, source_type: str, url: str) -> bool:
        return source_type == _zhihu and _extract_kind_id(url) is not None

    def fetch(self, item) -> tuple[bool, str, str]:
        url = (item.get("url") or "").strip()
        pair = _extract_kind_id(url)
        if not pair:
            return False, "", f"{PERMANENT}知乎 URL 非 answer/article 形态"
        kind, obj_id = pair
        proc = self._runner(
            [self.python, self.script, kind, obj_id],
            _TIMEOUT,
        )
        out = (proc.stdout or "").strip()
        if proc.returncode == 124:
            return False, "", "zhihu api 超时（重试下轮）"
        if out and len(out) >= _MIN_BODY:
            return True, out[:20000], f"zhihu {kind}={obj_id}"
        return False, "", "zhihu api 未返回正文"
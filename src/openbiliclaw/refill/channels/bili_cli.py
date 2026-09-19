"""bili_cli 通道：B 站正文（字幕口播稿 / AI 总结 兜底）。

复用 ``scripts/refill_article_bodies.py::fetch_bilibili_body`` 内核：``bili video
<BV> -s --ai --json`` 一次拿到 subtitle / ai_summary / description。优先字幕口播稿
（实测约 60% 视频有）；无字幕用 AI 总结 + 视频简介拼接（≥MIN_BODY 才算正文）。

区分两类结果（关键，避免限流误废队列）：
- 调用失败（超时/网络/B 站限流，``data`` 为空）→ ``ok=False``，计数重试下轮；
- 调用成功但字幕/AI/简介皆无 → ``ok=False`` + ``PERMANENT``，标 skipped 不消耗重试；
- 无 BV 号（URL 不合法）→ ``PERMANENT``。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable

from openbiliclaw.refill.channels.base import PERMANENT

_bilibili = "bilibili"
_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
_MIN_BODY = 50
_TIMEOUT = 120


def _bili_cli() -> str:
    return os.environ.get("BILI_CLI") or shutil.which("bili") or "/Users/imac/.local/bin/bili"


Runner = Callable[[list[str], int], subprocess.CompletedProcess]


def _default_runner(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", f"timeout after {timeout}s")


class BiliCliChannel:
    """B 站字幕/AI/简介补抓通道（需本机 bili CLI）。"""

    name = "bili_cli"
    requires_bridge = False

    def __init__(self, *, bili_cli: str | None = None, runner: Runner | None = None) -> None:
        self.bili_cli = bili_cli or _bili_cli()
        self._runner = runner or _default_runner

    def supports(self, source_type: str, url: str) -> bool:
        return source_type == _bilibili and bool(_BV_RE.search(url or ""))

    def fetch(self, item) -> tuple[bool, str, str]:
        url = (item.get("url") or "").strip()
        m = _BV_RE.search(url)
        if not m:
            return False, "", f"{PERMANENT}URL 无 BV 号"
        data = self._bili_json(m.group(1))
        if not data:
            # 调用失败（超时/限流/网络）→ 计数重试，绝不误判永久。
            return False, "", "B站接口调用失败（重试下轮）"
        subtitle = data.get("subtitle") or {}
        if subtitle.get("available") and (subtitle.get("text") or "").strip():
            return True, (subtitle.get("text") or "").strip(), "字幕口播稿"
        ai = data.get("ai_summary") or ""
        desc = ((data.get("video") or {}).get("description") or "").strip()
        parts = [p.strip() for p in (ai, desc) if isinstance(p, str) and p.strip()]
        combined = "\n\n".join(parts)
        if len(combined) >= _MIN_BODY:
            return True, combined, "AI 总结 + 简介"
        return False, "", f"{PERMANENT}无字幕/无AI/无简介"

    def _bili_json(self, bvid: str) -> dict:
        proc = self._runner([self.bili_cli, "video", bvid, "-s", "--ai", "--json"], _TIMEOUT)
        if proc.returncode == 124:
            return {}
        try:
            return json.loads(proc.stdout or "{}").get("data") or {}
        except Exception:  # noqa: BLE001
            return {}
"""getnote 通道：得到大脑服务端兜底抓正文。

复用 ``06_正文补抓/02_执行脚本/refill_via_getnote.py`` 内核，把 getnote 回补**完整**
收敛到通道内（不再依赖外部脚本 / `getnote_body_task` 表）：

1. ``getnote save <url>``：新版往往一次带回正文（``data.note.web_page.content`` 原始保真 /
   ``data.note.content`` AI 提炼稿），直接写回。
2. save 未同步带回正文时走**异步回收**（旧版 poll/collect 链路）：
   有 ``note_id`` → ``getnote note <note_id> --field content``；只有 ``task_id`` →
   ``getnote task`` 有界轮询拿 ``note_id`` 再取正文。

- 配额耗尽（网关 10203 quota_daily_exceeded）→ 抛 :class:`BridgeUnavailableError`
  （全局、不计数，明天 00:00 重置）。
- 支撑无本机通道的源（抖音）与主通道失败后的兜底（通道路由里排在最后）。
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable

from openbiliclaw.refill.channels.base import BridgeUnavailableError

_MIN_BODY = 30
# 覆盖：小红书/YouTube/抖音/微信/小宇宙 + 未来 zhihu/bili 兜底。
_SUPPORTED = ("xiaohongshu", "youtube", "douyin", "wechat", "xiaoyuzhou",
              "linuxdo", "zhihu", "bilibili", "web")


def _json_of(out: str) -> dict:
    out = (out or "").strip()
    if not out:
        return {}
    try:
        data = json.loads(out)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        start = out.find("{")
        if start >= 0:
            try:
                obj, _end = json.JSONDecoder().raw_decode(out[start:])
                return obj if isinstance(obj, dict) else {}
            except Exception:  # noqa: BLE001
                return {}
    return {}


def _quota_exhausted(payload: dict, raw: str) -> bool:
    if "quota_daily_exceeded" in raw or "请求配额已用尽" in raw:
        return True
    err = (payload or {}).get("error") or {}
    return err.get("reason") == "quota_daily_exceeded" or err.get("code") == 10203


def _dig(payload: dict, *keys: str):
    """在 data.note / data / 顶层里找第一个非空字段（镜像 refill_via_getnote._dig）。"""
    data = payload.get("data") or {}
    note = data.get("note") if isinstance(data, dict) else None
    for scope in (note if isinstance(note, dict) else {}, data if isinstance(data, dict) else {},
                  payload):
        if not isinstance(scope, dict):
            continue
        for k in keys:
            v = scope.get(k)
            if v not in (None, "", 0):
                return v
    return None


def _body_of(payload: dict) -> str:
    """挑服务端返回的最合适正文（镜像 refill_via_getnote.body_of_payload）。"""
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return ""
    note = data.get("note") if isinstance(data.get("note"), dict) else {}
    web = note.get("web_page") if isinstance(note.get("web_page"), dict) else {}
    if not web and isinstance(data.get("web_page"), dict):
        web = data["web_page"]
    cands: list[str] = []
    for c in (web.get("content"), note.get("content"), note.get("ref_content"), data.get("content")):
        if isinstance(c, str) and c.strip():
            cands.append(c.strip())
    if not cands:
        return ""
    raw = cands[0]
    return raw if len(raw) >= _MIN_BODY else max(cands, key=len)


Runner = Callable[[list[str], int], subprocess.CompletedProcess]


def _default_runner(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", f"timeout after {timeout}s")


class GetnoteChannel:
    """得到大脑服务端兜底通道（不依赖 AgentLimb，需本机 getnote CLI）。

    含异步回收（save 未同步带回正文 → 有界轮询 task + 取 note），完全自包含。
    """

    name = "getnote"
    requires_bridge = False

    def __init__(
        self,
        *,
        getnote_cmd: str = "getnote",
        runner: Runner | None = None,
        poll_rounds: int = 3,
        poll_interval: float = 10.0,
    ) -> None:
        self.cmd = getnote_cmd
        self._runner = runner or _default_runner
        self.poll_rounds = poll_rounds
        self.poll_interval = poll_interval

    def supports(self, source_type: str, url: str) -> bool:
        return source_type in _SUPPORTED

    def fetch(self, item) -> tuple[bool, str, str]:
        url = (item.get("url") or "").strip()
        if not url:
            return False, "", "空 URL"
        proc = self._runner([self.cmd, "save", url, "-o", "json"], 180)
        out = (proc.stdout or "")
        raw = out + (proc.stderr or "")
        payload = _json_of(out)
        if proc.returncode == 124:
            return False, "", "服务端超时，留给下轮"
        if _quota_exhausted(payload, raw):
            raise BridgeUnavailableError("getnote 配额已打满，本轮熔断（明天 00:00 重置）")
        body = _body_of(payload)
        if len(body) >= _MIN_BODY:
            return True, body, "getnote 服务端抓取"

        # 异步回收：save 未同步带回正文。
        note_id = str(_dig(payload, "note_id", "noteId") or "")
        task_id = str(_dig(payload, "task_id", "taskId") or "")
        if not note_id and task_id:
            note_id = self._poll_task(task_id)
        if note_id:
            body = self._reclaim_note(note_id)
            if len(body) >= _MIN_BODY:
                return True, body, "getnote 回收正文（异步）"
        return False, "", "getnote 未返回正文（save 异步且未回收成功，或服务端本身无正文）"

    def _poll_task(self, task_id: str) -> str:
        """有界轮询 ``getnote task`` 直到拿到 note_id。"""
        for _ in range(max(0, self.poll_rounds)):
            proc = self._runner([self.cmd, "task", task_id, "-o", "json"], 60)
            out = (proc.stdout or "")
            raw = out + (proc.stderr or "")
            payload = _json_of(out)
            if _quota_exhausted(payload, raw):
                raise BridgeUnavailableError("getnote 配额已打满（轮询），本轮熔断")
            note_id = str(_dig(payload, "note_id", "noteId", "id") or "")
            if note_id:
                return note_id
            if self.poll_interval > 0:
                time.sleep(self.poll_interval)
        return ""

    def _reclaim_note(self, note_id: str) -> str:
        """``getnote note <note_id> --field content`` 取回正文。"""
        proc = self._runner([self.cmd, "note", note_id, "-o", "json", "--field", "content"], 60)
        out = (proc.stdout or "")
        raw = out + (proc.stderr or "")
        payload = _json_of(out)
        if _quota_exhausted(payload, raw):
            raise BridgeUnavailableError("getnote 配额已打满（取正文），本轮熔断")
        body = _body_of(payload)
        if body:
            return body
        value = _dig(payload, "content", "text", "data")
        return value if isinstance(value, str) else ""
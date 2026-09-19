"""ytdlp 通道：YouTube 字幕/简介补抓。

复用 ``scripts/refill_youtube_subtitles.py`` 内核：yt-dlp ``--write-subs
--write-auto-subs --write-description``，字幕语言中文优先 + 英文兜底，拿不到字幕
用简介兜底（YouTube 自动字幕现需 PO token，命中率有限）。保留三大判定：

- **临时可重试**（429/超时/网络）→ ``ok=False``，Scheduler 计数重试；
- **永久无字幕**（视频无字幕/会员/私有/需登录）→ ``ok=False`` + ``PERMANENT`` → skipped；
- **bot 检测**（出口被判 bot）→ 抛 ``BridgeUnavailableError``（全局熔断，不计数）。

代理沿 IP 池轮换（``YT_PROXY_POOL`` 逗号分隔；缺省单代理 ``127.0.0.1:7890``），
每条视频用下一出口，避免单 IP 高频命中风控。
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import tempfile
import time

from openbiliclaw.refill.channels.base import BridgeUnavailableError, PERMANENT

_YOUTUBE = "youtube"
_YTDLP = shutil.which("yt-dlp") or "/opt/homebrew/bin/yt-dlp"
_PROXY = "http://127.0.0.1:7890"  # 本机代理（Clash 等），YouTube 需走代理
_COOKIE_FILE = os.environ.get("YT_COOKIE_FILE") or ""
_MIN_BODY = 50
_TIMEOUT = 180
_LANG_PRIORITY = ["zh-Hans", "zh-CN", "zh", "zh-TW", "zh-Hant", "en"]
_VID_RE = re.compile(r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})")


def _proxy_pool() -> list[str]:
    raw = os.environ.get("YT_PROXY_POOL", "").strip()
    if raw:
        pool = [p.strip() for p in raw.split(",") if p.strip()]
        if pool:
            return pool
    return [_PROXY]


class _ProxyRotator:
    """跨条目轮换出口 IP，相邻条目分散到不同代理，降低命中风控概率。"""

    def __init__(self, pool: list[str]) -> None:
        self.pool = pool or [_PROXY]
        self.i = 0

    def next(self) -> str:
        proxy = self.pool[self.i % len(self.pool)]
        self.i += 1
        return proxy


def _cookie_args() -> list[str]:
    if _COOKIE_FILE and os.path.exists(_COOKIE_FILE):
        return ["--cookies", _COOKIE_FILE]
    if os.environ.get("YT_COOKIES_FROM_BROWSER"):
        return ["--cookies-from-browser", os.environ.get("YT_COOKIES_FROM_BROWSER", "")]
    return []


def _extract_vid(url: str) -> str | None:
    m = _VID_RE.search(url or "")
    return m.group(1) if m else None


def _parse_vtt(path: str) -> str:
    out: list[str] = []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("WEBVTT") or "-->" in s or s.isdigit():
                continue
            s = re.sub(r"<[^>]+>", "", s)
            if s:
                out.append(s)
    dedup: list[str] = []
    for line in out:
        if not dedup or line != dedup[-1]:
            dedup.append(line)
    return "\n".join(dedup)


def _classify(stderr: str) -> str:
    """返回 kind: retryable / permanent / bot / unknown。"""
    if "Sign in to confirm" in stderr or "not a bot" in stderr:
        return "bot"
    if any(k in stderr for k in (
        "Subtitles are not available", "no subtitles", "No subtitles",
        "members-only", "Private video", "Video unavailable", "not available",
    )):
        return "permanent"
    if any(k in stderr for k in (
        "429", "Too Many Requests", "rate", "Rate",
        "timed out", "Temporary", "Connection", "Internet", "reset",
    )):
        return "retryable"
    return "unknown"


class YtdlpChannel:
    """YouTube 字幕/简介通道（不依赖 AgentLimb）。"""

    name = "ytdlp"
    requires_bridge = False

    def __init__(self) -> None:
        self._rotate = _ProxyRotator(_proxy_pool())

    def supports(self, source_type: str, url: str) -> bool:
        return source_type == _YOUTUBE and _extract_vid(url) is not None

    def fetch(self, item) -> tuple[bool, str, str]:
        url = (item.get("url") or "").strip()
        vid = _extract_vid(url)
        if not vid:
            return False, "", f"{PERMANENT}URL 无 YouTube ID"
        proxy = self._rotate.next()
        body, kind = self._fetch_subtitle(url, proxy)
        if kind == "ok" and body:
            return True, body, f"proxy={proxy}"
        if kind == "permanent":
            return False, "", f"{PERMANENT}无字幕/简介/不可抓"
        if kind == "bot":
            raise BridgeUnavailableError(f"YouTube 出口 {proxy} 被判 bot，本轮熔断")
        # retryable / unknown / 空正文 → 计数重试下轮
        reason = "429/网络临时故障" if kind == "retryable" else "抓取未返回正文"
        return False, "", f"{reason} (proxy={proxy})"

    def _fetch_subtitle(self, url: str, proxy: str) -> tuple[str, str]:
        """调用 yt-dlp 抓字幕/简介。返回 (body, kind)，kind ∈ ok/permanent/retryable/bot。"""
        tmp = tempfile.mkdtemp(prefix="yt_subs_")
        env = os.environ.copy()
        env["HTTP_PROXY"] = proxy
        env["HTTPS_PROXY"] = proxy
        try:
            for attempt in range(3):
                try:
                    run = subprocess.run(
                        [_YTDLP, "--proxy", proxy]
                        + _cookie_args()
                        + ["--skip-download", "--write-subs", "--write-auto-subs",
                           "--write-description",
                           "--sub-langs", ",".join(_LANG_PRIORITY),
                           "--sub-format", "vtt", "--no-progress", "--no-warnings",
                           "--output", f"{tmp}/%(id)s", url],
                        capture_output=True, text=True, timeout=_TIMEOUT, env=env,
                    )
                except subprocess.TimeoutExpired:
                    time.sleep(8 * (attempt + 1))
                    continue
                stderr = run.stderr or ""
                if run.returncode != 0:
                    kind = _classify(stderr)
                    if kind == "bot":
                        return "", "bot"
                    if kind == "permanent":
                        return "", "permanent"
                    time.sleep(5)
                    continue
                files = glob.glob(f"{tmp}/*.vtt")
                if not files:
                    desc = self._read_description(tmp)
                    if len(desc) >= _MIN_BODY:
                        return f"【视频简介】{desc}", "ok"
                    return "", "permanent"
                chosen = self._pick_caption(files)
                body = _parse_vtt(chosen)
                if len(body) >= _MIN_BODY:
                    return body, "ok"
                return "", "permanent"
            return "", "retryable"  # 重试耗尽 → 下轮
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _read_description(self, tmp: str) -> str:
        for df in glob.glob(f"{tmp}/*.description"):
            try:
                with open(df, encoding="utf-8", errors="ignore") as fh:
                    return fh.read().strip()
            except Exception:  # noqa: BLE001
                continue
        return ""

    def _pick_caption(self, files: list[str]) -> str:
        for lang in _LANG_PRIORITY:
            for f in files:
                if f".{lang}.vtt" in f:
                    return f
        return max(files, key=os.path.getsize)
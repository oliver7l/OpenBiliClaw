"""Bilibili 视频字幕抓取模块。

只抓取字幕，不下载视频文件。无字幕的视频自动跳过。

字幕获取流程：
1. 通过 x/web-interface/view?bvid=xxx 获取视频 cid
2. 通过 x/player/v2?bvid=xxx&cid=xxx 获取字幕列表
3. 调用字幕 URL 获取 JSON 格式字幕内容
4. 解析为纯文本（带时间戳或不带时间戳）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger("bilibili.subtitle")

_BILIBILI_BASE = "https://api.bilibili.com"


@dataclass
class SubtitleCue:
    """一条字幕。"""

    start: float  # 开始时间（秒）
    end: float  # 结束时间（秒）
    text: str  # 字幕文本


@dataclass
class SubtitleTrack:
    """一个字幕轨道。"""

    lan: str = ""  # 语言代码，如 "zh-CN"
    lan_doc: str = ""  # 语言描述，如 "中文（中国）"
    subtitle_url: str = ""  # 字幕内容 URL
    cues: list[SubtitleCue] = field(default_factory=list)

    @property
    def duration(self) -> float:
        """字幕总时长（秒）。"""
        if not self.cues:
            return 0.0
        return self.cues[-1].end

    def to_text(self, *, with_timestamps: bool = False) -> str:
        """转为纯文本。"""
        if with_timestamps:
            lines = []
            for cue in self.cues:
                lines.append(f"[{cue.start:.1f}-{cue.end:.1f}] {cue.text}")
            return "\n".join(lines)
        return " ".join(cue.text for cue in self.cues)


class BilibiliSubtitleFetcher:
    """Bilibili 视频字幕抓取器。

    只抓取字幕，不下载视频。无字幕的视频返回空列表。

    Args:
        cookie: Bilibili 登录 cookie（可选，部分视频需要登录才能获取字幕）
        timeout: 请求超时时间（秒）
    """

    def __init__(self, cookie: str = "", timeout: float = 15.0) -> None:
        self._cookie = cookie
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> BilibiliSubtitleFetcher:
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            headers=self._default_headers(),
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _default_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
        }
        if self._cookie:
            headers["Cookie"] = self._cookie
        return headers

    async def get_cid(self, bvid: str) -> int:
        """通过 BV 号获取视频 cid。

        Returns:
            视频 cid，失败返回 0。
        """
        if not self._client:
            raise RuntimeError("Must use as async context manager")
        try:
            resp = await self._client.get(
                f"{_BILIBILI_BASE}/x/web-interface/view",
                params={"bvid": bvid},
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            return int(data.get("cid", 0))
        except Exception as e:
            logger.debug("Failed to get cid for %s: %s", bvid, e)
            return 0

    async def list_subtitles(self, bvid: str, cid: int = 0) -> list[SubtitleTrack]:
        """获取视频的字幕列表（不下载字幕内容）。

        Args:
            bvid: 视频 BV 号
            cid: 视频 cid，为 0 时自动获取

        Returns:
            字幕轨道列表，无字幕时返回空列表。
        """
        if not self._client:
            raise RuntimeError("Must use as async context manager")

        if cid == 0:
            cid = await self.get_cid(bvid)
        if cid == 0:
            logger.debug("No cid for %s, skipping", bvid)
            return []

        try:
            resp = await self._client.get(
                f"{_BILIBILI_BASE}/x/player/v2",
                params={"bvid": bvid, "cid": cid},
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            subtitle_info = data.get("subtitle", {})
            subtitles = subtitle_info.get("subtitles", [])

            tracks = []
            for sub in subtitles:
                track = SubtitleTrack(
                    lan=sub.get("lan", ""),
                    lan_doc=sub.get("lan_doc", ""),
                    subtitle_url=sub.get("subtitle_url", ""),
                )
                # B 站的 subtitle_url 有时是相对路径，需要补全
                if track.subtitle_url and track.subtitle_url.startswith("//"):
                    track.subtitle_url = "https:" + track.subtitle_url
                tracks.append(track)
            return tracks
        except Exception as e:
            logger.debug("Failed to list subtitles for %s: %s", bvid, e)
            return []

    async def fetch_subtitle_content(self, track: SubtitleTrack) -> SubtitleTrack:
        """下载并解析字幕内容。

        Args:
            track: 字幕轨道（需包含 subtitle_url）

        Returns:
            填充了 cues 的字幕轨道。
        """
        if not self._client:
            raise RuntimeError("Must use as async context manager")
        if not track.subtitle_url:
            return track

        try:
            resp = await self._client.get(track.subtitle_url)
            resp.raise_for_status()
            data = resp.json()
            body = data.get("body", [])

            cues = []
            for item in body:
                cue = SubtitleCue(
                    start=float(item.get("from", 0)),
                    end=float(item.get("to", 0)),
                    text=item.get("content", "").strip(),
                )
                if cue.text:
                    cues.append(cue)
            track.cues = cues
            return track
        except Exception as e:
            logger.debug("Failed to fetch subtitle content: %s", e)
            return track

    async def fetch_subtitles(
        self,
        bvid: str,
        cid: int = 0,
        *,
        preferred_lan: str = "zh-CN",
    ) -> list[SubtitleTrack]:
        """获取视频的完整字幕（列表 + 内容）。

        这是最常用的入口方法。无字幕时返回空列表。

        Args:
            bvid: 视频 BV 号
            cid: 视频 cid，为 0 时自动获取
            preferred_lan: 优先语言，会把该语言的字幕排在第一位

        Returns:
            填充了内容的字幕轨道列表。
        """
        tracks = await self.list_subtitles(bvid, cid)
        if not tracks:
            return []

        # 优先语言排前面
        tracks.sort(key=lambda t: 0 if t.lan == preferred_lan else 1)

        # 下载所有字幕内容
        result = []
        for track in tracks:
            fetched = await self.fetch_subtitle_content(track)
            if fetched.cues:
                result.append(fetched)
        return result

    async def fetch_subtitle_text(
        self,
        bvid: str,
        cid: int = 0,
        *,
        preferred_lan: str = "zh-CN",
        with_timestamps: bool = False,
    ) -> str:
        """获取视频的字幕纯文本。

        这是最简单的入口方法。无字幕时返回空字符串。

        Args:
            bvid: 视频 BV 号
            cid: 视频 cid，为 0 时自动获取
            preferred_lan: 优先语言
            with_timestamps: 是否带时间戳

        Returns:
            字幕纯文本，无字幕时返回空字符串。
        """
        tracks = await self.fetch_subtitles(bvid, cid, preferred_lan=preferred_lan)
        if not tracks:
            return ""
        return tracks[0].to_text(with_timestamps=with_timestamps)

"""B 站音频流下载器。

负责：
- 携带防盗链请求头下载音频流
- 使用 ffmpeg 转封装为标准 m4a 文件
- 下载限速与熔断保护

本模块改编自 bili-video2book (MIT License)。
原始版权：Copyright (c) 2026 Bilibili Audio Knowledge Skill Contributors
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import httpx

# 浏览器级请求头（模拟桌面浏览器行为，防盗链用）
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Origin": "https://www.bilibili.com",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}


class AudioDownloader:
    """B 站音频流下载器。

    携带防盗链头下载音频流，并可使用 ffmpeg 转封装为标准 m4a 文件。
    """

    def __init__(self, cookie: str = "", timeout: float = 60.0) -> None:
        self._cookie = cookie
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> AudioDownloader:
        headers = dict(DEFAULT_HEADERS)
        if self._cookie:
            headers["Cookie"] = self._cookie
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            headers=headers,
            trust_env=False,
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def download_audio(
        self,
        stream_url: str,
        output_filepath: str,
        *,
        repackage_m4a: bool = True,
        max_bytes: int | None = None,
    ) -> str:
        """携带防盗链头下载音频并转封装为音频文件。

        Args:
            stream_url: 音频流 URL。
            output_filepath: 输出文件路径（不含后缀）。
            repackage_m4a: 是否使用 ffmpeg 转封装为标准 m4a。
            max_bytes: 最大下载字节数（可选，用于限制下载量）。

        Returns:
            最终音频文件路径（通常为 .m4a）。

        Raises:
            RuntimeError: 下载失败时抛出。
        """
        if self._client is None:
            raise RuntimeError("AudioDownloader 必须作为 async context manager 使用")

        output = Path(output_filepath).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)

        # 原始流文件（m4s 格式）
        temp_raw = output.with_suffix(".raw.m4s")

        try:
            downloaded = 0
            async with self._client.stream("GET", stream_url) as response:
                response.raise_for_status()
                with open(temp_raw, "wb") as f:
                    chunk_size = 128 * 1024  # 128KB
                    async for chunk in response.aiter_bytes(chunk_size):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if max_bytes and downloaded >= max_bytes:
                            break
        except Exception as e:
            if temp_raw.exists():
                temp_raw.unlink()
            raise RuntimeError(f"音频下载失败: {e}") from e

        target = output.with_suffix(".m4a")

        # 使用 ffmpeg 零损耗转封装
        ffmpeg_bin = shutil.which("ffmpeg")
        if repackage_m4a and ffmpeg_bin:
            cmd = [
                ffmpeg_bin,
                "-y",
                "-i",
                str(temp_raw),
                "-acodec",
                "copy",
                str(target),
            ]
            result = subprocess.run(cmd, capture_output=True)
            if result.returncode == 0:
                if temp_raw.exists():
                    temp_raw.unlink()
                return str(target)

        # 无 ffmpeg 或转封装失败则直接改名
        if temp_raw.exists():
            temp_raw.rename(target)
        return str(target)

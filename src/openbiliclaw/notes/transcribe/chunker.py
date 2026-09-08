"""基于 FFmpeg Stream Copy 的无损音频切片器。

优化面向多模态 AI 模型（音频 Agent / 多模态 LLM）：
- 将长视频（45 分钟 - 2 小时）切分为 10-20 分钟的语义块
- 零重编码：使用 `-acodec copy` 实现瞬时（<0.1s）分段
- 生成带时间戳的结构化清单，供下游 AI 聚合使用

本模块改编自 bili-video2book (MIT License)。
原始版权：Copyright (c) 2026 Bilibili Audio Knowledge Skill Contributors
"""

from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

SUPPORTED_VIDEO_EXTS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".flv",
    ".wmv",
    ".webm",
    ".ts",
    ".m4v",
    ".rmvb",
}


class AudioChunker:
    """音频切片器。"""

    @staticmethod
    def get_audio_duration(audio_filepath: str) -> float:
        """使用 ffprobe 或 ffmpeg 获取音频文件时长（秒）。

        Args:
            audio_filepath: 音频文件路径。

        Returns:
            时长（秒），失败返回 0.0。

        """
        ffprobe_bin = shutil.which("ffprobe")
        if ffprobe_bin:
            cmd = [
                ffprobe_bin,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_filepath),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                try:
                    return float(res.stdout.strip())
                except ValueError:
                    pass

        # 回退到 ffmpeg -i 解析
        ffmpeg_bin = shutil.which("ffmpeg")
        if ffmpeg_bin:
            cmd = [ffmpeg_bin, "-i", str(audio_filepath)]
            res = subprocess.run(cmd, capture_output=True, text=True)
            output = res.stderr
            import re

            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", output)
            if m:
                hours = float(m.group(1))
                minutes = float(m.group(2))
                seconds = float(m.group(3))
                return hours * 3600 + minutes * 60 + seconds

        return 0.0

    @classmethod
    def chunk_audio(
        cls,
        audio_filepath: str,
        chunk_minutes: int = 10,
        balanced: bool = True,
        output_dir: str | None = None,
    ) -> list[dict[str, Any]]:
        """使用 stream copy 将音频切分为片段。

        若时长 <= chunk_minutes（默认 10 分钟），返回单个文件。
        若时长 > chunk_minutes 且 balanced=True，则均匀分为 N=ceil(duration/chunk) 片。

        Args:
            audio_filepath: 输入音频文件路径。
            chunk_minutes: 每片目标时长（分钟）。
            balanced: 是否均衡切分。
            output_dir: 输出目录，默认为输入文件同级目录的 <stem>_chunks。

        Returns:
            切片信息列表，每个元素包含 chunk_index、start_sec、end_sec、filepath 等。

        """
        src = Path(audio_filepath).resolve()
        if not src.exists():
            raise FileNotFoundError(f"音频文件不存在: {audio_filepath}")

        total_duration = cls.get_audio_duration(str(src))
        chunk_seconds = chunk_minutes * 60

        # 如果音频短于或等于目标切片大小，直接返回单文件
        if total_duration <= chunk_seconds or chunk_seconds <= 0:
            return [
                {
                    "chunk_index": 1,
                    "start_sec": 0.0,
                    "end_sec": total_duration,
                    "duration_sec": total_duration,
                    "start_time_str": "00:00:00",
                    "end_time_str": cls.format_seconds(total_duration),
                    "filepath": str(src),
                }
            ]

        target_dir = Path(output_dir).resolve() if output_dir else src.parent / f"{src.stem}_chunks"
        target_dir.mkdir(parents=True, exist_ok=True)

        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            raise RuntimeError("音频切片需要 FFmpeg，请先安装 ffmpeg")

        # 均衡切分：N = ceil(duration / chunk_seconds)
        if balanced:
            num_chunks = max(1, math.ceil(total_duration / float(chunk_seconds)))
            slice_dur = total_duration / float(num_chunks)
        else:
            slice_dur = float(chunk_seconds)
            num_chunks = max(1, math.ceil(total_duration / float(chunk_seconds)))

        chunks = []
        for index in range(1, num_chunks + 1):
            start_sec = (index - 1) * slice_dur
            end_sec = (
                min(total_duration, index * slice_dur) if index < num_chunks else total_duration
            )
            duration_current = end_sec - start_sec

            chunk_filename = f"{src.stem}_part_{index:03d}{src.suffix}"
            chunk_path = target_dir / chunk_filename

            cmd = [
                ffmpeg_bin,
                "-y",
                "-ss",
                str(round(start_sec, 2)),
                "-i",
                str(src),
                "-t",
                str(round(duration_current, 2)),
                "-acodec",
                "copy",
                str(chunk_path),
            ]
            subprocess.run(cmd, capture_output=True, check=True)

            chunks.append(
                {
                    "chunk_index": index,
                    "start_sec": round(start_sec, 2),
                    "end_sec": round(end_sec, 2),
                    "duration_sec": round(duration_current, 2),
                    "start_time_str": cls.format_seconds(start_sec),
                    "end_time_str": cls.format_seconds(end_sec),
                    "filepath": str(chunk_path),
                }
            )

        return chunks

    @staticmethod
    def format_seconds(seconds: float) -> str:
        """将秒数格式化为 HH:MM:SS 字符串。"""
        s = int(round(seconds))
        hours = s // 3600
        minutes = (s % 3600) // 60
        secs = s % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

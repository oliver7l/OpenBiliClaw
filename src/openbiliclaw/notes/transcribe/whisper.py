"""基于 faster-whisper 的高性能本地音频转录器。

设计哲学：
- transcribe() = 纯本地 faster-whisper 离线转录（CPU int8 / CUDA float16）。
- 转录原则：优先字幕，whisper 仅兜底。
- 零环境变量、零端口：本工具不读写任何环境变量、不绑定端口。

本模块改编自 bili-video2book (MIT License)。
原始版权：Copyright (c) 2026 Bilibili Audio Knowledge Skill Contributors
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class AudioTranscriber:
    """本地音频转录器（基于 faster-whisper）。

    faster-whisper 为可选依赖，未安装时调用会抛出明确错误。
    """

    _cached_models: dict[str, Any] = {}

    @classmethod
    def get_model(
        cls,
        model_size: str = "base",
        device: str = "auto",
        compute_type: str | None = None,
    ):
        """获取或实例化缓存的 faster-whisper 模型。

        Args:
            model_size: 模型大小（tiny/base/small/medium/large-v3 等）。
            device: 设备（auto/cpu/cuda）。
            compute_type: 计算精度（int8/float16 等）。

        Returns:
            WhisperModel 实例。

        Raises:
            RuntimeError: faster-whisper 未安装时抛出。

        """
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped]
        except ImportError as err:
            raise RuntimeError(
                "faster-whisper 未安装。本地转录功能需要该可选依赖。"
                "请执行 `pip install faster-whisper` 安装。"
            ) from err

        if device == "auto":
            device = "cpu"
        compute_type = compute_type or ("float16" if device == "cuda" else "int8")

        cache_key = f"{model_size}_{device}_{compute_type}"
        if cache_key not in cls._cached_models:
            num_threads = min(8, os.cpu_count() or 4) if device == "cpu" else 4
            cls._cached_models[cache_key] = WhisperModel(
                model_size_or_path=model_size,
                device=device,
                compute_type=compute_type,
                cpu_threads=num_threads,
            )
        return cls._cached_models[cache_key]

    @classmethod
    def transcribe(
        cls,
        audio_path: str | Path,
        model_size: str = "base",
        language: str = "zh",
        device: str = "auto",
        compute_type: str | None = None,
        beam_size: int = 1,
        initial_prompt: str | None = None,
    ) -> dict[str, Any]:
        """使用纯本地 faster-whisper 转录音频文件（离线）。

        Args:
            audio_path: 音频文件路径。
            model_size: 模型大小。
            language: 语言代码（默认 zh）。
            device: 设备。
            compute_type: 计算精度。
            beam_size: beam search 大小。
            initial_prompt: 初始提示词（用于专有名词引导）。

        Returns:
            转录结果字典，包含 full_text、timestamped_text、segments 等。

        Raises:
            FileNotFoundError: 音频文件不存在。
            RuntimeError: faster-whisper 未安装。

        """
        path_obj = Path(audio_path).resolve()
        if not path_obj.exists():
            raise FileNotFoundError(f"音频文件不存在: {path_obj}")

        model = cls.get_model(
            model_size=model_size,
            device=device,
            compute_type=compute_type,
        )

        segments_gen, info = model.transcribe(
            str(path_obj),
            language=language,
            beam_size=beam_size,
            initial_prompt=initial_prompt,
        )

        segments_list = []
        raw_text_pieces = []
        timestamped_lines = []

        for seg in segments_gen:
            clean_t = seg.text.strip()
            if not clean_t:
                continue
            start_str = cls.format_seconds(seg.start)
            end_str = cls.format_seconds(seg.end)
            segments_list.append(
                {
                    "start": round(seg.start, 2),
                    "end": round(seg.end, 2),
                    "start_fmt": start_str,
                    "end_fmt": end_str,
                    "text": clean_t,
                }
            )
            raw_text_pieces.append(clean_t)
            timestamped_lines.append(f"[{start_str} -> {end_str}] {clean_t}")

        full_text = " ".join(raw_text_pieces)
        timestamped_text = "\n".join(timestamped_lines)

        return {
            "full_text": full_text,
            "timestamped_text": timestamped_text,
            "segments": segments_list,
            "total_segments": len(segments_list),
            "language": info.language,
            "language_probability": round(info.language_probability, 4),
            "duration": round(info.duration, 2),
            "engine": f"local-whisper-{model_size}",
        }

    @staticmethod
    def format_seconds(seconds: float) -> str:
        """将秒数格式化为 HH:MM:SS 字符串。"""
        s = int(round(seconds))
        hours = s // 3600
        minutes = (s % 3600) // 60
        secs = s % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

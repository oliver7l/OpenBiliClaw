"""视频转笔记管线。

编排从 B 站视频到结构化笔记的完整流程：
1. 字幕优先：尝试获取 CC 字幕（有则跳过音频下载）
2. 音频兜底：下载音频流 → 切片 → 本地转录
3. 文本清洗：非破坏性规范化
4. LLM 合成：ASR 校对 → 结构化笔记生成
5. 入库：写入 notes 表

临时文件策略：
- 音频下载、切片、转录中间产物均写入系统临时目录
- 管线完成后立即清理临时文件
- 只有最终结构化笔记正文和清洗后的转录文本持久化

本管线设计参考 bili-video2book (MIT License)。
原始版权：Copyright (c) 2026 Bilibili Audio Knowledge Skill Contributors
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..bilibili.api import BilibiliAPIClient, BilibiliAPIError
from ..bilibili.subtitle import BilibiliSubtitleFetcher
from .models import Note, NoteCreate
from .synthesis.generator import NoteGenerator
from .transcribe.cleaner import TextCleaner
from .transcribe.fetcher import AudioDownloader

logger = logging.getLogger(__name__)


@dataclass
class VideoToNoteResult:
    """视频转笔记结果。"""

    success: bool = False
    note: Note | None = None
    source: str = ""  # subtitle / audio
    error: str = ""
    stages: dict[str, Any] = field(default_factory=dict)


class VideoToNotePipeline:
    """视频转笔记管线。

    编排从 B 站视频到结构化笔记的完整流程。
    """

    def __init__(
        self,
        *,
        bilibili_client: BilibiliAPIClient | None = None,
        cookie: str = "",
        llm_service: Any | None = None,
        note_service: Any | None = None,
        prefer_subtitle: bool = True,
        enable_asr_rectify: bool = True,
        content_type: str = "article",
        whisper_model: str = "base",
    ) -> None:
        """初始化管线。

        Args:
            bilibili_client: BilibiliAPIClient 实例（复用现有连接和 WBI 密钥）。
            cookie: B 站登录 Cookie（bilibili_client 为 None 时使用）。
            llm_service: LLMService 实例，用于笔记生成。
            note_service: NoteService 实例，用于笔记入库。
            prefer_subtitle: 是否优先使用 CC 字幕。
            enable_asr_rectify: 是否启用 ASR 校对（需要 LLM）。
            content_type: 笔记内容类型（study/news/general/article）。
            whisper_model: faster-whisper 模型大小（base/small/medium 等）。

        """
        self._bilibili = bilibili_client
        self._cookie = cookie
        self._llm_service = llm_service
        self._note_service = note_service
        self._prefer_subtitle = prefer_subtitle
        self._enable_asr_rectify = enable_asr_rectify
        self._content_type = content_type
        self._whisper_model = whisper_model

    async def run(
        self,
        bvid: str,
        *,
        cid: int = 0,
        save_note: bool = True,
    ) -> VideoToNoteResult:
        """执行视频转笔记全流程。

        Args:
            bvid: 视频 BV 号。
            cid: 视频 cid，为 0 时自动获取。
            save_note: 是否保存到笔记库。

        Returns:
            VideoToNoteResult 结果对象。

        """
        result = VideoToNoteResult()
        temp_dir: Path | None = None

        try:
            # 1. 获取视频基本信息
            video_info = await self._get_video_info(bvid)
            result.stages["video_info"] = {
                "title": video_info.get("title", ""),
                "up_name": video_info.get("up_name", ""),
                "duration": video_info.get("duration", 0),
            }

            # 2. 获取转录文本（字幕优先，音频兜底）
            raw_text = ""
            source = ""

            if self._prefer_subtitle:
                raw_text = await self._try_subtitle(bvid, cid)
                if raw_text:
                    source = "subtitle"
                    result.stages["subtitle"] = {"success": True, "length": len(raw_text)}

            if not raw_text:
                # 字幕失败或未启用，走音频路径
                temp_dir = Path(tempfile.mkdtemp(prefix=f"note_{bvid}_"))
                raw_text = await self._transcribe_via_audio(bvid, cid, temp_dir, video_info)
                source = "audio"
                result.stages["audio_transcribe"] = {
                    "success": bool(raw_text),
                    "length": len(raw_text),
                }

            if not raw_text:
                result.error = "无法获取视频文本内容（无字幕且音频转录失败）"
                return result

            result.source = source

            # 3. 文本清洗
            clean_result = TextCleaner.clean(raw_text)
            cleaned_text = clean_result["cleaned_text"]
            result.stages["clean"] = {
                "original_length": clean_result["original_length"],
                "cleaned_length": clean_result["cleaned_length"],
                "compression_ratio": clean_result["compression_ratio"],
            }

            if not cleaned_text.strip():
                result.error = "清洗后文本为空"
                return result

            # 4. ASR 校对（可选）
            rectified_text = cleaned_text
            if self._enable_asr_rectify and self._llm_service and source == "audio":
                generator = NoteGenerator(self._llm_service)
                domain_hint = video_info.get("title", "")
                rectified_text = await generator.rectify_asr(cleaned_text, domain_hint=domain_hint)
                result.stages["asr_rectify"] = {
                    "success": True,
                    "length": len(rectified_text),
                }

            # 5. 生成结构化笔记
            note_content = ""
            if self._llm_service:
                generator = NoteGenerator(self._llm_service)
                note_content = await generator.generate_note(
                    title=video_info.get("title", bvid),
                    content=rectified_text,
                    part_title="P1",
                    content_type=self._content_type,
                )
                result.stages["note_generation"] = {
                    "success": True,
                    "length": len(note_content),
                }
            else:
                # 无 LLM 时直接用清洗后的文本作为笔记
                note_content = rectified_text
                result.stages["note_generation"] = {
                    "success": True,
                    "length": len(note_content),
                    "mode": "raw_text_no_llm",
                }

            # 6. 入库（可选）
            if save_note and self._note_service and note_content:
                note_data = NoteCreate(
                    title=video_info.get("title", bvid),
                    content_md=note_content,
                    note_type="video",
                    source_platform="bilibili",
                    source_url=f"https://www.bilibili.com/video/{bvid}",
                    source_ref=bvid,
                    author=video_info.get("up_name", ""),
                    tags=[],
                    metadata={
                        "source": source,
                        "content_type": self._content_type,
                        "duration": video_info.get("duration", 0),
                        "transcript_length": len(rectified_text),
                    },
                )
                note = self._note_service.create_note(note_data)
                result.note = note
                result.stages["save"] = {"success": True, "note_id": note.id}

            result.success = True
            return result

        except BilibiliAPIError as e:
            result.error = f"B站接口错误: {e}"
            logger.error("视频转笔记失败（B站接口）: %s", e)
            return result
        except Exception as e:
            result.error = f"未知错误: {e}"
            logger.exception("视频转笔记失败")
            return result
        finally:
            # 清理临时文件
            if temp_dir and temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception as e:
                    logger.warning("清理临时目录失败 %s: %s", temp_dir, e)

    async def _get_video_info(self, bvid: str) -> dict[str, Any]:
        """获取视频基本信息。"""
        if self._bilibili:
            info = await self._bilibili.get_video_info(bvid)
            return {
                "title": info.title,
                "up_name": info.up_name,
                "duration": info.duration,
                "cid": getattr(info, "cid", 0),
            }
        # 没有 client 时用 subtitle fetcher 获取 cid 和标题
        async with BilibiliSubtitleFetcher(cookie=self._cookie) as fetcher:
            fetched_cid = await fetcher.get_cid(bvid)
            return {"title": bvid, "up_name": "", "duration": 0, "cid": fetched_cid}

    async def _try_subtitle(self, bvid: str, cid: int) -> str:
        """尝试获取 CC 字幕。

        Returns:
            字幕纯文本，获取失败返回空字符串。

        """
        try:
            async with BilibiliSubtitleFetcher(cookie=self._cookie) as fetcher:
                actual_cid = cid or await fetcher.get_cid(bvid)
                if actual_cid == 0:
                    return ""
                text = await fetcher.fetch_subtitle_text(bvid, actual_cid, preferred_lan="zh-CN")
                return text
        except Exception as e:
            logger.debug("获取字幕失败 %s: %s", bvid, e)
            return ""

    async def _transcribe_via_audio(
        self,
        bvid: str,
        cid: int,
        temp_dir: Path,
        video_info: dict[str, Any],
    ) -> str:
        """通过音频下载 + 转录获取文本。

        Args:
            bvid: 视频 BV 号。
            cid: 视频 cid。
            temp_dir: 临时目录。
            video_info: 视频信息。

        Returns:
            转录文本，失败返回空字符串。

        """
        # 确保有 bilibili client 用于获取音频流
        if self._bilibili is None:
            logger.error("音频转录需要 BilibiliAPIClient 实例")
            return ""

        actual_cid = cid or video_info.get("cid", 0)
        if actual_cid == 0:
            logger.error("无法获取视频 cid，无法下载音频")
            return ""

        try:
            # 1. 获取音频流地址
            stream_info = await self._bilibili.get_audio_streams(
                bvid, actual_cid, prefer_quality="low"
            )
            stream_url = stream_info.get("best_stream_url", "")
            if not stream_url:
                logger.error("未获取到音频流地址")
                return ""

            # 2. 下载音频
            audio_path = temp_dir / "audio"
            async with AudioDownloader(cookie=self._cookie) as downloader:
                m4a_path = await downloader.download_audio(
                    stream_url, str(audio_path), repackage_m4a=True
                )

            if not Path(m4a_path).exists():
                logger.error("音频下载失败")
                return ""

            # 3. 切片（10 分钟一片，适配 whisper 上下文）
            from .transcribe.chunker import AudioChunker

            chunks = AudioChunker.chunk_audio(
                m4a_path, chunk_minutes=10, balanced=True, output_dir=str(temp_dir / "chunks")
            )

            # 4. 逐片转录
            from .transcribe.whisper import AudioTranscriber

            all_texts = []
            for chunk in chunks:
                try:
                    result = AudioTranscriber.transcribe(
                        chunk["filepath"],
                        model_size=self._whisper_model,
                        language="zh",
                        initial_prompt=video_info.get("title", ""),
                    )
                    all_texts.append(result["full_text"])
                except Exception as e:
                    logger.warning("转录分片失败 %s: %s", chunk.get("chunk_index"), e)
                    continue

            return " ".join(all_texts)

        except ImportError as e:
            logger.warning("本地转录依赖未安装: %s", e)
            return ""
        except Exception as e:
            logger.error("音频转录失败: %s", e)
            return ""

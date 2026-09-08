"""笔记业务逻辑层。

提供笔记的增删改查、导入导出、与现有文章/阅读归档系统的联动等高层接口。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .models import (
    Note,
    NoteCreate,
    NoteListParams,
    NoteStats,
    NoteTask,
    NoteTaskCreate,
    NoteUpdate,
)
from .pipeline import VideoToNotePipeline, VideoToNoteResult
from .store import NoteStore

if TYPE_CHECKING:
    from ..bilibili.api import BilibiliAPIClient
    from ..llm.service import LLMService
    from ..storage.database import Database

logger = logging.getLogger(__name__)


class NoteService:
    """笔记业务服务。

    封装存储层，提供高层业务接口。
    """

    def __init__(
        self,
        database: Database | None = None,
        db_path: str | None = None,
        llm_service: LLMService | None = None,
        bilibili_client: BilibiliAPIClient | None = None,
    ) -> None:
        self.store = NoteStore(database=database, db_path=db_path)
        self.store.initialize()
        self._llm_service = llm_service
        self._bilibili_client = bilibili_client

    # ── 笔记 CRUD ──

    def create_note(self, data: NoteCreate) -> Note:
        """创建笔记。"""
        return self.store.create_note(data)

    def get_note(self, note_id: int) -> Note | None:
        """获取笔记。"""
        return self.store.get_note(note_id)

    def update_note(self, note_id: int, data: NoteUpdate) -> Note | None:
        """更新笔记。"""
        return self.store.update_note(note_id, data)

    def delete_note(self, note_id: int) -> bool:
        """删除笔记。"""
        return self.store.delete_note(note_id)

    def list_notes(self, params: NoteListParams) -> list[Note]:
        """列出笔记。"""
        return self.store.list_notes(
            limit=params.limit,
            offset=params.offset,
            note_type=params.note_type,
            source_platform=params.source_platform,
            tag=params.tag,
            search=params.search,
            sort_by=params.sort_by,
            sort_order=params.sort_order,
        )

    def count_notes(self, params: NoteListParams) -> int:
        """统计笔记数量。"""
        return self.store.count_notes(
            note_type=params.note_type,
            source_platform=params.source_platform,
            tag=params.tag,
            search=params.search,
        )

    def get_stats(self) -> NoteStats:
        """获取笔记统计信息。"""
        raw = self.store.get_stats()
        return NoteStats(**raw)

    # ── 笔记任务 ──

    def create_task(
        self, source_platform: str, source_ref: str, resume_key: str | None = None
    ) -> NoteTask:
        """创建笔记生成任务。"""
        task_id = str(uuid.uuid4())[:8]
        if resume_key is None:
            resume_key = f"{source_platform}:{source_ref}:v1"
        data = NoteTaskCreate(
            task_id=task_id,
            source_platform=source_platform,
            source_ref=source_ref,
            resume_key=resume_key,
        )
        return self.store.create_task(data)

    def get_task(self, task_id: str) -> NoteTask | None:
        """获取任务。"""
        return self.store.get_task(task_id)

    def list_tasks(
        self, limit: int = 20, offset: int = 0, status: str | None = None
    ) -> list[NoteTask]:
        """列出任务。"""
        return self.store.list_tasks(limit=limit, offset=offset, status=status)

    # ── 导入功能 ──

    def import_from_read_archive(self, notes_dir: str | Path) -> dict[str, Any]:
        """从 notes/已读库 目录导入笔记。

        扫描 notes/已读库/<标题>/ 四件套（meta.json, raw.html, content.md, reading.html），
        将 content.md 导入为笔记。
        """
        notes_path = Path(notes_dir)
        if not notes_path.exists() or not notes_path.is_dir():
            return {"imported": 0, "skipped": 0, "errors": 0, "message": "目录不存在"}

        imported = 0
        skipped = 0
        errors = 0

        for item_dir in sorted(notes_path.iterdir()):
            if not item_dir.is_dir():
                continue

            meta_file = item_dir / "meta.json"
            content_file = item_dir / "content.md"

            if not meta_file.exists() or not content_file.exists():
                skipped += 1
                continue

            try:
                import json

                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                content_md = content_file.read_text(encoding="utf-8")

                title = meta.get("title", item_dir.name)
                source_url = meta.get("url", "")
                platform = meta.get("platform", "")
                author = meta.get("author", "")
                tags = meta.get("tags", [])
                source_ref = meta.get("source_ref", "")

                data = NoteCreate(
                    title=title,
                    content_md=content_md,
                    note_type="import",
                    source_platform=platform,
                    source_url=source_url,
                    source_ref=source_ref,
                    author=author,
                    tags=tags,
                    metadata={"imported_from": str(item_dir)},
                )
                self.store.create_note(data)
                imported += 1
            except Exception as e:
                logger.warning("导入笔记失败 %s: %s", item_dir, e)
                errors += 1

        return {"imported": imported, "skipped": skipped, "errors": errors}

    # ── 视频转笔记 ──

    async def video_to_note(
        self,
        bvid: str,
        *,
        cid: int = 0,
        save_note: bool = True,
        prefer_subtitle: bool = True,
        enable_asr_rectify: bool = True,
        content_type: str = "article",
        whisper_model: str = "base",
        cookie: str = "",
    ) -> VideoToNoteResult:
        """将 B 站视频转为结构化笔记。

        字幕优先策略：先尝试获取 CC 字幕，失败则下载音频用 faster-whisper 本地转录。
        转录完成后使用 LLM 生成结构化笔记并入库。

        Args:
            bvid: 视频 BV 号。
            cid: 视频 cid，为 0 时自动获取。
            save_note: 是否保存到笔记库。
            prefer_subtitle: 是否优先使用 CC 字幕。
            enable_asr_rectify: 是否启用 ASR 校对（需要 LLM）。
            content_type: 笔记内容类型（study/news/general/article）。
            whisper_model: faster-whisper 模型大小。
            cookie: B 站登录 Cookie（bilibili_client 未设置时使用）。

        Returns:
            VideoToNoteResult 结果对象。

        """
        pipeline = VideoToNotePipeline(
            bilibili_client=self._bilibili_client,
            cookie=cookie,
            llm_service=self._llm_service,
            note_service=self if save_note else None,
            prefer_subtitle=prefer_subtitle,
            enable_asr_rectify=enable_asr_rectify,
            content_type=content_type,
            whisper_model=whisper_model,
        )
        return await pipeline.run(bvid, cid=cid, save_note=save_note)

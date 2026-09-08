"""笔记系统：内容消费的知识沉淀层。

将看过的好内容（B 站视频、专栏、知乎、小红书等）转化为结构化笔记入库，
成为可检索、可回顾、可影响画像的私有知识库。

笔记类型：manual / video / article / essay / import
"""

from __future__ import annotations

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
from .service import NoteService
from .store import NoteStore

__all__ = [
    "Note",
    "NoteCreate",
    "NoteListParams",
    "NoteStats",
    "NoteStore",
    "NoteService",
    "NoteTask",
    "NoteTaskCreate",
    "NoteUpdate",
    "VideoToNotePipeline",
    "VideoToNoteResult",
]

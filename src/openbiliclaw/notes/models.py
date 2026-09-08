"""笔记系统数据模型。

定义笔记条目、笔记任务等核心数据结构，使用 Pydantic 进行类型校验与序列化。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Note(BaseModel):
    """单条笔记条目。"""

    id: int = Field(description="笔记唯一 ID")
    title: str = Field(description="笔记标题")
    content_md: str = Field(default="", description="结构化笔记正文（Markdown）")
    note_type: str = Field(
        default="manual",
        description="笔记类型：manual / video / article / essay / import",
    )
    source_platform: str = Field(
        default="", description="来源平台：bilibili / zhihu / xiaohongshu / ..."
    )
    source_url: str = Field(default="", description="来源 URL")
    source_ref: str = Field(default="", description="来源 ID，如 BV 号、知乎专栏 ID")
    author: str = Field(default="", description="来源作者/UP主")
    tags: list[str] = Field(default_factory=list, description="标签列表")
    metadata: dict = Field(default_factory=dict, description="JSON 扩展字段，存平台差异信息")
    raw_ref: str = Field(default="", description="关联清洗后转录文本 / 原文摘要路径")
    task_id: str = Field(default="", description="关联生成任务 ID")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")


class NoteCreate(BaseModel):
    """创建笔记条目请求。"""

    title: str = Field(description="笔记标题")
    content_md: str = Field(default="", description="结构化笔记正文（Markdown）")
    note_type: str = Field(default="manual", description="笔记类型")
    source_platform: str = Field(default="", description="来源平台")
    source_url: str = Field(default="", description="来源 URL")
    source_ref: str = Field(default="", description="来源 ID")
    author: str = Field(default="", description="来源作者")
    tags: list[str] = Field(default_factory=list, description="标签列表")
    metadata: dict = Field(default_factory=dict, description="JSON 扩展字段")
    raw_ref: str = Field(default="", description="关联文本路径")
    task_id: str = Field(default="", description="关联生成任务 ID")


class NoteUpdate(BaseModel):
    """更新笔记条目请求。"""

    title: str | None = Field(default=None, description="笔记标题")
    content_md: str | None = Field(default=None, description="结构化笔记正文")
    note_type: str | None = Field(default=None, description="笔记类型")
    source_platform: str | None = Field(default=None, description="来源平台")
    source_url: str | None = Field(default=None, description="来源 URL")
    source_ref: str | None = Field(default=None, description="来源 ID")
    author: str | None = Field(default=None, description="来源作者")
    tags: list[str] | None = Field(default=None, description="标签列表")
    metadata: dict | None = Field(default=None, description="JSON 扩展字段")


class NoteTask(BaseModel):
    """笔记生成任务记录。"""

    task_id: str = Field(description="任务唯一 ID")
    source_platform: str = Field(description="来源平台")
    source_ref: str = Field(description="来源 ID，BV 号或合集 ID")
    resume_key: str = Field(description="恢复键：source_ref + pipeline_version")
    status: str = Field(
        default="pending",
        description="状态：pending / running / done / failed / partial",
    )
    current_stage: str = Field(default="", description="当前阶段")
    items_json: str = Field(default="[]", description="分集状态 JSON")
    error_summary: str = Field(default="", description="错误摘要")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")


class NoteTaskCreate(BaseModel):
    """创建笔记任务请求。"""

    task_id: str = Field(description="任务唯一 ID")
    source_platform: str = Field(description="来源平台")
    source_ref: str = Field(description="来源 ID")
    resume_key: str = Field(description="恢复键")
    items_json: str = Field(default="[]", description="分集状态")


class NoteListParams(BaseModel):
    """笔记列表查询参数。"""

    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    note_type: str | None = Field(default=None, description="按类型筛选")
    source_platform: str | None = Field(default=None, description="按平台筛选")
    tag: str | None = Field(default=None, description="按标签筛选")
    search: str | None = Field(default=None, description="全文搜索关键词")
    sort_by: str = Field(default="created_at", description="排序字段")
    sort_order: str = Field(default="DESC", description="排序方向")


class NoteStats(BaseModel):
    """笔记统计信息。"""

    total: int = Field(description="笔记总数")
    by_type: dict[str, int] = Field(default_factory=dict, description="按类型统计")
    by_platform: dict[str, int] = Field(default_factory=dict, description="按平台统计")
    top_tags: list[dict] = Field(default_factory=list, description="热门标签")

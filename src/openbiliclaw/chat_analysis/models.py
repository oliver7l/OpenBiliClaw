"""聊天记录分析系统数据模型。

定义聊天会话、消息、AI 分析结果、统计信息等核心数据结构，
使用 Pydantic 进行类型校验与序列化。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import StrEnum

from pydantic import BaseModel, Field


class ChatType(StrEnum):
    """聊天类型枚举。"""

    GROUP = "group"  # 群聊
    PRIVATE = "private"  # 私聊


class MessageType(StrEnum):
    """消息类型枚举。"""

    TEXT = "text"  # 文本消息
    MEDIA = "media"  # 媒体消息（图片/视频/语音）
    SELF = "self"  # 自己发送的消息
    SYSTEM = "system"  # 系统消息


class ChatSession(BaseModel):
    """聊天会话。

    对应一个微信群聊或私聊，包含所有消息的元数据。
    """

    id: int = Field(description="会话唯一 ID")
    title: str = Field(description="会话标题/名称")
    source_file: str | None = Field(default=None, description="原始导出文件路径")
    time_range: str = Field(default="最早 ~ 最新", description="消息时间范围")
    export_time: str | None = Field(default=None, description="导出时间")
    message_count: int = Field(default=0, description="消息总数")
    chat_type: ChatType | None = Field(default=None, description="聊天类型")
    file_path: str | None = Field(default=None, description="源文件路径")
    file_size: int | None = Field(default=None, description="源文件大小")
    analyzed: bool = Field(default=False, description="是否已分析")
    last_analyzed_at: str | None = Field(default=None, description="上次分析时间")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")


class ChatSessionCreate(BaseModel):
    """创建聊天会话请求。"""

    title: str = Field(description="会话标题/名称")
    source_file: str | None = Field(default=None, description="原始导出文件路径")
    time_range: str = Field(default="最早 ~ 最新", description="消息时间范围")
    export_time: str | None = Field(default=None, description="导出时间")
    message_count: int = Field(default=0, description="消息总数")
    chat_type: ChatType | None = Field(default=None, description="聊天类型")
    file_path: str | None = Field(default=None, description="源文件路径")
    file_size: int | None = Field(default=None, description="源文件大小")


class ChatSessionUpdate(BaseModel):
    """更新聊天会话请求。"""

    title: str | None = Field(default=None, description="会话标题/名称")
    source_file: str | None = Field(default=None, description="原始导出文件路径")
    time_range: str | None = Field(default=None, description="消息时间范围")
    export_time: str | None = Field(default=None, description="导出时间")
    message_count: int | None = Field(default=None, description="消息总数")
    chat_type: ChatType | None = Field(default=None, description="聊天类型")


class ChatMessage(BaseModel):
    """单条聊天消息。"""

    id: int = Field(description="消息唯一 ID")
    session_id: int = Field(description="关联的会话 ID")
    timestamp: str | None = Field(default=None, description="消息时间戳 YYYY-MM-DD HH:MM")
    sender: str = Field(default="", description="发送者名称")
    content: str = Field(default="", description="消息内容")
    message_type: MessageType = Field(default=MessageType.TEXT, description="消息类型")
    created_at: datetime = Field(description="创建时间")


class ChatMessageCreate(BaseModel):
    """创建消息请求。"""

    session_id: int = Field(description="关联的会话 ID")
    timestamp: str | None = Field(default=None, description="消息时间戳 YYYY-MM-DD HH:MM")
    sender: str = Field(default="", description="发送者名称")
    content: str = Field(default="", description="消息内容")
    message_type: MessageType = Field(default=MessageType.TEXT, description="消息类型")


class ChatAnalysisChunk(BaseModel):
    """AI 分析片段。

    对应 DeepSeek 分块分析的一个文件，保存分析结果。
    """

    id: int = Field(description="分析片段 ID")
    session_title: str = Field(description="会话标题")
    start_line: int = Field(description="起始行号")
    end_line: int = Field(description="结束行号")
    analysis_content: str = Field(description="分析内容（Markdown）")
    analysis_file: str = Field(description="原始分析文件路径")
    model_used: str = Field(default="deepseek", description="使用的模型")
    created_at: datetime = Field(description="创建时间")


class ChatAnalysisChunkCreate(BaseModel):
    """创建分析片段请求。"""

    session_title: str = Field(description="会话标题")
    start_line: int = Field(description="起始行号")
    end_line: int = Field(description="结束行号")
    analysis_content: str = Field(description="分析内容（Markdown）")
    analysis_file: str = Field(description="原始分析文件路径")
    model_used: str = Field(default="deepseek", description="使用的模型")


class ChatSenderStats(BaseModel):
    """发送者统计信息。"""

    sender: str = Field(description="发送者名称")
    message_count: int = Field(description="消息数量")
    total_words: int = Field(description="总字数")
    avg_words_per_message: float = Field(description="平均每条消息字数")
    first_message_time: str | None = Field(default=None, description="第一条消息时间")
    last_message_time: str | None = Field(default=None, description="最后一条消息时间")


class ChatSessionStats(BaseModel):
    """聊天会话统计信息。"""

    session_id: int = Field(description="会话 ID")
    session_title: str = Field(description="会话标题")
    total_messages: int = Field(description="总消息数")
    total_words: int = Field(description="总字数")
    distinct_senders: int = Field(description="不同发送者数量")
    sender_stats: list[ChatSenderStats] = Field(
        default_factory=list, description="发送者统计（降序）"
    )
    message_types: dict[str, int] = Field(default_factory=dict, description="消息类型分布")
    time_range: tuple[str | None, str | None] = Field(default=(None, None), description="时间范围")
    analysis_count: int = Field(default=0, description="已分析片段数")


class ChatTopic(BaseModel):
    """聊天话题。

    AI 从聊天记录中提取的主题讨论。
    """

    id: int = Field(description="话题 ID")
    session_id: int = Field(description="关联的会话 ID")
    topic_name: str = Field(description="话题名称")
    keywords: list[str] = Field(default_factory=list, description="关键词列表")
    start_time: str | None = Field(default=None, description="开始时间")
    end_time: str | None = Field(default=None, description="结束时间")
    participant_count: int = Field(default=0, description="参与人数")
    message_count: int = Field(default=0, description="消息数")
    summary: str = Field(default="", description="话题摘要")
    created_at: datetime = Field(description="创建时间")


class ChatInsight(BaseModel):
    """聊天洞见。

    AI 从聊天记录中提炼的有价值洞见。
    """

    id: int = Field(description="洞见 ID")
    session_id: int = Field(description="关联的会话 ID")
    insight_type: str = Field(description="洞见类型：insight/guidance/action")
    content: str = Field(description="洞见内容")
    evidence_messages: list[int] = Field(default_factory=list, description="证据消息 ID 列表")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="置信度")
    created_at: datetime = Field(description="创建时间")


class ChatTag(BaseModel):
    """聊天标签。

    从聊天内容中提取的结构化标签。
    """

    id: int = Field(description="标签 ID")
    name: str = Field(description="标签名称")
    category: str = Field(default="other", description="分类：topic/tech/business/life/other")
    mention_count: int = Field(default=0, description="被提及次数")
    created_at: datetime = Field(description="创建时间")


class ChatTagCreate(BaseModel):
    """创建标签请求。"""

    name: str = Field(description="标签名称")
    category: str = Field(default="other", description="分类")


class ChatSearchResult(BaseModel):
    """全文搜索结果。"""

    message_id: int = Field(description="消息 ID")
    session_id: int = Field(description="会话 ID")
    session_title: str = Field(description="会话标题")
    sender: str = Field(description="发送者")
    timestamp: str | None = Field(default=None, description="时间戳")
    content: str = Field(description="内容片段（高亮匹配）")
    rank_score: float = Field(default=0.0, description="排序分数")


class ChatSearchResponse(BaseModel):
    """搜索响应。"""

    total: int = Field(description="总匹配数")
    results: list[ChatSearchResult] = Field(default_factory=list, description="搜索结果")
    took_ms: float = Field(description="搜索耗时（毫秒）")


class ChatEmbedding(BaseModel):
    """消息嵌入向量。

    用于语义搜索和相似消息查找。
    """

    id: int = Field(description="嵌入 ID")
    message_id: int = Field(description="关联的消息 ID")
    session_id: int = Field(description="关联的会话 ID")
    vector: list[float] = Field(description="嵌入向量")
    dimension: int = Field(description="向量维度")
    model: str = Field(description="使用的嵌入模型")
    created_at: datetime = Field(description="创建时间")


class ImportStats(BaseModel):
    """导入统计。"""

    sessions_imported: int = Field(description="导入的会话数")
    messages_imported: int = Field(description="导入的消息数")
    analysis_chunks_imported: int = Field(description="导入的分析片段数")
    errors: list[str] = Field(default_factory=list, description="错误列表")
    skipped: int = Field(default=0, description="跳过的数量（已存在）")


class DeepseekAnalysisImportResult(BaseModel):
    """DeepSeek 分析导入结果。"""

    session_title: str = Field(description="会话标题")
    chunks_imported: int = Field(description="导入的片段数")
    chunks_skipped: int = Field(default=0, description="已存在而跳过的片段数（幂等）")
    total_lines: int = Field(description="总分析行数")
    errors: list[str] = Field(default_factory=list, description="错误")

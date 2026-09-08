"""聊天记录分析系统：存储、导入、检索微信聊天记录及其 AI 分析结果。

参考日记模块架构设计，提供完整的聊天记录持久化、全文检索、统计分析功能。
支持从 mindback_data 导入原始聊天消息和 DeepSeek AI 分析结果。
"""

from .importer import ChatImporter, DeepseekAnalysisImportResult, ImportStats
from .models import (
    ChatAnalysisChunk,
    ChatAnalysisChunkCreate,
    ChatEmbedding,
    ChatInsight,
    ChatMessage,
    ChatMessageCreate,
    ChatSearchResponse,
    ChatSearchResult,
    ChatSenderStats,
    ChatSession,
    ChatSessionCreate,
    ChatSessionStats,
    ChatSessionUpdate,
    ChatTag,
    ChatTagCreate,
    ChatTopic,
    ChatType,
    MessageType,
)
from .service import ChatAnalysisService, LLMQuota
from .store import ChatAnalysisStore

__all__ = [
    "ChatAnalysisChunk",
    "ChatAnalysisChunkCreate",
    "ChatAnalysisService",
    "ChatAnalysisStore",
    "ChatEmbedding",
    "ChatImporter",
    "ChatInsight",
    "ChatMessage",
    "ChatMessageCreate",
    "ChatSearchResponse",
    "ChatSearchResult",
    "ChatSenderStats",
    "ChatSession",
    "ChatSessionCreate",
    "ChatSessionStats",
    "ChatSessionUpdate",
    "ChatTag",
    "ChatTagCreate",
    "ChatTopic",
    "ChatType",
    "DeepseekAnalysisImportResult",
    "ImportStats",
    "LLMQuota",
    "MessageType",
]

"""聊天记录分析业务逻辑层。

封装 ChatAnalysisStore，提供导入、统计、搜索、LLM 分析等高级操作。

LLM 配额说明：
- 默认每天最多消耗 100,000 tokens（约 2-3 次会话全量分析）
- 每次分析默认最多取 100 条消息，可在调用时调整 max_messages
- 超出配额会返回空结果并记录警告，不自动调用 LLM
"""

from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

from .importer import ChatImporter, DeepseekAnalysisImportResult, ImportStats
from .models import (
    ChatAnalysisChunk,
    ChatEmbedding,
    ChatInsight,
    ChatMessage,
    ChatSearchResponse,
    ChatSearchResult,
    ChatSession,
    ChatTag,
    ChatTopic,
)
from .store import ChatAnalysisStore

if TYPE_CHECKING:
    from ..storage.database import Database


class LLMQuota:
    """LLM 调用配额跟踪器，防止意外打爆额度。"""

    def __init__(self, max_tokens_per_day: int = 100_000):
        self.max_tokens_per_day = max_tokens_per_day
        self._today = date.today()
        self._used_tokens = 0

    def _reset_if_new_day(self) -> None:
        today = date.today()
        if today != self._today:
            self._today = today
            self._used_tokens = 0

    def check(self, estimated_tokens: int = 1) -> bool:
        """检查是否还有配额。返回 True 表示可以调用。"""
        self._reset_if_new_day()
        return self._used_tokens + estimated_tokens <= self.max_tokens_per_day

    def consume(self, tokens: int) -> None:
        """记录消耗的 token 数。"""
        self._reset_if_new_day()
        self._used_tokens += tokens

    @property
    def remaining(self) -> int:
        self._reset_if_new_day()
        return max(0, self.max_tokens_per_day - self._used_tokens)

    @property
    def used(self) -> int:
        self._reset_if_new_day()
        return self._used_tokens

    @property
    def is_exhausted(self) -> bool:
        self._reset_if_new_day()
        return self._used_tokens >= self.max_tokens_per_day


class ChatAnalysisService:
    """聊天记录分析服务。

    包装 ChatAnalysisStore 和 ChatImporter，提供业务操作接口。
    支持两种构造方式：
    - database: 使用项目主数据库（Database 实例）
    - db_path: 使用独立数据库文件（Path）
    - llm_service: 可选的 LLM 服务，用于话题提取、摘要、洞察生成
    """

    PROMPT_TOPIC_EXTRACT = """你是一个聊天记录分析专家。请分析以下群聊消息，提取出讨论的**主要话题**。

每条消息格式为：`[发送者]: 消息内容`

请返回 JSON 数组，每项包含：
- topic_name: 话题名称（简洁，10字以内）
- keywords: 关键词列表（3-5个）
- summary: 一句话摘要（30字以内）
- participant_count: 参与人数
- message_count: 涉及的消息数

只返回 JSON 数组，不要其他内容。"""

    PROMPT_INSIGHT_EXTRACT = """你是一个深度洞察分析专家。请分析以下聊天记录，提炼出有价值的**洞察**。

每条消息格式为：`[发送者]: 消息内容`

请返回 JSON 数组，每项包含：
- insight_type: 类型（"insight"关键洞见 / "guidance"行动指导 / "action"具体行动项）
- content: 洞察内容（50字以内）
- evidence_senders: 相关发言者列表
- confidence: 置信度 0.0-1.0

只返回 JSON 数组，不要其他内容。"""

    PROMPT_SESSION_SUMMARY = """你是一个聊天记录分析专家。请总结以下聊天记录的核心内容。

每条消息格式为：`[发送者]: 消息内容`

请返回 JSON 对象：
- summary: 整体摘要（100字以内）
- hot_topics: 热门话题列表（3-5个）
- active_members: 活跃成员列表（3-5个）
- overall_mood: 整体氛围（positive/neutral/negative）
- conversation_style: 交流风格描述

只返回 JSON 对象，不要其他内容。"""

    def __init__(self, database=None, db_path: Path | None = None, llm_service=None,
                 max_llm_tokens_per_day: int = 100_000):
        self.database = database
        self.db_path = db_path
        self._llm_service = llm_service
        self._quota = LLMQuota(max_tokens_per_day=max_llm_tokens_per_day)
        self._store: ChatAnalysisStore | None = None
        self._importer: ChatImporter | None = None

    @property
    def store(self) -> ChatAnalysisStore:
        if self._store is None:
            if self.database is not None:
                self._store = ChatAnalysisStore(database=self.database)
            else:
                self._store = ChatAnalysisStore(db_path=self.db_path)
        return self._store

    @property
    def importer(self) -> ChatImporter:
        if self._importer is None:
            self._importer = ChatImporter(store=self.store)
        return self._importer

    # ── 会话管理 ──

    def list_sessions(
        self,
        offset: int = 0,
        limit: int = 50,
        chat_type: str | None = None,
    ) -> list[ChatSession]:
        return self.store.list_sessions(
            offset=offset,
            limit=limit,
            chat_type=chat_type,
        )

    def count_sessions(self, chat_type: str | None = None) -> int:
        return self.store.count_sessions(chat_type=chat_type)

    def get_session(self, session_id: int) -> ChatSession | None:
        return self.store.get_session(session_id)

    def delete_session(self, session_id: int) -> bool:
        return self.store.delete_session(session_id)

    # ── 消息管理 ──

    def get_messages(
        self,
        session_id: int,
        offset: int = 0,
        limit: int = 100,
        sender: str | None = None,
        message_type: str | None = None,
    ) -> list[ChatMessage]:
        return self.store.get_messages(
            session_id,
            offset=offset,
            limit=limit,
            sender=sender,
            message_type=message_type,
        )

    def count_messages(self, session_id: int) -> int:
        return self.store.count_messages(session_id)

    # ── FTS5 搜索 ──

    def search(
        self,
        query: str,
        session_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ChatSearchResponse:
        start = time.time()
        results, total = self.store.search_messages(
            query,
            session_id=session_id,
            limit=limit,
            offset=offset,
        )
        took_ms = (time.time() - start) * 1000
        return ChatSearchResponse(
            total=total,
            results=results,
            took_ms=round(took_ms, 2),
        )

    # ── 混合检索（FTS5 + 向量） ──

    def hybrid_search(
        self,
        query: str,
        query_vector: list[float] | None = None,
        session_id: int | None = None,
        limit: int = 30,
        fts_weight: float = 0.6,
        vector_weight: float = 0.4,
    ) -> ChatSearchResponse:
        """混合检索：FTS5 BM25 与向量相似度 RRF 融合。"""
        start = time.time()

        # FTS5 搜索
        fts_results, fts_total = self.store.search_messages(
            query, session_id=session_id, limit=limit * 2,
        )
        fts_map = {r.message_id: r for r in fts_results}

        # 向量搜索（仅当提供了 query_vector）
        vec_map: dict[int, float] = {}
        if query_vector:
            vec_results = self.store.search_similar_messages(
                query_vector, session_id=session_id, limit=limit * 2,
            )
            for r, sim in vec_results:
                vec_map[r.message_id] = sim

        # RRF 融合
        seen: set[int] = set()
        all_ids: set[int] = set(fts_map.keys()) | set(vec_map.keys())
        scored: list[tuple[float, ChatSearchResult]] = []

        for msg_id in all_ids:
            rank_fts = 0.0
            rank_vec = 0.0
            fts_item = fts_map.get(msg_id)
            if fts_item:
                rank_fts = fts_weight * fts_item.rank_score
            if msg_id in vec_map:
                rank_vec = vector_weight * vec_map[msg_id]

            combined = rank_fts + rank_vec
            if fts_item:
                scored.append((combined, fts_item))
            seen.add(msg_id)

        scored.sort(key=lambda x: -x[0])
        top = scored[:limit]

        results = [r for _, r in top]
        took_ms = (time.time() - start) * 1000
        total = max(fts_total, len(results))

        return ChatSearchResponse(
            total=total,
            results=results,
            took_ms=round(took_ms, 2),
        )

    # ── 重建 FTS 索引 ──

    def rebuild_fts_index(self) -> int:
        return self.store.rebuild_fts_index()

    # ── 分析片段管理 ──

    def get_analysis_chunks(
        self,
        session_title: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[ChatAnalysisChunk]:
        return self.store.get_analysis_chunks(
            session_title=session_title,
            offset=offset,
            limit=limit,
        )

    def count_analysis_chunks(self, session_title: str | None = None) -> int:
        return self.store.count_analysis_chunks(session_title=session_title)

    def get_analysis_distinct_sessions(self) -> list[tuple[str, int]]:
        return self.store.get_analysis_distinct_sessions()

    # ── 话题管理 ──

    def get_topics(self, session_id: int | None = None, limit: int = 50) -> list[ChatTopic]:
        return self.store.get_topics(session_id=session_id, limit=limit)

    def count_topics(self, session_id: int | None = None) -> int:
        return self.store.count_topics(session_id=session_id)

    # ── 洞见管理 ──

    def get_insights(self, session_id: int | None = None, insight_type: str | None = None,
                     limit: int = 50) -> list[ChatInsight]:
        return self.store.get_insights(
            session_id=session_id, insight_type=insight_type, limit=limit,
        )

    def count_insights(self, session_id: int | None = None) -> int:
        return self.store.count_insights(session_id=session_id)

    # ── 嵌入向量管理 ──

    def create_embedding(self, message_id: int, session_id: int, vector: list[float],
                         model: str = "sentence-transformers") -> ChatEmbedding:
        return self.store.create_embedding(message_id, session_id, vector, model=model)

    def get_embeddings(self, session_id: int | None = None, limit: int = 100) -> list[ChatEmbedding]:
        return self.store.get_embeddings(session_id=session_id, limit=limit)

    def search_similar(self, query_vector: list[float], session_id: int | None = None,
                        limit: int = 20) -> list[tuple[ChatSearchResult, float]]:
        return self.store.search_similar_messages(query_vector, session_id=session_id, limit=limit)

    def count_embeddings(self) -> int:
        return self.store.count_embeddings()

    @property
    def quota(self) -> LLMQuota:
        """获取 LLM 配额跟踪器。"""
        return self._quota

    # ── LLM 分析 ──

    def _get_llm(self):
        if not self._llm_service:
            return None
        if hasattr(self._llm_service, "complete"):
            return self._llm_service
        if hasattr(self._llm_service, "chat"):
            return self._llm_service
        return None

    def _estimate_tokens(self, text: str) -> int:
        """粗略估算 token 数（中文约 1.5 char/token，英文约 4 char/token）。"""
        chinese = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        ascii_chars = len(text) - chinese
        return int(chinese * 1.5 + ascii_chars / 4) + 50  # +50 系统 prompt 开销

    def _check_llm_quota(self, text: str) -> bool:
        """检查 LLM 配额，配额不足时记录警告。"""
        estimated = self._estimate_tokens(text)
        if self._quota.is_exhausted:
            logger.warning(
                "LLM 配额已用完（今日已用 %d/%d tokens），"
                "跳过调用。可通过 max_llm_tokens_per_day 调整，或明天再试。",
                self._quota.used, self._quota.max_tokens_per_day,
            )
            return False
        if not self._quota.check(estimated):
            logger.warning(
                "LLM 配额不足（剩余 %d，需约 %d），跳过调用。",
                self._quota.remaining, estimated,
            )
            return False
        return True

    def _call_llm(self, prompt: str, messages_text: str) -> str | None:
        llm = self._get_llm()
        if not llm:
            return None
        full_prompt = f"{prompt}\n\n---\n{messages_text}"
        if not self._check_llm_quota(full_prompt):
            return None
        estimated = self._estimate_tokens(full_prompt)
        try:
            if hasattr(llm, "complete"):
                resp = llm.complete(full_prompt)
                self._quota.consume(estimated)
                return resp if isinstance(resp, str) else getattr(resp, "text", str(resp))
            elif hasattr(llm, "chat"):
                resp = llm.chat([{"role": "user", "content": full_prompt}])
                self._quota.consume(estimated)
                return resp if isinstance(resp, str) else getattr(resp, "content", str(resp))
        except Exception as e:
            logger.warning("LLM 调用失败: %s", e)
        return None

    def _format_messages_for_llm(self, messages: list[ChatMessage], max_len: int = 8000) -> str:
        lines = []
        total = 0
        for m in messages:
            line = f"[{m.sender}]: {m.content[:200]}"
            total += len(line)
            if total > max_len:
                break
            lines.append(line)
        return "\n".join(lines)

    def extract_topics(self, session_id: int, max_messages: int = 500) -> list[ChatTopic]:
        """使用 LLM 从会话中提取话题。"""
        messages = self.store.get_messages(session_id, limit=max_messages)
        if not messages:
            return []

        text = self._format_messages_for_llm(messages)
        resp = self._call_llm(self.PROMPT_TOPIC_EXTRACT, text)
        if not resp:
            return []

        try:
            import json
            data = json.loads(resp)
            if not isinstance(data, list):
                return []
            topics = []
            for item in data:
                topic = self.store.create_topic(
                    session_id=session_id,
                    topic_name=item.get("topic_name", "未命名话题"),
                    keywords=item.get("keywords", []),
                    summary=item.get("summary", ""),
                    participant_count=item.get("participant_count", 0),
                    message_count=item.get("message_count", 0),
                )
                topics.append(topic)
            return topics
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("解析话题结果失败: %s", e)
            return []

    def generate_insights(self, session_id: int, max_messages: int = 500) -> list[ChatInsight]:
        """使用 LLM 从会话中生成洞察。"""
        messages = self.store.get_messages(session_id, limit=max_messages)
        if not messages:
            return []

        text = self._format_messages_for_llm(messages)
        resp = self._call_llm(self.PROMPT_INSIGHT_EXTRACT, text)
        if not resp:
            return []

        try:
            import json
            data = json.loads(resp)
            if not isinstance(data, list):
                return []
            insights = []
            for item in data:
                insight = self.store.create_insight(
                    session_id=session_id,
                    insight_type=item.get("insight_type", "insight"),
                    content=item.get("content", ""),
                    confidence=item.get("confidence", 0.5),
                )
                insights.append(insight)
            return insights
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("解析洞察结果失败: %s", e)
            return []

    def summarize_session(self, session_id: int, max_messages: int = 500) -> dict[str, Any] | None:
        """使用 LLM 生成会话摘要。"""
        messages = self.store.get_messages(session_id, limit=max_messages)
        if not messages:
            return None

        text = self._format_messages_for_llm(messages)
        resp = self._call_llm(self.PROMPT_SESSION_SUMMARY, text)
        if not resp:
            return None

        try:
            import json
            return json.loads(resp)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("解析摘要结果失败: %s", e)
            return None

    def analyze_session(self, session_id: int) -> dict[str, Any]:
        """对会话执行完整分析：话题提取 + 洞察生成 + 摘要。"""
        result = {}
        topics = self.extract_topics(session_id)
        result["topics"] = topics
        result["topic_count"] = len(topics)

        insights = self.generate_insights(session_id)
        result["insights"] = insights
        result["insight_count"] = len(insights)

        summary = self.summarize_session(session_id)
        result["summary"] = summary

        return result

    # ── 统计 ──

    def get_session_stats(self, session_id: int) -> dict[str, Any]:
        return self.store.get_session_stats(session_id)

    def get_global_stats(self) -> dict[str, Any]:
        return self.store.get_global_stats()

    def get_top_senders(self, session_id: int, limit: int = 20) -> list[dict[str, Any]]:
        return self.store.get_top_senders(session_id, limit=limit)

    # ── 导入 ──

    def import_from_sqlite(
        self,
        db_path: str,
        max_sessions: int = 0,
        max_messages: int = 0,
    ) -> ImportStats:
        return self.importer.import_from_sqlite(
            db_path,
            max_sessions=max_sessions,
            max_messages_per_session=max_messages,
        )

    def import_from_deepseek_analysis(
        self,
        analysis_dir: str | None = None,
    ) -> list[DeepseekAnalysisImportResult]:
        return self.importer.import_from_deepseek_analysis(analysis_dir)

    def import_from_existing_articles(self, db_path: str) -> ImportStats:
        return self.importer.import_from_existing_articles(db_path)

    def import_all(
        self,
        mindback_root: str | None = None,
        openbiliclaw_db: str | None = None,
        max_sessions: int = 0,
        max_messages: int = 0,
    ) -> dict[str, Any]:
        return self.importer.import_all(
            mindback_root=mindback_root,
            openbiliclaw_db=openbiliclaw_db,
            max_sessions=max_sessions,
            max_messages=max_messages,
        )

    # ── 标签 ──

    def list_tags(self, category: str | None = None) -> list[ChatTag]:
        return self.store.list_tags(category=category)

    # ── 工具方法 ──

    def get_session_senders(self, session_id: int) -> list[str]:
        rows = self.store.conn.execute(
            "SELECT DISTINCT sender FROM chat_messages WHERE session_id = ? AND sender != '' ORDER BY sender",
            (session_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def get_session_message_types(self, session_id: int) -> dict[str, int]:
        rows = self.store.conn.execute(
            "SELECT message_type, COUNT(*) FROM chat_messages WHERE session_id = ? GROUP BY message_type",
            (session_id,),
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def get_session_time_range(self, session_id: int) -> tuple[str | None, str | None]:
        row = self.store.conn.execute(
            "SELECT MIN(timestamp), MAX(timestamp) FROM chat_messages WHERE session_id = ? AND timestamp != ''",
            (session_id,),
        ).fetchone()
        return (row[0], row[1]) if row else (None, None)
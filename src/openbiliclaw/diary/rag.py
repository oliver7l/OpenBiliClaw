"""日记 RAG（检索增强生成）服务。

提供语义搜索、相似日记推荐、基于日记内容的问答对话。
复用项目已有的 EmbeddingService（L1+L2 缓存、多 provider 支持），
向量存储在 diary_embeddings 表中。

核心功能：
1. 语义搜索：用自然语言搜索日记，按语义相似度排序
2. 相似日记推荐：查看一篇日记时，推荐相关的历史日记
3. RAG 问答：基于日记内容回答问题，引用具体日记作为证据
4. 批量生成 embedding：为所有日记生成向量
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..llm.embedding import EmbeddingService, cosine_similarity
from .store import DiaryStore

if TYPE_CHECKING:
    from ..llm.service import LLMService
    from ..storage.database import Database
    from .models import DiaryEntry

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """语义搜索结果。"""

    entry: DiaryEntry
    score: float  # 相似度分数 0-1
    highlight: str = ""  # 匹配片段高亮


@dataclass
class RAGAnswer:
    """RAG 问答结果。"""

    answer: str  # AI 生成的回答
    sources: list[dict]  # 引用的日记来源 [{id, date, title, snippet, score}]
    related_questions: list[str]  # 推荐的相关问题


# RAG 问答的系统 Prompt
_RAG_SYSTEM_PROMPT = (
    "你是一位专业的个人日记分析助手。用户会问关于他/她日记的问题，"
    "你需要基于提供的日记内容来回答。\n\n"
    "回答规则：\n"
    "1. **必须基于提供的日记内容回答**，不要编造日记中没有的信息\n"
    "2. 如果日记内容不足以回答问题，诚实地说\"根据现有日记，无法确定...\"\n"
    "3. 引用日记时，用 [日期] 格式标注来源，例如 [2024-03-15]\n"
    "4. 回答要客观、有洞察力，不要过度解读\n"
    "5. 如果涉及情绪分析，要基于日记中的具体描述，不要凭空猜测\n"
    "6. 回答长度适中，重点突出，不要冗长\n\n"
    "用户的问题：{question}\n\n"
    "相关日记内容：\n{context}\n\n"
    "请基于以上日记内容回答用户的问题。"
)


# 生成相关问题的 Prompt
_RELATED_QUESTIONS_PROMPT = """基于用户的问题和回答，生成 3 个用户可能感兴趣的相关问题。
要求：
1. 问题要具体，和日记内容相关
2. 不要重复用户已经问过的问题
3. 用中文，简洁明了

用户问题：{question}
回答摘要：{answer_summary}

请输出 3 个相关问题，每行一个，不要编号。"""


class DiaryRAGService:
    """日记 RAG 服务。

    提供语义搜索、相似日记推荐、RAG 问答等功能。
    """

    def __init__(
        self,
        store: DiaryStore | None = None,
        database: Database | None = None,
        db_path: str | None = None,
        embedding_service: EmbeddingService | None = None,
        llm_service: LLMService | None = None,
    ) -> None:
        self.store = store or DiaryStore(database=database, db_path=db_path)
        self.store.initialize()
        self._embedding_service = embedding_service
        self._llm_service = llm_service

    @property
    def embedding_service(self) -> EmbeddingService | None:
        return self._embedding_service

    def set_embedding_service(self, service: EmbeddingService) -> None:
        """设置 Embedding 服务。"""
        self._embedding_service = service

    @property
    def llm_service(self) -> LLMService | None:
        return self._llm_service

    def set_llm_service(self, service: LLMService) -> None:
        """设置 LLM 服务。"""
        self._llm_service = service

    # ── 批量生成 Embedding ─────────────────────────────────────

    async def batch_generate_embeddings(
        self,
        limit: int = 100,
        batch_size: int = 10,
    ) -> dict:
        """批量为尚未生成向量的日记生成 embedding。

        Args:
            limit: 最多处理多少篇日记
            batch_size: 每批并发处理的数量

        Returns:
            统计信息 {total, success, failed, skipped}
        """
        if self._embedding_service is None:
            raise RuntimeError("EmbeddingService 未设置，无法生成向量")

        entries = self.store.get_unembedded_entries(limit=limit)
        if not entries:
            return {
                "total": 0,
                "success": 0,
                "failed": 0,
                "skipped": 0,
                "message": "所有日记都已有向量",
            }

        logger.info("开始为 %d 篇日记生成 embedding", len(entries))
        success = 0
        failed = 0

        # 分批处理，控制并发
        for i in range(0, len(entries), batch_size):
            batch = entries[i : i + batch_size]
            tasks = [self._generate_single_embedding(entry) for entry in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for j, result in enumerate(results):
                if isinstance(result, Exception):
                    failed += 1
                    logger.warning("生成 embedding 失败 entry_id=%d: %s", batch[j].id, result)
                elif result:
                    success += 1

            logger.info(
                "已处理 %d/%d，成功 %d，失败 %d", i + len(batch), len(entries), success, failed
            )

        return {
            "total": len(entries),
            "success": success,
            "failed": failed,
            "skipped": 0,
        }

    async def _generate_single_embedding(self, entry: DiaryEntry) -> bool:
        """为单篇日记生成 embedding。

        为了提高语义搜索的准确性，embedding 的文本包含标题、日期和内容的前 1000 字。
        """
        if self._embedding_service is None:
            return False

        # 构造 embedding 文本：标题 + 日期 + 内容前 1000 字
        text_parts = []
        if entry.title:
            text_parts.append(f"标题：{entry.title}")
        text_parts.append(f"日期：{entry.entry_date}")
        content = entry.content[:1000] if len(entry.content) > 1000 else entry.content
        text_parts.append(f"内容：{content}")
        text = "\n".join(text_parts)

        vector = await self._embedding_service.embed(text)
        if not vector:
            return False

        self.store.upsert_embedding(entry.id, vector, model=self._embedding_service._model)
        return True

    # ── 语义搜索 ───────────────────────────────────────────────

    async def semantic_search(
        self,
        query: str,
        top_k: int = 10,
        min_score: float = 0.3,
        start_date: str | None = None,
        end_date: str | None = None,
        source: str | None = None,
    ) -> list[SearchResult]:
        """语义搜索日记。

        Args:
            query: 搜索查询（自然语言）
            top_k: 返回最多多少条结果
            min_score: 最低相似度阈值
            start_date: 起始日期过滤（YYYY-MM-DD）
            end_date: 结束日期过滤
            source: 来源过滤

        Returns:
            按相似度降序排列的搜索结果列表
        """
        if self._embedding_service is None:
            raise RuntimeError("EmbeddingService 未设置，无法进行语义搜索")

        # 1. 生成查询的 embedding
        query_vector = await self._embedding_service.embed(query)
        if not query_vector:
            return []

        # 2. 获取所有日记的 embedding
        all_embeddings = self.store.get_all_embeddings()
        if not all_embeddings:
            return []

        # 3. 计算相似度
        scored_entries: list[tuple[int, float]] = []
        for entry_id, vector in all_embeddings:
            if len(vector) != len(query_vector):
                continue
            score = cosine_similarity(query_vector, vector)
            if score >= min_score:
                scored_entries.append((entry_id, score))

        # 4. 按相似度降序排序
        scored_entries.sort(key=lambda x: x[1], reverse=True)

        # 5. 获取日记详情并应用过滤
        results = []
        for entry_id, score in scored_entries[: top_k * 2]:  # 多取一些，过滤后可能不够
            try:
                entry = self.store.get_entry(entry_id)
            except Exception:
                continue

            # 应用日期过滤
            if start_date and entry.entry_date < start_date:
                continue
            if end_date and entry.entry_date > end_date:
                continue
            # 应用来源过滤
            if source and entry.source != source:
                continue

            # 生成高亮片段（取内容中最相关的前 200 字）
            highlight = self._extract_highlight(entry.content, query)

            results.append(SearchResult(entry=entry, score=score, highlight=highlight))
            if len(results) >= top_k:
                break

        return results

    def _extract_highlight(self, content: str, query: str, max_len: int = 200) -> str:
        """从日记内容中提取与查询最相关的片段作为高亮。

        简单实现：取内容的前 200 字，后续可以优化为基于关键词定位。
        """
        if not content:
            return ""
        # 简单策略：取前 200 字
        if len(content) <= max_len:
            return content
        return content[:max_len] + "..."

    # ── 相似日记推荐 ────────────────────────────────────────────

    def find_similar_entries(
        self,
        entry_id: int,
        top_k: int = 5,
        min_score: float = 0.5,
    ) -> list[SearchResult]:
        """查找与指定日记相似的历史日记。

        Args:
            entry_id: 目标日记 ID
            top_k: 返回最多多少条
            min_score: 最低相似度阈值

        Returns:
            按相似度降序排列的相似日记列表
        """
        # 1. 获取目标日记的向量
        target_vector = self.store.get_embedding(entry_id)
        if not target_vector:
            return []

        # 2. 获取所有其他日记的向量
        all_embeddings = self.store.get_all_embeddings()

        # 3. 计算相似度
        scored_entries: list[tuple[int, float]] = []
        for eid, vector in all_embeddings:
            if eid == entry_id:
                continue
            if len(vector) != len(target_vector):
                continue
            score = cosine_similarity(target_vector, vector)
            if score >= min_score:
                scored_entries.append((eid, score))

        # 4. 排序并返回
        scored_entries.sort(key=lambda x: x[1], reverse=True)

        results = []
        for eid, score in scored_entries[:top_k]:
            try:
                entry = self.store.get_entry(eid)
                highlight = self._extract_highlight(
                    entry.content, entry.title or entry.content[:50]
                )
                results.append(SearchResult(entry=entry, score=score, highlight=highlight))
            except Exception:
                continue

        return results

    # ── RAG 问答 ────────────────────────────────────────────────

    async def ask_question(
        self,
        question: str,
        top_k: int = 8,
    ) -> RAGAnswer:
        """基于日记内容回答问题。

        Args:
            question: 用户问题
            top_k: 检索多少篇相关日记作为上下文

        Returns:
            RAGAnswer 包含回答、引用来源和相关问题
        """
        if self._llm_service is None:
            raise RuntimeError("LLMService 未设置，无法进行问答")

        # 1. 语义搜索相关日记
        search_results = await self.semantic_search(question, top_k=top_k, min_score=0.25)
        if not search_results:
            return RAGAnswer(
                answer="根据现有日记，没有找到与这个问题相关的内容。你可以换个问法试试，或者先写更多日记。",
                sources=[],
                related_questions=[],
            )

        # 2. 构造上下文
        context_parts = []
        sources = []
        for i, result in enumerate(search_results, 1):
            entry = result.entry
            snippet = entry.content[:500] if len(entry.content) > 500 else entry.content
            context_parts.append(
                f"【日记 {i}】\n"
                f"日期：{entry.entry_date}\n"
                f"标题：{entry.title or '无标题'}\n"
                f"相似度：{result.score:.2f}\n"
                f"内容：{snippet}\n"
            )
            sources.append(
                {
                    "id": entry.id,
                    "date": entry.entry_date,
                    "title": entry.title or "",
                    "snippet": snippet[:200] + ("..." if len(snippet) > 200 else ""),
                    "score": round(result.score, 4),
                }
            )

        context = "\n---\n".join(context_parts)

        # 3. 调用 LLM 生成回答
        prompt = _RAG_SYSTEM_PROMPT.format(question=question, context=context)
        try:
            answer = await self._llm_service.complete(prompt)
        except Exception as e:
            logger.error("RAG 问答生成失败: %s", e)
            answer = f"回答生成失败：{str(e)}"

        # 4. 生成相关问题
        related_questions = await self._generate_related_questions(question, answer[:300])

        return RAGAnswer(
            answer=answer,
            sources=sources,
            related_questions=related_questions,
        )

    async def _generate_related_questions(self, question: str, answer_summary: str) -> list[str]:
        """生成相关问题推荐。"""
        if self._llm_service is None:
            return []

        prompt = _RELATED_QUESTIONS_PROMPT.format(
            question=question,
            answer_summary=answer_summary,
        )
        try:
            result = await self._llm_service.complete(prompt)
            # 解析每行一个问题
            questions = [
                line.strip().lstrip("0123456789.、- ")
                for line in result.strip().split("\n")
                if line.strip()
            ]
            return questions[:3]
        except Exception as e:
            logger.warning("生成相关问题失败: %s", e)
            return []

    # ── 统计信息 ────────────────────────────────────────────────

    def get_embedding_stats(self) -> dict:
        """获取向量生成统计信息。"""
        total_entries = self.store.count_entries()
        embedded_count = self.store.count_embeddings()
        return {
            "total_entries": total_entries,
            "embedded_count": embedded_count,
            "unembedded_count": total_entries - embedded_count,
            "coverage": round(embedded_count / total_entries * 100, 1) if total_entries > 0 else 0,
        }

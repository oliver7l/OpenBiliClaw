"""日记 RAG（检索增强生成）服务。

提供语义搜索、相似日记推荐、基于日记内容的问答对话。
复用项目已有的 EmbeddingService（L1+L2 缓存、多 provider 支持），
支持父子文档分块（长日记拆成多个 chunk 分别生成 embedding）、
混合检索（语义搜索 + FTS5 全文检索）、时间衰减排序。

核心功能：
1. 语义搜索：用自然语言搜索日记，按语义相似度排序
2. 混合搜索：语义 + FTS5 全文检索融合排序
3. 相似日记推荐：查看一篇日记时，推荐相关的历史日记
4. RAG 问答：基于日记内容回答问题，引用具体日记作为证据
5. 批量生成 embedding：为所有日记生成向量
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from ..llm.embedding import EmbeddingService, cosine_similarity
from .store import DiaryStore

if TYPE_CHECKING:
    from ..llm.service import LLMService
    from ..storage.database import Database
    from .models import DiaryEntry

logger = logging.getLogger(__name__)

# 分块参数
CHUNK_MAX_SIZE = 500  # 每个 chunk 最大字符数
CHUNK_OVERLAP = 50  # chunk 间重叠字符数
CHUNK_THRESHOLD = 500  # 超过此长度的日记才分块

# 时间衰减参数
TIME_DECAY_RATE = 0.05  # 衰减速率
TIME_DECAY_DAYS_FULL = 7  # 最近 N 天无衰减


@dataclass
class SearchResult:
    """语义搜索结果。"""

    entry: DiaryEntry
    score: float  # 相似度分数 0-1
    highlight: str = ""  # 匹配片段高亮
    chunk_index: int = -1  # 命中的 chunk 索引（-1 表示整篇）


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
    '2. 如果日记内容不足以回答问题，诚实地说"根据现有日记，无法确定..."\n'
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

    # ── 父子文档分块 ─────────────────────────────────────────

    def _chunk_content(self, content: str, title: str = "") -> list[dict]:
        """将长日记拆分为语义 chunk。

        策略：
        - 若内容 <= CHUNK_THRESHOLD，返回一个 chunk
        - 否则按段落拆分，合并成 ~CHUNK_MAX_SIZE 的 chunk，带 CHUNK_OVERLAP 重叠

        Returns:
            [{"index": 0, "text": "str", "start": 0, "end": 100}, ...]

        """
        if not content:
            return [{"index": 0, "text": content, "start": 0, "end": 0}]

        if len(content) <= CHUNK_THRESHOLD:
            return [{"index": 0, "text": content, "start": 0, "end": len(content)}]

        # 按段落分割（双换行）
        paragraphs = re.split(r"\n\s*\n", content)
        paragraphs = [p.strip() for p in paragraphs if p.strip()]

        if not paragraphs:
            # 没有自然段落，按句子分割
            paragraphs = re.split(r"([。！？\n])", content)
            # 重组句子
            sentences = []
            buffer = ""
            for p in paragraphs:
                buffer += p
                if p in ("。", "！", "？", "\n") or len(buffer) > 100 and buffer.strip():
                    sentences.append(buffer.strip())
                    buffer = ""
            if buffer.strip():
                sentences.append(buffer.strip())
            paragraphs = sentences or [content]

        chunks = []
        current_chunk = ""
        chunk_index = 0
        start_pos = 0

        for para in paragraphs:
            if not current_chunk:
                current_chunk = para
                continue

            if len(current_chunk) + len(para) <= CHUNK_MAX_SIZE:
                current_chunk += "\n\n" + para
            else:
                chunks.append(
                    {
                        "index": chunk_index,
                        "text": current_chunk,
                        "start": start_pos,
                        "end": start_pos + len(current_chunk),
                    }
                )
                # 保留部分重叠
                if len(current_chunk) > CHUNK_OVERLAP * 2:
                    overlap = current_chunk[-CHUNK_OVERLAP:]
                    current_chunk = overlap + "\n\n" + para
                    start_pos = start_pos + len(current_chunk) - CHUNK_OVERLAP - len(para) - 2
                else:
                    current_chunk = para
                    start_pos = start_pos + len(current_chunk)
                chunk_index += 1

        if current_chunk:
            chunks.append(
                {
                    "index": chunk_index,
                    "text": current_chunk,
                    "start": start_pos,
                    "end": start_pos + len(current_chunk),
                }
            )

        return chunks

    def _build_chunk_text(self, title: str, entry_date: str, chunk_text: str) -> str:
        """构建用于 embedding 的 chunk 文本。"""
        parts = []
        if title:
            parts.append(f"标题：{title}")
        parts.append(f"日期：{entry_date}")
        parts.append(f"内容：{chunk_text}")
        return "\n".join(parts)

    # ── 高亮片段按关键词定位 ────────────────────────────────

    _STOPWORDS = frozenset(
        "的了在是把我有和就不人都一个上也很到说要去会着没有看好"
        "自己这她他它那你们我你她他她们我们他们这个那个什么怎么"
        "哪里为什么因为所以但是然后而且虽然如果还是只是不过"
        "已经可以应该可能能够需要觉得认为知道"
    )

    def _extract_keywords(self, query: str) -> list[str]:
        """从查询中提取关键词（过滤停用词）。"""
        # 对中文：按字符提取，过滤停用词
        chars = []
        for ch in query.strip():
            if ch.strip() and ch not in self._STOPWORDS and not ch.isascii():
                chars.append(ch)
        # 对英文：提取单词
        words = re.findall(r"[a-zA-Z]{2,}", query)
        return chars + words

    def _extract_highlight(self, content: str, query: str, max_len: int = 200) -> str:
        """从日记内容中提取与查询最相关的片段作为高亮。

        策略：提取查询中的关键词，在内容中定位出现最密集的区域，
        返回该区域附近 ~max_len 字的窗口。
        """
        if not content:
            return ""

        if len(content) <= max_len:
            return content

        keywords = self._extract_keywords(query)
        if not keywords:
            return content[:max_len] + "..."

        # 为每个字符位置计算关键词密度
        # 简化版：找第一个匹配关键词的位置
        best_pos = 0
        best_density = 0

        for keyword in keywords:
            pos = content.find(keyword)
            if pos < 0:
                continue
            # 计算以该位置为中心的窗口内关键词密度
            window_start = max(0, pos - max_len // 2)
            window_end = min(len(content), window_start + max_len)
            window = content[window_start:window_end]
            count = sum(window.count(kw) for kw in keywords)
            density = count / (len(window) or 1)
            if density > best_density:
                best_density = density
                best_pos = pos

        if best_density == 0:
            # 没有匹配到任何关键词，取前 200 字
            return content[:max_len] + "..."

        # 以 best_pos 为中心取窗口
        half = max_len // 2
        start = max(0, best_pos - half)
        end = min(len(content), start + max_len)
        # 如果 end 不够，回退 start
        if end - start < max_len:
            start = max(0, end - max_len)

        result = content[start:end]
        if start > 0:
            result = "..." + result
        if end < len(content):
            result = result + "..."

        return result

    # ── 时间衰减 ─────────────────────────────────────────────

    def _time_decay_factor(self, entry_date: str) -> float:
        """计算时间衰减因子。

        最近 TIME_DECAY_DAYS_FULL 天：权重 1.0
        之后按指数衰减，最低 0.5
        """
        try:
            days_ago = (datetime.now() - datetime.strptime(entry_date, "%Y-%m-%d")).days
        except (ValueError, TypeError):
            return 1.0

        if days_ago <= TIME_DECAY_DAYS_FULL:
            return 1.0
        return max(0.5, math.exp(-TIME_DECAY_RATE * (days_ago - TIME_DECAY_DAYS_FULL)))

    # ── 批量生成 Embedding ─────────────────────────────────────

    async def batch_generate_embeddings(
        self,
        limit: int = 100,
        batch_size: int = 10,
        use_chunks: bool = True,
    ) -> dict:
        """批量为尚未生成向量的日记生成 embedding（使用分块策略）。

        Args:
            limit: 最多处理多少篇日记
            batch_size: 每批并发处理的数量
            use_chunks: 是否使用分块策略

        Returns:
            统计信息 {total, success, failed, skipped, total_chunks}

        """
        if self._embedding_service is None:
            raise RuntimeError("EmbeddingService 未设置，无法生成向量")

        if use_chunks:
            entries = self.store.get_unembedded_chunks(limit=limit)
            if not entries:
                # 全量更新已有 embedding 为 chunk 模式
                entries = [
                    (self.store.get_entry(eid), 0)
                    for eid, _ in self.store.get_all_embeddings()[:limit]
                ]

            logger.info("开始为 %d 篇日记生成 chunk embedding", len(entries))
            success = 0
            failed = 0
            total_chunks = 0

            for i in range(0, len(entries), batch_size):
                batch = entries[i : i + batch_size]
                tasks = [
                    self._generate_chunk_embeddings(entry, entry_title)
                    for entry, entry_title in batch
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for j, result in enumerate(results):
                    if isinstance(result, Exception):
                        failed += 1
                        logger.warning(
                            "生成 chunk embedding 失败 entry_id=%d: %s", batch[j][0].id, result
                        )
                    elif result:
                        success += 1
                        total_chunks += result

                logger.info(
                    "已处理 %d/%d，成功 %d，失败 %d，chunk 数 %d",
                    i + len(batch),
                    len(entries),
                    success,
                    failed,
                    total_chunks,
                )

            return {
                "total": len(entries),
                "success": success,
                "failed": failed,
                "skipped": 0,
                "total_chunks": total_chunks,
            }
        else:
            # 旧模式：全篇 embedding（兼容）
            entries = self.store.get_unembedded_entries(limit=limit)
            if not entries:
                return {
                    "total": 0,
                    "success": 0,
                    "failed": 0,
                    "skipped": 0,
                    "message": "所有日记都已有向量",
                }

            logger.info("开始为 %d 篇日记生成全篇 embedding", len(entries))
            success = 0
            failed = 0

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

            return {"total": len(entries), "success": success, "failed": failed, "skipped": 0}

    async def _generate_single_embedding(self, entry: DiaryEntry) -> bool:
        """为单篇日记生成全篇 embedding（兼容旧模式）。"""
        if self._embedding_service is None:
            return False

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

    async def _generate_chunk_embeddings(self, entry: DiaryEntry, _unused: int = 0) -> int:
        """为单篇日记生成所有 chunk 的 embedding。

        Returns:
            生成的 chunk 数量

        """
        if self._embedding_service is None:
            return 0

        chunks = self._chunk_content(entry.content, entry.title or "")
        chunk_count = 0

        for chunk in chunks:
            text = self._build_chunk_text(entry.title or "", entry.entry_date, chunk["text"])
            vector = await self._embedding_service.embed(text)
            if not vector:
                continue
            self.store.upsert_chunk_embedding(
                entry.id,
                chunk["index"],
                chunk["text"],
                vector,
                model=self._embedding_service._model,
            )
            chunk_count += 1

        return chunk_count

    # ── 语义搜索（基于 chunk） ─────────────────────────────────

    async def semantic_search(
        self,
        query: str,
        top_k: int = 10,
        min_score: float = 0.3,
        start_date: str | None = None,
        end_date: str | None = None,
        source: str | None = None,
        use_time_decay: bool = True,
    ) -> list[SearchResult]:
        """语义搜索日记（基于 chunk embedding，支持时间衰减和过滤）。

        Args:
            query: 搜索查询（自然语言）
            top_k: 返回最多多少条结果
            min_score: 最低相似度阈值
            start_date: 起始日期过滤（YYYY-MM-DD）
            end_date: 结束日期过滤
            source: 来源过滤
            use_time_decay: 是否应用时间衰减

        Returns:
            按调整后分数降序排列的搜索结果列表

        """
        if self._embedding_service is None:
            raise RuntimeError("EmbeddingService 未设置，无法进行语义搜索")

        # 1. 生成查询的 embedding
        query_vector = await self._embedding_service.embed(query)
        if not query_vector:
            return []

        # 2. 获取所有 chunk embedding
        all_chunks = self.store.get_all_chunk_embeddings()
        if not all_chunks:
            # 降级到全篇 embedding
            return await self._semantic_search_legacy(
                query, query_vector, top_k, min_score, start_date, end_date, source, use_time_decay
            )

        # 3. 计算相似度
        scored: list[tuple[int, float, int, str]] = []  # (entry_id, score, chunk_index, chunk_text)
        for entry_id, chunk_index, vector, chunk_text in all_chunks:
            if len(vector) != len(query_vector):
                continue
            score = cosine_similarity(query_vector, vector)
            if score >= min_score:
                scored.append((entry_id, score, chunk_index, chunk_text))

        if not scored:
            return []

        # 4. 按 entry_id 分组，取每个 entry 的最佳 chunk 分数
        entry_best: dict[int, tuple[float, int, str]] = {}
        for entry_id, score, chunk_index, chunk_text in scored:
            if entry_id not in entry_best or score > entry_best[entry_id][0]:
                entry_best[entry_id] = (score, chunk_index, chunk_text)

        # 5. 排序并应用时间衰减
        scored_entries: list[tuple[int, float, int, str]] = [
            (entry_id, score, chunk_index, chunk_text)
            for entry_id, (score, chunk_index, chunk_text) in entry_best.items()
        ]
        scored_entries.sort(key=lambda x: x[1], reverse=True)

        # 6. 获取日记详情并应用过滤
        results = []
        seen_ids = set()
        for entry_id, score, chunk_index, chunk_text in scored_entries:
            if entry_id in seen_ids:
                continue
            try:
                entry = self.store.get_entry(entry_id)
            except Exception:
                continue

            # 应用日期过滤
            if start_date and entry.entry_date < start_date:
                continue
            if end_date and entry.entry_date > end_date:
                continue
            if source and entry.source != source:
                continue

            # 应用时间衰减
            adjusted_score = score
            if use_time_decay:
                decay = self._time_decay_factor(entry.entry_date)
                adjusted_score = score * decay

            # 生成高亮（优先用 chunk 文本）
            highlight = chunk_text if chunk_text else self._extract_highlight(entry.content, query)

            results.append(
                SearchResult(
                    entry=entry,
                    score=adjusted_score,
                    highlight=highlight,
                    chunk_index=chunk_index,
                )
            )
            seen_ids.add(entry_id)
            if len(results) >= top_k:
                break

        # 按调整后分数重排
        results.sort(key=lambda x: x.score, reverse=True)
        return results

    async def _semantic_search_legacy(
        self,
        query: str,
        query_vector: list[float],
        top_k: int,
        min_score: float,
        start_date: str | None,
        end_date: str | None,
        source: str | None,
        use_time_decay: bool,
    ) -> list[SearchResult]:
        """降级到全篇 embedding 搜索（无 chunk 时的回退）。"""
        all_embeddings = self.store.get_all_embeddings()
        if not all_embeddings:
            return []

        scored_entries: list[tuple[int, float]] = []
        for entry_id, vector in all_embeddings:
            if len(vector) != len(query_vector):
                continue
            score = cosine_similarity(query_vector, vector)
            if score >= min_score:
                scored_entries.append((entry_id, score))

        scored_entries.sort(key=lambda x: x[1], reverse=True)

        results = []
        for entry_id, score in scored_entries[: top_k * 2]:
            try:
                entry = self.store.get_entry(entry_id)
            except Exception:
                continue

            if start_date and entry.entry_date < start_date:
                continue
            if end_date and entry.entry_date > end_date:
                continue
            if source and entry.source != source:
                continue

            if use_time_decay:
                score = score * self._time_decay_factor(entry.entry_date)

            highlight = self._extract_highlight(entry.content, query)
            results.append(SearchResult(entry=entry, score=score, highlight=highlight))
            if len(results) >= top_k:
                break

        results.sort(key=lambda x: x.score, reverse=True)
        return results

    # ── 混合搜索 ─────────────────────────────────────────────

    async def hybrid_search(
        self,
        query: str,
        top_k: int = 10,
        min_score: float = 0.25,
        semantic_weight: float = 0.7,
        fts5_weight: float = 0.3,
        start_date: str | None = None,
        end_date: str | None = None,
        use_time_decay: bool = True,
    ) -> list[SearchResult]:
        """混合搜索：语义搜索 + FTS5 全文检索，按权重融合排序。

        Args:
            query: 搜索查询
            top_k: 返回最多多少条结果
            min_score: 最低融合分数阈值
            semantic_weight: 语义搜索权重（0-1）
            fts5_weight: FTS5 权重（0-1）
            start_date: 起始日期过滤
            end_date: 结束日期过滤
            use_time_decay: 是否应用时间衰减

        Returns:
            按融合分数降序排列的搜索结果

        """
        if self._embedding_service is None:
            raise RuntimeError("EmbeddingService 未设置，无法进行混合搜索")

        # 1. 语义搜索
        semantic_results = await self.semantic_search(
            query,
            top_k=top_k * 2,
            min_score=min_score,
            start_date=start_date,
            end_date=end_date,
            use_time_decay=use_time_decay,
        )

        # 2. FTS5 全文检索
        fts5_results = self.store.fts5_search(query, limit=top_k * 2)

        # 3. 融合分数
        # 语义分数：[0, 1]，按排名归一化
        semantic_scores: dict[int, float] = {}
        for i, r in enumerate(semantic_results):
            semantic_scores[r.entry.id] = r.score * (1 - i / (len(semantic_results) * 2))

        # FTS5 BM25 分数：越小越好，归一化为 [0, 1]
        fts5_scores: dict[int, float] = {}
        if fts5_results:
            max_bm25 = max(abs(s) for _, s in fts5_results) if fts5_results else 1
            for eid, bm25_score in fts5_results:
                # BM25 越接近 0 越好，归一化后越大越好
                normalized = 1.0 - min(abs(bm25_score) / max_bm25, 1.0)
                fts5_scores[eid] = normalized

        # 4. 融合
        all_entry_ids = set(semantic_scores.keys()) | set(fts5_scores.keys())
        merged: list[tuple[int, float]] = []
        for eid in all_entry_ids:
            s_score = semantic_scores.get(eid, 0.0)
            f_score = fts5_scores.get(eid, 0.0)
            combined = s_score * semantic_weight + f_score * fts5_weight
            if combined >= min_score:
                merged.append((eid, combined))

        merged.sort(key=lambda x: x[1], reverse=True)

        # 5. 获取日记详情
        results = []
        seen_ids = set()
        for entry_id, score in merged:
            if entry_id in seen_ids:
                continue
            try:
                entry = self.store.get_entry(entry_id)
            except Exception:
                continue

            if start_date and entry.entry_date < start_date:
                continue
            if end_date and entry.entry_date > end_date:
                continue

            if use_time_decay:
                score = score * self._time_decay_factor(entry.entry_date)

            highlight = self._extract_highlight(entry.content, query)
            results.append(SearchResult(entry=entry, score=score, highlight=highlight))
            seen_ids.add(entry_id)
            if len(results) >= top_k:
                break

        return results

    # ── 相似日记推荐 ────────────────────────────────────────────

    async def find_similar_entries(
        self,
        entry_id: int,
        top_k: int = 5,
        min_score: float = 0.5,
        use_time_decay: bool = True,
    ) -> list[SearchResult]:
        """查找与指定日记相似的历史日记。

        Args:
            entry_id: 目标日记 ID
            top_k: 返回最多多少条
            min_score: 最低相似度阈值
            use_time_decay: 是否应用时间衰减

        Returns:
            按相似度降序排列的相似日记列表

        """
        if self._embedding_service is None:
            raise RuntimeError("EmbeddingService 未设置")

        # 1. 获取目标日记的 chunk 向量
        target_chunks = self.store.get_entry_chunks(entry_id)
        if not target_chunks:
            return []

        # 2. 获取所有其他 chunk 向量
        all_chunks = self.store.get_all_chunk_embeddings()

        # 3. 计算每个 chunk 的相似度
        scored_entries: dict[int, float] = {}
        for eid, _cidx, vector, _chunk_text in all_chunks:
            if eid == entry_id:
                continue
            for _, target_vector, _ in target_chunks:
                if len(vector) != len(target_vector):
                    continue
                score = cosine_similarity(target_vector, vector)
                if score >= min_score and eid not in scored_entries or score > scored_entries[eid]:
                    scored_entries[eid] = score

        # 4. 排序
        scored_list = sorted(scored_entries.items(), key=lambda x: x[1], reverse=True)

        # 5. 获取日记详情
        results = []
        seen_ids = set()
        for eid, score in scored_list:
            if eid in seen_ids:
                continue
            try:
                entry = self.store.get_entry(eid)
            except Exception:
                continue

            if use_time_decay:
                score = score * self._time_decay_factor(entry.entry_date)

            # 用目标日记的标题作为查询生成高亮
            try:
                target = self.store.get_entry(entry_id)
                query = target.title or target.content[:50]
            except Exception:
                query = ""
            highlight = self._extract_highlight(entry.content, query)

            results.append(SearchResult(entry=entry, score=score, highlight=highlight))
            seen_ids.add(eid)
            if len(results) >= top_k:
                break

        results.sort(key=lambda x: x.score, reverse=True)
        return results

    # ── RAG 问答 ────────────────────────────────────────────────

    async def ask_question(
        self,
        question: str,
        top_k: int = 8,
        use_hybrid: bool = True,
    ) -> RAGAnswer:
        """基于日记内容回答问题。

        Args:
            question: 用户问题
            top_k: 检索多少篇相关日记作为上下文
            use_hybrid: 是否使用混合搜索（否则纯语义搜索）

        Returns:
            RAGAnswer 包含回答、引用来源和相关问题

        """
        if self._llm_service is None:
            raise RuntimeError("LLMService 未设置，无法进行问答")

        # 1. 搜索相关日记
        if use_hybrid:
            search_results = await self.hybrid_search(question, top_k=top_k, min_score=0.2)
        else:
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
            snippet = result.highlight or (
                entry.content[:500] if len(entry.content) > 500 else entry.content
            )
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
        chunk_count = self.store.count_chunks()
        return {
            "total_entries": total_entries,
            "embedded_count": embedded_count,
            "chunk_count": chunk_count,
            "entries_with_chunks": self.store.conn.execute(
                "SELECT COUNT(DISTINCT entry_id) FROM diary_embedding_chunks"
            ).fetchone()[0]
            if total_entries > 0
            else 0,
            "coverage": round(embedded_count / total_entries * 100, 1) if total_entries > 0 else 0,
        }

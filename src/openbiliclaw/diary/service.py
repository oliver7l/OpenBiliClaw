"""日记业务逻辑层。

提供日记的增删改查、LLM 驱动的内容分析、情绪识别、
主题提取、成长洞察等高级功能。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from ..llm.service import LLMService
from ..storage.database import Database
from .models import (
    DiaryAnalysis,
    DiaryEntry,
    DiaryEntryCreate,
    DiaryEntryUpdate,
    DiaryFragment,
    DiaryPerson,
    DiaryStats,
    DiaryTag,
    ExtractionResult,
    MoodLevel,
    TagType,
)
from .store import DiaryStore

logger = logging.getLogger(__name__)

# 延迟导入，避免循环依赖
_rag_service = None
_self_evolution_service = None


_DIARY_ANALYSIS_PROMPT = """你是一位专业的日记分析师和成长陪伴者。请分析以下日记内容，输出结构化的 JSON 结果。

日记日期：{entry_date}
日记标题：{title}
日记内容：
\"\"\"
{content}
\"\"\"

请严格输出以下 JSON 格式（不要输出任何其他文字）：
{{
  "summary": "用 2-3 句话概括这篇日记的核心内容",
  "key_points": ["关键要点1", "关键要点2", "关键要点3"],
  "emotions": {{"开心": 0.8, "平静": 0.5, "焦虑": 0.2}},
  "themes": ["主题标签1", "主题标签2"],
  "people_mentioned": ["提到的人物1", "提到的人物2"],
  "growth_insight": "从这篇日记中提炼的成长洞察或正向思考，50-100字",
  "mood_score": 0.5,
  "mood": "happy"
}}

情绪分值 mood_score 范围 -1.0 到 1.0，-1 为非常低落，1 为非常开心。
mood 可选值：very_happy, happy, neutral, sad, very_sad, angry, anxious, unknown
"""


_EXTRACTION_PROMPT = """你是一位专业的日记内容分析专家。请从以下日记中提取结构化的标签、人物、地点和事件信息。

日记日期：{entry_date}
日记标题：{title}
日记内容：
\"\"\"
{content}
\"\"\"

请严格输出以下 JSON 格式（不要输出任何其他文字，不要使用 markdown 代码块）：
{{
  "tags": [
    {{"name": "标签名称", "type": "emotion|topic|event|location|work|family|health|finance|other", "confidence": 0.9}}
  ],
  "persons": [
    {{"name": "人物名称", "relation": "与作者的关系（如：家人/朋友/同事/儿子/母亲/伴侣等）", "context": "人物在日记中出现的上下文片段，50字以内"}}
  ],
  "locations": ["地点1", "地点2"],
  "events": ["事件1", "事件2"]
}}

提取规则：
1. tags：提取 3-8 个最能代表这篇日记的标签。type 必须是枚举值之一。
   - emotion：情绪类标签（如：开心、焦虑、难过、平静、兴奋）
   - topic：主题类（如：工作、家庭、旅行、学习、健身、美食）
   - event：具体事件（如：生日、面试、搬家、聚会、出差）
   - location：地点类（如：深圳、北京、家里、公司、咖啡馆）
   - work/family/health/finance：更具体的分类
2. persons：只提取真实出现的人物，不要提取虚构人物。
   - 如果人物有称呼（如"妈妈"、"乐乐"），用称呼作为 name
   - relation 描述与作者的关系，不确定时填"朋友"或"其他"
   - context 截取人物出现的关键句子
3. locations：提取日记中提到的具体地点
4. events：提取日记中记录的具体事件
5. confidence：0-1 之间的浮数字，表示提取的置信度
6. 如果某类没有内容，返回空数组 []
"""


class DiaryService:
    """日记业务服务。

    封装存储层与 LLM 分析，提供高层业务接口。
    """

    def __init__(
        self,
        database: Database | None = None,
        db_path: str | None = None,
        llm_service: LLMService | None = None,
        embedding_service: object | None = None,
    ) -> None:
        self.store = DiaryStore(database=database, db_path=db_path)
        self.store.initialize()
        self._llm_service = llm_service
        self._embedding_service = embedding_service

    @property
    def llm_service(self) -> LLMService | None:
        return self._llm_service

    @property
    def embedding_service(self) -> object | None:
        return self._embedding_service

    def set_llm_service(self, llm_service: LLMService) -> None:
        """设置 LLM 服务，用于日记分析。"""
        self._llm_service = llm_service

    def set_embedding_service(self, service: object) -> None:
        """设置 Embedding 服务，用于自动生成向量。"""
        self._embedding_service = service

    # ── 基础 CRUD ───────────────────────────────────────────────

    def create_entry(
        self, data: DiaryEntryCreate, auto_embed: bool = True,
        auto_similar: bool = True,
    ) -> DiaryEntry:
        """创建日记。

        Args:
            data: 日记数据
            auto_embed: 创建后自动生成 chunk embedding 并同步 FTS5
            auto_similar: 创建后自动查找相似历史日记

        Returns:
            创建的日记
        """
        entry = self.store.create_entry(data)
        if auto_embed or auto_similar:
            asyncio.ensure_future(self._auto_process_new_entry(
                entry.id, do_embed=auto_embed, do_similar=auto_similar,
            ))
        return entry

    def get_entry(self, entry_id: int) -> DiaryEntry:
        """获取日记详情。"""
        return self.store.get_entry(entry_id)

    def update_entry(self, entry_id: int, data: DiaryEntryUpdate) -> DiaryEntry:
        """更新日记。"""
        return self.store.update_entry(entry_id, data)

    def delete_entry(self, entry_id: int) -> None:
        """删除日记。"""
        self.store.delete_entry(entry_id)

    def list_entries(
        self,
        limit: int = 50,
        offset: int = 0,
        start_date: str | None = None,
        end_date: str | None = None,
        mood: MoodLevel | None = None,
        source: str | None = None,
        tag: str | None = None,
        search: str | None = None,
        sort_by: str = "entry_date",
        sort_order: str = "DESC",
    ) -> tuple[list[DiaryEntry], int]:
        """列出日记，返回 (列表, 总数)。"""
        entries = self.store.list_entries(
            limit=limit,
            offset=offset,
            start_date=start_date,
            end_date=end_date,
            mood=mood,
            source=source,
            tag=tag,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        total = self.store.count_entries(
            start_date=start_date,
            end_date=end_date,
            mood=mood,
            source=source,
        )
        return entries, total

    def get_stats(self) -> DiaryStats:
        """获取日记统计。"""
        return self.store.get_stats()

    # ── 分析功能 ─────────────────────────────────────────────────

    async def analyze_entry(self, entry_id: int, force: bool = False) -> DiaryAnalysis | None:
        """分析单篇日记。

        Args:
            entry_id: 日记 ID
            force: 是否强制重新分析

        Returns:
            分析结果，若 LLM 不可用则返回 None
        """
        entry = self.store.get_entry(entry_id)
        if not force and entry.analysis_id is not None:
            existing = self.store.get_analysis_by_diary(entry_id)
            if existing is not None:
                return existing

        if self._llm_service is None:
            logger.warning("LLM service 未配置，跳过日记分析: id=%s", entry_id)
            return None

        try:
            resp = await self._llm_service.complete_structured_task(
                system_instruction=_DIARY_ANALYSIS_PROMPT,
                user_input=f"日期：{entry.entry_date}\n标题：{entry.title or '(无标题)'}\n\n{entry.content[:8000]}",
                temperature=0.3,
                max_tokens=4096,
                caller="diary.analyze_entry",
                reasoning_effort="none",
                inject_core_memory=False,
            )
            response = getattr(resp, "content", "")
            result = self._parse_analysis_response(response)
            if result is None:
                logger.error("日记分析结果解析失败: id=%s", entry_id)
                return None
            model_used = getattr(response, "model", "") or ""
            analysis = self.store.create_analysis(entry_id, result, model_used=model_used)
            logger.info("日记分析完成: id=%s, mood=%s", entry_id, result.get("mood"))
            return analysis
        except Exception as exc:
            logger.exception("日记分析失败: id=%s, error=%s", entry_id, exc)
            return None

    # ── 自动处理（新日记后触发） ─────────────────────────────────

    async def _auto_process_new_entry(
        self, entry_id: int, do_embed: bool = True, do_similar: bool = True,
    ) -> dict:
        """新日记创建后的自动处理：生成 embedding + 同步 FTS5 + 发现相似日记。

        Returns:
            {"embedding": bool, "fts5": bool, "similar_entries": list}
        """
        result: dict = {"embedding": False, "fts5": False, "similar_entries": []}
        try:
            entry = self.store.get_entry(entry_id)
        except Exception:
            return result

        # 1. 生成 chunk embedding
        if do_embed:
            try:
                from .rag import DiaryRAGService
                rag = DiaryRAGService(store=self.store)
                rag.set_embedding_service(self._embedding_service if hasattr(self, '_embedding_service') else None)
                if rag.embedding_service:
                    chunk_count = await rag._generate_chunk_embeddings(entry)
                    result["embedding"] = chunk_count > 0
            except Exception as e:
                logger.warning("自动生成 chunk embedding 失败: entry_id=%d, %s", entry_id, e)

        # 2. 同步 FTS5
        try:
            self.store.sync_fts5(entry_id)
            result["fts5"] = True
        except Exception as e:
            logger.warning("自动同步 FTS5 失败: entry_id=%d, %s", entry_id, e)

        # 3. 发现相似历史日记
        if do_similar:
            try:
                from .rag import DiaryRAGService, cosine_similarity
                rag = DiaryRAGService(store=self.store)
                # 用 chunk embedding 找相似
                target_chunks = self.store.get_entry_chunks(entry_id)
                if not target_chunks:
                    # 全篇 embedding 降级
                    target_vec = self.store.get_embedding(entry_id)
                    if target_vec:
                        all_embs = self.store.get_all_embeddings()
                        similar = []
                        for eid, vec in all_embs:
                            if eid == entry_id:
                                continue
                            if len(vec) != len(target_vec):
                                continue
                            score = cosine_similarity(target_vec, vec)
                            if score >= 0.5:
                                similar.append((eid, score))
                        similar.sort(key=lambda x: x[1], reverse=True)
                        result["similar_entries"] = [
                            {"id": eid, "score": round(score, 4)}
                            for eid, score in similar[:5]
                        ]
                else:
                    all_chunks = self.store.get_all_chunk_embeddings()
                    similar: dict[int, float] = {}
                    for _, tvec, _ in target_chunks:
                        for eid, _, vec, _ in all_chunks:
                            if eid == entry_id:
                                continue
                            if len(vec) != len(tvec):
                                continue
                            score = cosine_similarity(tvec, vec)
                            if score >= 0.5:
                                if eid not in similar or score > similar[eid]:
                                    similar[eid] = score
                    ranked = sorted(similar.items(), key=lambda x: x[1], reverse=True)[:5]
                    result["similar_entries"] = [
                        {"id": eid, "score": round(score, 4)} for eid, score in ranked
                    ]
            except Exception as e:
                logger.warning("自动发现相似日记失败: entry_id=%d, %s", entry_id, e)

        if result["similar_entries"]:
            logger.info(
                "新日记 #%d 自动处理完成：embedding=%s, fts5=%s, 相似日记=%d篇",
                entry_id, result["embedding"], result["fts5"], len(result["similar_entries"]),
            )
        return result

    async def find_similar_for_entry(
        self, entry_id: int, top_k: int = 5, min_score: float = 0.5,
    ) -> list[dict]:
        """查找与指定日记相似的历史日记（供 API 调用）。"""
        from .rag import DiaryRAGService, cosine_similarity
        rag = DiaryRAGService(store=self.store)

        target_chunks = self.store.get_entry_chunks(entry_id)
        if not target_chunks:
            return []

        all_chunks = self.store.get_all_chunk_embeddings()
        similar: dict[int, float] = {}
        for _, tvec, _ in target_chunks:
            for eid, _, vec, _ in all_chunks:
                if eid == entry_id:
                    continue
                if len(vec) != len(tvec):
                    continue
                score = cosine_similarity(tvec, vec)
                if score >= min_score:
                    if eid not in similar or score > similar[eid]:
                        similar[eid] = score

        ranked = sorted(similar.items(), key=lambda x: x[1], reverse=True)[:top_k]
        results = []
        for eid, score in ranked:
            try:
                entry = self.store.get_entry(eid)
                results.append({
                    "id": eid,
                    "entry_date": entry.entry_date,
                    "title": entry.title,
                    "mood": entry.mood.value,
                    "score": round(score, 4),
                    "word_count": entry.word_count,
                })
            except Exception:
                continue
        return results

    # ── 夜间 Consolidation ─────────────────────────────────────

    async def run_nightly_consolidation(
        self, target_date: str | None = None,
        do_profile: bool = True, do_drift: bool = True,
        do_tags: bool = True, do_embedding: bool = True,
    ) -> dict:
        """执行一次完整的夜间 consolidation。

        这是一个组合流程：
        1. 更新用户画像（SelfEvolutionService）
        2. 检测漂移事件
        3. 标签优化
        4. 为未生成 embedding 的日记补全 chunk 向量
        5. 同步 FTS5 索引

        Args:
            target_date: 目标日期，默认昨天
            do_profile: 是否更新用户画像
            do_drift: 是否检测漂移
            do_tags: 是否优化标签
            do_embedding: 是否补全 embedding

        Returns:
            执行结果摘要
        """
        if target_date is None:
            from datetime import timedelta
            target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        logger.info("开始夜间 consolidation，目标日期: %s", target_date)
        report: dict = {
            "target_date": target_date,
            "profile_updated": False,
            "drifts_detected": 0,
            "tags_optimized": False,
            "embeddings_generated": 0,
            "fts5_synced": False,
        }

        # 1. 用户画像 + 漂移检测
        if do_profile or do_drift:
            try:
                from .self_evolution import SelfEvolutionService
                evo = SelfEvolutionService(self.store)
                if do_profile:
                    evo.update_user_profile(target_date)
                    report["profile_updated"] = True
                if do_drift:
                    drifts = evo.detect_drifts(target_date)
                    report["drifts_detected"] = len(drifts)
            except Exception as e:
                logger.warning("夜间 consolidation 画像/漂移检测失败: %s", e)

        # 2. 标签优化
        if do_tags:
            try:
                from .self_evolution import SelfEvolutionService
                evo = SelfEvolutionService(self.store)
                evo.optimize_tags(target_date)
                report["tags_optimized"] = True
            except Exception as e:
                logger.warning("夜间 consolidation 标签优化失败: %s", e)

        # 3. 补全 embedding
        if do_embedding:
            try:
                from .rag import DiaryRAGService
                rag = DiaryRAGService(store=self.store)
                if rag.embedding_service or (hasattr(self, '_embedding_service') and self._embedding_service):
                    if not rag.embedding_service:
                        rag.set_embedding_service(self._embedding_service)
                    stats = await rag.batch_generate_embeddings(limit=50, use_chunks=True)
                    report["embeddings_generated"] = stats.get("success", 0)
            except Exception as e:
                logger.warning("夜间 consolidation 补全 embedding 失败: %s", e)

        # 4. 同步 FTS5
        try:
            self.store.sync_fts5()
            report["fts5_synced"] = True
        except Exception as e:
            logger.warning("夜间 consolidation 同步 FTS5 失败: %s", e)

        logger.info("夜间 consolidation 完成: %s", report)
        return report

    async def analyze_batch(
        self, entry_ids: list[int], concurrency: int = 3
    ) -> dict[int, DiaryAnalysis | None]:
        """批量分析日记。

        Args:
            entry_ids: 日记 ID 列表
            concurrency: 并发数

        Returns:
            {entry_id: analysis_or_none}
        """
        results: dict[int, DiaryAnalysis | None] = {}
        semaphore = asyncio.Semaphore(concurrency)

        async def _analyze_one(eid: int) -> None:
            async with semaphore:
                results[eid] = await self.analyze_entry(eid)

        tasks = [_analyze_one(eid) for eid in entry_ids]
        await asyncio.gather(*tasks, return_exceptions=True)
        return results

    async def analyze_unanalyzed(
        self, limit: int = 50, concurrency: int = 3
    ) -> dict[int, DiaryAnalysis | None]:
        """分析所有未分析的日记。"""
        entries = self.store.get_unanalyzed_entries(limit=limit)
        if not entries:
            return {}
        logger.info("开始批量分析 %d 篇未分析日记", len(entries))
        return await self.analyze_batch([e.id for e in entries], concurrency=concurrency)

    def get_analysis(self, entry_id: int) -> DiaryAnalysis | None:
        """获取日记的分析结果。"""
        return self.store.get_analysis_by_diary(entry_id)

    # ── 时间线与回顾 ─────────────────────────────────────────────

    def get_timeline(self, year: int | None = None, month: int | None = None) -> list[DiaryEntry]:
        """获取时间线视图。"""
        start_date = None
        end_date = None
        if year and month:
            start_date = f"{year:04d}-{month:02d}-01"
            if month == 12:
                end_date = f"{year + 1:04d}-01-01"
            else:
                end_date = f"{year:04d}-{month + 1:02d}-01"
        elif year:
            start_date = f"{year:04d}-01-01"
            end_date = f"{year + 1:04d}-01-01"
        return self.store.list_entries(
            limit=1000,
            start_date=start_date,
            end_date=end_date,
            sort_by="entry_date",
            sort_order="ASC",
        )

    def search_entries(self, query: str, limit: int = 50) -> list[DiaryEntry]:
        """全文搜索日记。"""
        return self.store.list_entries(search=query, limit=limit)

    # ── 内部方法 ─────────────────────────────────────────────────

    @staticmethod
    def _parse_analysis_response(response: object) -> dict | None:
        """解析 LLM 分析响应为字典。"""
        text = ""
        if hasattr(response, "content"):
            text = str(response.content)
        elif hasattr(response, "text"):
            text = str(response.text)
        elif isinstance(response, str):
            text = response
        elif isinstance(response, dict):
            return response

        if not text:
            return None

        # 尝试提取 JSON
        text = text.strip()
        if text.startswith("```"):
            # 移除代码块标记
            lines = text.split("\n")
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        import json

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 尝试找到第一个 { 和最后一个 }
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass
        return None

    # ─── 碎片（随手记）业务逻辑 ───

    def create_fragment(
        self,
        content: str,
        mood: MoodLevel = MoodLevel.UNKNOWN,
        fragment_date: str | None = None,
        source: str = "manual",
        fragment_type: str = "text",
        media_path: str = "",
        media_description: str = "",
        tags: list[str] | None = None,
    ) -> DiaryFragment:
        """创建一条碎片。

        Args:
            content: 碎片内容
            mood: 情绪标签
            fragment_date: 日期，默认今天
            source: 来源
            fragment_type: 碎片类型（text/image/voice/link）
            media_path: 媒体文件路径
            media_description: 媒体内容描述
            tags: 标签列表
        """
        return self.store.create_fragment(
            content=content,
            mood=mood,
            fragment_date=fragment_date,
            source=source,
            fragment_type=fragment_type,
            media_path=media_path,
            media_description=media_description,
            tags=tags,
        )

    def list_fragments(
        self,
        fragment_date: str | None = None,
        limit: int = 100,
        offset: int = 0,
        fragment_type: str | None = None,
    ) -> list[DiaryFragment]:
        """列出碎片。"""
        return self.store.list_fragments(fragment_date, limit, offset, fragment_type)

    def delete_fragment(self, fragment_id: int) -> bool:
        """删除碎片。"""
        return self.store.delete_fragment(fragment_id)

    async def auto_tag_fragment(self, fragment_id: int) -> DiaryFragment | None:
        """对单条碎片执行 AI 自动标签和情绪识别。

        如果没有 LLM 服务，使用基于关键词的规则提取降级方案。
        """
        fragment = self.store.get_fragment(fragment_id)
        if fragment is None:
            return None

        # 已有标签和情绪时跳过
        if fragment.tags and fragment.mood != MoodLevel.UNKNOWN:
            return fragment

        llm = self.llm_service
        if llm is None:
            # 无 LLM 时使用规则提取
            tags, mood = self._extract_tags_and_mood_by_rules(fragment.content)
        else:
            try:
                prompt = f"""请分析以下随手记碎片，提取标签和情绪。

碎片内容：{fragment.content}

请严格输出 JSON（不要输出其他文字）：
{{
  "tags": ["标签1", "标签2", "标签3"],
  "mood": "very_happy|happy|neutral|sad|very_sad|angry|anxious|unknown"
}}

标签要求：3-5 个，涵盖主题、情绪、人物、地点等。
mood 可选值：very_happy, happy, neutral, sad, very_sad, angry, anxious, unknown"""
                resp = await llm.complete_structured_task(
                    system_instruction="",
                    user_input=prompt,
                    temperature=0.3,
                    max_tokens=2048,
                    caller="diary.auto_tag_fragment",
                    reasoning_effort="none",
                    inject_core_memory=False,
                )
                response = getattr(resp, "content", "")
                parsed = self._parse_analysis_response(response)
                tags = parsed.get("tags", []) if parsed else []
                mood_str = parsed.get("mood", "unknown") if parsed else "unknown"
                try:
                    mood = MoodLevel(mood_str)
                except (ValueError, KeyError):
                    mood = MoodLevel.UNKNOWN
            except Exception:
                tags, mood = self._extract_tags_and_mood_by_rules(fragment.content)

        # 保存标签和情绪
        if tags:
            self.store.update_fragment_tags(fragment_id, tags)
        if mood != MoodLevel.UNKNOWN:
            self.store.update_fragment_mood(fragment_id, mood)

        return self.store.get_fragment(fragment_id)

    async def batch_auto_tag_fragments(self, limit: int = 50) -> dict:
        """批量对未标注的碎片执行自动标签和情绪识别。"""
        # 获取所有碎片，筛选未标注的
        all_fragments = self.store.list_fragments(limit=500)
        untagged = [
            f for f in all_fragments
            if not f.tags or f.mood == MoodLevel.UNKNOWN
        ][:limit]

        if not untagged:
            return {"total": 0, "success": 0, "failed": 0, "message": "所有碎片都已标注"}

        success = 0
        failed = 0
        for fragment in untagged:
            try:
                result = await self.auto_tag_fragment(fragment.id)
                if result:
                    success += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

        return {"total": len(untagged), "success": success, "failed": failed}

    def _extract_tags_and_mood_by_rules(self, content: str) -> tuple[list[str], MoodLevel]:
        """基于关键词规则的标签和情绪提取（无 LLM 时的降级方案）。"""
        tags = []
        content_lower = content.lower()

        # 情绪关键词
        happy_keywords = ["开心", "高兴", "快乐", "幸福", "满足", "惊喜", "棒", "好", "爱", "喜欢", "笑"]
        sad_keywords = ["难过", "伤心", "失落", "沮丧", "痛苦", "哭", "累", "疲惫", "无力", "绝望"]
        anxious_keywords = ["焦虑", "紧张", "担心", "害怕", "不安", "压力", "烦", "烦躁", "纠结"]
        angry_keywords = ["生气", "愤怒", "火", "气", "讨厌", "烦", "不爽", "吵架"]

        # 主题关键词
        work_keywords = ["工作", "上班", "加班", "会议", "项目", "同事", "老板", "公司", "代码", "bug", "需求"]
        family_keywords = ["妈妈", "爸爸", "老公", "老婆", "孩子", "儿子", "女儿", "家", "家人", "乐乐", "艳艳"]
        health_keywords = ["身体", "生病", "医院", "医生", "药", "睡", "失眠", "运动", "健身", "跑步"]
        travel_keywords = ["旅行", "旅游", "出去玩", "度假", "景点", "酒店", "飞机", "高铁", "开车"]
        food_keywords = ["吃", "美食", "饭", "菜", "火锅", "烧烤", "咖啡", "奶茶", "蛋糕"]

        # 检测情绪
        happy_count = sum(1 for k in happy_keywords if k in content_lower)
        sad_count = sum(1 for k in sad_keywords if k in content_lower)
        anxious_count = sum(1 for k in anxious_keywords if k in content_lower)
        angry_count = sum(1 for k in angry_keywords if k in content_lower)

        mood = MoodLevel.UNKNOWN
        if happy_count > sad_count and happy_count > anxious_count and happy_count > angry_count:
            mood = MoodLevel.HAPPY if happy_count < 3 else MoodLevel.VERY_HAPPY
        elif sad_count > happy_count and sad_count > anxious_count:
            mood = MoodLevel.SAD if sad_count < 3 else MoodLevel.VERY_SAD
        elif anxious_count > happy_count and anxious_count > sad_count:
            mood = MoodLevel.ANXIOUS
        elif angry_count > happy_count:
            mood = MoodLevel.ANGRY
        elif happy_count > 0:
            mood = MoodLevel.HAPPY

        # 检测主题标签
        if any(k in content_lower for k in work_keywords):
            tags.append("工作")
        if any(k in content_lower for k in family_keywords):
            tags.append("家庭")
        if any(k in content_lower for k in health_keywords):
            tags.append("健康")
        if any(k in content_lower for k in travel_keywords):
            tags.append("旅行")
        if any(k in content_lower for k in food_keywords):
            tags.append("美食")

        # 情绪标签
        if mood in (MoodLevel.HAPPY, MoodLevel.VERY_HAPPY):
            tags.append("开心")
        elif mood in (MoodLevel.SAD, MoodLevel.VERY_SAD):
            tags.append("难过")
        elif mood == MoodLevel.ANXIOUS:
            tags.append("焦虑")
        elif mood == MoodLevel.ANGRY:
            tags.append("生气")

        return tags[:5], mood

    async def generate_diary_from_fragments(self, fragment_date: str | None = None,
                                               auto_delete: bool = True) -> DiaryEntry | None:
        """从当天碎片 AI 聚合生成一篇完整日记（证据驱动版）。

        借鉴 Night-Journal 和 echolog 的设计：把白天的碎片整理成一篇连贯、私人的日记。
        证据驱动：每条结论都要有原始碎片支撑，事实先行，不堆空洞形容词。
        生成后默认删除已使用的碎片。

        Args:
            fragment_date: 碎片日期，默认今天
            auto_delete: 生成后是否删除已使用的碎片
        """
        if fragment_date is None:
            fragment_date = datetime.now().strftime("%Y-%m-%d")

        fragments = self.store.list_fragments(fragment_date, limit=200)
        if not fragments:
            return None

        # 构建碎片文本（包含时间、类型、媒体描述）
        fragments_parts = []
        for i, f in enumerate(fragments):
            time_str = f.created_at.strftime("%H:%M") if hasattr(f.created_at, "strftime") else ""
            type_label = {
                "text": "📝 文字",
                "image": "🖼️ 图片",
                "voice": "🎙️ 语音",
                "link": "🔗 链接",
            }.get(f.fragment_type, "📝 文字")

            part = f"【碎片 {i+1}】{time_str} {type_label}"
            if f.mood != MoodLevel.UNKNOWN:
                part += f"（情绪：{f.mood.value}）"
            part += f"\n{f.content}"
            if f.media_description:
                part += f"\n媒体描述：{f.media_description}"
            if f.tags:
                part += f"\n标签：{', '.join(f.tags)}"
            fragments_parts.append(part)

        fragments_text = "\n\n".join(fragments_parts)

        # 构建 AI prompt（证据驱动，参考 echolog 的设计）
        prompt = f"""你是一个安静的记录者，坐在用户这一天的记忆里，把零散的念头、情绪和画面整理成一篇属于他自己的日记。

今天的碎片记录（按时间顺序）：
{fragments_text}

请根据以上碎片，生成一篇连贯的日记。

【核心原则：证据驱动】
1. 事实先行：每一个描述都必须来自上面的碎片，不要编造没有的内容
2. 不堆空洞形容词：不要写"非常开心"、"特别难过"这种空洞的词，用具体的细节和画面来表达
3. 保留原始语气：用户怎么说的就怎么写，不要过度润色
4. 时间顺序：尽量按碎片的时间顺序组织，但可以把相关的碎片放在一起

【写作要求】
1. 用第一人称「我」写作，像用户本人在回望这一天
2. 感受碎片里的情绪变化，让每一句话都从他自己的视角自然流出
3. 不只做事实罗列，而是找出这一天真正碰到他的东西
4. 保留他原本的语气、混乱感和真实情绪，只做轻微的文字整理
5. 不虚构重大事件，不进行心理诊断，不说教，不写鸡汤
6. 信息少的时候就写短一点，不硬凑
7. 正文结尾可以留一句轻微的余味，但不要鸡汤

【输出格式】
请严格输出 JSON（不要包含任何其他文字，不要使用 markdown 代码块）：
{{
  "title": "日记标题（简短，不超过15字，从碎片中提炼）",
  "content": "完整日记正文（根据碎片数量调整长度，一般 200-800 字）",
  "tags": ["从碎片中提取的 3-5 个标签"],
  "mood": "当天整体情绪：very_happy|happy|neutral|sad|very_sad|angry|anxious|unknown"
}}"""

        # 调用 LLM
        llm = self.llm_service
        all_tags = []
        overall_mood = MoodLevel.UNKNOWN

        if llm is None:
            # 没有 LLM 时，直接把碎片拼接成日记
            content = "\n\n".join(f.content for f in fragments)
            title = f"{fragment_date} 日记"
            # 从碎片中收集标签
            for f in fragments:
                all_tags.extend(f.tags)
            all_tags = list(set(all_tags))[:5]
        else:
            try:
                resp = await llm.complete_structured_task(
                    system_instruction="",
                    user_input=prompt,
                    temperature=0.3,
                    max_tokens=4096,
                    caller="diary.compose_fragment",
                    reasoning_effort="none",
                    inject_core_memory=False,
                )
                response = getattr(resp, "content", "")
                parsed = self._parse_analysis_response(response)
                if parsed:
                    title = parsed.get("title", f"{fragment_date} 日记")
                    content = parsed.get("content", fragments_text)
                    all_tags = parsed.get("tags", [])
                    mood_str = parsed.get("mood", "unknown")
                    try:
                        overall_mood = MoodLevel(mood_str)
                    except (ValueError, KeyError):
                        overall_mood = MoodLevel.UNKNOWN
                else:
                    title = f"{fragment_date} 日记"
                    content = fragments_text
            except Exception:
                # LLM 失败时降级为直接拼接
                title = f"{fragment_date} 日记"
                content = fragments_text

        # 合并碎片中的标签和 AI 提取的标签
        for f in fragments:
            all_tags.extend(f.tags)
        all_tags = list(set(all_tags))
        if "随手记" not in all_tags:
            all_tags.append("随手记")
        if "AI聚合" not in all_tags:
            all_tags.append("AI聚合")
        all_tags = all_tags[:8]

        # 创建日记
        entry = self.create_entry(
            DiaryEntryCreate(
                entry_date=fragment_date,
                title=title,
                content=content,
                source="fragment",
                tags=all_tags,
                mood=overall_mood,
            )
        )

        # 删除已使用的碎片
        if auto_delete:
            for f in fragments:
                self.store.delete_fragment(f.id)

        return entry

    # ═══════════════════════════════════════════
    # AI 标签/人物提取
    # ═══════════════════════════════════════════

    async def extract_tags_and_persons(self, entry_id: int) -> ExtractionResult | None:
        """对单篇日记执行 AI 提取，保存标签和人物到数据库。

        Args:
            entry_id: 日记 ID

        Returns:
            ExtractionResult 提取结果，失败返回 None
        """
        entry = self.store.get_entry(entry_id)
        if entry is None:
            return None

        # 构建 prompt
        prompt = _EXTRACTION_PROMPT.format(
            entry_date=entry.entry_date,
            title=entry.title or "(无标题)",
            content=entry.content[:3000],  # 限制长度避免 token 过多
        )

        llm = self.llm_service
        if llm is None:
            # 没有 LLM 时，基于简单规则提取
            result = self._extract_by_rules(entry)
        else:
            try:
                resp = await llm.complete_structured_task(
                    system_instruction="",
                    user_input=prompt,
                    temperature=0.3,
                    max_tokens=4096,
                    caller="diary.extract_tags",
                    reasoning_effort="none",
                    inject_core_memory=False,
                )
                response = getattr(resp, "content", "")
                parsed = self._parse_extraction_response(response)
                if parsed is None:
                    return None
                result = ExtractionResult(
                    tags=parsed.get("tags", []),
                    persons=parsed.get("persons", []),
                    locations=parsed.get("locations", []),
                    events=parsed.get("events", []),
                )
            except Exception as e:
                logger.error(f"提取日记 {entry_id} 标签/人物失败: {e}")
                return None

        # 保存标签
        for tag_data in result.tags:
            name = tag_data.get("name", "").strip()
            if not name or len(name) > 20:
                continue
            tag_type_str = tag_data.get("type", "other")
            try:
                tag_type = TagType(tag_type_str)
            except ValueError:
                tag_type = TagType.OTHER
            confidence = float(tag_data.get("confidence", 0.8))
            tag = self.store.get_or_create_tag(name, tag_type)
            self.store.add_tag_to_entry(entry_id, tag.id, confidence)

        # 保存人物
        for person_data in result.persons:
            name = person_data.get("name", "").strip()
            if not name or len(name) > 20:
                continue
            relation = person_data.get("relation", "")[:20]
            context = person_data.get("context", "")[:200]
            person = self.store.get_or_create_person(name, relation)
            self.store.add_person_to_entry(entry_id, person.id, context, entry.entry_date)

        return result

    def _extract_by_rules(self, entry: DiaryEntry) -> ExtractionResult:
        """无 LLM 时，基于简单规则提取标签和人物。"""
        content = entry.content
        tags = []
        persons = []

        # 简单关键词匹配
        emotion_keywords = {
            "开心": "emotion", "高兴": "emotion", "快乐": "emotion",
            "难过": "emotion", "伤心": "emotion", "焦虑": "emotion",
            "紧张": "emotion", "平静": "emotion", "兴奋": "emotion",
            "累": "emotion", "疲惫": "emotion",
        }
        topic_keywords = {
            "工作": "work", "上班": "work", "加班": "work",
            "家": "family", "妈妈": "family", "爸爸": "family",
            "旅行": "topic", "旅游": "topic", "出去玩": "topic",
            "学习": "topic", "看书": "topic", "读书": "topic",
            "健身": "health", "运动": "health", "跑步": "health",
            "生病": "health", "医院": "health",
        }

        for keyword, tag_type in {**emotion_keywords, **topic_keywords}.items():
            if keyword in content:
                tags.append({"name": keyword, "type": tag_type, "confidence": 0.6})

        # 简单人物识别（基于常见称呼）
        person_keywords = ["妈妈", "爸爸", "乐乐", "艳艳", "狄胖胖", "老公", "老婆", "儿子", "女儿", "同事", "朋友"]
        for name in person_keywords:
            if name in content:
                # 找上下文
                idx = content.find(name)
                context = content[max(0, idx-20):idx+30].replace("\n", " ")
                persons.append({"name": name, "relation": "", "context": context})

        return ExtractionResult(tags=tags[:8], persons=persons[:10], locations=[], events=[])

    def _parse_extraction_response(self, response: str) -> dict | None:
        """解析 AI 提取的 JSON 响应。"""
        import json
        import re

        # 清理 markdown 代码块
        text = response.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        try:
            data = json.loads(text)
            return data
        except json.JSONDecodeError:
            # 尝试提取 JSON 对象
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            logger.warning(f"无法解析提取响应: {text[:200]}")
            return None

    async def batch_extract(self, limit: int = 100, start_id: int | None = None,
                             only_unextracted: bool = True) -> dict:
        """批量提取日记的标签和人物。

        Args:
            limit: 处理数量上限
            start_id: 起始日记 ID
            only_unextracted: 只处理未提取过的日记

        Returns:
            统计信息 {total, success, failed, skipped}
        """
        # 获取需要处理的日记
        entries = self.store.list_entries(limit=limit + 500)  # 多取一些用于过滤

        if start_id:
            entries = [e for e in entries if e.id >= start_id]

        if only_unextracted:
            # 过滤已提取的日记（已有标签或人物）
            unextracted = []
            for entry in entries:
                entry_tags = self.store.get_entry_tags(entry.id)
                entry_persons = self.store.get_entry_persons(entry.id)
                if not entry_tags and not entry_persons:
                    unextracted.append(entry)
            entries = unextracted

        entries = entries[:limit]
        total = len(entries)
        success = 0
        failed = 0

        logger.info(f"开始批量提取 {total} 篇日记的标签和人物")

        for i, entry in enumerate(entries):
            try:
                result = await self.extract_tags_and_persons(entry.id)
                if result:
                    success += 1
                else:
                    failed += 1
            except Exception as e:
                logger.error(f"提取日记 {entry.id} 失败: {e}")
                failed += 1

            if (i + 1) % 10 == 0:
                logger.info(f"已处理 {i+1}/{total}，成功 {success}，失败 {failed}")

        logger.info(f"批量提取完成：共 {total} 篇，成功 {success}，失败 {failed}")
        return {"total": total, "success": success, "failed": failed, "skipped": 0}

    # ═══════════════════════════════════════════
    # 标签/人物查询
    # ═══════════════════════════════════════════

    def get_tags(self, tag_type: TagType | None = None,
                 limit: int = 200, min_count: int = 1) -> list[DiaryTag]:
        """获取标签列表。"""
        return self.store.list_tags(tag_type, limit, min_count)

    def get_persons(self, relation: str | None = None,
                    limit: int = 200, min_appearances: int = 1) -> list[DiaryPerson]:
        """获取人物列表。"""
        return self.store.list_persons(relation, limit, min_appearances)

    def get_person_detail(self, person_id: int) -> dict | None:
        """获取人物详情，包含相关日记。"""
        person = self.store.get_person(person_id)
        if person is None:
            return None
        related_entries = self.store.get_person_entries(person_id, limit=200)
        return {
            "id": person.id,
            "name": person.name,
            "aliases": person.aliases,
            "relation": person.relation,
            "description": person.description,
            "first_appeared": person.first_appeared,
            "last_appeared": person.last_appeared,
            "appearance_count": person.appearance_count,
            "related_entries": related_entries,
        }

    def get_entry_tags_and_persons(self, entry_id: int) -> dict:
        """获取某篇日记的标签和人物。"""
        tags = self.store.get_entry_tags(entry_id)
        persons = self.store.get_entry_persons(entry_id)
        return {
            "tags": [{"id": t.id, "name": t.name, "type": t.type.value, "count": t.count} for t in tags],
            "persons": [{"id": p.id, "name": p.name, "relation": p.relation, "context": ctx}
                        for p, ctx in persons],
        }

    def get_extraction_stats(self) -> dict:
        """获取提取统计信息。"""
        total_entries = self.store.count_entries()
        total_tags = self.store.count_tags()
        total_persons = self.store.count_persons()
        tag_types = {}
        for tag_type in TagType:
            tags = self.store.list_tags(tag_type, limit=1000)
            if tags:
                tag_types[tag_type.value] = len(tags)
        person_relations = self.store.get_person_relations()
        return {
            "total_entries": total_entries,
            "total_tags": total_tags,
            "total_persons": total_persons,
            "tag_types": tag_types,
            "person_relations": [{"relation": r, "count": c} for r, c in person_relations],
        }

"""日记业务逻辑层。

提供日记的增删改查、LLM 驱动的内容分析、情绪识别、
主题提取、成长洞察等高级功能。
"""

from __future__ import annotations

import asyncio
import logging

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
    ) -> None:
        self.store = DiaryStore(database=database, db_path=db_path)
        self.store.initialize()
        self._llm_service = llm_service

    @property
    def llm_service(self) -> LLMService | None:
        return self._llm_service

    def set_llm_service(self, llm_service: LLMService) -> None:
        """设置 LLM 服务，用于日记分析。"""
        self._llm_service = llm_service

    # ── 基础 CRUD ───────────────────────────────────────────────

    def create_entry(self, data: DiaryEntryCreate) -> DiaryEntry:
        """创建日记。"""
        return self.store.create_entry(data)

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

        prompt = _DIARY_ANALYSIS_PROMPT.format(
            entry_date=entry.entry_date,
            title=entry.title or "(无标题)",
            content=entry.content[:8000],
        )

        try:
            response = await self._llm_service.complete_structured_task(
                prompt=prompt,
                task_name="diary_analysis",
                timeout_seconds=60,
            )
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

    def create_fragment(self, content: str, mood: MoodLevel = MoodLevel.UNKNOWN,
                         fragment_date: str | None = None, source: str = "manual") -> DiaryFragment:
        """创建一条碎片。"""
        return self.store.create_fragment(content, mood, fragment_date, source)

    def list_fragments(self, fragment_date: str | None = None,
                        limit: int = 100, offset: int = 0) -> list[DiaryFragment]:
        """列出碎片。"""
        return self.store.list_fragments(fragment_date, limit, offset)

    def delete_fragment(self, fragment_id: int) -> bool:
        """删除碎片。"""
        return self.store.delete_fragment(fragment_id)

    async def generate_diary_from_fragments(self, fragment_date: str | None = None,
                                               auto_delete: bool = True) -> DiaryEntry | None:
        """从当天碎片 AI 聚合生成一篇完整日记。

        借鉴 Night-Journal 的设计：把白天的碎片整理成一篇连贯、私人的日记。
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

        # 构建碎片文本
        fragments_text = "\n".join(
            f"{i+1}. {f.content}" + (f"（情绪：{f.mood.value}）" if f.mood != MoodLevel.UNKNOWN else "")
            for i, f in enumerate(fragments)
        )

        # 构建 AI prompt（借鉴 Night-Journal 的温柔真实风格）
        prompt = f"""你是一个安静的记录者，坐在用户这一天的记忆里，把零散的念头、情绪和画面整理成一篇属于他自己的日记。

今天的碎片记录：
{fragments_text}

请根据以上碎片，生成一篇连贯的日记。

要求：
1. 用第一人称「我」写作，像用户本人在回望这一天
2. 感受碎片里的情绪变化，让每一句话都从他自己的视角自然流出
3. 不只做事实罗列，而是找出这一天真正碰到他的东西
4. 保留他原本的语气、混乱感和真实情绪，只做轻微的文字整理
5. 不虚构重大事件，不进行心理诊断，不说教，不写鸡汤
6. 信息少的时候就写短一点，不硬凑
7. 正文结尾可以留一句轻微的余味，但不要鸡汤

输出 JSON（不要包含任何其他文字）：
{{
  "title": "日记标题（简短，不超过15字）",
  "content": "完整日记正文（300-800字）"
}}"""

        # 调用 LLM
        llm = self.llm_service
        if llm is None:
            # 没有 LLM 时，直接把碎片拼接成日记
            content = "\n\n".join(f.content for f in fragments)
            entry = self.create_entry(
                DiaryEntryCreate(
                    entry_date=fragment_date,
                    title=f"{fragment_date} 日记",
                    content=content,
                    source="fragment",
                    tags=["随手记"],
                )
            )
        else:
            try:
                response = await llm.chat(prompt)
                parsed = self._parse_analysis_response(response)
                title = parsed.get("title", f"{fragment_date} 日记") if parsed else f"{fragment_date} 日记"
                content = parsed.get("content", fragments_text) if parsed else fragments_text
            except Exception:
                # LLM 失败时降级为直接拼接
                title = f"{fragment_date} 日记"
                content = fragments_text

            entry = self.create_entry(
                DiaryEntryCreate(
                    entry_date=fragment_date,
                    title=title,
                    content=content,
                    source="fragment",
                    tags=["随手记", "AI聚合"],
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
                response = await llm.chat(prompt)
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

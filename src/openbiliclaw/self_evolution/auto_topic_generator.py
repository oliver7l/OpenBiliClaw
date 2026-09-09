"""自动专题生成引擎 — 从内容库中自动发现有价值的主题并生成跨平台综合专题。

核心功能：
- 基于知识图谱热门实体，自动发现候选专题主题
- 从 articles 表中搜索相关文章，按平台/时间/质量聚合
- 用 LLM 生成专题综述（AI 综合页面）：核心观点、时间线、关键人物、平台视角对比
- 自动创建专题并添加相关文章到 topic_items 表
- 专题质量评分：文章数量、平台多样性、时间跨度、实体关联度

设计思路（借鉴 MiJi 多源融合 + Agent-SaveMark 实体综合）：
- 一个专题 = 跨平台文章聚合 + AI 综合综述 + 时间线 + 关键人物 + 核心观点对比
- 不同平台对同一主题有不同视角（知乎深度长文、V2EX讨论、B站视频、小红书笔记）
- 融合多平台视角，生成更全面的专题内容
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from openbiliclaw.storage.database import open_db_conn
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TopicCandidate:
    """候选专题主题。"""

    name: str
    slug: str
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    entity_mention_count: int = 0
    article_count: int = 0
    platforms: list[str] = field(default_factory=list)
    quality_score: float = 0.0
    related_entities: list[str] = field(default_factory=list)


@dataclass
class GeneratedTopic:
    """生成的完整专题。"""

    name: str
    slug: str
    description: str
    keywords: list[str]
    platforms: list[str]
    ai_summary: str = ""  # LLM 生成的专题综述
    key_insights: list[str] = field(default_factory=list)  # 核心观点
    timeline: list[dict[str, Any]] = field(default_factory=list)  # 时间线
    key_figures: list[str] = field(default_factory=list)  # 关键人物
    platform_perspectives: dict[str, str] = field(default_factory=dict)  # 各平台视角
    article_ids: list[int] = field(default_factory=list)
    article_count: int = 0
    quality_score: float = 0.0
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "keywords": self.keywords,
            "platforms": self.platforms,
            "ai_summary": self.ai_summary,
            "key_insights": self.key_insights,
            "timeline": self.timeline,
            "key_figures": self.key_figures,
            "platform_perspectives": self.platform_perspectives,
            "article_ids": self.article_ids,
            "article_count": self.article_count,
            "quality_score": self.quality_score,
            "created_at": self.created_at,
        }


class AutoTopicGenerator:
    """自动专题生成引擎。

    Args:
        db_path: SQLite 数据库路径。
        llm_service: LLM 服务（用于生成专题综述）。

    """

    # 排除的噪声实体（太泛化或非主题性的）
    _NOISE_ENTITIES = {
        "分析",
        "实验",
        "EE",
        "Insights",
        "V2EX",
        "模型",
        "录分析",
        "聊天记录",
        "转化",
        "狄胖胖",
        "问题",
        "方法",
        "系统",
        "技术",
        "工作",
        "学习",
        "生活",
        "时间",
        "东西",
        "事情",
    }

    # 平台名称映射
    _PLATFORM_NAMES = {
        "zhihu": "知乎",
        "bilibili": "B站",
        "xiaohongshu": "小红书",
        "v2ex": "V2EX",
        "youtube": "YouTube",
        "douyin": "抖音",
        "hupu": "虎扑",
        "weibo": "微博",
        "wechat": "微信公众号",
        "juejin": "掘金",
        "sspai": "少数派",
        "36kr": "36氪",
        "huxiu": "虎嗅",
        "csdn": "CSDN",
        "jianshu": "简书",
        "douban": "豆瓣",
        "gcores": "机核",
        "xiaoyuzhou": "小宇宙",
        "web": "网页",
    }

    def __init__(self, db_path: str, llm_service: Any = None) -> None:
        self.db_path = db_path

    def _get_conn(self):
        """返回 ATTACH 了 content.db 的连接（v0.4.0+ articles 表迁移）。"""
        conn = open_db_conn(self.db_path)
        conn.row_factory = sqlite3.Row
        from pathlib import Path as _Path
        from contextlib import suppress as _suppress
        _content_path = _Path(str(self.db_path)).with_name('content.db')
        if _content_path.exists():
            with _suppress(Exception):
                conn.execute('ATTACH DATABASE ? AS content', (str(_content_path),))
        return conn

        self.llm_service = llm_service

    def discover_candidates(self, min_mentions: int = 20, limit: int = 20) -> list[TopicCandidate]:
        """从知识图谱中发现候选专题主题。

        Args:
            min_mentions: 实体最少出现次数。
            limit: 返回候选数量。

        Returns:
            候选专题列表，按质量评分排序。

        """
        candidates: list[TopicCandidate] = []

        with self._get_conn() as conn:
            # 加载最新知识图谱
            row = conn.execute(
                "SELECT graph_json FROM knowledge_graph ORDER BY id DESC LIMIT 1"
            ).fetchone()

            if not row:
                logger.warning("No knowledge graph found, cannot discover candidates")
                return candidates

            graph = json.loads(row[0])
            entities = graph.get("entities", [])

            # 过滤噪声实体，按出现次数排序
            valid_entities = [
                e
                for e in entities
                if e.get("name", "") not in self._NOISE_ENTITIES
                and len(e.get("name", "")) >= 2
                and e.get("mention_count", 0) >= min_mentions
            ]
            valid_entities.sort(key=lambda x: x.get("mention_count", 0), reverse=True)

            for entity in valid_entities[:limit]:
                name = entity["name"]
                slug = self._slugify(name)
                keywords = [name] + entity.get("aliases", [])[:3]

                # 搜索相关文章
                article_count, platforms = self._count_related_articles(conn, keywords)

                if article_count < 3:
                    continue  # 文章太少，不值得做专题

                # 计算质量评分
                quality_score = self._calc_quality_score(
                    mention_count=entity.get("mention_count", 0),
                    article_count=article_count,
                    platform_count=len(platforms),
                )

                # 获取相关实体
                related = self._get_related_entities(graph, name, max_related=5)

                candidate = TopicCandidate(
                    name=name,
                    slug=slug,
                    description=f"关于{name}的跨平台综合专题",
                    keywords=keywords,
                    entity_mention_count=entity.get("mention_count", 0),
                    article_count=article_count,
                    platforms=platforms,
                    quality_score=quality_score,
                    related_entities=related,
                )
                candidates.append(candidate)

        # 按质量评分排序
        candidates.sort(key=lambda x: x.quality_score, reverse=True)
        logger.info(
            "Discovered %d topic candidates (min_mentions=%d)", len(candidates), min_mentions
        )
        return candidates

    def generate_topic(
        self, candidate: TopicCandidate, max_articles: int = 50, use_llm: bool = True
    ) -> GeneratedTopic | None:
        """为候选主题生成完整专题。

        Args:
            candidate: 候选主题。
            max_articles: 最多添加的文章数量。
            use_llm: 是否使用 LLM 生成综述。

        Returns:
            生成的专题，如果失败返回 None。

        """
        with self._get_conn() as conn:
            # 搜索相关文章
            articles = self._search_related_articles(conn, candidate.keywords, limit=max_articles)

            if not articles:
                logger.warning("No articles found for topic: %s", candidate.name)
                return None

            # 按平台分组
            platform_articles: dict[str, list[dict[str, Any]]] = {}
            for art in articles:
                platform = art["source_type"]
                if platform not in platform_articles:
                    platform_articles[platform] = []
                platform_articles[platform].append(art)

            platforms = list(platform_articles.keys())

            # 生成时间线
            timeline = self._build_timeline(articles)

            # 提取关键人物（从文章作者中）
            key_figures = list({art["author"] for art in articles if art.get("author")})[:10]

            # 生成核心观点（从文章摘要/标题中提取）
            key_insights = self._extract_key_insights(articles, max_insights=8)

            # 各平台视角
            platform_perspectives = {}
            for platform, arts in platform_articles.items():
                platform_name = self._PLATFORM_NAMES.get(platform, platform)
                sample_titles = [a["title"][:50] for a in arts[:3]]
                platform_perspectives[platform] = f"{platform_name}（{len(arts)}篇）：" + "；".join(
                    sample_titles
                )

            # 用 LLM 生成专题综述
            ai_summary = ""
            if use_llm and self.llm_service:
                ai_summary = self._generate_ai_summary(
                    topic_name=candidate.name,
                    articles=articles,
                    key_insights=key_insights,
                    platform_perspectives=platform_perspectives,
                )

            # 创建专题
            topic_id = self._create_topic(
                conn=conn,
                name=candidate.name,
                slug=candidate.slug,
                description=candidate.description,
                keywords=candidate.keywords,
                platforms=platforms,
                ai_summary=ai_summary,
            )

            # 添加文章到专题
            article_ids = []
            for art in articles:
                self._add_topic_item(
                    conn=conn,
                    topic_id=topic_id,
                    article=art,
                    topic_label=candidate.name,
                )
                article_ids.append(art["id"])

            # 计算质量评分
            quality_score = self._calc_quality_score(
                mention_count=candidate.entity_mention_count,
                article_count=len(articles),
                platform_count=len(platforms),
            )

            created_at = datetime.now(UTC).isoformat()

            return GeneratedTopic(
                name=candidate.name,
                slug=candidate.slug,
                description=candidate.description,
                keywords=candidate.keywords,
                platforms=platforms,
                ai_summary=ai_summary,
                key_insights=key_insights,
                timeline=timeline,
                key_figures=key_figures,
                platform_perspectives=platform_perspectives,
                article_ids=article_ids,
                article_count=len(articles),
                quality_score=quality_score,
                created_at=created_at,
            )

    def auto_generate(
        self,
        min_mentions: int = 30,
        max_topics: int = 3,
        max_articles_per_topic: int = 50,
        use_llm: bool = True,
    ) -> list[GeneratedTopic]:
        """自动发现并生成专题。

        Args:
            min_mentions: 实体最少出现次数。
            max_topics: 最多生成专题数量。
            max_articles_per_topic: 每个专题最多文章数。
            use_llm: 是否使用 LLM 生成综述。

        Returns:
            生成的专题列表。

        """
        # 发现候选
        candidates = self.discover_candidates(min_mentions=min_mentions, limit=max_topics * 3)

        # 过滤已存在的专题
        existing_slugs = self._get_existing_topic_slugs()
        new_candidates = [c for c in candidates if c.slug not in existing_slugs]

        logger.info(
            "Found %d new candidates (out of %d total)", len(new_candidates), len(candidates)
        )

        # 生成专题
        generated: list[GeneratedTopic] = []
        for candidate in new_candidates[:max_topics]:
            try:
                topic = self.generate_topic(
                    candidate, max_articles=max_articles_per_topic, use_llm=use_llm
                )
                if topic:
                    generated.append(topic)
                    logger.info(
                        "Generated topic: %s (%d articles)", topic.name, topic.article_count
                    )
            except Exception as e:
                logger.exception("Failed to generate topic for %s: %s", candidate.name, e)

        return generated

    # ── 内部方法 ──────────────────────────────────────────────────

    def _slugify(self, name: str) -> str:
        """将名称转换为 slug。"""
        # 中文直接用拼音或原名称
        slug = re.sub(r"[^\w\u4e00-\u9fff-]", "-", name.lower())
        slug = re.sub(r"-+", "-", slug).strip("-")
        return slug or "topic"

    def _count_related_articles(
        self, conn: sqlite3.Connection, keywords: list[str]
    ) -> tuple[int, list[str]]:
        """统计相关文章数量和涉及的平台。"""
        if not keywords:
            return 0, []

        # 构建搜索条件
        conditions = []
        params = []
        for kw in keywords[:3]:  # 最多用3个关键词
            conditions.append("(title LIKE ? OR content_text LIKE ? OR tags LIKE ?)")
            params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%"])

        where_clause = " OR ".join(conditions)

        try:
            row = conn.execute(
                f"SELECT COUNT(*) FROM articles WHERE {where_clause}",
                params,
            ).fetchone()
            count = row[0] if row else 0

            # 获取平台列表
            platform_rows = conn.execute(
                f"SELECT DISTINCT source_type FROM articles WHERE {where_clause} LIMIT 10",
                params,
            ).fetchall()
            platforms = [r[0] for r in platform_rows if r[0]]

            return count, platforms
        except Exception as e:
            logger.warning("Failed to count related articles: %s", e)
            return 0, []

    def _search_related_articles(
        self, conn: sqlite3.Connection, keywords: list[str], limit: int = 50
    ) -> list[dict[str, Any]]:
        """搜索相关文章。"""
        if not keywords:
            return []

        conditions = []
        params = []
        for kw in keywords[:3]:
            conditions.append("(title LIKE ? OR content_text LIKE ? OR tags LIKE ?)")
            params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%"])

        where_clause = " OR ".join(conditions)

        try:
            rows = conn.execute(
                f"""SELECT id, title, url, author, summary, source_type, source_name,
                           published_at, tags, created_at
                    FROM articles
                    WHERE {where_clause}
                    ORDER BY published_at DESC
                    LIMIT ?""",
                params + [limit],
            ).fetchall()

            articles = []
            for row in rows:
                articles.append(
                    {
                        "id": row[0],
                        "title": row[1] or "",
                        "url": row[2] or "",
                        "author": row[3] or "",
                        "summary": row[4] or "",
                        "source_type": row[5] or "",
                        "source_name": row[6] or "",
                        "published_at": row[7] or "",
                        "tags": row[8] or "[]",
                        "created_at": row[9] or "",
                    }
                )
            return articles
        except Exception as e:
            logger.warning("Failed to search related articles: %s", e)
            return []

    def _calc_quality_score(
        self, mention_count: int, article_count: int, platform_count: int
    ) -> float:
        """计算专题质量评分（0-100）。"""
        # 实体出现次数（权重 30%）
        mention_score = min(mention_count / 100.0, 1.0) * 30
        # 文章数量（权重 40%）
        article_score = min(article_count / 50.0, 1.0) * 40
        # 平台多样性（权重 30%）
        platform_score = min(platform_count / 5.0, 1.0) * 30
        return round(mention_score + article_score + platform_score, 1)

    def _get_related_entities(
        self, graph: dict[str, Any], entity_name: str, max_related: int = 5
    ) -> list[str]:
        """获取与指定实体相关的其他实体。"""
        relations = graph.get("relations", [])
        related = set()

        for rel in relations:
            source = rel.get("source", "")
            target = rel.get("target", "")
            if source == entity_name and target != entity_name:
                related.add(target)
            elif target == entity_name and source != entity_name:
                related.add(source)

        return list(related)[:max_related]

    def _build_timeline(self, articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """从文章中构建时间线。"""
        timeline = []
        for art in articles:
            pub_date = art.get("published_at", "")
            if pub_date and len(pub_date) >= 10:
                timeline.append(
                    {
                        "date": pub_date[:10],
                        "title": art["title"][:60],
                        "platform": self._PLATFORM_NAMES.get(
                            art["source_type"], art["source_type"]
                        ),
                        "article_id": art["id"],
                    }
                )

        # 按日期排序
        timeline.sort(key=lambda x: x["date"])
        return timeline[:20]  # 最多20条

    def _extract_key_insights(
        self, articles: list[dict[str, Any]], max_insights: int = 8
    ) -> list[str]:
        """从文章摘要/标题中提取核心观点。"""
        insights = []
        seen = set()

        for art in articles:
            summary = art.get("summary", "").strip()
            title = art.get("title", "").strip()

            # 优先用摘要，如果摘要太短就用标题
            text = summary if len(summary) > 20 else title
            if text and text not in seen and len(text) > 10:
                # 截断过长的文本
                insight = text[:100] + "..." if len(text) > 100 else text
                insights.append(insight)
                seen.add(text)

            if len(insights) >= max_insights:
                break

        return insights

    def _generate_ai_summary(
        self,
        topic_name: str,
        articles: list[dict[str, Any]],
        key_insights: list[str],
        platform_perspectives: dict[str, str],
    ) -> str:
        """用 LLM 生成专题综述。"""
        try:
            if self.llm_service is None:
                return self._fallback_summary(topic_name, articles, key_insights)

            # 构建提示词
            article_summaries = "\n".join(
                f"- [{self._PLATFORM_NAMES.get(a['source_type'], a['source_type'])}] {a['title']}"
                for a in articles[:15]
            )

            system_instruction = "你是一个专题综述撰写专家。请根据提供的文章列表和核心观点，生成一篇综合专题综述。要求：1. 概括主题核心内容和发展脉络；2. 总结不同平台的视角差异；3. 提炼3-5个核心观点；4. 语言简洁专业，300-500字。"

            user_input = f"""专题名称：{topic_name}

相关文章（{len(articles)}篇）：
{article_summaries}

核心观点：
{chr(10).join(f"- {i}" for i in key_insights[:5])}

各平台视角：
{chr(10).join(f"- {v}" for v in platform_perspectives.values())}

请生成综述（300-500字）："""

            # 调用 LLM（统一使用 generate_structured）
            from openbiliclaw.llm.generation import generate_structured
            from openbiliclaw.self_evolution.insight_report import _run_async

            result = _run_async(
                generate_structured(
                    self.llm_service,
                    system_instruction=system_instruction,
                    user_input=user_input,
                    parse=lambda x: x,
                    label="auto_topic_summary",
                    temperature=0.3,
                    max_tokens=800,
                )
            )

            text = str(result).strip()
            if text:
                return text

            return self._fallback_summary(topic_name, articles, key_insights)

        except Exception as e:
            logger.warning("LLM summary generation failed, using fallback: %s", e)
            return self._fallback_summary(topic_name, articles, key_insights)

    def _fallback_summary(
        self, topic_name: str, articles: list[dict[str, Any]], key_insights: list[str]
    ) -> str:
        """LLM 不可用时的降级综述。"""
        platforms = set(a["source_type"] for a in articles)
        platform_names = [self._PLATFORM_NAMES.get(p, p) for p in platforms]

        summary = f"本专题聚合了关于「{topic_name}」的 {len(articles)} 篇内容，"
        summary += f"涵盖 {', '.join(platform_names[:5])} 等 {len(platforms)} 个平台。\n\n"
        summary += "核心观点包括：\n"
        for i, insight in enumerate(key_insights[:5], 1):
            summary += f"{i}. {insight}\n"
        summary += f"\n通过跨平台内容的融合，可以更全面地理解「{topic_name}」这一主题的发展脉络和多元视角。"
        return summary

    def _create_topic(
        self,
        conn: sqlite3.Connection,
        name: str,
        slug: str,
        description: str,
        keywords: list[str],
        platforms: list[str],
        ai_summary: str,
    ) -> int:
        """创建专题。"""
        now = datetime.now(UTC).isoformat()

        cursor = conn.execute(
            """INSERT INTO topics
               (name, slug, description, keywords, platforms, status, item_count,
                created_at, updated_at, last_collected_at)
               VALUES (?, ?, ?, ?, ?, 'active', 0, ?, ?, ?)""",
            (
                name,
                slug,
                description,
                json.dumps(keywords, ensure_ascii=False),
                json.dumps(platforms, ensure_ascii=False),
                now,
                now,
                now,
            ),
        )
        topic_id = cursor.lastrowid
        assert topic_id is not None

        # 如果有 AI 综述，保存到 description 中
        if ai_summary:
            full_desc = description + "\n\n" + ai_summary if description else ai_summary
            conn.execute(
                "UPDATE topics SET description = ? WHERE id = ?",
                (full_desc[:2000], topic_id),  # 限制长度
            )

        conn.commit()
        logger.info("Created topic: %s (id=%s)", name, topic_id)
        return topic_id

    def _add_topic_item(
        self, conn: sqlite3.Connection, topic_id: int, article: dict[str, Any], topic_label: str
    ) -> None:
        """添加文章到专题。"""
        now = datetime.now(UTC).isoformat()
        content_key = f"article_{article['id']}"

        # 检查是否已存在
        existing = conn.execute(
            "SELECT id FROM topic_items WHERE topic_id = ? AND content_key = ?",
            (topic_id, content_key),
        ).fetchone()

        if existing:
            return  # 已存在，跳过

        conn.execute(
            """INSERT INTO topic_items
               (topic_id, content_key, title, url, source_platform, source_name,
                summary, topic_label, collected_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                topic_id,
                content_key,
                article["title"][:200],
                article["url"],
                article["source_type"],
                article["source_name"],
                article["summary"][:500],
                topic_label,
                now,
            ),
        )

        # 更新专题文章计数
        conn.execute(
            "UPDATE topics SET item_count = item_count + 1, updated_at = ? WHERE id = ?",
            (now, topic_id),
        )

    def _get_existing_topic_slugs(self) -> set[str]:
        """获取已存在的专题 slug。"""
        try:
            with self._get_conn() as conn:
                rows = conn.execute("SELECT slug FROM topics").fetchall()
                return {r[0] for r in rows if r[0]}
        except Exception:
            return set()

"""Periodic insight report generator.

Analyzes the user's reading behavior over a time window and generates
a natural-language insight report with:
- Platform / topic distribution
- Interest drift detection (vs previous window)
- Deep-dive candidates (favorited but unfinished, related deep content)
- Personalized recommendations
- Key takeaways

Reports are stored in the ``insight_reports`` table and can be
served via the API or pushed as notifications.
"""

from __future__ import annotations
from openbiliclaw.storage.database import open_db_conn

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger("self_evolution.insight")


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from sync or async context.

    Handles both cases:
    - Called from sync context: use asyncio.run()
    - Called from async context (e.g., FastAPI): run in a thread
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PlatformStats:
    """Reading stats for one platform."""

    platform: str
    view_count: int = 0
    favorite_count: int = 0
    like_count: int = 0
    total_time_seconds: int = 0

    @property
    def engagement_score(self) -> float:
        return self.view_count * 1.0 + self.favorite_count * 3.0 + self.like_count * 2.0


@dataclass
class TopicStats:
    """Reading stats for one topic / keyword."""

    topic: str
    count: int = 0
    favorite_count: int = 0
    sample_titles: list[str] = field(default_factory=list)


@dataclass
class InterestDrift:
    """Detected interest change between two windows."""

    topic: str
    current_count: int
    previous_count: int
    change_ratio: float  # current / previous (1.0 = no change)
    direction: str  # "rising" | "declining" | "stable"
    significance: str  # "high" | "medium" | "low"


@dataclass
class DeepDiveCandidate:
    """A content item worth deeper reading."""

    title: str
    url: str
    platform: str
    reason: str
    source: str  # "favorited_unfinished" | "related_deep" | "high_quality_unread"


@dataclass
class InsightReport:
    """A complete periodic insight report."""

    report_id: str
    period_start: str
    period_end: str
    window_days: int
    generated_at: str
    # Stats
    total_views: int = 0
    total_favorites: int = 0
    total_likes: int = 0
    platform_stats: list[PlatformStats] = field(default_factory=list)
    topic_stats: list[TopicStats] = field(default_factory=list)
    interest_drifts: list[InterestDrift] = field(default_factory=list)
    deep_dive_candidates: list[DeepDiveCandidate] = field(default_factory=list)
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    # LLM-generated
    natural_language_summary: str = ""
    key_takeaways: list[str] = field(default_factory=list)
    # Raw data for debugging
    raw_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "window_days": self.window_days,
            "generated_at": self.generated_at,
            "total_views": self.total_views,
            "total_favorites": self.total_favorites,
            "total_likes": self.total_likes,
            "platform_stats": [vars(s) for s in self.platform_stats],
            "topic_stats": [vars(s) for s in self.topic_stats],
            "interest_drifts": [vars(d) for d in self.interest_drifts],
            "deep_dive_candidates": [vars(c) for c in self.deep_dive_candidates],
            "recommendations": self.recommendations,
            "natural_language_summary": self.natural_language_summary,
            "key_takeaways": self.key_takeaways,
        }


# ---------------------------------------------------------------------------
# Topic extraction (lightweight, no external deps)
# ---------------------------------------------------------------------------

# Common stopwords for Chinese topic extraction
_STOPWORDS = {
    "的",
    "了",
    "在",
    "是",
    "我",
    "有",
    "和",
    "就",
    "不",
    "人",
    "都",
    "一",
    "一个",
    "上",
    "也",
    "很",
    "到",
    "说",
    "要",
    "去",
    "你",
    "会",
    "着",
    "没有",
    "看",
    "好",
    "自己",
    "这",
    "那",
    "他",
    "她",
    "它",
    "们",
    "什么",
    "怎么",
    "为什么",
    "可以",
    "这个",
    "那个",
    "因为",
    "所以",
    "但是",
    "如果",
    "虽然",
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "must",
    "shall",
    "can",
    "need",
    "dare",
    "to",
    "of",
    "in",
    "for",
    "on",
    "with",
    "at",
    "by",
    "from",
    "as",
    "into",
    "through",
    "during",
    "before",
    "after",
    "above",
    "below",
    "and",
    "but",
    "or",
    "nor",
    "not",
    "so",
    "yet",
    "both",
    "either",
    "neither",
    "each",
    "every",
    "all",
    "any",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "no",
    "only",
    "own",
    "same",
    "than",
    "too",
    "very",
    "just",
    "about",
    "up",
    "out",
    "then",
    "here",
    "there",
    "when",
    "where",
    "why",
    "how",
    "which",
    "who",
    "whom",
    "whose",
    "what",
}

# Known high-value topics (predefined for better signal)
_KNOWN_TOPICS = [
    "广告算法",
    "推荐系统",
    "程序化广告",
    "RTB",
    "DSP",
    "SSP",
    "ADX",
    "CTR",
    "CVR",
    "归因",
    "转化率",
    "点击率",
    "出价",
    "竞价",
    "拍卖",
    "大模型",
    "LLM",
    "GPT",
    "Agent",
    "多智能体",
    "RAG",
    "检索增强",
    "深度学习",
    "机器学习",
    "强化学习",
    "神经网络",
    "Transformer",
    "短剧",
    "短剧创业",
    "短剧商业化",
    "微短剧",
    "面试",
    "面经",
    "求职",
    "跳槽",
    "简历",
    "offer",
    "数据分析",
    "数据科学",
    "SQL",
    "Python",
    "算法题",
    "LeetCode",
    "产品经理",
    "产品设计",
    "用户增长",
    "增长黑客",
    "留存",
    "活跃",
    "积分运营",
    "用户运营",
    "内容运营",
    "订阅制",
    "SaaS",
    "商业化",
    "变现",
    "生成式推荐",
    "生成式AI",
    "AIGC",
    "知识图谱",
    "Embedding",
    "向量检索",
    "冷启动",
    "探索利用",
    "EE",
    "多臂老虎机",
    "粗排",
    "精排",
    "召回",
    "排序",
    "重排",
    "DIN",
    "DIEN",
    "DSIN",
    "multi-head",
    "注意力机制",
    "用户画像",
    "标签体系",
    "特征工程",
    "AB测试",
    "实验",
    "因果推断",
    "边际收益",
    "频控",
    "曝光",
    "点击",
    "转化",
]


def extract_topics(text: str, top_k: int = 5) -> list[str]:
    """Extract topic keywords from text.

    Uses a combination of known-topic matching and simple Chinese/English
    keyword extraction.  Returns up to ``top_k`` topics sorted by relevance.
    """
    if not text:
        return []

    text_lower = text.lower()
    found: list[tuple[str, int]] = []

    # 1. Match known topics (highest signal)
    for topic in _KNOWN_TOPICS:
        count = text_lower.count(topic.lower())
        if count > 0:
            found.append((topic, count * 10))  # known topics weighted higher

    # 2. Extract capitalized English phrases / acronyms
    for match in re.findall(r"\b[A-Z]{2,}\b", text):
        if match.lower() not in _STOPWORDS and len(match) >= 2:
            found.append((match, 2))

    # 3. Extract Chinese 2-4 char words (simple n-gram approach)
    # This is a lightweight approach — for production use jieba or similar
    chinese_chars = re.findall(r"[\u4e00-\u9fff]{2,4}", text)
    for word in chinese_chars:
        if word not in _STOPWORDS and len(word) >= 2:
            # Boost words that appear multiple times
            count = text.count(word)
            if count >= 2:
                found.append((word, count))

    # Deduplicate and sort
    seen: set[str] = set()
    result: list[str] = []
    for topic, _score in sorted(found, key=lambda x: -x[1]):
        if topic.lower() not in seen and len(topic) >= 2:
            seen.add(topic.lower())
            result.append(topic)
            if len(result) >= top_k:
                break

    return result


def infer_platform_from_url(url: str) -> str:
    """Infer platform name from URL."""
    if not url:
        return "unknown"
    url_lower = url.lower()
    if "bilibili.com" in url_lower or "b23.tv" in url_lower:
        return "bilibili"
    if "zhihu.com" in url_lower:
        return "zhihu"
    if "xiaohongshu.com" in url_lower or "xhslink" in url_lower:
        return "xiaohongshu"
    if "douyin.com" in url_lower:
        return "douyin"
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "youtube"
    if "v2ex.com" in url_lower:
        return "v2ex"
    if "twitter.com" in url_lower or "x.com" in url_lower:
        return "twitter"
    if "weibo.com" in url_lower:
        return "weibo"
    if "mp.weixin.qq.com" in url_lower:
        return "wechat"
    if "xiaoyuzhoufm.com" in url_lower:
        return "xiaoyuzhou"
    return "other"


# ---------------------------------------------------------------------------
# Report generator
# ---------------------------------------------------------------------------


class InsightReportGenerator:
    """Generate periodic insight reports from user behavior data.

    Args:
        db_path: Path to the SQLite database.
        llm_service: Optional LLM service for natural-language generation.

    """

    def __init__(self, db_path: str, *, llm_service: Any | None = None) -> None:
        self.db_path = db_path
        self.llm_service = llm_service

    def _get_conn(self) -> Any:
        import sqlite3

        conn = open_db_conn(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def generate_report(
        self,
        *,
        window_days: int = 7,
        end_date: datetime | None = None,
        include_llm_summary: bool = True,
    ) -> InsightReport:
        """Generate an insight report for the given time window.

        Args:
            window_days: Number of days to analyze (default: 7 = weekly).
            end_date: End of the analysis window (default: now).
            include_llm_summary: Whether to generate an LLM natural-language summary.

        Returns:
            An InsightReport with all stats and recommendations.

        """
        if end_date is None:
            end_date = datetime.now()
        start_date = end_date - timedelta(days=window_days)
        prev_start = start_date - timedelta(days=window_days)

        report_id = f"insight-{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}"

        report = InsightReport(
            report_id=report_id,
            period_start=start_date.isoformat(),
            period_end=end_date.isoformat(),
            window_days=window_days,
            generated_at=datetime.now().isoformat(),
        )

        conn = self._get_conn()
        try:
            self._collect_behavior_stats(conn, report, start_date, end_date)
            self._collect_topic_stats(conn, report, start_date, end_date)
            self._detect_interest_drift(conn, report, start_date, end_date, prev_start)
            self._find_deep_dive_candidates(conn, report, start_date, end_date)
            self._generate_recommendations(conn, report)
        finally:
            conn.close()

        if include_llm_summary and self.llm_service is not None:
            try:
                self._generate_llm_summary(report)
            except Exception:
                logger.exception("Failed to generate LLM summary for insight report")
                report.natural_language_summary = self._generate_fallback_summary(report)
                report.key_takeaways = self._generate_fallback_takeaways(report)
        else:
            report.natural_language_summary = self._generate_fallback_summary(report)
            report.key_takeaways = self._generate_fallback_takeaways(report)

        return report

    def _collect_behavior_stats(
        self, conn: Any, report: InsightReport, start: datetime, end: datetime
    ) -> None:
        """Collect view / favorite / like stats by platform."""
        rows = conn.execute(
            """
            SELECT event_type, url, title, created_at
            FROM events
            WHERE created_at >= ? AND created_at <= ?
              AND event_type IN ('view', 'favorite', 'like', 'click', 'article_finished')
            ORDER BY created_at
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()

        platform_map: dict[str, PlatformStats] = {}

        for row in rows:
            platform = infer_platform_from_url(row["url"] or "")
            if platform not in platform_map:
                platform_map[platform] = PlatformStats(platform=platform)
            stats = platform_map[platform]

            etype = row["event_type"]
            if etype == "view":
                stats.view_count += 1
                report.total_views += 1
            elif etype == "favorite":
                stats.favorite_count += 1
                report.total_favorites += 1
            elif etype == "like":
                stats.like_count += 1
                report.total_likes += 1

        report.platform_stats = sorted(platform_map.values(), key=lambda s: -s.engagement_score)

    def _collect_topic_stats(
        self, conn: Any, report: InsightReport, start: datetime, end: datetime
    ) -> None:
        """Collect topic distribution from viewed/favorited content."""
        rows = conn.execute(
            """
            SELECT e.event_type, e.url, e.title, a.tags, a.content_text
            FROM events e
            LEFT JOIN articles a ON a.url = e.url
            WHERE e.created_at >= ? AND e.created_at <= ?
              AND e.event_type IN ('view', 'favorite', 'like')
              AND (e.title IS NOT NULL AND length(e.title) > 0)
            ORDER BY e.created_at DESC
            LIMIT 500
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()

        topic_map: dict[str, TopicStats] = {}

        for row in rows:
            text = " ".join(
                filter(
                    None, [row["title"] or "", row["tags"] or "", (row["content_text"] or "")[:500]]
                )
            )
            topics = extract_topics(text, top_k=3)
            is_favorite = row["event_type"] == "favorite"

            for topic in topics:
                if topic not in topic_map:
                    topic_map[topic] = TopicStats(topic=topic)
                ts = topic_map[topic]
                ts.count += 1
                if is_favorite:
                    ts.favorite_count += 1
                if row["title"] and len(ts.sample_titles) < 3:
                    ts.sample_titles.append(row["title"][:80])

        report.topic_stats = sorted(topic_map.values(), key=lambda t: -t.count)[:20]

    def _detect_interest_drift(
        self,
        conn: Any,
        report: InsightReport,
        start: datetime,
        end: datetime,
        prev_start: datetime,
    ) -> None:
        """Detect interest drift by comparing current vs previous window."""
        # Current window topics
        current_topics = {t.topic: t.count for t in report.topic_stats}

        # Previous window topics (recompute)
        prev_rows = conn.execute(
            """
            SELECT e.title, a.tags
            FROM events e
            LEFT JOIN articles a ON a.url = e.url
            WHERE e.created_at >= ? AND e.created_at < ?
              AND e.event_type IN ('view', 'favorite', 'like')
              AND e.title IS NOT NULL
            LIMIT 500
            """,
            (prev_start.isoformat(), start.isoformat()),
        ).fetchall()

        prev_topic_map: dict[str, int] = {}
        for row in prev_rows:
            text = " ".join(filter(None, [row["title"] or "", row["tags"] or ""]))
            for topic in extract_topics(text, top_k=3):
                prev_topic_map[topic] = prev_topic_map.get(topic, 0) + 1

        # Compute drift for topics that appear in either window
        all_topics = set(current_topics.keys()) | set(prev_topic_map.keys())
        drifts: list[InterestDrift] = []

        for topic in all_topics:
            curr = current_topics.get(topic, 0)
            prev = prev_topic_map.get(topic, 0)

            if prev == 0 and curr > 0:
                ratio = float("inf")
                direction = "rising"
                significance = "high" if curr >= 3 else "medium"
            elif curr == 0 and prev > 0:
                ratio = 0.0
                direction = "declining"
                significance = "high" if prev >= 3 else "medium"
            elif prev > 0:
                ratio = curr / prev
                if ratio >= 1.5:
                    direction = "rising"
                    significance = "high" if ratio >= 2.0 else "medium"
                elif ratio <= 0.5:
                    direction = "declining"
                    significance = "high" if ratio <= 0.3 else "medium"
                else:
                    direction = "stable"
                    significance = "low"
            else:
                continue

            # Only report significant drifts
            if significance in ("high", "medium") and (curr + prev) >= 3:
                drifts.append(
                    InterestDrift(
                        topic=topic,
                        current_count=curr,
                        previous_count=prev,
                        change_ratio=min(ratio, 10.0) if ratio != float("inf") else 10.0,
                        direction=direction,
                        significance=significance,
                    )
                )

        report.interest_drifts = sorted(
            drifts, key=lambda d: -d.change_ratio if d.direction == "rising" else d.change_ratio
        )

    def _find_deep_dive_candidates(
        self, conn: Any, report: InsightReport, start: datetime, end: datetime
    ) -> None:
        """Find content worth deeper reading."""
        candidates: list[DeepDiveCandidate] = []

        # 1. Favorited but not finished (reading_percent < 80)
        rows = conn.execute(
            """
            SELECT a.title, a.url, a.source_type, a.reading_percent, a.tags
            FROM events e
            JOIN articles a ON a.url = e.url
            WHERE e.event_type = 'favorite'
              AND e.created_at >= ? AND e.created_at <= ?
              AND (a.reading_percent IS NULL OR a.reading_percent < 80)
              AND a.content_text IS NOT NULL AND length(a.content_text) > 500
            ORDER BY e.created_at DESC
            LIMIT 5
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()

        for row in rows:
            pct = row["reading_percent"]
            reason = f"已收藏但只读了{pct:.0f}%" if pct else "已收藏但还没开始读"
            candidates.append(
                DeepDiveCandidate(
                    title=row["title"] or "无标题",
                    url=row["url"] or "",
                    platform=row["source_type"] or infer_platform_from_url(row["url"] or ""),
                    reason=reason,
                    source="favorited_unfinished",
                )
            )

        # 2. High-quality unread content in top topics
        top_topics = [t.topic for t in report.topic_stats[:5]]
        if top_topics:
            ",".join(["?"] * len(top_topics))
            # Build LIKE conditions
            like_conditions = " OR ".join(["(a.title LIKE ? OR a.tags LIKE ?)" for _ in top_topics])
            params = [f"%{t}%" for t in top_topics for _ in range(2)]
            params.extend([start.isoformat()])

            rows = conn.execute(
                f"""
                SELECT a.title, a.url, a.source_type, a.tags, a.ai_summary
                FROM articles a
                WHERE ({like_conditions})
                  AND a.created_at >= ?
                  AND a.content_text IS NOT NULL AND length(a.content_text) > 1000
                  AND a.url NOT IN (SELECT url FROM events WHERE event_type = 'view' AND created_at >= ?)
                ORDER BY length(a.content_text) DESC
                LIMIT 5
                """,
                params + [start.isoformat()],
            ).fetchall()

            for row in rows:
                candidates.append(
                    DeepDiveCandidate(
                        title=row["title"] or "无标题",
                        url=row["url"] or "",
                        platform=row["source_type"] or infer_platform_from_url(row["url"] or ""),
                        reason="与你近期关注的主题相关，内容较长值得深读",
                        source="related_deep",
                    )
                )

        report.deep_dive_candidates = candidates[:10]

    def _generate_recommendations(self, conn: Any, report: InsightReport) -> None:
        """Generate personalized recommendations based on recent interests."""
        # Use top topics to find related high-quality content
        top_topics = [t.topic for t in report.topic_stats[:5]]
        if not top_topics:
            return

        ",".join(["?"] * len(top_topics))
        like_conditions = " OR ".join(["(title LIKE ? OR tags LIKE ?)" for _ in top_topics])
        params = [f"%{t}%" for t in top_topics for _ in range(2)]

        rows = conn.execute(
            f"""
            SELECT id, title, url, source_type, author, tags, ai_summary, reading_percent
            FROM articles
            WHERE ({like_conditions})
              AND content_text IS NOT NULL AND length(content_text) > 300
            ORDER BY created_at DESC
            LIMIT 10
            """,
            params,
        ).fetchall()

        for row in rows:
            report.recommendations.append(
                {
                    "id": row["id"],
                    "title": row["title"] or "",
                    "url": row["url"] or "",
                    "platform": row["source_type"] or "",
                    "author": row["author"] or "",
                    "tags": row["tags"] or "",
                    "summary": (row["ai_summary"] or "")[:200],
                    "reading_percent": row["reading_percent"],
                }
            )

    def _generate_llm_summary(self, report: InsightReport) -> None:
        """Generate natural-language summary using LLM."""
        from openbiliclaw.llm.generation import generate_structured

        # Build a compact data summary for the LLM
        data_summary = {
            "period": f"{report.period_start[:10]} 到 {report.period_end[:10]}",
            "total_views": report.total_views,
            "total_favorites": report.total_favorites,
            "top_platforms": [
                {"platform": s.platform, "views": s.view_count, "favorites": s.favorite_count}
                for s in report.platform_stats[:5]
            ],
            "top_topics": [
                {"topic": t.topic, "count": t.count, "favorites": t.favorite_count}
                for t in report.topic_stats[:10]
            ],
            "rising_interests": [
                {"topic": d.topic, "change": f"{d.previous_count}→{d.current_count}"}
                for d in report.interest_drifts
                if d.direction == "rising"
            ][:5],
            "declining_interests": [
                {"topic": d.topic, "change": f"{d.previous_count}→{d.current_count}"}
                for d in report.interest_drifts
                if d.direction == "declining"
            ][:5],
            "deep_dive_count": len(report.deep_dive_candidates),
        }

        system_instruction = (
            "你是一个个人阅读洞察分析师。根据用户最近的阅读行为数据，生成一份亲切、有洞察力的周报。"
            "要求：1. 用朋友聊天的语气，不要太正式；2. 指出用户的兴趣变化和值得关注的趋势；"
            "3. 给出具体的建议（比如应该深读什么、可以探索什么新方向）；"
            "4. 不要编造数据，只基于提供的统计数据；5. 控制在300字以内。"
        )

        user_input = f"用户阅读数据：\n{json.dumps(data_summary, ensure_ascii=False, indent=2)}"

        try:
            result = _run_async(
                generate_structured(
                    self.llm_service,
                    system_instruction=system_instruction,
                    user_input=user_input,
                    parse=lambda x: x,
                    label="insight_report_summary",
                    temperature=0.7,
                    max_tokens=500,
                )
            )
            report.natural_language_summary = str(result).strip()
        except Exception:
            logger.exception("LLM summary generation failed")
            report.natural_language_summary = self._generate_fallback_summary(report)

        # Generate key takeaways
        try:
            takeaway_result = _run_async(
                generate_structured(
                    self.llm_service,
                    system_instruction=(
                        "根据用户阅读数据，提取3-5条关键洞察，每条不超过30字。"
                        "格式：每行一条，用数字编号。只基于数据，不要编造。"
                    ),
                    user_input=f"用户阅读数据：\n{json.dumps(data_summary, ensure_ascii=False)}",
                    parse=lambda x: x,
                    label="insight_report_takeaways",
                    temperature=0.5,
                    max_tokens=200,
                )
            )
            text = str(takeaway_result).strip()
            report.key_takeaways = [
                line.strip().lstrip("0123456789.、) ")
                for line in text.split("\n")
                if line.strip() and len(line.strip()) > 5
            ][:5]
        except Exception:
            logger.exception("LLM takeaways generation failed")
            report.key_takeaways = self._generate_fallback_takeaways(report)

    def _generate_fallback_summary(self, report: InsightReport) -> str:
        """Generate a simple fallback summary without LLM."""
        parts = [
            f"这是你 {report.window_days} 天的阅读洞察（{report.period_start[:10]} 到 {report.period_end[:10]}）。",
            f"你一共浏览了 {report.total_views} 条内容，收藏了 {report.total_favorites} 条。",
        ]

        if report.platform_stats:
            top = report.platform_stats[0]
            parts.append(f"最活跃的平台是 {top.platform}（{top.view_count} 次浏览）。")

        if report.topic_stats:
            top_topics = "、".join([t.topic for t in report.topic_stats[:5]])
            parts.append(f"关注最多的主题：{top_topics}。")

        rising = [d for d in report.interest_drifts if d.direction == "rising"]
        if rising:
            parts.append(f"兴趣上升中：{', '.join([d.topic for d in rising[:3]])}。")

        if report.deep_dive_candidates:
            parts.append(f"有 {len(report.deep_dive_candidates)} 条内容值得深读，建议抽空看看。")

        return " ".join(parts)

    def _generate_fallback_takeaways(self, report: InsightReport) -> list[str]:
        """Generate fallback key takeaways without LLM."""
        takeaways: list[str] = []

        if report.topic_stats:
            takeaways.append(
                f"最关注：{report.topic_stats[0].topic}（{report.topic_stats[0].count}次）"
            )

        rising = [
            d
            for d in report.interest_drifts
            if d.direction == "rising" and d.significance == "high"
        ]
        if rising:
            takeaways.append(
                f"新兴趣：{rising[0].topic}（从{rising[0].previous_count}升到{rising[0].current_count}）"
            )

        if report.deep_dive_candidates:
            unfinished = [
                c for c in report.deep_dive_candidates if c.source == "favorited_unfinished"
            ]
            if unfinished:
                takeaways.append(f"待深读：{len(unfinished)}条收藏还没读完")

        if report.total_favorites > 0:
            takeaways.append(
                f"收藏率：{report.total_favorites}/{report.total_views} = {report.total_favorites / max(report.total_views, 1) * 100:.1f}%"
            )

        return takeaways[:5]

    def save_report(self, report: InsightReport) -> None:
        """Save report to database."""
        conn = self._get_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS insight_reports (
                    report_id TEXT PRIMARY KEY,
                    period_start TEXT,
                    period_end TEXT,
                    window_days INTEGER,
                    generated_at TEXT,
                    report_json TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO insight_reports
                (report_id, period_start, period_end, window_days, generated_at, report_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    report.report_id,
                    report.period_start,
                    report.period_end,
                    report.window_days,
                    report.generated_at,
                    json.dumps(report.to_dict(), ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def list_reports(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """List saved reports."""
        conn = self._get_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS insight_reports (
                    report_id TEXT PRIMARY KEY,
                    period_start TEXT,
                    period_end TEXT,
                    window_days INTEGER,
                    generated_at TEXT,
                    report_json TEXT
                )
                """
            )
            rows = conn.execute(
                "SELECT report_id, period_start, period_end, window_days, generated_at FROM insight_reports ORDER BY generated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        """Get a specific report by ID."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT report_json FROM insight_reports WHERE report_id = ?",
                (report_id,),
            ).fetchone()
            if row:
                return json.loads(row["report_json"])
            return None
        finally:
            conn.close()

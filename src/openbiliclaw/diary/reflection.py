"""日记反思模块：周报、月度反思、年度回顾、里程碑识别。

借鉴 memex、echolog 等项目的设计，基于历史日记数据自动生成
结构化的反思报告，帮助用户回顾成长轨迹、识别人生重要节点。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .models import DiaryEntry, MoodLevel

if TYPE_CHECKING:
    from .store import DiaryStore

logger = logging.getLogger(__name__)


@dataclass
class WeeklyReport:
    """周报数据结构。"""

    week_start: str
    week_end: str
    entry_count: int
    total_words: int
    mood_distribution: dict[str, int]
    dominant_mood: str
    key_events: list[str]
    highlights: list[str]
    lowlights: list[str]
    themes: list[str]
    ai_summary: str = ""
    ai_reflection: str = ""


@dataclass
class MonthlyReflection:
    """月度反思数据结构。"""

    year: int
    month: int
    entry_count: int
    total_words: int
    mood_distribution: dict[str, int]
    dominant_mood: str
    mood_trend: list[dict]
    key_events: list[str]
    milestones: list[str]
    themes: list[str]
    top_tags: list[tuple[str, int]]
    ai_summary: str = ""
    ai_reflection: str = ""
    ai_growth_insights: list[str] = field(default_factory=list)


@dataclass
class YearlyReview:
    """年度回顾数据结构。"""

    year: int
    entry_count: int
    total_words: int
    mood_distribution: dict[str, int]
    dominant_mood: str
    monthly_stats: list[dict]
    top_10_events: list[str]
    milestones: list[str]
    themes: list[str]
    top_tags: list[tuple[str, int]]
    people_met: list[str]
    places_visited: list[str]
    ai_summary: str = ""
    ai_reflection: str = ""
    ai_growth_trajectory: str = ""
    ai_lessons_learned: list[str] = field(default_factory=list)


@dataclass
class Milestone:
    """人生里程碑数据结构。"""

    date: str
    title: str
    description: str
    category: str  # career/relationship/health/finance/family/travel/education/other
    significance: float  # 0-1
    related_entries: list[int] = field(default_factory=list)


class ReflectionService:
    """日记反思服务：生成周报、月度反思、年度回顾、识别里程碑。"""

    def __init__(self, store: DiaryStore):
        self.store = store

    # ═══════════════════════════════════════════
    # 周报生成
    # ═══════════════════════════════════════════

    def get_week_range(self, date_str: str | None = None) -> tuple[str, str]:
        """获取指定日期所在周的起止日期（周一到周日）。"""
        dt = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.now()
        # 周一为一周开始
        start = dt - timedelta(days=dt.weekday())
        end = start + timedelta(days=6)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    def generate_weekly_report(self, week_start: str | None = None) -> WeeklyReport:
        """生成周报统计数据（不调用 LLM）。"""
        if week_start is None:
            week_start, _ = self.get_week_range()
        start_dt = datetime.strptime(week_start, "%Y-%m-%d")
        week_end = (start_dt + timedelta(days=6)).strftime("%Y-%m-%d")

        # 获取本周日记
        entries = self._get_entries_in_range(week_start, week_end)
        if not entries:
            return WeeklyReport(
                week_start=week_start,
                week_end=week_end,
                entry_count=0,
                total_words=0,
                mood_distribution={},
                dominant_mood="unknown",
                key_events=[],
                highlights=[],
                lowlights=[],
                themes=[],
            )

        # 统计
        total_words = sum(e.word_count for e in entries)
        mood_dist = self._count_moods(entries)
        dominant_mood = max(mood_dist, key=mood_dist.get) if mood_dist else "unknown"

        # 提取关键事件（标题非空的日记）
        key_events = [
            f"[{e.entry_date}] {e.title or e.content[:50]}"
            for e in entries
            if e.title or len(e.content) > 100
        ][:10]

        # 高光时刻（开心的日记）
        highlights = [
            f"[{e.entry_date}] {e.title or e.content[:50]}"
            for e in entries
            if e.mood in (MoodLevel.VERY_HAPPY, MoodLevel.HAPPY)
        ][:5]

        # 低谷时刻（难过/焦虑的日记）
        lowlights = [
            f"[{e.entry_date}] {e.title or e.content[:50]}"
            for e in entries
            if e.mood in (MoodLevel.SAD, MoodLevel.VERY_SAD, MoodLevel.ANXIOUS, MoodLevel.ANGRY)
        ][:5]

        # 主题（从标签提取）
        themes = self._extract_themes(entries, top_n=8)

        return WeeklyReport(
            week_start=week_start,
            week_end=week_end,
            entry_count=len(entries),
            total_words=total_words,
            mood_distribution=mood_dist,
            dominant_mood=dominant_mood,
            key_events=key_events,
            highlights=highlights,
            lowlights=lowlights,
            themes=themes,
        )

    def build_weekly_report_prompt(self, report: WeeklyReport) -> str:
        """构建周报生成的 LLM Prompt。"""
        entries_text = "\n".join(report.key_events) if report.key_events else "无"
        highlights_text = "\n".join(report.highlights) if report.highlights else "无"
        lowlights_text = "\n".join(report.lowlights) if report.lowlights else "无"
        themes_text = ", ".join(report.themes) if report.themes else "无"

        return f"""你是一位温柔的日记陪伴者，正在帮用户回顾这一周的生活。

本周时间：{report.week_start} 至 {report.week_end}
本周日记：{report.entry_count} 篇，共 {report.total_words} 字
主导情绪：{report.dominant_mood}
本周主题：{themes_text}

本周关键事件：
{entries_text}

高光时刻：
{highlights_text}

低谷时刻：
{lowlights_text}

请基于以上信息，生成一份温暖、有洞察力的周报。

要求：
1. 用第二人称「你」写作，像朋友在和用户聊天
2. 先总结这一周的整体感受，再分别说说高光和低谷
3. 找出这一周的主题和模式，给出温和的观察和建议
4. 不要说教，不要写鸡汤，要真诚、有温度
5. 信息少的时候就写短一点，不硬凑
6. 结尾可以留一句鼓励的话，但不要鸡汤

输出 JSON（不要包含任何其他文字）：
{{
  "summary": "本周整体总结（200-400字）",
  "reflection": "深度反思和观察（150-300字）",
  "suggestions": ["建议1", "建议2", "建议3"]
}}"""

    # ═══════════════════════════════════════════
    # 月度反思
    # ═══════════════════════════════════════════

    def generate_monthly_reflection(self, year: int, month: int) -> MonthlyReflection:
        """生成月度反思统计数据（不调用 LLM）。"""
        month_start = f"{year}-{month:02d}-01"
        next_month = f"{year + 1}-01-01" if month == 12 else f"{year}-{month + 1:02d}-01"
        month_end = (datetime.strptime(next_month, "%Y-%m-%d") - timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )

        entries = self._get_entries_in_range(month_start, month_end)
        if not entries:
            return MonthlyReflection(
                year=year,
                month=month,
                entry_count=0,
                total_words=0,
                mood_distribution={},
                dominant_mood="unknown",
                mood_trend=[],
                key_events=[],
                milestones=[],
                themes=[],
                top_tags=[],
            )

        # 统计
        total_words = sum(e.word_count for e in entries)
        mood_dist = self._count_moods(entries)
        dominant_mood = max(mood_dist, key=mood_dist.get) if mood_dist else "unknown"

        # 情绪趋势（按周统计）
        mood_trend = self._get_mood_trend(entries, freq="weekly")

        # 关键事件
        key_events = [
            f"[{e.entry_date}] {e.title or e.content[:50]}"
            for e in entries
            if e.title or len(e.content) > 200
        ][:15]

        # 里程碑（重大事件：标题含特定关键词或内容较长）
        milestones = self._detect_milestones_from_entries(entries)[:5]

        # 主题
        themes = self._extract_themes(entries, top_n=10)

        # 热门标签
        top_tags = self._get_top_tags(entries, top_n=10)

        return MonthlyReflection(
            year=year,
            month=month,
            entry_count=len(entries),
            total_words=total_words,
            mood_distribution=mood_dist,
            dominant_mood=dominant_mood,
            mood_trend=mood_trend,
            key_events=key_events,
            milestones=milestones,
            themes=themes,
            top_tags=top_tags,
        )

    def build_monthly_reflection_prompt(self, reflection: MonthlyReflection) -> str:
        """构建月度反思的 LLM Prompt。"""
        events_text = "\n".join(reflection.key_events) if reflection.key_events else "无"
        milestones_text = "\n".join(reflection.milestones) if reflection.milestones else "无"
        themes_text = ", ".join(reflection.themes) if reflection.themes else "无"
        tags_text = (
            ", ".join([f"{tag}({count})" for tag, count in reflection.top_tags])
            if reflection.top_tags
            else "无"
        )

        return f"""你是一位温柔的日记陪伴者，正在帮用户回顾这一个月的生活。

时间：{reflection.year}年{reflection.month}月
日记数量：{reflection.entry_count} 篇，共 {reflection.total_words} 字
主导情绪：{reflection.dominant_mood}
本月主题：{themes_text}
热门标签：{tags_text}

本月关键事件：
{events_text}

本月里程碑：
{milestones_text}

请基于以上信息，生成一份有深度的月度反思。

要求：
1. 用第二人称「你」写作，像朋友在和用户聊天
2. 先总结这个月的整体感受和变化
3. 分析这个月的情绪模式和主题
4. 识别成长和进步的地方，也温柔地指出需要关注的地方
5. 给出 2-3 条下个月的小建议
6. 不要说教，不要写鸡汤，要真诚、有温度、有洞察力
7. 信息少的时候就写短一点，不硬凑

输出 JSON（不要包含任何其他文字）：
{{
  "summary": "本月整体总结（300-500字）",
  "reflection": "深度反思和观察（200-400字）",
  "growth_insights": ["成长洞察1", "成长洞察2", "成长洞察3"],
  "suggestions": ["下月建议1", "下月建议2"]
}}"""

    # ═══════════════════════════════════════════
    # 年度回顾
    # ═══════════════════════════════════════════

    def generate_yearly_review(self, year: int) -> YearlyReview:
        """生成年度回顾统计数据（不调用 LLM）。"""
        year_start = f"{year}-01-01"
        year_end = f"{year}-12-31"

        entries = self._get_entries_in_range(year_start, year_end)
        if not entries:
            return YearlyReview(
                year=year,
                entry_count=0,
                total_words=0,
                mood_distribution={},
                dominant_mood="unknown",
                monthly_stats=[],
                top_10_events=[],
                milestones=[],
                themes=[],
                top_tags=[],
                people_met=[],
                places_visited=[],
            )

        # 统计
        total_words = sum(e.word_count for e in entries)
        mood_dist = self._count_moods(entries)
        dominant_mood = max(mood_dist, key=mood_dist.get) if mood_dist else "unknown"

        # 月度统计
        monthly_stats = []
        for month in range(1, 13):
            month_entries = [e for e in entries if e.entry_date.startswith(f"{year}-{month:02d}")]
            if month_entries:
                month_moods = self._count_moods(month_entries)
                monthly_stats.append(
                    {
                        "month": month,
                        "entry_count": len(month_entries),
                        "total_words": sum(e.word_count for e in month_entries),
                        "dominant_mood": max(month_moods, key=month_moods.get)
                        if month_moods
                        else "unknown",
                    }
                )

        # 十大事件（按字数和标题筛选）
        sorted_entries = sorted(entries, key=lambda e: e.word_count, reverse=True)
        top_10_events = [
            f"[{e.entry_date}] {e.title or e.content[:50]}" for e in sorted_entries[:10]
        ]

        # 里程碑
        milestones = self._detect_milestones_from_entries(entries)[:10]

        # 主题
        themes = self._extract_themes(entries, top_n=15)

        # 热门标签
        top_tags = self._get_top_tags(entries, top_n=15)

        # 认识的人（从人物表获取）
        people_met = self._get_people_in_range(year_start, year_end)

        # 去过的地方（从标签提取）
        places_visited = [
            tag for tag, _ in top_tags if any(kw in tag for kw in ["旅行", "旅游", "城市", "地方"])
        ]

        return YearlyReview(
            year=year,
            entry_count=len(entries),
            total_words=total_words,
            mood_distribution=mood_dist,
            dominant_mood=dominant_mood,
            monthly_stats=monthly_stats,
            top_10_events=top_10_events,
            milestones=milestones,
            themes=themes,
            top_tags=top_tags,
            people_met=people_met,
            places_visited=places_visited,
        )

    def build_yearly_review_prompt(self, review: YearlyReview) -> str:
        """构建年度回顾的 LLM Prompt。"""
        events_text = "\n".join(review.top_10_events) if review.top_10_events else "无"
        milestones_text = "\n".join(review.milestones) if review.milestones else "无"
        themes_text = ", ".join(review.themes) if review.themes else "无"
        tags_text = (
            ", ".join([f"{tag}({count})" for tag, count in review.top_tags])
            if review.top_tags
            else "无"
        )
        people_text = ", ".join(review.people_met) if review.people_met else "无"
        places_text = ", ".join(review.places_visited) if review.places_visited else "无"

        monthly_text = (
            "\n".join(
                [
                    f"{m['month']}月：{m['entry_count']}篇，{m['total_words']}字，主导情绪{m['dominant_mood']}"
                    for m in review.monthly_stats
                ]
            )
            if review.monthly_stats
            else "无"
        )

        return f"""你是一位温柔的日记陪伴者，正在帮用户做年度回顾。

年份：{review.year}年
日记数量：{review.entry_count} 篇，共 {review.total_words} 字
主导情绪：{review.dominant_mood}
年度主题：{themes_text}
热门标签：{tags_text}
认识的人：{people_text}
去过的地方：{places_text}

月度统计：
{monthly_text}

年度十大事件：
{events_text}

年度里程碑：
{milestones_text}

请基于以上信息，生成一份有深度、有温度的年度回顾。

要求：
1. 用第二人称「你」写作，像朋友在和用户聊天
2. 先总结这一年的整体感受和变化
3. 按季度或主题回顾这一年的重要时刻
4. 分析成长轨迹：这一年最大的变化是什么？学到了什么？
5. 温柔地指出遗憾和可以改进的地方
6. 给出 3-5 条经验教训，以及对下一年的期许
7. 不要说教，不要写鸡汤，要真诚、有温度、有洞察力
8. 信息少的时候就写短一点，不硬凑

输出 JSON（不要包含任何其他文字）：
{{
  "summary": "年度整体总结（500-800字）",
  "reflection": "深度反思和观察（300-500字）",
  "growth_trajectory": "成长轨迹分析（200-400字）",
  "lessons_learned": ["经验教训1", "经验教训2", "经验教训3", "经验教训4", "经验教训5"],
  "hopes_for_next_year": "对下一年的期许（100-200字）"
}}"""

    # ═══════════════════════════════════════════
    # 里程碑识别
    # ═══════════════════════════════════════════

    def detect_milestones(
        self, start_date: str | None = None, end_date: str | None = None
    ) -> list[Milestone]:
        """识别人生里程碑（基于规则，不调用 LLM）。"""
        if start_date is None:
            start_date = "2000-01-01"
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        entries = self._get_entries_in_range(start_date, end_date)
        if not entries:
            return []

        milestones = []
        milestone_keywords = {
            "career": [
                "入职",
                "离职",
                "跳槽",
                "升职",
                "加薪",
                "转正",
                "面试",
                "offer",
                "工作",
                "项目",
                "创业",
            ],
            "relationship": [
                "结婚",
                "恋爱",
                "分手",
                "表白",
                "相亲",
                "订婚",
                "纪念日",
                "男朋友",
                "女朋友",
                "老公",
                "老婆",
            ],
            "health": ["生病", "住院", "手术", "体检", "康复", "怀孕", "生产", "宝宝", "出生"],
            "finance": ["买房", "买车", "投资", "理财", "贷款", "存款", "涨薪", "奖金"],
            "family": ["搬家", "装修", "团聚", "春节", "回家", "父母", "孩子", "生日"],
            "travel": ["旅行", "旅游", "出国", "自驾游", "度假", "景点"],
            "education": ["毕业", "入学", "考试", "学习", "证书", "培训", "考研", "留学"],
        }

        for entry in entries:
            content = (entry.title or "") + " " + entry.content
            matched_categories = []
            for category, keywords in milestone_keywords.items():
                if any(kw in content for kw in keywords):
                    matched_categories.append(category)

            if matched_categories and (entry.title or len(entry.content) > 150):
                # 计算重要性（基于内容长度和匹配的类别数）
                significance = min(
                    1.0, (len(entry.content) / 1000) * 0.5 + len(matched_categories) * 0.2
                )

                milestones.append(
                    Milestone(
                        date=entry.entry_date,
                        title=entry.title or content[:50],
                        description=entry.content[:200]
                        + ("..." if len(entry.content) > 200 else ""),
                        category=matched_categories[0],
                        significance=significance,
                        related_entries=[entry.id],
                    )
                )

        # 按重要性排序
        milestones.sort(key=lambda m: m.significance, reverse=True)
        return milestones[:50]

    # ═══════════════════════════════════════════
    # 内部辅助方法
    # ═══════════════════════════════════════════

    def _get_entries_in_range(self, start_date: str, end_date: str) -> list[DiaryEntry]:
        """获取指定日期范围内的日记。"""
        all_entries = self.store.list_entries(limit=5000)
        return [e for e in all_entries if start_date <= e.entry_date <= end_date]

    def _count_moods(self, entries: list[DiaryEntry]) -> dict[str, int]:
        """统计情绪分布。"""
        mood_count: dict[str, int] = {}
        for e in entries:
            mood = e.mood.value if e.mood else "unknown"
            mood_count[mood] = mood_count.get(mood, 0) + 1
        return mood_count

    def _get_mood_trend(self, entries: list[DiaryEntry], freq: str = "weekly") -> list[dict]:
        """获取情绪趋势（按周或按月）。"""
        from collections import defaultdict

        grouped: dict[str, list[DiaryEntry]] = defaultdict(list)
        for e in entries:
            dt = datetime.strptime(e.entry_date, "%Y-%m-%d")
            if freq == "weekly":
                # 按周分组（周一为开始）
                week_start = dt - timedelta(days=dt.weekday())
                key = week_start.strftime("%Y-%m-%d")
            else:
                key = e.entry_date[:7]  # YYYY-MM
            grouped[key].append(e)

        trend = []
        for key in sorted(grouped.keys()):
            group_entries = grouped[key]
            moods = self._count_moods(group_entries)
            dominant = max(moods, key=moods.get) if moods else "unknown"
            trend.append(
                {
                    "period": key,
                    "entry_count": len(group_entries),
                    "dominant_mood": dominant,
                    "mood_distribution": moods,
                }
            )
        return trend

    def _extract_themes(self, entries: list[DiaryEntry], top_n: int = 10) -> list[str]:
        """从日记标签中提取主题。"""
        from collections import Counter

        all_tags = []
        for e in entries:
            if e.tags:
                all_tags.extend(e.tags)
        counter = Counter(all_tags)
        return [tag for tag, _ in counter.most_common(top_n)]

    def _get_top_tags(self, entries: list[DiaryEntry], top_n: int = 10) -> list[tuple[str, int]]:
        """获取热门标签。"""
        from collections import Counter

        all_tags = []
        for e in entries:
            if e.tags:
                all_tags.extend(e.tags)
        counter = Counter(all_tags)
        return counter.most_common(top_n)

    def _detect_milestones_from_entries(self, entries: list[DiaryEntry]) -> list[str]:
        """从日记中识别里程碑（简单规则）。"""
        milestone_keywords = [
            "入职",
            "离职",
            "跳槽",
            "升职",
            "结婚",
            "恋爱",
            "分手",
            "生病",
            "住院",
            "手术",
            "买房",
            "买车",
            "搬家",
            "装修",
            "旅行",
            "旅游",
            "出国",
            "毕业",
            "入学",
            "宝宝",
            "出生",
            "纪念日",
            "生日",
            "春节",
            "团聚",
        ]
        milestones = []
        for e in entries:
            content = (e.title or "") + " " + e.content
            if any(kw in content for kw in milestone_keywords) and (
                e.title or len(e.content) > 150
            ):
                milestones.append(f"[{e.entry_date}] {e.title or e.content[:50]}")
        return milestones

    def _get_people_in_range(self, start_date: str, end_date: str) -> list[str]:
        """获取指定日期范围内出现的人物。"""
        try:
            persons = self.store.list_persons(limit=100)
            return [p.name for p in persons]
        except Exception:
            return []

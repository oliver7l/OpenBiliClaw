"""日记数据洞察模块。

提供情绪趋势、写作频率、字数统计、连续打卡、年度/月度洞察报告等
数据分析能力。借鉴 Journiv 的情绪追踪、memex 的洞察引擎、
Night-Journal 的记忆机制设计。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .models import MoodLevel

# ─── 中文情绪关键词词典（用于无 AI 分析时的快速情绪推断） ───

_POSITIVE_WORDS = {
    "开心",
    "高兴",
    "快乐",
    "愉快",
    "幸福",
    "满足",
    "欣慰",
    "感动",
    "兴奋",
    "激动",
    "期待",
    "希望",
    "乐观",
    "轻松",
    "平静",
    "安宁",
    "舒服",
    "惬意",
    "温馨",
    "温暖",
    "美好",
    "棒",
    "好",
    "赞",
    "喜欢",
    "爱",
    "成功",
    "进步",
    "成长",
    "收获",
    "惊喜",
    "感恩",
    "珍惜",
}

_NEGATIVE_WORDS = {
    "难过",
    "伤心",
    "悲伤",
    "痛苦",
    "焦虑",
    "紧张",
    "担心",
    "害怕",
    "恐惧",
    "愤怒",
    "生气",
    "烦躁",
    "郁闷",
    "低落",
    "沮丧",
    "失望",
    "绝望",
    "孤独",
    "寂寞",
    "空虚",
    "无聊",
    "疲惫",
    "累",
    "烦",
    "压力",
    "纠结",
    "矛盾",
    "后悔",
    "遗憾",
    "愧疚",
    "自责",
    "自卑",
    "迷茫",
    "困惑",
    "无助",
    "无力",
    "崩溃",
    "想哭",
    "眼泪",
}

_ANXIOUS_WORDS = {
    "焦虑",
    "紧张",
    "担心",
    "害怕",
    "恐惧",
    "慌",
    "忐忑",
    "不安",
    "忧心",
    "烦恼",
    "烦躁",
    "心急",
    "急迫",
    "压力",
    "纠结",
}

_ANGRY_WORDS = {
    "愤怒",
    "生气",
    "恼火",
    "烦躁",
    "暴怒",
    "气愤",
    "不爽",
    "火大",
    "发脾气",
    "怒吼",
    "咆哮",
    "恨",
    "讨厌",
    "厌恶",
}


@dataclass
class MoodTrendPoint:
    """情绪趋势数据点。"""

    period: str  # 月份或年份，如 "2024-01" 或 "2024"
    avg_score: float  # 平均情绪分 -1~1
    entry_count: int  # 日记篇数
    mood_distribution: dict[str, int]  # 情绪分布


@dataclass
class WritingStreak:
    """连续打卡信息。"""

    current_streak: int  # 当前连续天数
    longest_streak: int  # 最长连续天数
    total_days: int  # 总写作天数
    this_week_count: int  # 本周写作天数
    this_month_count: int  # 本月写作天数


@dataclass
class YearlyInsight:
    """年度洞察报告。"""

    year: int
    entry_count: int
    total_words: int
    avg_words: float
    mood_summary: str
    top_themes: list[str]
    top_people: list[str]
    highlight_quotes: list[str]
    growth_summary: str
    word_cloud: list[tuple[str, int]]


class MoodAnalyzer:
    """基于关键词的快速情绪分析器（无 LLM 时的 fallback）。"""

    @staticmethod
    def analyze_text(content: str) -> tuple[MoodLevel, float]:
        """分析文本情绪，返回 (情绪等级, 情绪分值 -1~1)。"""
        if not content:
            return MoodLevel.UNKNOWN, 0.0

        text = content.lower()
        pos_count = sum(1 for w in _POSITIVE_WORDS if w in text)
        neg_count = sum(1 for w in _NEGATIVE_WORDS if w in text)
        anxious_count = sum(1 for w in _ANXIOUS_WORDS if w in text)
        angry_count = sum(1 for w in _ANGRY_WORDS if w in text)

        total = pos_count + neg_count
        if total == 0:
            return MoodLevel.NEUTRAL, 0.0

        score = (pos_count - neg_count) / total
        score = max(-1.0, min(1.0, score))

        if anxious_count > neg_count * 0.5 and score < 0.2:
            return MoodLevel.ANXIOUS, score
        if angry_count > neg_count * 0.5 and score < 0.2:
            return MoodLevel.ANGRY, score
        if score > 0.5:
            return MoodLevel.VERY_HAPPY, score
        if score > 0.1:
            return MoodLevel.HAPPY, score
        if score > -0.1:
            return MoodLevel.NEUTRAL, score
        if score > -0.5:
            return MoodLevel.SAD, score
        return MoodLevel.VERY_SAD, score


class DiaryInsightsService:
    """日记数据洞察服务。

    提供统计分析、趋势计算、洞察报告生成等能力。
    不直接操作数据库，通过传入的 DiaryStore 获取数据。
    """

    def __init__(self, store: Any) -> None:
        self._store = store

    # ─── 情绪趋势 ───

    def get_mood_trend(
        self,
        granularity: str = "month",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[MoodTrendPoint]:
        """获取情绪趋势数据。

        Args:
            granularity: "month" 按月，"year" 按年
            start_date: 起始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD

        """
        entries = self._store.list_entries(
            limit=10000,
            start_date=start_date,
            end_date=end_date,
        )

        # 按周期分组
        groups: dict[str, list] = defaultdict(list)
        for entry in entries:
            period = entry.entry_date[:4] if granularity == "year" else entry.entry_date[:7]
            groups[period].append(entry)

        result = []
        for period in sorted(groups.keys()):
            group = groups[period]
            # 计算平均情绪分（优先用已分析的 mood_score，否则用关键词推断）
            scores = []
            mood_dist: Counter = Counter()
            for entry in group:
                if entry.mood != MoodLevel.UNKNOWN and entry.mood_score != 0:
                    scores.append(entry.mood_score)
                    mood_dist[entry.mood.value] += 1
                else:
                    # 用关键词快速推断
                    _, score = MoodAnalyzer.analyze_text(entry.content)
                    scores.append(score)
                    mood_dist["inferred"] += 1

            avg_score = sum(scores) / len(scores) if scores else 0.0
            result.append(
                MoodTrendPoint(
                    period=period,
                    avg_score=round(avg_score, 3),
                    entry_count=len(group),
                    mood_distribution=dict(mood_dist),
                )
            )

        return result

    # ─── 写作频率与连续打卡 ───

    def get_writing_streak(self) -> WritingStreak:
        """获取写作连续打卡统计。"""
        entries = self._store.list_entries(limit=10000)
        dates = {e.entry_date for e in entries}

        if not dates:
            return WritingStreak(0, 0, 0, 0, 0)

        sorted_dates = sorted(dates)

        # 最长连续天数
        longest = 1
        current = 1
        for i in range(1, len(sorted_dates)):
            prev = datetime.strptime(sorted_dates[i - 1], "%Y-%m-%d")
            curr = datetime.strptime(sorted_dates[i], "%Y-%m-%d")
            if (curr - prev).days == 1:
                current += 1
                longest = max(longest, current)
            else:
                current = 1

        # 当前连续天数（从最新日期往回数）
        today = datetime.now().strftime("%Y-%m-%d")
        current_streak = 0
        check_date = datetime.strptime(today, "%Y-%m-%d")
        # 如果今天没写，从昨天开始算
        if today not in dates:
            check_date -= timedelta(days=1)
        while check_date.strftime("%Y-%m-%d") in dates:
            current_streak += 1
            check_date -= timedelta(days=1)

        # 本周/本月写作天数
        now = datetime.now()
        week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
        month_start = now.strftime("%Y-%m-01")
        this_week = sum(1 for d in dates if d >= week_start)
        this_month = sum(1 for d in dates if d >= month_start)

        return WritingStreak(
            current_streak=current_streak,
            longest_streak=longest,
            total_days=len(dates),
            this_week_count=this_week,
            this_month_count=this_month,
        )

    # ─── 字数趋势 ───

    def get_word_trend(self, granularity: str = "month") -> list[dict[str, Any]]:
        """获取字数趋势数据。"""
        entries = self._store.list_entries(limit=10000)

        groups: dict[str, list[int]] = defaultdict(list)
        for entry in entries:
            period = entry.entry_date[:4] if granularity == "year" else entry.entry_date[:7]
            groups[period].append(entry.word_count)

        result = []
        for period in sorted(groups.keys()):
            counts = groups[period]
            result.append(
                {
                    "period": period,
                    "total_words": sum(counts),
                    "avg_words": round(sum(counts) / len(counts), 1),
                    "entry_count": len(counts),
                }
            )
        return result

    # ─── 高频关键词（简易词云） ───

    def get_top_keywords(
        self,
        limit: int = 50,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[tuple[str, int]]:
        """获取高频关键词（简易中文分词）。"""
        entries = self._store.list_entries(
            limit=10000,
            start_date=start_date,
            end_date=end_date,
        )

        # 停用词
        stop_words = {
            "的",
            "了",
            "是",
            "在",
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
            "这个",
            "那个",
            "什么",
            "怎么",
            "为什么",
            "因为",
            "所以",
            "但是",
            "然后",
            "还是",
            "或者",
            "如果",
            "虽然",
            "今天",
            "昨天",
            "明天",
            "现在",
            "时候",
            "一下",
            "一些",
            "有点",
            "比较",
            "非常",
            "特别",
            "真的",
            "确实",
            "其实",
            "可能",
            "应该",
            "可以",
            "能够",
            "需要",
            "想要",
            "觉得",
            "感觉",
            "知道",
            "认为",
            "这样",
            "那样",
            "这么",
            "那么",
            "一起",
            "出来",
            "回来",
            "过去",
            "起来",
            "下来",
            "上来",
            "过来",
            "以后",
            "以前",
            "之前",
            "之后",
            "里面",
            "外面",
            "上面",
            "下面",
            "前面",
            "后面",
            "中间",
            "旁边",
        }

        counter: Counter = Counter()
        for entry in entries:
            # 简易分词：提取 2-4 字的中文词组
            text = re.sub(r"[^\u4e00-\u9fa5]", " ", entry.content)
            words = text.split()
            for word in words:
                # 提取 2-4 字的词
                for length in range(2, min(5, len(word) + 1)):
                    for i in range(len(word) - length + 1):
                        w = word[i : i + length]
                        if w not in stop_words and len(w) >= 2:
                            counter[w] += 1

        return counter.most_common(limit)

    # ─── 年度洞察报告（基于已有数据的统计分析，不含 LLM） ───

    def get_yearly_insight_stats(self, year: int) -> dict[str, Any]:
        """获取年度洞察的统计数据（用于前端展示或传给 LLM 生成报告）。"""
        start = f"{year}-01-01"
        end = f"{year}-12-31"
        entries = self._store.list_entries(limit=10000, start_date=start, end_date=end)

        if not entries:
            return {"year": year, "entry_count": 0, "message": "该年度暂无日记"}

        total_words = sum(e.word_count for e in entries)
        avg_words = round(total_words / len(entries), 1)

        # 情绪统计
        mood_scores = []
        mood_dist: Counter = Counter()
        for entry in entries:
            if entry.mood != MoodLevel.UNKNOWN:
                mood_scores.append(entry.mood_score)
                mood_dist[entry.mood.value] += 1
            else:
                _, score = MoodAnalyzer.analyze_text(entry.content)
                mood_scores.append(score)

        avg_mood = round(sum(mood_scores) / len(mood_scores), 3) if mood_scores else 0

        # 高频关键词
        keywords = self.get_top_keywords(limit=30, start_date=start, end_date=end)

        # 月度分布
        monthly_dist: Counter = Counter()
        for entry in entries:
            monthly_dist[entry.entry_date[:7]] += 1

        # 最长/最短日记
        sorted_by_words = sorted(entries, key=lambda e: e.word_count, reverse=True)
        longest = sorted_by_words[0] if sorted_by_words else None
        shortest = sorted_by_words[-1] if sorted_by_words else None

        return {
            "year": year,
            "entry_count": len(entries),
            "total_words": total_words,
            "avg_words": avg_words,
            "avg_mood_score": avg_mood,
            "mood_distribution": dict(mood_dist),
            "monthly_distribution": dict(sorted(monthly_dist.items())),
            "top_keywords": keywords,
            "longest_entry": {
                "date": longest.entry_date,
                "title": longest.title,
                "word_count": longest.word_count,
            }
            if longest
            else None,
            "shortest_entry": {
                "date": shortest.entry_date,
                "title": shortest.title,
                "word_count": shortest.word_count,
            }
            if shortest
            else None,
            "date_range": {
                "earliest": entries[0].entry_date,
                "latest": entries[-1].entry_date,
            },
        }

    # ─── 生成年度洞察报告的 LLM Prompt ───

    @staticmethod
    def build_yearly_report_prompt(year: int, stats: dict[str, Any]) -> str:
        """构建年度洞察报告的 LLM Prompt。

        借鉴 Night-Journal 的写作风格：温柔真实，第一人称，不说教，不鸡汤。
        """
        keywords_str = ", ".join(f"{w}({c})" for w, c in stats.get("top_keywords", [])[:20])
        monthly_str = ", ".join(
            f"{m}:{c}篇" for m, c in list(stats.get("monthly_distribution", {}).items())[:12]
        )

        return f"""你是一个温柔的年度回顾者，正在帮用户回顾 {year} 年的日记。

请根据以下统计数据，生成一份温暖、真实、有洞察力的年度回顾报告。

## 年度数据
- 日记篇数：{stats.get("entry_count", 0)} 篇
- 总字数：{stats.get("total_words", 0)} 字
- 平均每篇：{stats.get("avg_words", 0)} 字
- 平均情绪分：{stats.get("avg_mood_score", 0)}（-1到1，正数偏积极）
- 时间范围：{stats.get("date_range", {}).get("earliest", "?")} ~ {stats.get("date_range", {}).get("latest", "?")}

## 月度分布
{monthly_str}

## 高频关键词
{keywords_str}

## 情绪分布
{stats.get("mood_distribution", {})}

## 最长日记
{stats.get("longest_entry", {})}

## 最短日记
{stats.get("shortest_entry", {})}

请按以下结构生成报告（用中文，第一人称"我"，温柔真实的语气）：

1. **年度摘要**（200字以内）：用温柔的笔触概括这一年
2. **情绪轨迹**：描述这一年的情绪变化，有高潮也有低谷
3. **高频主题**：从关键词中提炼出3-5个年度主题
4. **成长洞察**：这一年最大的成长和变化是什么
5. **给未来的自己**：一句温暖的话，留给明年的自己

要求：
- 不说教，不鸡汤，不强行积极
- 允许有遗憾和不舍，真实最重要
- 用第一人称"我"，像在跟自己对话
- 不要编造具体事件，只基于统计数据做概括性描述
- 总字数控制在800字以内"""

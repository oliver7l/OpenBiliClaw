"""主动洞察引擎模块。

主动发现日记中的有趣模式、历史重现、晨间简报和开放循环追踪。
参考 Dear Agent 的 recall/reflect 设计、nightDiary 的模式发现。
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .store import DiaryStore

logger = logging.getLogger(__name__)


# ─── 数据结构 ────────────────────────────────────────────────────────


@dataclass
class MemoryOnThisDay:
    """历史上的今天。"""

    target_date: str
    entries: list[dict[str, Any]] = field(default_factory=list)
    years_span: list[int] = field(default_factory=list)
    summary: str = ""


@dataclass
class PatternInsight:
    """模式洞察。"""

    pattern_type: str
    title: str
    description: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    severity: str = "info"
    detected_at: str = ""


@dataclass
class MorningBriefing:
    """晨间简报。"""

    briefing_date: str
    yesterday_summary: dict[str, Any] = field(default_factory=dict)
    today_reminders: list[str] = field(default_factory=list)
    historical_context: list[str] = field(default_factory=list)
    mood_forecast: str = ""
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OpenLoop:
    """开放循环。"""

    loop_id: str
    content: str
    loop_type: str
    created_date: str
    last_mentioned_date: str | None = None
    mention_count: int = 1
    status: str = "open"
    related_entries: list[str] = field(default_factory=list)
    priority: str = "medium"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InsightReport:
    """综合洞察报告。"""

    report_date: str
    memory_on_this_day: MemoryOnThisDay | None = None
    patterns: list[PatternInsight] = field(default_factory=list)
    morning_briefing: MorningBriefing | None = None
    open_loops: list[OpenLoop] = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_date": self.report_date,
            "memory_on_this_day": asdict(self.memory_on_this_day)
            if self.memory_on_this_day
            else None,
            "patterns": [asdict(p) for p in self.patterns],
            "morning_briefing": self.morning_briefing.to_dict() if self.morning_briefing else None,
            "open_loops": [loop.to_dict() for loop in self.open_loops],
            "generated_at": self.generated_at,
        }


# ─── 关键词模式 ──────────────────────────────────────────────────────

PROMISE_PATTERNS = [
    r"我(要|会|一定|必须|得|应该)(.{0,20})",
    r"(承诺|答应|保证|发誓)(.{0,30})",
    r"下次(一定|要|会)(.{0,20})",
    r"以后(要|会|一定)(.{0,20})",
]

GOAL_PATTERNS = [
    r"(目标|计划|打算|想要|希望|梦想)(.{0,30})",
    r"今年(要|想|计划)(.{0,30})",
    r"这个月(要|想|计划)(.{0,30})",
    r"(减肥|健身|学习|存钱|旅行|换工作)(.{0,20})",
]

TODO_PATTERNS = [
    r"(待办|todo|TODO|要做|需要做|得做)(.{0,30})",
    r"明天(要|得|必须)(.{0,30})",
    r"下周(要|得|必须)(.{0,30})",
    r"(记得|别忘了|不要忘记)(.{0,30})",
]

QUESTION_PATTERNS = [
    r"(为什么|怎么|如何|能不能|可不可以|是不是|有没有)(.{0,30})[?？]",
]

IDEA_PATTERNS = [
    r"(想法|主意|灵感|创意|点子)(.{0,30})",
    r"突然想到(.{0,30})",
]

COMPLETION_PATTERNS = [
    r"(完成|搞定|做完|实现|达成|做到了)(.{0,20})",
    r"终于(.{0,20})(了|啦|呢)",
]

ABANDON_PATTERNS = [
    r"(放弃|不做了|算了|不想了|搁置)(.{0,20})",
    r"以后再说(.{0,10})",
]


# ─── 主动洞察引擎服务 ────────────────────────────────────────────────


class InsightEngineService:
    """主动洞察引擎服务。"""

    def __init__(self, store: DiaryStore):
        self.store = store
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """确保数据库表存在。"""
        conn = self.store.conn
        conn.execute("""
            CREATE TABLE IF NOT EXISTS diary_insight_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_type TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                evidence TEXT,
                confidence REAL DEFAULT 0,
                severity TEXT DEFAULT 'info',
                detected_date TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS diary_morning_briefings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                briefing_date TEXT UNIQUE NOT NULL,
                content TEXT NOT NULL,
                generated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS diary_open_loops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                loop_id TEXT UNIQUE NOT NULL,
                content TEXT NOT NULL,
                loop_type TEXT NOT NULL,
                created_date TEXT NOT NULL,
                last_mentioned_date TEXT,
                mention_count INTEGER DEFAULT 1,
                status TEXT DEFAULT 'open',
                related_entries TEXT,
                priority TEXT DEFAULT 'medium',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()

    # ─── 历史上的今天 ────────────────────────────────────────────────

    def get_memory_on_this_day(self, target_date: str | None = None) -> MemoryOnThisDay:
        """获取历史上的今天。"""
        if target_date is None:
            target_date = datetime.now().strftime("%Y-%m-%d")

        month_day = target_date[5:]

        conn = self.store.conn
        rows = conn.execute(
            """
            SELECT id, title, content, entry_date, mood, source
            FROM diary_entries
            WHERE substr(entry_date, 6, 5) = ?
            AND entry_date != ?
            ORDER BY entry_date DESC
            """,
            (month_day, target_date),
        ).fetchall()

        entries = []
        years = set()
        for row in rows:
            entry_date = row["entry_date"]
            year = int(entry_date[:4])
            years.add(year)
            entries.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "content": row["content"][:200] if row["content"] else "",
                    "entry_date": entry_date,
                    "years_ago": datetime.now().year - year,
                    "mood": row["mood"],
                    "source": row["source"],
                }
            )

        if entries:
            years_list = sorted(years)
            if len(years_list) > 1:
                summary = (
                    f"在过去的 {len(years_list)} 年里"
                    f"（{years_list[0]}-{years_list[-1]}），"
                    f"这一天共有 {len(entries)} 篇日记。"
                )
            else:
                summary = f"{years_list[0]} 年的这一天，你写了 {len(entries)} 篇日记。"
        else:
            summary = "历史上的今天没有日记记录。"

        return MemoryOnThisDay(
            target_date=target_date,
            entries=entries,
            years_span=sorted(years),
            summary=summary,
        )

    # ─── 模式发现 ────────────────────────────────────────────────────

    def discover_patterns(self, lookback_days: int = 90) -> list[PatternInsight]:
        """发现日记中的模式。"""
        patterns: list[PatternInsight] = []
        now = datetime.now()
        start_date = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

        conn = self.store.conn
        rows = conn.execute(
            """
            SELECT id, title, content, entry_date, mood, word_count
            FROM diary_entries
            WHERE entry_date >= ?
            ORDER BY entry_date ASC
            """,
            (start_date,),
        ).fetchall()

        if not rows:
            return patterns

        entries = [dict(row) for row in rows]

        patterns.extend(self._discover_emotion_patterns(entries))
        patterns.extend(self._discover_time_patterns(entries))
        patterns.extend(self._discover_topic_patterns(entries))
        patterns.extend(self._discover_relationship_patterns(entries))

        patterns.sort(key=lambda p: -p.confidence)
        self._save_patterns(patterns)
        return patterns

    def _discover_emotion_patterns(self, entries: list[dict]) -> list[PatternInsight]:
        """发现情绪模式。"""
        patterns = []

        weekday_moods: dict[int, list[float]] = defaultdict(list)
        for entry in entries:
            if entry.get("mood") is not None:
                try:
                    weekday = datetime.strptime(entry["entry_date"], "%Y-%m-%d").weekday()
                    weekday_moods[weekday].append(float(entry["mood"]))
                except (ValueError, TypeError):
                    pass

        weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        if weekday_moods:
            avg_moods = {
                w: sum(moods) / len(moods) for w, moods in weekday_moods.items() if len(moods) >= 3
            }
            if avg_moods:
                best_day = max(avg_moods, key=avg_moods.get)
                worst_day = min(avg_moods, key=avg_moods.get)
                mood_range = avg_moods[best_day] - avg_moods[worst_day]

                if mood_range > 0.3:
                    patterns.append(
                        PatternInsight(
                            pattern_type="emotion",
                            title=f"情绪的星期规律：{weekday_names[best_day]}最开心，{weekday_names[worst_day]}最低落",
                            description=(
                                f"过去 {len(entries)} 天的数据分析显示，你的情绪有明显的星期规律。"
                                f"{weekday_names[best_day]}平均情绪分最高（{avg_moods[best_day]:.2f}），"
                                f"{weekday_names[worst_day]}最低（{avg_moods[worst_day]:.2f}），"
                                f"相差 {mood_range:.2f}。"
                            ),
                            confidence=min(0.9, mood_range * 2),
                            severity="interesting",
                            detected_at=datetime.now().isoformat(),
                        )
                    )

        return patterns

    def _discover_time_patterns(self, entries: list[dict]) -> list[PatternInsight]:
        """发现时间模式。"""
        patterns = []

        weekday_counts: dict[int, int] = Counter()
        for entry in entries:
            try:
                weekday = datetime.strptime(entry["entry_date"], "%Y-%m-%d").weekday()
                weekday_counts[weekday] += 1
            except (ValueError, TypeError):
                pass

        if weekday_counts:
            total = sum(weekday_counts.values())
            most_active_day = max(weekday_counts, key=weekday_counts.get)
            least_active_day = min(weekday_counts, key=weekday_counts.get)
            weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

            most_pct = weekday_counts[most_active_day] / total * 100
            least_pct = weekday_counts[least_active_day] / total * 100

            if most_pct - least_pct > 15:
                patterns.append(
                    PatternInsight(
                        pattern_type="time",
                        title=f"写作时间规律：{weekday_names[most_active_day]}写得最多",
                        description=(
                            f"过去 {len(entries)} 天中，{weekday_names[most_active_day]}写了 "
                            f"{weekday_counts[most_active_day]} 篇（{most_pct:.0f}%），"
                            f"{weekday_names[least_active_day]}只写了 "
                            f"{weekday_counts[least_active_day]} 篇（{least_pct:.0f}%）。"
                        ),
                        confidence=0.8,
                        severity="info",
                        detected_at=datetime.now().isoformat(),
                    )
                )

        return patterns

    def _discover_topic_patterns(self, entries: list[dict]) -> list[PatternInsight]:
        """发现主题模式。"""
        patterns = []

        topic_keywords = {
            "工作": [
                "工作",
                "上班",
                "加班",
                "同事",
                "老板",
                "项目",
                "会议",
                "代码",
                "面试",
                "离职",
            ],
            "家庭": ["妈妈", "爸爸", "老公", "老婆", "孩子", "儿子", "女儿", "家", "家人"],
            "学习": ["学习", "读书", "看书", "课程", "考试", "知识", "技能"],
            "健康": ["身体", "健康", "生病", "医院", "医生", "睡觉", "失眠", "运动", "健身"],
            "情绪": ["开心", "难过", "焦虑", "压力", "烦躁", "郁闷", "幸福", "感动"],
            "旅行": ["旅行", "旅游", "出去玩", "景点", "酒店", "机票"],
            "财务": ["钱", "花钱", "工资", "存钱", "消费", "买"],
            "社交": ["朋友", "聚会", "吃饭", "聊天", "闺蜜", "哥们"],
        }

        topic_counts: Counter[str] = Counter()
        for entry in entries:
            content = (entry.get("content") or "") + (entry.get("title") or "")
            for topic, keywords in topic_keywords.items():
                for keyword in keywords:
                    if keyword in content:
                        topic_counts[topic] += 1
                        break

        if topic_counts:
            total = sum(topic_counts.values())
            top_topics = topic_counts.most_common(3)
            topic_names = [t[0] for t in top_topics]
            topic_pcts = [t[1] / total * 100 for t in top_topics]

            patterns.append(
                PatternInsight(
                    pattern_type="topic",
                    title=f"近期关注焦点：{'、'.join(topic_names)}",
                    description=(
                        f"过去 {len(entries)} 天的日记中，出现频率最高的话题是 "
                        f"{topic_names[0]}（{topic_pcts[0]:.0f}%），"
                        f"其次是 {topic_names[1]}（{topic_pcts[1]:.0f}%）和 "
                        f"{topic_names[2]}（{topic_pcts[2]:.0f}%）。"
                    ),
                    confidence=0.85,
                    severity="info",
                    detected_at=datetime.now().isoformat(),
                )
            )

        return patterns

    def _discover_relationship_patterns(self, entries: list[dict]) -> list[PatternInsight]:
        """发现人际关系模式。"""
        patterns = []

        common_people = [
            "艳艳",
            "乐乐",
            "妈妈",
            "爸爸",
            "老公",
            "老婆",
            "狄胖胖",
            "童先海",
            "周英",
            "朋友",
            "同事",
        ]

        person_counts: Counter[str] = Counter()
        for entry in entries:
            content = (entry.get("content") or "") + (entry.get("title") or "")
            for person in common_people:
                if person in content:
                    person_counts[person] += 1

        if person_counts:
            top_person = person_counts.most_common(1)[0]
            if top_person[1] >= 5:
                patterns.append(
                    PatternInsight(
                        pattern_type="relationship",
                        title=f"最常提及的人：{top_person[0]}（{top_person[1]} 次）",
                        description=(
                            f"过去 {len(entries)} 天的日记中，{top_person[0]} 被提及了 "
                            f"{top_person[1]} 次，是你生活中最重要的人。"
                        ),
                        confidence=0.9,
                        severity="interesting",
                        detected_at=datetime.now().isoformat(),
                    )
                )

        return patterns

    def _save_patterns(self, patterns: list[PatternInsight]) -> None:
        """保存模式到数据库。"""
        if not patterns:
            return
        conn = self.store.conn
        for p in patterns:
            conn.execute(
                """
                INSERT OR REPLACE INTO diary_insight_patterns
                (pattern_type, title, description, evidence,
                 confidence, severity, detected_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    p.pattern_type,
                    p.title,
                    p.description,
                    json.dumps(p.evidence, ensure_ascii=False),
                    p.confidence,
                    p.severity,
                    datetime.now().strftime("%Y-%m-%d"),
                ),
            )
        conn.commit()

    # ─── 晨间简报 ────────────────────────────────────────────────────

    def generate_morning_briefing(self, briefing_date: str | None = None) -> MorningBriefing:
        """生成晨间简报。"""
        if briefing_date is None:
            briefing_date = datetime.now().strftime("%Y-%m-%d")

        yesterday = (datetime.strptime(briefing_date, "%Y-%m-%d") - timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )

        conn = self.store.conn
        yesterday_rows = conn.execute(
            """
            SELECT id, title, content, entry_date, mood, word_count
            FROM diary_entries
            WHERE entry_date = ?
            ORDER BY entry_date ASC
            """,
            (yesterday,),
        ).fetchall()

        yesterday_summary: dict[str, Any] = {
            "date": yesterday,
            "entry_count": len(yesterday_rows),
            "total_words": sum(r["word_count"] or 0 for r in yesterday_rows),
            "avg_mood": None,
            "highlights": [],
        }

        moods = [float(r["mood"]) for r in yesterday_rows if r.get("mood") is not None]
        if moods:
            yesterday_summary["avg_mood"] = sum(moods) / len(moods)

        for row in yesterday_rows[:3]:
            content = (row["content"] or "")[:100]
            yesterday_summary["highlights"].append(
                {
                    "title": row["title"],
                    "content": content,
                    "mood": row["mood"],
                }
            )

        today_reminders: list[str] = []
        open_loops = self.get_open_loops(status="open", limit=5)
        for loop in open_loops:
            if loop.loop_type in ("todo", "goal"):
                today_reminders.append(f"📌 {loop.content[:50]}")

        memory = self.get_memory_on_this_day(briefing_date)
        historical_context: list[str] = []
        if memory.entries:
            for entry in memory.entries[:2]:
                historical_context.append(
                    f"📅 {entry['years_ago']}年前的今天：{entry['title'] or '无标题'}"
                )

        mood_forecast = ""
        if memory.entries:
            past_moods = []
            for e in memory.entries:
                if e.get("mood") is not None:
                    with contextlib.suppress(ValueError, TypeError):
                        past_moods.append(float(e["mood"]))
            if past_moods:
                avg_past = sum(past_moods) / len(past_moods)
                if avg_past > 0.3:
                    mood_forecast = "历史上的今天情绪普遍较好，祝你今天也有好心情！"
                elif avg_past < -0.3:
                    mood_forecast = "历史上的今天情绪普遍偏低，今天多做点让自己开心的事吧。"
                else:
                    mood_forecast = "历史上的今天情绪平稳，今天也要加油哦！"

        briefing = MorningBriefing(
            briefing_date=briefing_date,
            yesterday_summary=yesterday_summary,
            today_reminders=today_reminders,
            historical_context=historical_context,
            mood_forecast=mood_forecast,
            generated_at=datetime.now().isoformat(),
        )

        self._save_morning_briefing(briefing)
        return briefing

    def _save_morning_briefing(self, briefing: MorningBriefing) -> None:
        """保存晨间简报到数据库。"""
        conn = self.store.conn
        conn.execute(
            """
            INSERT OR REPLACE INTO diary_morning_briefings (briefing_date, content)
            VALUES (?, ?)
            """,
            (briefing.briefing_date, json.dumps(briefing.to_dict(), ensure_ascii=False)),
        )
        conn.commit()

    def get_morning_briefing(self, briefing_date: str | None = None) -> MorningBriefing | None:
        """获取指定日期的晨间简报。"""
        if briefing_date is None:
            briefing_date = datetime.now().strftime("%Y-%m-%d")

        conn = self.store.conn
        row = conn.execute(
            "SELECT content FROM diary_morning_briefings WHERE briefing_date = ?",
            (briefing_date,),
        ).fetchone()

        if row is None:
            return None

        data = json.loads(row["content"])
        return MorningBriefing(**data)

    # ─── 开放循环追踪 ────────────────────────────────────────────────

    def scan_open_loops(self, lookback_days: int = 365) -> list[OpenLoop]:
        """扫描日记中的开放循环。"""
        start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

        conn = self.store.conn
        rows = conn.execute(
            """
            SELECT id, title, content, entry_date
            FROM diary_entries
            WHERE entry_date >= ?
            ORDER BY entry_date ASC
            """,
            (start_date,),
        ).fetchall()

        loops: dict[str, OpenLoop] = {}

        for row in rows:
            content = (row["content"] or "") + "\n" + (row["title"] or "")
            entry_date = row["entry_date"]
            entry_id = str(row["id"])
            self._detect_loops_in_text(content, entry_date, entry_id, loops)

        self._save_open_loops(list(loops.values()))
        return list(loops.values())

    def _detect_loops_in_text(
        self,
        text: str,
        entry_date: str,
        entry_id: str,
        loops: dict[str, OpenLoop],
    ) -> None:
        """在文本中检测开放循环。"""
        pattern_groups = [
            ("promise", PROMISE_PATTERNS),
            ("goal", GOAL_PATTERNS),
            ("todo", TODO_PATTERNS),
            ("question", QUESTION_PATTERNS),
            ("idea", IDEA_PATTERNS),
        ]

        for loop_type, patterns in pattern_groups:
            for pattern in patterns:
                matches = re.findall(pattern, text)
                for match in matches:
                    content = "".join(match).strip() if isinstance(match, tuple) else match.strip()

                    if len(content) < 4 or len(content) > 100:
                        continue

                    loop_id = f"{loop_type}_{hash(content) % 100000:05d}"

                    status = "open"
                    for comp_pattern in COMPLETION_PATTERNS:
                        if re.search(comp_pattern, content):
                            status = "completed"
                            break
                    if status == "open":
                        for aban_pattern in ABANDON_PATTERNS:
                            if re.search(aban_pattern, content):
                                status = "abandoned"
                                break

                    if loop_id in loops:
                        loop = loops[loop_id]
                        loop.mention_count += 1
                        loop.last_mentioned_date = entry_date
                        if entry_id not in loop.related_entries:
                            loop.related_entries.append(entry_id)
                        if status == "completed":
                            loop.status = "completed"
                    else:
                        priority = self._calculate_priority(loop_type, content)
                        loops[loop_id] = OpenLoop(
                            loop_id=loop_id,
                            content=content,
                            loop_type=loop_type,
                            created_date=entry_date,
                            last_mentioned_date=entry_date,
                            mention_count=1,
                            status=status,
                            related_entries=[entry_id],
                            priority=priority,
                        )

    def _calculate_priority(self, loop_type: str, content: str) -> str:
        """计算开放循环的优先级。"""
        high_keywords = ["必须", "一定", "紧急", "重要", "马上", "立刻", "今天"]
        medium_keywords = ["应该", "需要", "打算", "计划", "想"]

        for keyword in high_keywords:
            if keyword in content:
                return "high"
        for keyword in medium_keywords:
            if keyword in content:
                return "medium"
        return "low"

    def _save_open_loops(self, loops: list[OpenLoop]) -> None:
        """保存开放循环到数据库。"""
        if not loops:
            return
        conn = self.store.conn
        for loop in loops:
            conn.execute(
                """
                INSERT OR REPLACE INTO diary_open_loops
                (loop_id, content, loop_type, created_date, last_mentioned_date,
                 mention_count, status, related_entries, priority, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    loop.loop_id,
                    loop.content,
                    loop.loop_type,
                    loop.created_date,
                    loop.last_mentioned_date,
                    loop.mention_count,
                    loop.status,
                    json.dumps(loop.related_entries),
                    loop.priority,
                ),
            )
        conn.commit()

    def get_open_loops(
        self,
        status: str | None = None,
        loop_type: str | None = None,
        priority: str | None = None,
        limit: int = 50,
    ) -> list[OpenLoop]:
        """获取开放循环列表。"""
        query = "SELECT * FROM diary_open_loops WHERE 1=1"
        params: list[Any] = []

        if status:
            query += " AND status = ?"
            params.append(status)
        if loop_type:
            query += " AND loop_type = ?"
            params.append(loop_type)
        if priority:
            query += " AND priority = ?"
            params.append(priority)

        query += (
            " ORDER BY CASE priority WHEN 'high' THEN 1"
            " WHEN 'medium' THEN 2 ELSE 3 END,"
            " created_date DESC LIMIT ?"
        )
        params.append(limit)

        conn = self.store.conn
        rows = conn.execute(query, params).fetchall()

        loops = []
        for row in rows:
            loops.append(
                OpenLoop(
                    loop_id=row["loop_id"],
                    content=row["content"],
                    loop_type=row["loop_type"],
                    created_date=row["created_date"],
                    last_mentioned_date=row["last_mentioned_date"],
                    mention_count=row["mention_count"],
                    status=row["status"],
                    related_entries=json.loads(row["related_entries"] or "[]"),
                    priority=row["priority"],
                )
            )
        return loops

    def update_open_loop_status(self, loop_id: str, status: str) -> bool:
        """更新开放循环状态。"""
        if status not in ("open", "in_progress", "completed", "abandoned"):
            return False

        conn = self.store.conn
        cursor = conn.execute(
            "UPDATE diary_open_loops SET status = ?, "
            "updated_at = datetime('now') WHERE loop_id = ?",
            (status, loop_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    # ─── 综合洞察报告 ────────────────────────────────────────────────

    def generate_insight_report(self, target_date: str | None = None) -> InsightReport:
        """生成综合洞察报告。"""
        if target_date is None:
            target_date = datetime.now().strftime("%Y-%m-%d")

        report = InsightReport(
            report_date=target_date,
            generated_at=datetime.now().isoformat(),
        )

        report.memory_on_this_day = self.get_memory_on_this_day(target_date)
        report.patterns = self.discover_patterns(lookback_days=90)
        report.morning_briefing = self.generate_morning_briefing(target_date)
        report.open_loops = self.get_open_loops(status="open", limit=10)

        return report

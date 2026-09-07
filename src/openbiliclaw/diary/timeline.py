"""智能时间线卡片模块。

参考 memex-lab/memex 的设计：
- AI 自动将日记组织成类型化卡片（事件/人物/地点/任务/指标/文章等）
- 每张卡片自动提取实体、标签、关联知识
- 可以按类型/人物/地点/日期筛选浏览
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .store import DiaryStore

logger = logging.getLogger(__name__)


# ─── 数据结构 ────────────────────────────────────────────────────────


@dataclass
class TimelineCard:
    """时间线卡片。"""

    id: int = 0
    diary_id: int = 0
    card_type: str = (
        "event"  # event / person / place / task / metric / article / emotion / milestone
    )
    title: str = ""
    content: str = ""
    entry_date: str = ""
    entities: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    importance: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class TimelineStats:
    """时间线统计。"""

    total_cards: int = 0
    type_distribution: dict[str, int] = field(default_factory=dict)
    top_entities: list[tuple[str, int]] = field(default_factory=list)
    top_tags: list[tuple[str, int]] = field(default_factory=list)
    date_range: tuple[str, str] = ("", "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cards": self.total_cards,
            "type_distribution": self.type_distribution,
            "top_entities": self.top_entities,
            "top_tags": self.top_tags,
            "date_range": self.date_range,
        }


# ─── 卡片类型关键词词典 ──────────────────────────────────────────────

CARD_TYPE_KEYWORDS = {
    "milestone": [  # 里程碑/重大事件
        "结婚",
        "离婚",
        "生孩子",
        "出生",
        "去世",
        "毕业",
        "入职",
        "辞职",
        "搬家",
        "买房",
        "买车",
        "创业",
        "转行",
        "升职",
        "加薪",
        "第一次",
        "终于",
        "里程碑",
        "转折点",
        "新生活",
        "新开始",
    ],
    "event": [  # 普通事件
        "今天",
        "昨天",
        "上午",
        "下午",
        "晚上",
        "去了",
        "参加",
        "聚会",
        "旅行",
        "旅游",
        "出去玩",
        "看电影",
        "吃饭",
        "聚餐",
        "会议",
        "面试",
        "体检",
        "看病",
        "医院",
        "学校",
        "幼儿园",
    ],
    "person": [  # 人物相关
        "和",
        "跟",
        "与",
        "妈妈",
        "爸爸",
        "老公",
        "老婆",
        "孩子",
        "儿子",
        "女儿",
        "朋友",
        "同事",
        "领导",
        "老师",
        "同学",
        "家人",
    ],
    "place": [  # 地点相关
        "在",
        "去",
        "到",
        "北京",
        "上海",
        "广州",
        "深圳",
        "杭州",
        "成都",
        "重庆",
        "武汉",
        "西安",
        "南京",
        "苏州",
        "厦门",
        "青岛",
        "大连",
        "公司",
        "家里",
        "学校",
        "医院",
        "商场",
        "超市",
        "公园",
        "机场",
        "车站",
        "酒店",
        "餐厅",
        "咖啡馆",
        "图书馆",
        "博物馆",
    ],
    "task": [  # 任务/待办
        "要",
        "需要",
        "必须",
        "得",
        "应该",
        "计划",
        "打算",
        "准备",
        "完成",
        "做完",
        "搞定",
        "处理",
        "解决",
        "提交",
        "交付",
        "待办",
        "任务",
        "清单",
        "deadline",
        "截止",
    ],
    "metric": [  # 指标/数据
        "体重",
        "身高",
        "血压",
        "血糖",
        "睡眠",
        "步数",
        "公里",
        "小时",
        "分钟",
        "元",
        "块",
        "工资",
        "收入",
        "支出",
        "花费",
        "消费",
        "分数",
        "成绩",
        "排名",
        "数量",
        "百分比",
        "%",
    ],
    "article": [  # 文章/学习
        "看了",
        "读了",
        "学了",
        "学习",
        "阅读",
        "书籍",
        "书",
        "文章",
        "论文",
        "课程",
        "视频",
        "播客",
        "知识",
        "概念",
        "理论",
        "总结",
        "笔记",
        "整理",
        "归纳",
    ],
    "emotion": [  # 情绪/感受
        "开心",
        "高兴",
        "快乐",
        "幸福",
        "难过",
        "伤心",
        "悲伤",
        "痛苦",
        "焦虑",
        "紧张",
        "担心",
        "害怕",
        "压力",
        "压抑",
        "郁闷",
        "烦躁",
        "生气",
        "愤怒",
        "失望",
        "绝望",
        "沮丧",
        "低落",
        "孤独",
        "寂寞",
        "感动",
        "温暖",
        "舒适",
        "放松",
        "平静",
        "安心",
        "踏实",
    ],
}

# 常见人物实体
COMMON_PEOPLE = [
    "艳艳",
    "狄胖胖",
    "乐乐",
    "妈妈",
    "爸爸",
    "童先海",
    "周英",
    "老公",
    "老婆",
    "儿子",
    "女儿",
    "孩子",
    "宝宝",
    "朋友",
    "同事",
    "领导",
    "老师",
    "同学",
    "家人",
]

# 常见地点实体
COMMON_PLACES = [
    "北京",
    "上海",
    "广州",
    "深圳",
    "杭州",
    "成都",
    "重庆",
    "武汉",
    "西安",
    "南京",
    "苏州",
    "厦门",
    "青岛",
    "大连",
    "长沙",
    "郑州",
    "公司",
    "家里",
    "学校",
    "幼儿园",
    "医院",
    "商场",
    "超市",
    "公园",
    "机场",
    "车站",
    "酒店",
    "餐厅",
    "咖啡馆",
    "图书馆",
    "博物馆",
]


# ─── 智能时间线服务 ──────────────────────────────────────────────────


class TimelineService:
    """智能时间线卡片服务。"""

    CARD_TYPE_NAMES_CN = {
        "event": "事件",
        "person": "人物",
        "place": "地点",
        "task": "任务",
        "metric": "指标",
        "article": "文章",
        "emotion": "情绪",
        "milestone": "里程碑",
    }

    def __init__(self, store: DiaryStore):
        self.store = store
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """确保数据库表存在。"""
        conn = self.store.conn
        conn.execute("""
            CREATE TABLE IF NOT EXISTS diary_timeline_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                diary_id INTEGER NOT NULL,
                card_type TEXT NOT NULL,
                title TEXT,
                content TEXT,
                entry_date TEXT,
                entities TEXT,
                tags TEXT,
                importance REAL DEFAULT 0.5,
                metadata TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (diary_id) REFERENCES diary_entries(id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_timeline_cards_type ON diary_timeline_cards(card_type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_timeline_cards_date ON diary_timeline_cards(entry_date)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_timeline_cards_diary ON diary_timeline_cards(diary_id)"
        )
        conn.commit()

    def generate_cards_from_diary(self, diary_id: int) -> list[TimelineCard]:
        """从单篇日记生成时间线卡片。"""
        conn = self.store.conn
        row = conn.execute(
            "SELECT id, title, content, entry_date, mood, tags FROM diary_entries WHERE id = ?",
            (diary_id,),
        ).fetchone()

        if row is None:
            return []

        title = row["title"] or ""
        content = row["content"] or ""
        full_text = title + " " + content
        entry_date = row["entry_date"] or ""
        tags = self._parse_tags(row["tags"])

        # 删除该日记的旧卡片
        conn.execute("DELETE FROM diary_timeline_cards WHERE diary_id = ?", (diary_id,))

        cards = []

        # 1. 按句子分割，为每个重要句子生成卡片
        sentences = re.split(r"[。！？\n]", full_text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 8]

        for sentence in sentences[:10]:  # 每篇日记最多 10 张卡片
            card_type = self._classify_card_type(sentence)
            entities = self._extract_entities(sentence)
            importance = self._calculate_card_importance(sentence, card_type)

            if importance < 0.3:
                continue  # 跳过不重要的句子

            card = TimelineCard(
                diary_id=diary_id,
                card_type=card_type,
                title=self._generate_card_title(sentence, card_type),
                content=sentence[:200],
                entry_date=entry_date,
                entities=entities,
                tags=tags,
                importance=importance,
                metadata={"source": "sentence"},
            )
            cards.append(card)

        # 2. 如果没有生成任何卡片，至少生成一张日记摘要卡片
        if not cards:
            summary = self._extract_summary(full_text)
            card = TimelineCard(
                diary_id=diary_id,
                card_type="event",
                title=title[:50] or "日记记录",
                content=summary,
                entry_date=entry_date,
                entities=self._extract_entities(full_text),
                tags=tags,
                importance=0.4,
                metadata={"source": "summary"},
            )
            cards.append(card)

        # 保存到数据库
        for card in cards:
            conn.execute(
                """
                INSERT INTO diary_timeline_cards
                (diary_id, card_type, title, content, entry_date, entities, tags, importance, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    card.diary_id,
                    card.card_type,
                    card.title,
                    card.content,
                    card.entry_date,
                    json.dumps(card.entities, ensure_ascii=False),
                    json.dumps(card.tags, ensure_ascii=False),
                    card.importance,
                    json.dumps(card.metadata, ensure_ascii=False),
                ),
            )
        conn.commit()

        return cards

    def generate_all_cards(self) -> int:
        """为所有日记生成时间线卡片。返回生成的卡片总数。"""
        conn = self.store.conn

        # 清空现有卡片
        conn.execute("DELETE FROM diary_timeline_cards")
        conn.commit()

        rows = conn.execute("SELECT id FROM diary_entries ORDER BY entry_date").fetchall()
        total = 0

        for row in rows:
            cards = self.generate_cards_from_diary(row["id"])
            total += len(cards)

        return total

    def _classify_card_type(self, text: str) -> str:
        """分类卡片类型。"""
        scores = defaultdict(int)

        for card_type, keywords in CARD_TYPE_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    scores[card_type] += 1

        if not scores:
            return "event"

        # 返回得分最高的类型
        return max(scores.items(), key=lambda x: x[1])[0]

    def _extract_entities(self, text: str) -> list[str]:
        """提取实体（人物、地点）。"""
        entities = []

        for person in COMMON_PEOPLE:
            if person in text:
                entities.append(f"人物:{person}")

        for place in COMMON_PLACES:
            if place in text:
                entities.append(f"地点:{place}")

        return list(set(entities))[:10]

    def _calculate_card_importance(self, text: str, card_type: str) -> float:
        """计算卡片重要性。"""
        importance = 0.3  # 基础重要性

        # 里程碑类型重要性更高
        if card_type == "milestone":
            importance += 0.4

        # 情绪类型重要性较高
        if card_type == "emotion":
            importance += 0.1

        # 包含实体的重要性更高
        entities = self._extract_entities(text)
        if entities:
            importance += min(0.2, len(entities) * 0.05)

        # 长度加权
        if len(text) > 50:
            importance += 0.1

        return min(1.0, importance)

    def _generate_card_title(self, text: str, card_type: str) -> str:
        """生成卡片标题。"""
        # 取前 30 个字符作为标题
        title = text[:30].strip()
        if len(text) > 30:
            title += "..."

        # 根据类型添加前缀
        prefix = self.CARD_TYPE_NAMES_CN.get(card_type, "")
        if prefix and not title.startswith(prefix):
            title = f"[{prefix}] {title}"

        return title

    def _extract_summary(self, text: str, max_len: int = 150) -> str:
        """提取摘要。"""
        if not text:
            return ""

        sentences = re.split(r"[。！？\n]", text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 5]

        if not sentences:
            return text[:max_len]

        summary = "。".join(sentences[:2])
        return summary[:max_len]

    def _parse_tags(self, tags_str: str | None) -> list[str]:
        """解析标签字符串。"""
        if not tags_str:
            return []
        try:
            tags = json.loads(tags_str)
            if isinstance(tags, list):
                return tags
        except (json.JSONDecodeError, TypeError):
            pass
        return [t.strip() for t in tags_str.split(",") if t.strip()]

    # ─── 查询接口 ──────────────────────────────────────────────────────

    def get_cards(
        self,
        card_type: str | None = None,
        entity: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        min_importance: float = 0.0,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TimelineCard]:
        """查询时间线卡片。"""
        conn = self.store.conn

        sql = "SELECT * FROM diary_timeline_cards WHERE importance >= ?"
        params: list[Any] = [min_importance]

        if card_type:
            sql += " AND card_type = ?"
            params.append(card_type)

        if start_date:
            sql += " AND entry_date >= ?"
            params.append(start_date)

        if end_date:
            sql += " AND entry_date <= ?"
            params.append(end_date)

        if entity:
            sql += " AND entities LIKE ?"
            params.append(f"%{entity}%")

        sql += " ORDER BY entry_date DESC, importance DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(sql, params).fetchall()

        cards = []
        for r in rows:
            cards.append(
                TimelineCard(
                    id=r["id"],
                    diary_id=r["diary_id"],
                    card_type=r["card_type"],
                    title=r["title"] or "",
                    content=r["content"] or "",
                    entry_date=r["entry_date"] or "",
                    entities=json.loads(r["entities"]) if r["entities"] else [],
                    tags=json.loads(r["tags"]) if r["tags"] else [],
                    importance=r["importance"],
                    metadata=json.loads(r["metadata"]) if r["metadata"] else {},
                )
            )

        return cards

    def get_stats(self) -> TimelineStats:
        """获取时间线统计。"""
        conn = self.store.conn

        # 总数
        total = conn.execute("SELECT COUNT(*) as cnt FROM diary_timeline_cards").fetchone()["cnt"]

        # 类型分布
        type_rows = conn.execute(
            "SELECT card_type, COUNT(*) as cnt FROM diary_timeline_cards GROUP BY card_type ORDER BY cnt DESC"
        ).fetchall()
        type_dist = {r["card_type"]: r["cnt"] for r in type_rows}

        # 日期范围
        date_row = conn.execute(
            "SELECT MIN(entry_date) as min_date, MAX(entry_date) as max_date FROM diary_timeline_cards"
        ).fetchone()

        # 高频实体
        all_entities = []
        entity_rows = conn.execute(
            "SELECT entities FROM diary_timeline_cards WHERE entities IS NOT NULL"
        ).fetchall()
        for r in entity_rows:
            try:
                entities = json.loads(r["entities"])
                all_entities.extend(entities)
            except (json.JSONDecodeError, TypeError):
                pass

        entity_counter = Counter(all_entities)
        top_entities = entity_counter.most_common(15)

        # 高频标签
        all_tags = []
        tag_rows = conn.execute(
            "SELECT tags FROM diary_timeline_cards WHERE tags IS NOT NULL"
        ).fetchall()
        for r in tag_rows:
            try:
                tags = json.loads(r["tags"])
                all_tags.extend(tags)
            except (json.JSONDecodeError, TypeError):
                pass

        tag_counter = Counter(all_tags)
        top_tags = tag_counter.most_common(15)

        return TimelineStats(
            total_cards=total,
            type_distribution=type_dist,
            top_entities=top_entities,
            top_tags=top_tags,
            date_range=(date_row["min_date"] or "", date_row["max_date"] or ""),
        )

    def get_cards_by_entity(self, entity: str, limit: int = 50) -> list[TimelineCard]:
        """按实体查询卡片。"""
        return self.get_cards(entity=entity, limit=limit)

    def get_milestones(self, limit: int = 20) -> list[TimelineCard]:
        """获取里程碑卡片。"""
        return self.get_cards(card_type="milestone", min_importance=0.5, limit=limit)

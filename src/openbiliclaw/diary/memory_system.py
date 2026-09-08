"""三层记忆系统模块。

参考 Doppelganger AI 的三层记忆设计：
- Hot Memory（0-24h）：完整日记内容，高优先级
- Warm Memory（1-7天）：聚类压缩为摘要，中优先级
- Cold Memory（7+天）：只保留实体+关系图，内容归档，低优先级

重要性评分基于：情感权重、访问频率、实体密度、时间衰减。
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .store import DiaryStore

logger = logging.getLogger(__name__)


# ─── 数据结构 ────────────────────────────────────────────────────────


@dataclass
class MemoryEntry:
    """记忆条目。"""

    diary_id: int
    memory_tier: str  # hot / warm / cold
    importance_score: float = 0.0
    access_count: int = 0
    last_accessed: str | None = None
    compressed_summary: str | None = None
    entities: list[str] = field(default_factory=list)
    emotion_intensity: float = 0.0
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryStats:
    """记忆系统统计。"""

    total_entries: int = 0
    hot_count: int = 0
    warm_count: int = 0
    cold_count: int = 0
    avg_importance: float = 0.0
    total_accesses: int = 0
    compression_ratio: float = 0.0
    top_entities: list[tuple[str, int]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_entries": self.total_entries,
            "hot_count": self.hot_count,
            "warm_count": self.warm_count,
            "cold_count": self.cold_count,
            "avg_importance": self.avg_importance,
            "total_accesses": self.total_accesses,
            "compression_ratio": self.compression_ratio,
            "top_entities": [{"entity": e, "count": c} for e, c in self.top_entities],
        }


@dataclass
class MemoryCompressionResult:
    """记忆压缩结果。"""

    compressed_count: int = 0
    promoted_count: int = 0
    demoted_count: int = 0
    archived_count: int = 0
    details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─── 实体提取关键词 ──────────────────────────────────────────────────

COMMON_ENTITIES = [
    # 人物
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
    "闺蜜",
    "哥们",
    "老板",
    "老师",
    "医生",
    # 地点
    "深圳",
    "北京",
    "上海",
    "广州",
    "家",
    "公司",
    "学校",
    "医院",
    "商场",
    # 事件/活动
    "工作",
    "面试",
    "加班",
    "旅行",
    "旅游",
    "聚会",
    "吃饭",
    "看电影",
    "购物",
    "学习",
    "读书",
    "运动",
    "健身",
    "减肥",
    "睡觉",
    "失眠",
    # 物品
    "手机",
    "电脑",
    "车",
    "房子",
    "钱",
    "工资",
    # 情绪/状态
    "开心",
    "难过",
    "焦虑",
    "压力",
    "幸福",
    "感动",
    "生气",
    "失望",
    "兴奋",
]


# ─── 三层记忆系统服务 ────────────────────────────────────────────────


class MemorySystemService:
    """三层记忆系统服务。

    功能：
    - 自动分层：根据日记时间自动分为 Hot/Warm/Cold 三层
    - 重要性评分：基于情感权重、访问频率、实体密度、时间衰减
    - 记忆压缩：自动将旧日记压缩为结构化摘要
    - 记忆检索：根据重要性和相关性检索记忆
    - 记忆统计：统计各层记忆数量、压缩比、高频实体等
    """

    # 时间阈值（天）
    HOT_THRESHOLD_DAYS = 1
    WARM_THRESHOLD_DAYS = 7

    # 重要性权重
    EMOTION_WEIGHT = 0.3
    ACCESS_WEIGHT = 0.2
    ENTITY_WEIGHT = 0.25
    RECENCY_WEIGHT = 0.25

    def __init__(self, store: DiaryStore):
        self.store = store
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """确保数据库表存在。"""
        conn = self.store.conn
        conn.execute("""
            CREATE TABLE IF NOT EXISTS diary_memory_entries (
                diary_id INTEGER PRIMARY KEY,
                memory_tier TEXT NOT NULL DEFAULT 'cold',
                importance_score REAL DEFAULT 0,
                access_count INTEGER DEFAULT 0,
                last_accessed TEXT,
                compressed_summary TEXT,
                entities TEXT,
                emotion_intensity REAL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_tier
            ON diary_memory_entries(memory_tier)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_importance
            ON diary_memory_entries(importance_score DESC)
        """)
        conn.commit()

    # ─── 记忆分层 ────────────────────────────────────────────────────

    def classify_memory_tier(self, entry_date: str) -> str:
        """根据日记日期分类记忆层级。"""
        try:
            entry_dt = datetime.strptime(entry_date, "%Y-%m-%d")
            days_ago = (datetime.now() - entry_dt).days

            if days_ago <= self.HOT_THRESHOLD_DAYS:
                return "hot"
            elif days_ago <= self.WARM_THRESHOLD_DAYS:
                return "warm"
            else:
                return "cold"
        except (ValueError, TypeError):
            return "cold"

    def update_memory_tiers(self) -> int:
        """更新所有日记的记忆层级。返回更新的数量。"""
        conn = self.store.conn
        rows = conn.execute(
            "SELECT id, entry_date FROM diary_entries ORDER BY entry_date DESC"
        ).fetchall()

        updated = 0
        for row in rows:
            tier = self.classify_memory_tier(row["entry_date"])

            # 检查是否已存在
            existing = conn.execute(
                "SELECT memory_tier FROM diary_memory_entries WHERE diary_id = ?",
                (row["id"],),
            ).fetchone()

            if existing is None:
                # 新建记忆条目
                importance = self._calculate_importance(row["id"], row["entry_date"])
                entities = self._extract_entities(row["id"])
                emotion_intensity = self._calculate_emotion_intensity(row["id"])

                conn.execute(
                    """
                    INSERT INTO diary_memory_entries
                    (diary_id, memory_tier, importance_score, entities, emotion_intensity)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"],
                        tier,
                        importance,
                        json.dumps(entities, ensure_ascii=False),
                        emotion_intensity,
                    ),
                )
                updated += 1
            elif existing["memory_tier"] != tier:
                # 更新层级
                conn.execute(
                    "UPDATE diary_memory_entries SET memory_tier = ?, "
                    "updated_at = datetime('now') WHERE diary_id = ?",
                    (tier, row["id"]),
                )
                updated += 1

        conn.commit()
        return updated

    # ─── 重要性评分 ──────────────────────────────────────────────────

    def _calculate_importance(self, diary_id: int, entry_date: str) -> float:
        """计算日记的重要性评分（0-1）。"""
        # 1. 情感强度（0-1）
        emotion_intensity = self._calculate_emotion_intensity(diary_id)

        # 2. 访问频率（0-1）
        access_count = self._get_access_count(diary_id)
        access_score = min(1.0, access_count / 10.0)

        # 3. 实体密度（0-1）
        entities = self._extract_entities(diary_id)
        entity_score = min(1.0, len(entities) / 10.0)

        # 4. 时间衰减（0-1，越新越高）
        try:
            entry_dt = datetime.strptime(entry_date, "%Y-%m-%d")
            days_ago = (datetime.now() - entry_dt).days
            recency_score = math.exp(-days_ago / 30.0)  # 30天半衰期
        except (ValueError, TypeError):
            recency_score = 0.5

        # 加权平均
        importance = (
            emotion_intensity * self.EMOTION_WEIGHT
            + access_score * self.ACCESS_WEIGHT
            + entity_score * self.ENTITY_WEIGHT
            + recency_score * self.RECENCY_WEIGHT
        )

        return round(min(1.0, max(0.0, importance)), 4)

    def _calculate_emotion_intensity(self, diary_id: int) -> float:
        """计算日记的情感强度（0-1）。"""
        conn = self.store.conn
        row = conn.execute(
            "SELECT mood, content FROM diary_entries WHERE id = ?",
            (diary_id,),
        ).fetchone()

        if row is None:
            return 0.0

        # 基于 mood 值的绝对值
        mood_intensity = 0.5
        if row["mood"] is not None:
            try:
                mood = float(row["mood"])
                mood_intensity = min(1.0, abs(mood))
            except (ValueError, TypeError):
                pass

        # 基于情感关键词密度
        content = row["content"] or ""
        emotion_keywords = [
            "非常",
            "特别",
            "超级",
            "太",
            "真的",
            "好",
            "很",
            "开心",
            "难过",
            "焦虑",
            "压力",
            "幸福",
            "感动",
            "生气",
            "失望",
            "兴奋",
            "爱",
            "恨",
            "怕",
            "担心",
            "期待",
            "后悔",
            "遗憾",
        ]
        emotion_count = sum(1 for kw in emotion_keywords if kw in content)
        keyword_intensity = min(1.0, emotion_count / 5.0)

        return round((mood_intensity * 0.6 + keyword_intensity * 0.4), 4)

    def _get_access_count(self, diary_id: int) -> int:
        """获取日记的访问次数。"""
        conn = self.store.conn
        row = conn.execute(
            "SELECT access_count FROM diary_memory_entries WHERE diary_id = ?",
            (diary_id,),
        ).fetchone()
        return row["access_count"] if row else 0

    def _extract_entities(self, diary_id: int) -> list[str]:
        """从日记中提取实体。"""
        conn = self.store.conn
        row = conn.execute(
            "SELECT title, content FROM diary_entries WHERE id = ?",
            (diary_id,),
        ).fetchone()

        if row is None:
            return []

        text = (row["title"] or "") + " " + (row["content"] or "")
        entities = []
        for entity in COMMON_ENTITIES:
            if entity in text:
                entities.append(entity)

        return list(set(entities))

    # ─── 记忆压缩 ────────────────────────────────────────────────────

    def compress_memory(self, diary_id: int) -> str | None:
        """将日记压缩为结构化摘要。"""
        conn = self.store.conn
        row = conn.execute(
            "SELECT title, content, entry_date, mood FROM diary_entries WHERE id = ?",
            (diary_id,),
        ).fetchone()

        if row is None:
            return None

        content = row["content"] or ""
        title = row["title"] or ""

        # 提取关键句子（包含实体或情感词的句子）
        sentences = re.split(r"[。！？\n]", content)
        key_sentences = []
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 10:
                continue
            # 检查是否包含实体或情感词
            has_entity = any(e in sentence for e in COMMON_ENTITIES)
            has_emotion = any(
                e in sentence
                for e in ["开心", "难过", "焦虑", "压力", "幸福", "感动", "生气", "失望", "兴奋"]
            )
            if has_entity or has_emotion:
                key_sentences.append(sentence)

        # 生成摘要
        if key_sentences:
            summary = "。".join(key_sentences[:3]) + "。"
        elif sentences:
            # 取前两句
            summary = "。".join(s.strip() for s in sentences[:2] if s.strip()) + "。"
        else:
            summary = title

        # 限制摘要长度
        if len(summary) > 200:
            summary = summary[:200] + "..."

        # 更新数据库
        conn.execute(
            """
            UPDATE diary_memory_entries
            SET compressed_summary = ?, updated_at = datetime('now')
            WHERE diary_id = ?
            """,
            (summary, diary_id),
        )
        conn.commit()

        return summary

    def compress_all_cold_memories(self) -> MemoryCompressionResult:
        """压缩所有 Cold 层记忆。"""
        result = MemoryCompressionResult()
        conn = self.store.conn

        # 获取所有 Cold 层记忆
        rows = conn.execute(
            """
            SELECT me.diary_id, me.compressed_summary
            FROM diary_memory_entries me
            WHERE me.memory_tier = 'cold'
            ORDER BY me.importance_score DESC
            """
        ).fetchall()

        for row in rows:
            if row["compressed_summary"] is None:
                summary = self.compress_memory(row["diary_id"])
                if summary:
                    result.compressed_count += 1
                    result.details.append(f"压缩日记 #{row['diary_id']}: {summary[:50]}...")

        return result

    # ─── 记忆检索 ────────────────────────────────────────────────────

    def search_memories(
        self,
        query: str,
        tier: str | None = None,
        limit: int = 20,
        min_importance: float = 0.0,
    ) -> list[dict[str, Any]]:
        """搜索记忆。

        Args:
            query: 搜索关键词
            tier: 记忆层级过滤（hot/warm/cold）
            limit: 返回数量上限
            min_importance: 最低重要性评分

        """
        conn = self.store.conn

        # 构建查询
        query_sql = """
            SELECT
                me.diary_id,
                me.memory_tier,
                me.importance_score,
                me.access_count,
                me.compressed_summary,
                me.entities,
                me.emotion_intensity,
                de.title,
                de.entry_date,
                de.mood,
                de.word_count
            FROM diary_memory_entries me
            JOIN diary_entries de ON me.diary_id = de.id
            WHERE me.importance_score >= ?
        """
        params: list[Any] = [min_importance]

        if tier:
            query_sql += " AND me.memory_tier = ?"
            params.append(tier)

        if query:
            query_sql += (
                " AND (de.title LIKE ? OR de.content LIKE ? OR me.compressed_summary LIKE ?)"
            )
            like_query = f"%{query}%"
            params.extend([like_query, like_query, like_query])

        query_sql += " ORDER BY me.importance_score DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query_sql, params).fetchall()

        results = []
        for row in rows:
            # 增加访问计数
            conn.execute(
                """
                UPDATE diary_memory_entries
                SET access_count = access_count + 1,
                    last_accessed = datetime('now')
                WHERE diary_id = ?
                """,
                (row["diary_id"],),
            )

            results.append(
                {
                    "diary_id": row["diary_id"],
                    "memory_tier": row["memory_tier"],
                    "importance_score": row["importance_score"],
                    "access_count": row["access_count"],
                    "compressed_summary": row["compressed_summary"],
                    "entities": json.loads(row["entities"] or "[]"),
                    "emotion_intensity": row["emotion_intensity"],
                    "title": row["title"],
                    "entry_date": row["entry_date"],
                    "mood": row["mood"],
                    "word_count": row["word_count"],
                }
            )

        conn.commit()
        return results

    def get_memory_by_id(self, diary_id: int) -> MemoryEntry | None:
        """根据 ID 获取记忆条目。"""
        conn = self.store.conn
        row = conn.execute(
            "SELECT * FROM diary_memory_entries WHERE diary_id = ?",
            (diary_id,),
        ).fetchone()

        if row is None:
            return None

        return MemoryEntry(
            diary_id=row["diary_id"],
            memory_tier=row["memory_tier"],
            importance_score=row["importance_score"],
            access_count=row["access_count"],
            last_accessed=row["last_accessed"],
            compressed_summary=row["compressed_summary"],
            entities=json.loads(row["entities"] or "[]"),
            emotion_intensity=row["emotion_intensity"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ─── 记忆统计 ────────────────────────────────────────────────────

    def get_memory_stats(self) -> MemoryStats:
        """获取记忆系统统计。"""
        conn = self.store.conn

        # 各层数量
        row = conn.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN memory_tier = 'hot' THEN 1 ELSE 0 END) as hot,
                SUM(CASE WHEN memory_tier = 'warm' THEN 1 ELSE 0 END) as warm,
                SUM(CASE WHEN memory_tier = 'cold' THEN 1 ELSE 0 END) as cold,
                AVG(importance_score) as avg_importance,
                SUM(access_count) as total_accesses
            FROM diary_memory_entries
            """
        ).fetchone()

        # 压缩比
        compressed_row = conn.execute(
            "SELECT COUNT(*) as compressed FROM diary_memory_entries "
            "WHERE compressed_summary IS NOT NULL"
        ).fetchone()

        compression_ratio = 0.0
        if row["total"] and row["total"] > 0:
            compression_ratio = compressed_row["compressed"] / row["total"]

        # 高频实体
        entity_rows = conn.execute(
            "SELECT entities FROM diary_memory_entries "
            "WHERE entities IS NOT NULL AND entities != '[]'"
        ).fetchall()

        entity_counter: Counter[str] = Counter()
        for er in entity_rows:
            try:
                entities = json.loads(er["entities"])
                entity_counter.update(entities)
            except (json.JSONDecodeError, TypeError):
                pass

        top_entities = entity_counter.most_common(20)

        return MemoryStats(
            total_entries=row["total"] or 0,
            hot_count=row["hot"] or 0,
            warm_count=row["warm"] or 0,
            cold_count=row["cold"] or 0,
            avg_importance=round(row["avg_importance"] or 0, 4),
            total_accesses=row["total_accesses"] or 0,
            compression_ratio=round(compression_ratio, 4),
            top_entities=top_entities,
        )

    # ─── 记忆维护 ────────────────────────────────────────────────────

    def run_memory_maintenance(self) -> MemoryCompressionResult:
        """运行记忆维护（分层更新 + 压缩 + 重要性重算）。"""
        result = MemoryCompressionResult()

        # 1. 更新记忆层级
        updated = self.update_memory_tiers()
        result.details.append(f"更新了 {updated} 个记忆条目的层级")

        # 2. 压缩所有 Cold 层记忆
        compression_result = self.compress_all_cold_memories()
        result.compressed_count = compression_result.compressed_count
        result.details.extend(compression_result.details)

        # 3. 重算重要性评分
        conn = self.store.conn
        rows = conn.execute("SELECT diary_id FROM diary_memory_entries").fetchall()

        recalculated = 0
        for row in rows:
            entry_row = conn.execute(
                "SELECT entry_date FROM diary_entries WHERE id = ?",
                (row["diary_id"],),
            ).fetchone()
            if entry_row:
                importance = self._calculate_importance(row["diary_id"], entry_row["entry_date"])
                conn.execute(
                    "UPDATE diary_memory_entries SET importance_score = ?, "
                    "updated_at = datetime('now') WHERE diary_id = ?",
                    (importance, row["diary_id"]),
                )
                recalculated += 1

        conn.commit()
        result.details.append(f"重算了 {recalculated} 个记忆条目的重要性评分")

        return result

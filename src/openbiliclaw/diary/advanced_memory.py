"""高级记忆系统——6 层记忆 + 信念冲突检测 + 记忆巩固与遗忘曲线。

参考 emergent-diary-agent、nmem、Anima、Echo Agent 的设计：
- 6 层记忆：情景/语义自我/信念/关系/叙事状态/日记
- 信念冲突检测与版本化
- 基于艾宾浩斯遗忘曲线的记忆生命周期管理
- 夜间"梦境状态"回顾：用新证据验证过去的教训
- 重要性评分细化：决定/教训/情感时刻 → 高优先级
"""

from __future__ import annotations

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
class MemoryLayer:
    """记忆层统计。"""

    layer: str  # episodic / semantic_self / beliefs / relationships / narrative / diary
    count: int = 0
    avg_importance: float = 0.0
    avg_recency: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Belief:
    """信念条目。"""

    id: int = 0
    content: str = ""
    category: str = "general"  # general / work / family / self / world / values
    confidence: float = 0.5  # 0-1
    evidence_count: int = 0
    first_seen: str = ""
    last_seen: str = ""
    version: int = 1
    status: str = "active"  # active / disputed / superseded / abandoned
    related_beliefs: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BeliefConflict:
    """信念冲突。"""

    belief_a_id: int
    belief_b_id: int
    belief_a_content: str
    belief_b_content: str
    conflict_type: str  # contradiction / tension / evolution
    resolution: str = ""  # unresolved / resolved_a / resolved_b / merged / both_valid
    detected_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConsolidationResult:
    """记忆巩固结果。"""

    consolidated_at: str
    entries_processed: int = 0
    entries_promoted: int = 0  # 提升到更高层
    entries_demoted: int = 0  # 降级到更低层
    entries_archived: int = 0  # 归档
    entries_forgotten: int = 0  # 遗忘（重要性过低）
    beliefs_updated: int = 0
    conflicts_detected: int = 0
    lessons_validated: int = 0
    lessons_disputed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DreamStateReview:
    """夜间"梦境状态"回顾结果。"""

    review_date: str
    lessons_reviewed: int = 0
    lessons_validated: int = 0
    lessons_disputed: int = 0
    new_patterns: list[str] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)
    memory_health_score: float = 0.0  # 0-100

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─── 信念关键词词典 ──────────────────────────────────────────────────

BELIEF_PATTERNS = [
    # (pattern, category, belief_template)
    (r"我(觉得|认为|相信|感觉).{0,20}(工作|事业|赚钱)", "work", "关于工作的信念"),
    (r"我(觉得|认为|相信|感觉).{0,20}(家人|家庭|孩子|老公|老婆)", "family", "关于家庭的信念"),
    (r"我(觉得|认为|相信|感觉).{0,20}(自己|我|人生|生活)", "self", "关于自我的信念"),
    (r"我(觉得|认为|相信|感觉).{0,20}(世界|社会|别人|他人)", "world", "关于世界的信念"),
    (r"(应该|必须|要|得).{0,15}(努力|奋斗|拼搏|学习)", "values", "关于努力的价值观"),
    (r"(健康|身体|休息|睡眠).{0,10}(最重要|第一|优先)", "values", "关于健康的价值观"),
    (r"(家庭|家人|孩子).{0,10}(最重要|第一|优先)", "values", "关于家庭的价值观"),
    (r"(钱|赚钱|财富).{0,10}(不重要|身外之物|够花就行)", "values", "关于金钱的价值观"),
]

# 教训关键词（从日记中提取"学到的教训"）
LESSON_KEYWORDS = [
    "学到了",
    "明白了",
    "懂得了",
    "意识到",
    "发现",
    "总结",
    "以后要",
    "下次要",
    "不能再",
    "不要再",
    "应该",
    "原来",
    "教训",
    "经验",
    "感悟",
    "体会",
    "领悟",
]

# 决定关键词（重要决定 → 高优先级记忆）
DECISION_KEYWORDS = [
    "决定",
    "选择",
    "辞职",
    "入职",
    "搬家",
    "买房",
    "买车",
    "结婚",
    "离婚",
    "生孩子",
    "创业",
    "转行",
    "换工作",
    "承诺",
    "发誓",
    "下定决心",
]

# 情感时刻关键词（强烈情感 → 高优先级记忆）
EMOTION_KEYWORDS = [
    "永远记得",
    "难忘",
    "刻骨铭心",
    "感动",
    "痛哭",
    "狂喜",
    "心碎",
    "崩溃",
    "重生",
    "蜕变",
    "转折点",
    "里程碑",
]


# ─── 高级记忆服务 ────────────────────────────────────────────────────


class AdvancedMemoryService:
    """高级记忆服务——6 层记忆 + 信念冲突检测 + 记忆巩固。"""

    # 6 层记忆定义
    LAYERS = [
        "episodic",  # 情景记忆：原始日记内容、具体事件
        "semantic_self",  # 语义自我：关于自己的知识、偏好、习惯
        "beliefs",  # 信念：核心信念、价值观、人生观
        "relationships",  # 关系：人际关系网络、亲密度、互动模式
        "narrative",  # 叙事状态：当前生活故事线、目标、阶段
        "diary",  # 日记：压缩后的日记摘要
    ]

    LAYER_NAMES_CN = {
        "episodic": "情景记忆",
        "semantic_self": "语义自我",
        "beliefs": "信念",
        "relationships": "关系",
        "narrative": "叙事状态",
        "diary": "日记摘要",
    }

    # 遗忘曲线参数（艾宾浩斯）
    FORGETTING_CURVE = {
        "episodic": 7,  # 情景记忆 7 天半衰期
        "semantic_self": 90,  # 语义自我 90 天半衰期
        "beliefs": 365,  # 信念 365 天半衰期
        "relationships": 180,  # 关系 180 天半衰期
        "narrative": 60,  # 叙事状态 60 天半衰期
        "diary": 30,  # 日记摘要 30 天半衰期
    }

    def __init__(self, store: DiaryStore):
        self.store = store
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """确保数据库表存在。"""
        conn = self.store.conn

        # 6 层记忆条目表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS diary_advanced_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                diary_id INTEGER,
                layer TEXT NOT NULL,
                content TEXT NOT NULL,
                importance REAL DEFAULT 0.5,
                access_count INTEGER DEFAULT 0,
                last_accessed TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                metadata TEXT
            )
        """)

        # 信念表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS diary_beliefs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                confidence REAL DEFAULT 0.5,
                evidence_count INTEGER DEFAULT 0,
                first_seen TEXT,
                last_seen TEXT,
                version INTEGER DEFAULT 1,
                status TEXT DEFAULT 'active',
                parent_belief_id INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # 信念冲突表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS diary_belief_conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                belief_a_id INTEGER NOT NULL,
                belief_b_id INTEGER NOT NULL,
                conflict_type TEXT NOT NULL,
                resolution TEXT DEFAULT 'unresolved',
                detected_at TEXT DEFAULT (datetime('now')),
                resolved_at TEXT
            )
        """)

        # 教训表（用于梦境状态回顾）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS diary_lessons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                diary_id INTEGER,
                category TEXT DEFAULT 'general',
                confidence REAL DEFAULT 0.5,
                status TEXT DEFAULT 'active',
                first_seen TEXT,
                last_validated TEXT,
                validation_count INTEGER DEFAULT 0,
                dispute_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # 记忆巩固记录表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS diary_consolidation_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                consolidated_at TEXT DEFAULT (datetime('now')),
                entries_processed INTEGER DEFAULT 0,
                entries_promoted INTEGER DEFAULT 0,
                entries_demoted INTEGER DEFAULT 0,
                entries_archived INTEGER DEFAULT 0,
                entries_forgotten INTEGER DEFAULT 0,
                beliefs_updated INTEGER DEFAULT 0,
                conflicts_detected INTEGER DEFAULT 0,
                lessons_validated INTEGER DEFAULT 0,
                lessons_disputed INTEGER DEFAULT 0
            )
        """)

        conn.commit()

    # ─── 6 层记忆构建 ────────────────────────────────────────────────

    def build_memory_layers(self) -> dict[str, int]:
        """从日记构建 6 层记忆。返回每层的条目数。"""
        conn = self.store.conn

        # 清空现有记忆（重新构建）
        conn.execute("DELETE FROM diary_advanced_memories")
        conn.commit()

        result = {layer: 0 for layer in self.LAYERS}

        # 获取所有日记
        sql = "SELECT id, title, content, entry_date, mood, tags FROM diary_entries ORDER BY entry_date"
        rows = conn.execute(sql).fetchall()

        for row in rows:
            diary_id = row["id"]
            title = row["title"] or ""
            content = row["content"] or ""
            full_text = title + " " + content
            entry_date = row["entry_date"] or ""

            # 1. 情景记忆：原始日记（只保留摘要，避免重复存储）
            summary = self._extract_summary(full_text)
            if summary:
                importance = self._calculate_importance(full_text, entry_date)
                self._add_memory(diary_id, "episodic", summary, importance, entry_date)
                result["episodic"] += 1

            # 2. 语义自我：关于自己的知识、偏好、习惯
            self_knowledge = self._extract_self_knowledge(full_text)
            for knowledge in self_knowledge:
                self._add_memory(diary_id, "semantic_self", knowledge, 0.7, entry_date)
                result["semantic_self"] += 1

            # 3. 信念：核心信念、价值观
            beliefs = self._extract_beliefs(full_text)
            for belief in beliefs:
                self._add_memory(diary_id, "beliefs", belief, 0.8, entry_date)
                result["beliefs"] += 1
                # 同时存入信念表
                self._add_belief(belief, self._detect_belief_category(belief), entry_date)

            # 4. 关系：人际关系
            relationships = self._extract_relationships(full_text)
            for rel in relationships:
                self._add_memory(diary_id, "relationships", rel, 0.6, entry_date)
                result["relationships"] += 1

            # 5. 叙事状态：生活故事线、目标、阶段
            narrative = self._extract_narrative(full_text, entry_date)
            if narrative:
                self._add_memory(diary_id, "narrative", narrative, 0.65, entry_date)
                result["narrative"] += 1

            # 6. 日记摘要：压缩后的日记
            if summary:
                self._add_memory(diary_id, "diary", summary, 0.4, entry_date)
                result["diary"] += 1

            # 提取教训
            self._extract_lessons(full_text, diary_id, entry_date)

        return result

    def _extract_summary(self, text: str, max_len: int = 200) -> str:
        """提取日记摘要（前几句 + 关键句）。"""
        if not text:
            return ""

        # 按句子分割
        sentences = re.split(r"[。！？\n]", text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 5]

        if not sentences:
            return text[:max_len]

        # 取前 2-3 句作为摘要
        summary = "。".join(sentences[:3])
        return summary[:max_len]

    def _calculate_importance(self, text: str, entry_date: str) -> float:
        """计算记忆重要性（0-1）。决定/教训/情感时刻 → 高优先级。"""
        importance = 0.3  # 基础重要性

        # 决定关键词 → 高优先级
        decision_count = sum(text.count(kw) for kw in DECISION_KEYWORDS)
        if decision_count > 0:
            importance += 0.3

        # 教训关键词 → 高优先级
        lesson_count = sum(text.count(kw) for kw in LESSON_KEYWORDS)
        if lesson_count > 0:
            importance += 0.2

        # 情感时刻关键词 → 高优先级
        emotion_count = sum(text.count(kw) for kw in EMOTION_KEYWORDS)
        if emotion_count > 0:
            importance += 0.2

        # 长度加权（较长的日记可能更重要）
        if len(text) > 500:
            importance += 0.1

        return min(1.0, importance)

    def _extract_self_knowledge(self, text: str) -> list[str]:
        """提取关于自己的知识、偏好、习惯。"""
        knowledge = []
        patterns = [
            (r"我(喜欢|爱|爱好|擅长|习惯).{0,20}", "偏好"),
            (r"我(不喜欢|讨厌|害怕|抗拒).{0,20}", "厌恶"),
            (r"我(是|属于|典型的).{0,15}(人|性格|类型)", "性格"),
            (r"我(每天|经常|总是|偶尔).{0,15}", "习惯"),
        ]
        for pattern, _type in patterns:
            matches = re.findall(pattern, text)
            for m in matches:
                if isinstance(m, tuple):
                    m = "".join(m)
                if len(m) > 3:
                    knowledge.append(m[:50])
        return list(set(knowledge))[:5]  # 去重，最多 5 条

    def _extract_beliefs(self, text: str) -> list[str]:
        """提取核心信念、价值观。"""
        beliefs = []
        for pattern, _category, _template in BELIEF_PATTERNS:
            matches = re.findall(pattern, text)
            for m in matches:
                if isinstance(m, tuple):
                    m = "".join(m)
                if len(m) > 5:
                    beliefs.append(m[:80])
        return list(set(beliefs))[:3]

    def _detect_belief_category(self, belief: str) -> str:
        """检测信念类别。"""
        for pattern, category, _ in BELIEF_PATTERNS:
            if re.search(pattern, belief):
                return category
        return "general"

    def _extract_relationships(self, text: str) -> list[str]:
        """提取人际关系信息。"""
        relationships = []
        # 常见人物
        people = ["艳艳", "乐乐", "妈妈", "爸爸", "狄胖胖", "童先海", "周英"]
        for person in people:
            if person in text:
                # 提取与该人物相关的句子
                sentences = re.split(r"[。！？\n]", text)
                for s in sentences:
                    if person in s and len(s.strip()) > 5:
                        relationships.append(f"{person}: {s.strip()[:60]}")
                        break
        return relationships[:5]

    def _extract_narrative(self, text: str, entry_date: str) -> str:
        """提取生活故事线、目标、阶段。"""
        narrative_keywords = [
            "目标",
            "计划",
            "打算",
            "准备",
            "即将",
            "新阶段",
            "转折点",
            "开始",
            "结束",
        ]
        for kw in narrative_keywords:
            if kw in text:
                sentences = re.split(r"[。！？\n]", text)
                for s in sentences:
                    if kw in s and len(s.strip()) > 5:
                        return f"[{entry_date}] {s.strip()[:80]}"
        return ""

    def _extract_lessons(self, text: str, diary_id: int, entry_date: str) -> None:
        """从日记中提取教训。"""
        conn = self.store.conn
        for kw in LESSON_KEYWORDS:
            if kw in text:
                sentences = re.split(r"[。！？\n]", text)
                for s in sentences:
                    if kw in s and len(s.strip()) > 8:
                        lesson = s.strip()[:100]
                        # 检查是否已存在
                        existing = conn.execute(
                            "SELECT id FROM diary_lessons WHERE content = ?",
                            (lesson,),
                        ).fetchone()
                        if not existing:
                            conn.execute(
                                """
                                INSERT INTO diary_lessons
                                (content, diary_id, category, first_seen)
                                VALUES (?, ?, 'general', ?)
                                """,
                                (lesson, diary_id, entry_date),
                            )
                        break
        conn.commit()

    def _add_memory(
        self, diary_id: int, layer: str, content: str, importance: float, entry_date: str
    ) -> None:
        """添加记忆条目。"""
        conn = self.store.conn
        conn.execute(
            """
            INSERT INTO diary_advanced_memories
            (diary_id, layer, content, importance, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (diary_id, layer, content, importance, entry_date),
        )
        conn.commit()

    def _add_belief(self, content: str, category: str, first_seen: str) -> None:
        """添加或更新信念。"""
        conn = self.store.conn

        # 检查是否已存在相似信念
        existing = conn.execute(
            "SELECT id, evidence_count, confidence FROM diary_beliefs WHERE content = ?",
            (content,),
        ).fetchone()

        if existing:
            # 更新已有信念
            conn.execute(
                """
                UPDATE diary_beliefs
                SET evidence_count = evidence_count + 1,
                    last_seen = ?,
                    confidence = MIN(1.0, confidence + 0.05),
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (first_seen, existing["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO diary_beliefs
                (content, category, confidence, evidence_count, first_seen, last_seen)
                VALUES (?, ?, 0.5, 1, ?, ?)
                """,
                (content, category, first_seen, first_seen),
            )
        conn.commit()

    # ─── 信念冲突检测 ────────────────────────────────────────────────

    def detect_belief_conflicts(self) -> list[BeliefConflict]:
        """检测信念之间的冲突。"""
        conn = self.store.conn
        beliefs = conn.execute(
            "SELECT id, content, category, confidence FROM diary_beliefs WHERE status = 'active' ORDER BY id"
        ).fetchall()

        conflicts = []

        for i in range(len(beliefs)):
            for j in range(i + 1, len(beliefs)):
                b1 = beliefs[i]
                b2 = beliefs[j]

                # 简单冲突检测：关键词对立
                conflict_type = self._detect_conflict_type(b1["content"], b2["content"])
                if conflict_type:
                    # 检查是否已记录
                    existing = conn.execute(
                        """
                        SELECT id FROM diary_belief_conflicts
                        WHERE (belief_a_id = ? AND belief_b_id = ?)
                           OR (belief_a_id = ? AND belief_b_id = ?)
                        """,
                        (b1["id"], b2["id"], b2["id"], b1["id"]),
                    ).fetchone()

                    if not existing:
                        conn.execute(
                            """
                            INSERT INTO diary_belief_conflicts
                            (belief_a_id, belief_b_id, conflict_type)
                            VALUES (?, ?, ?)
                            """,
                            (b1["id"], b2["id"], conflict_type),
                        )
                        conn.commit()

                    conflicts.append(
                        BeliefConflict(
                            belief_a_id=b1["id"],
                            belief_b_id=b2["id"],
                            belief_a_content=b1["content"],
                            belief_b_content=b2["content"],
                            conflict_type=conflict_type,
                            detected_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        )
                    )

        return conflicts

    def _detect_conflict_type(self, text1: str, text2: str) -> str | None:
        """检测两段文字之间的冲突类型。"""
        # 对立关键词对
        opposites = [
            ("应该", "不应该"),
            ("必须", "不必"),
            ("重要", "不重要"),
            ("喜欢", "讨厌"),
            ("努力", "躺平"),
            ("乐观", "悲观"),
            ("相信", "怀疑"),
            ("健康", "无所谓"),
            ("家庭", "工作"),
        ]

        for pos, neg in opposites:
            if (pos in text1 and neg in text2) or (neg in text1 and pos in text2):
                return "contradiction"

        # 同一主题不同观点 → tension
        topics = ["工作", "家庭", "健康", "金钱", "人生", "未来"]
        for topic in topics:
            if (
                topic in text1
                and topic in text2
                and (("应该" in text1 or "必须" in text1) and ("不" in text2 or "别" in text2))
            ):
                return "tension"

        return None

    def get_beliefs(self, category: str | None = None, status: str = "active") -> list[Belief]:
        """获取信念列表。"""
        conn = self.store.conn
        if category:
            rows = conn.execute(
                "SELECT * FROM diary_beliefs WHERE category = ? AND status = ? ORDER BY confidence DESC",
                (category, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM diary_beliefs WHERE status = ? ORDER BY confidence DESC",
                (status,),
            ).fetchall()

        return [
            Belief(
                id=r["id"],
                content=r["content"],
                category=r["category"],
                confidence=r["confidence"],
                evidence_count=r["evidence_count"],
                first_seen=r["first_seen"] or "",
                last_seen=r["last_seen"] or "",
                version=r["version"],
                status=r["status"],
            )
            for r in rows
        ]

    # ─── 记忆巩固与遗忘曲线 ────────────────────────────────────────────

    def consolidate_memories(self) -> ConsolidationResult:
        """记忆巩固：运行遗忘曲线、提升/降级/归档/遗忘。"""
        result = ConsolidationResult(consolidated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        conn = self.store.conn

        # 获取所有记忆
        rows = conn.execute(
            "SELECT id, layer, content, importance, created_at, access_count FROM diary_advanced_memories"
        ).fetchall()

        result.entries_processed = len(rows)

        for row in rows:
            memory_id = row["id"]
            layer = row["layer"]
            importance = row["importance"]
            created_at = row["created_at"] or ""
            access_count = row["access_count"] or 0

            # 计算遗忘后的保留度（基于遗忘曲线）
            retention = self._calculate_retention(layer, created_at, access_count)

            # 调整重要性
            adjusted_importance = importance * retention

            # 根据调整后的重要性决定操作
            if adjusted_importance < 0.1 and layer != "beliefs":
                # 重要性过低 → 遗忘（只对非信念层）
                conn.execute("DELETE FROM diary_advanced_memories WHERE id = ?", (memory_id,))
                result.entries_forgotten += 1
            elif adjusted_importance > 0.7 and layer in ("episodic", "diary"):
                # 重要性高 → 提升到语义自我层
                conn.execute(
                    "UPDATE diary_advanced_memories SET layer = 'semantic_self', importance = ?, updated_at = datetime('now') WHERE id = ?",
                    (adjusted_importance, memory_id),
                )
                result.entries_promoted += 1
            elif adjusted_importance < 0.2 and layer == "semantic_self":
                # 语义自我重要性低 → 降级到日记层
                conn.execute(
                    "UPDATE diary_advanced_memories SET layer = 'diary', importance = ?, updated_at = datetime('now') WHERE id = ?",
                    (adjusted_importance, memory_id),
                )
                result.entries_demoted += 1
            else:
                # 更新重要性
                conn.execute(
                    "UPDATE diary_advanced_memories SET importance = ?, updated_at = datetime('now') WHERE id = ?",
                    (round(adjusted_importance, 4), memory_id),
                )

        conn.commit()

        # 检测信念冲突
        conflicts = self.detect_belief_conflicts()
        result.conflicts_detected = len(conflicts)

        # 记录巩固日志
        conn.execute(
            """
            INSERT INTO diary_consolidation_logs
            (entries_processed, entries_promoted, entries_demoted, entries_archived,
             entries_forgotten, beliefs_updated, conflicts_detected,
             lessons_validated, lessons_disputed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.entries_processed,
                result.entries_promoted,
                result.entries_demoted,
                result.entries_archived,
                result.entries_forgotten,
                result.beliefs_updated,
                result.conflicts_detected,
                result.lessons_validated,
                result.lessons_disputed,
            ),
        )
        conn.commit()

        return result

    def _calculate_retention(self, layer: str, created_at: str, access_count: int) -> float:
        """基于遗忘曲线计算记忆保留度。"""
        if not created_at:
            return 1.0

        try:
            created = datetime.strptime(created_at[:10], "%Y-%m-%d")
            days = (datetime.now() - created).days
        except (ValueError, TypeError):
            return 1.0

        # 半衰期（天）
        half_life = self.FORGETTING_CURVE.get(layer, 30)

        # 艾宾浩斯遗忘曲线：R = e^(-t / (half_life * ln2))
        # 但访问会增强记忆
        access_boost = 1.0 + min(access_count, 10) * 0.1  # 最多提升 100%
        effective_half_life = half_life * access_boost

        retention = math.exp(-days / (effective_half_life * 0.693))
        return max(0.05, min(1.0, retention))

    # ─── 夜间"梦境状态"回顾 ────────────────────────────────────────────

    def dream_state_review(self) -> DreamStateReview:
        """夜间"梦境状态"回顾：用新证据验证过去的教训。"""
        result = DreamStateReview(review_date=datetime.now().strftime("%Y-%m-%d"))
        conn = self.store.conn

        # 获取所有活跃教训
        lessons = conn.execute(
            "SELECT id, content, status, validation_count, dispute_count FROM diary_lessons WHERE status IN ('active', 'validated')"
        ).fetchall()

        result.lessons_reviewed = len(lessons)

        # 获取最近 30 天的日记内容，用于验证教训
        recent_rows = conn.execute(
            """
            SELECT content FROM diary_entries
            WHERE entry_date >= date('now', '-30 days')
            ORDER BY entry_date DESC
            """
        ).fetchall()

        recent_text = " ".join(r["content"] or "" for r in recent_rows)

        for lesson in lessons:
            lesson_id = lesson["id"]
            content = lesson["content"]

            # 简单验证：教训中的关键词是否在最近日记中出现
            keywords = self._extract_keywords(content)
            matches = sum(1 for kw in keywords if kw in recent_text)

            if matches > 0 and len(keywords) > 0:
                # 有新证据支持 → 验证
                conn.execute(
                    """
                    UPDATE diary_lessons
                    SET status = 'validated',
                        validation_count = validation_count + 1,
                        last_validated = datetime('now'),
                        confidence = MIN(1.0, confidence + 0.1),
                        updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (lesson_id,),
                )
                result.lessons_validated += 1
            elif matches == 0 and lesson["validation_count"] == 0 and len(recent_rows) > 10:
                # 没有新证据支持，且从未被验证 → 标记为有争议
                conn.execute(
                    """
                    UPDATE diary_lessons
                    SET status = 'disputed',
                        dispute_count = dispute_count + 1,
                        confidence = MAX(0.1, confidence - 0.05),
                        updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (lesson_id,),
                )
                result.lessons_disputed += 1

        conn.commit()

        # 发现新模式
        result.new_patterns = self._discover_new_patterns()

        # 生成洞察
        result.insights = self._generate_memory_insights()

        # 记忆健康评分
        result.memory_health_score = self._calculate_memory_health()

        return result

    def _extract_keywords(self, text: str) -> list[str]:
        """从文本中提取关键词（简单实现：取 2-4 字词）。"""
        words = re.findall(r"[\u4e00-\u9fa5]{2,4}", text)
        # 过滤停用词
        stopwords = {
            "的",
            "了",
            "是",
            "我",
            "在",
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
        }
        return [w for w in words if w not in stopwords][:5]

    def _discover_new_patterns(self) -> list[str]:
        """发现新的行为模式。"""
        conn = self.store.conn
        patterns = []

        # 最近 30 天 vs 之前 30 天的写作频率对比
        recent_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM diary_entries WHERE entry_date >= date('now', '-30 days')"
        ).fetchone()["cnt"]

        previous_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM diary_entries WHERE entry_date >= date('now', '-60 days') AND entry_date < date('now', '-30 days')"
        ).fetchone()["cnt"]

        if previous_count > 0:
            change = (recent_count - previous_count) / previous_count * 100
            if change > 30:
                patterns.append(f"近期写作频率提升 {change:.0f}%，表达欲增强")
            elif change < -30:
                patterns.append(f"近期写作频率下降 {abs(change):.0f}%，可能状态较低落")

        return patterns[:3]

    def _generate_memory_insights(self) -> list[str]:
        """生成记忆洞察。"""
        conn = self.store.conn
        insights = []

        # 信念统计
        belief_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM diary_beliefs WHERE status = 'active'"
        ).fetchone()["cnt"]
        if belief_count > 0:
            insights.append(f"已构建 {belief_count} 条核心信念，持续验证中")

        # 教训统计
        lesson_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM diary_lessons WHERE status = 'validated'"
        ).fetchone()["cnt"]
        if lesson_count > 0:
            insights.append(f"{lesson_count} 条教训已被新证据验证，经验在积累")

        # 记忆层数
        layer_counts = conn.execute(
            "SELECT layer, COUNT(*) as cnt FROM diary_advanced_memories GROUP BY layer"
        ).fetchall()
        if layer_counts:
            total = sum(r["cnt"] for r in layer_counts)
            insights.append(f"6 层记忆共 {total} 条，覆盖情景/自我/信念/关系/叙事/日记")

        return insights[:5]

    def _calculate_memory_health(self) -> float:
        """计算记忆健康评分（0-100）。"""
        conn = self.store.conn

        # 1. 记忆覆盖率（有多少日记被构建了记忆）
        total_diaries = conn.execute("SELECT COUNT(*) as cnt FROM diary_entries").fetchone()["cnt"]
        memories_with_diary = conn.execute(
            "SELECT COUNT(DISTINCT diary_id) as cnt FROM diary_advanced_memories WHERE diary_id IS NOT NULL"
        ).fetchone()["cnt"]
        coverage = memories_with_diary / total_diaries if total_diaries > 0 else 0

        # 2. 信念验证率
        total_beliefs = conn.execute("SELECT COUNT(*) as cnt FROM diary_beliefs").fetchone()["cnt"]
        high_confidence = conn.execute(
            "SELECT COUNT(*) as cnt FROM diary_beliefs WHERE confidence > 0.7"
        ).fetchone()["cnt"]
        belief_health = high_confidence / total_beliefs if total_beliefs > 0 else 0.5

        # 3. 教训验证率
        total_lessons = conn.execute("SELECT COUNT(*) as cnt FROM diary_lessons").fetchone()["cnt"]
        validated = conn.execute(
            "SELECT COUNT(*) as cnt FROM diary_lessons WHERE status = 'validated'"
        ).fetchone()["cnt"]
        lesson_health = validated / total_lessons if total_lessons > 0 else 0.3

        # 综合评分
        health_score = coverage * 40 + belief_health * 30 + lesson_health * 30
        return round(health_score, 1)

    # ─── 查询接口 ──────────────────────────────────────────────────────

    def get_memory_layer_stats(self) -> list[MemoryLayer]:
        """获取各记忆层统计。"""
        conn = self.store.conn
        rows = conn.execute(
            """
            SELECT layer, COUNT(*) as count, AVG(importance) as avg_importance
            FROM diary_advanced_memories
            GROUP BY layer
            ORDER BY count DESC
            """
        ).fetchall()

        stats = []
        for r in rows:
            stats.append(
                MemoryLayer(
                    layer=r["layer"],
                    count=r["count"],
                    avg_importance=round(r["avg_importance"] or 0, 4),
                )
            )

        # 补充空层
        existing_layers = {s.layer for s in stats}
        for layer in self.LAYERS:
            if layer not in existing_layers:
                stats.append(MemoryLayer(layer=layer, count=0, avg_importance=0))

        return stats

    def search_memories(
        self, query: str, layer: str | None = None, min_importance: float = 0.0, limit: int = 20
    ) -> list[dict[str, Any]]:
        """搜索记忆。"""
        conn = self.store.conn

        sql = "SELECT * FROM diary_advanced_memories WHERE importance >= ?"
        params = [min_importance]

        if layer:
            sql += " AND layer = ?"
            params.append(layer)

        if query:
            sql += " AND content LIKE ?"
            params.append(f"%{query}%")

        sql += " ORDER BY importance DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()

        results = []
        for r in rows:
            # 更新访问计数
            conn.execute(
                "UPDATE diary_advanced_memories SET access_count = access_count + 1, last_accessed = datetime('now') WHERE id = ?",
                (r["id"],),
            )
            results.append(
                {
                    "id": r["id"],
                    "diary_id": r["diary_id"],
                    "layer": r["layer"],
                    "layer_name": self.LAYER_NAMES_CN.get(r["layer"], r["layer"]),
                    "content": r["content"],
                    "importance": r["importance"],
                    "access_count": r["access_count"],
                    "created_at": r["created_at"],
                }
            )

        conn.commit()
        return results

    def get_overview(self) -> dict[str, Any]:
        """获取高级记忆系统概览。"""
        layer_stats = self.get_memory_layer_stats()
        beliefs = self.get_beliefs()
        conflicts = self.detect_belief_conflicts()

        return {
            "layers": [s.to_dict() for s in layer_stats],
            "layer_names": self.LAYER_NAMES_CN,
            "total_memories": sum(s.count for s in layer_stats),
            "total_beliefs": len(beliefs),
            "total_conflicts": len(conflicts),
            "belief_categories": dict(Counter(b.category for b in beliefs)),
        }

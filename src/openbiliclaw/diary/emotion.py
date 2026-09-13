"""情绪分析模块——效价/唤醒二维模型。

参考 emergent-diary-agent 的设计：
- 不用 LLM 自由命名情绪（容易崩溃到 happy/sad/anxious 三个标签）
- 用 valence（效价/愉悦度）+ arousal（唤醒度/活跃度）二维坐标
- 通过代码端字典映射到丰富的情绪标签
- 效价范围：-1（非常不愉快）到 +1（非常愉快）
- 唤醒范围：-1（非常平静/低能量）到 +1（非常兴奋/高能量）
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .store import DiaryStore

logger = logging.getLogger(__name__)


# ─── 数据结构 ────────────────────────────────────────────────────────


@dataclass
class ValenceArousal:
    """效价/唤醒二维情绪坐标。"""

    valence: float = 0.0  # 愉悦度：-1 到 +1
    arousal: float = 0.0  # 活跃度：-1 到 +1
    confidence: float = 0.0  # 置信度：0 到 1
    emotion_label: str = "neutral"  # 映射后的情绪标签
    evidence: list[str] = field(default_factory=list)  # 证据关键词

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def distance_to(self, other: ValenceArousal) -> float:
        """计算与另一个情绪点的欧氏距离。"""
        return math.sqrt((self.valence - other.valence) ** 2 + (self.arousal - other.arousal) ** 2)


@dataclass
class EmotionTrendPoint:
    """情绪趋势点。"""

    date: str
    valence: float
    arousal: float
    emotion_label: str
    entry_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EmotionForecast:
    """情绪预测结果。"""

    forecast_date: str
    predicted_valence: float
    predicted_arousal: float
    predicted_emotion: str
    confidence: float
    trend: str  # improving / declining / stable
    factors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BurnoutAssessment:
    """倦怠评估结果。"""

    assessment_date: str
    overall_score: float = 0.0  # 0-100，越高越倦怠
    level: str = "healthy"  # healthy / mild / moderate / severe
    emotional_exhaustion: float = 0.0  # 情绪耗竭
    depersonalization: float = 0.0  # 去人格化
    reduced_accomplishment: float = 0.0  # 个人成就感降低
    writing_consistency: float = 0.0  # 写作一致性
    negative_keyword_density: float = 0.0  # 负面关键词密度
    sleep_health_concern: float = 0.0  # 睡眠/健康关注
    warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─── 情绪关键词词典 ──────────────────────────────────────────────────

# 效价关键词（愉悦度）
VALENCE_POSITIVE = {
    "开心": 0.6,
    "高兴": 0.5,
    "快乐": 0.7,
    "幸福": 0.8,
    "愉快": 0.5,
    "兴奋": 0.5,
    "激动": 0.4,
    "期待": 0.4,
    "希望": 0.5,
    "乐观": 0.5,
    "满足": 0.6,
    "满意": 0.5,
    "感恩": 0.6,
    "感动": 0.5,
    "温暖": 0.5,
    "舒适": 0.4,
    "放松": 0.3,
    "平静": 0.2,
    "安心": 0.4,
    "踏实": 0.4,
    "喜欢": 0.5,
    "爱": 0.7,
    "热爱": 0.6,
    "有趣": 0.4,
    "好玩": 0.4,
    "棒": 0.5,
    "好": 0.3,
    "不错": 0.3,
    "太好了": 0.6,
    "太棒了": 0.7,
    "成功": 0.5,
    "完成": 0.4,
    "达成": 0.5,
    "收获": 0.4,
    "进步": 0.4,
}

VALENCE_NEGATIVE = {
    "难过": -0.6,
    "伤心": -0.6,
    "悲伤": -0.7,
    "痛苦": -0.7,
    "难受": -0.5,
    "焦虑": -0.5,
    "紧张": -0.4,
    "担心": -0.4,
    "害怕": -0.6,
    "恐惧": -0.7,
    "压力": -0.5,
    "压抑": -0.5,
    "郁闷": -0.5,
    "烦躁": -0.4,
    "生气": -0.5,
    "愤怒": -0.6,
    "失望": -0.5,
    "绝望": -0.8,
    "沮丧": -0.6,
    "低落": -0.5,
    "孤独": -0.5,
    "寂寞": -0.4,
    "无聊": -0.3,
    "疲惫": -0.4,
    "累": -0.3,
    "讨厌": -0.5,
    "恨": -0.7,
    "烦": -0.3,
    "糟": -0.5,
    "糟糕": -0.6,
    "失败": -0.5,
    "错过": -0.4,
    "遗憾": -0.4,
    "后悔": -0.5,
    "愧疚": -0.5,
    "生病": -0.4,
    "不舒服": -0.3,
    "疼": -0.4,
    "痛": -0.5,
}

# 唤醒关键词（活跃度）
AROUSAL_HIGH = {
    "兴奋": 0.6,
    "激动": 0.7,
    "紧张": 0.5,
    "焦虑": 0.4,
    "愤怒": 0.6,
    "生气": 0.5,
    "恐惧": 0.6,
    "害怕": 0.5,
    "期待": 0.4,
    "热情": 0.5,
    "激情": 0.6,
    "活力": 0.5,
    "精力充沛": 0.6,
    "忙碌": 0.4,
    "忙": 0.3,
    "加班": 0.4,
    "赶": 0.4,
    "急": 0.4,
    "冲": 0.5,
    "拼": 0.4,
    "运动": 0.4,
    "跑步": 0.4,
    "健身": 0.4,
    "跳舞": 0.5,
    "唱歌": 0.4,
    "旅行": 0.3,
    "出去玩": 0.4,
    "聚会": 0.4,
    "热闹": 0.4,
    "吵": 0.3,
    "哭": 0.4,
    "笑": 0.3,
    "大喊": 0.5,
}

AROUSAL_LOW = {
    "平静": -0.5,
    "放松": -0.4,
    "安静": -0.4,
    "宁静": -0.5,
    "安宁": -0.5,
    "疲惫": -0.5,
    "累": -0.4,
    "困倦": -0.5,
    "困": -0.4,
    "想睡觉": -0.5,
    "无聊": -0.4,
    "空虚": -0.4,
    "麻木": -0.5,
    "发呆": -0.4,
    "走神": -0.3,
    "忧郁": -0.3,
    "低落": -0.3,
    "消沉": -0.4,
    "萎靡": -0.5,
    "无精打采": -0.5,
    "休息": -0.3,
    "睡觉": -0.5,
    "躺": -0.4,
    "宅": -0.3,
    "宅家": -0.3,
    "看书": -0.2,
    "读书": -0.2,
    "冥想": -0.4,
    "瑜伽": -0.2,
    "散步": -0.1,
}

# 情绪标签映射（效价/唤醒象限 → 情绪标签）
EMOTION_LABELS = [
    # (valence_min, valence_max, arousal_min, arousal_max, label)
    (0.5, 1.0, 0.5, 1.0, "ecstatic"),  # 狂喜
    (0.3, 0.7, 0.2, 0.6, "happy"),  # 开心
    (0.5, 1.0, -0.3, 0.3, "content"),  # 满足
    (0.2, 0.6, -0.6, -0.2, "calm"),  # 平静
    (0.0, 0.4, 0.3, 0.7, "excited"),  # 兴奋
    (-0.2, 0.2, -0.2, 0.2, "neutral"),  # 中性
    (-0.5, -0.1, 0.3, 0.7, "anxious"),  # 焦虑
    (-0.7, -0.3, 0.5, 1.0, "angry"),  # 愤怒
    (-0.7, -0.3, -0.5, -0.1, "sad"),  # 悲伤
    (-0.5, -0.1, -0.6, -0.2, "tired"),  # 疲惫
    (-0.3, 0.1, 0.4, 0.8, "stressed"),  # 压力
    (0.1, 0.5, -0.5, -0.1, "relaxed"),  # 放松
    (-0.6, -0.2, -0.3, 0.1, "depressed"),  # 抑郁
    (0.3, 0.7, 0.4, 0.8, "enthusiastic"),  # 热情
    (-0.4, 0.0, -0.4, 0.0, "bored"),  # 无聊
]

# 中文情绪标签
EMOTION_LABELS_CN = {
    "ecstatic": "狂喜",
    "happy": "开心",
    "content": "满足",
    "calm": "平静",
    "excited": "兴奋",
    "neutral": "中性",
    "anxious": "焦虑",
    "angry": "愤怒",
    "sad": "悲伤",
    "tired": "疲惫",
    "stressed": "压力",
    "relaxed": "放松",
    "depressed": "抑郁",
    "enthusiastic": "热情",
    "bored": "无聊",
}

# 细粒度情绪标签 → DiaryEntry.mood（MoodLevel 枚举值）
#
# 为什么需要这张表：diary_entries.mood 是画像（emotional_baseline）和复盘链路
# 实际消费的字段，而 EmotionAnalyzer 产出的是 valence/arousal + 细粒度标签。
# 早期版本只写 diary_emotion_analyses、从不回写 entries，导致画像侧恒为 unknown。
MOOD_LEVEL_BY_EMOTION_LABEL: dict[str, str] = {
    "ecstatic": "very_happy",
    "enthusiastic": "very_happy",
    "happy": "happy",
    "content": "happy",
    "excited": "happy",
    "calm": "neutral",
    "relaxed": "neutral",
    "neutral": "neutral",
    "bored": "neutral",
    "tired": "sad",
    "stressed": "anxious",
    "anxious": "anxious",
    "sad": "sad",
    "depressed": "very_sad",
    "angry": "angry",
}

# MoodLevel → 先验效价。用于条目已带 mood 标注时做加权融合（见 analyze_diary）。
# 注意：mood 存的是 "happy" 这类枚举字符串，不是数字。
MOOD_PRIOR_VALENCE: dict[str, float] = {
    "very_happy": 0.8,
    "happy": 0.5,
    "neutral": 0.0,
    "sad": -0.5,
    "very_sad": -0.8,
    "angry": -0.6,
    "anxious": -0.4,
}


def mood_level_for_emotion(emotion_label: str) -> str:
    """把细粒度情绪标签映射回 ``DiaryEntry.mood`` 的枚举值。

    Args:
        emotion_label: ``EMOTION_LABELS`` 中的标签，如 ``happy`` / ``anxious``。

    Returns:
        ``MoodLevel`` 的字符串值；未知标签一律回落 ``neutral``。

    """
    return MOOD_LEVEL_BY_EMOTION_LABEL.get(emotion_label, "neutral")


# ─── 情绪分析服务 ────────────────────────────────────────────────────


class EmotionAnalyzer:
    """情绪分析服务——效价/唤醒二维模型。"""

    def __init__(self, store: DiaryStore):
        self.store = store
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """确保数据库表存在。"""
        conn = self.store.conn
        conn.execute("""
            CREATE TABLE IF NOT EXISTS diary_emotion_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                diary_id INTEGER UNIQUE NOT NULL,
                valence REAL DEFAULT 0,
                arousal REAL DEFAULT 0,
                confidence REAL DEFAULT 0,
                emotion_label TEXT DEFAULT 'neutral',
                evidence TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()

    def analyze_text(self, text: str) -> ValenceArousal:
        """分析文本的效价/唤醒坐标。"""
        if not text:
            return ValenceArousal(valence=0, arousal=0, confidence=0, emotion_label="neutral")

        valence_scores = []
        arousal_scores = []
        evidence = []

        # 效价分析
        for keyword, score in VALENCE_POSITIVE.items():
            count = text.count(keyword)
            if count > 0:
                valence_scores.append(score * min(count, 3))
                evidence.append(f"+{keyword}({count})")

        for keyword, score in VALENCE_NEGATIVE.items():
            count = text.count(keyword)
            if count > 0:
                valence_scores.append(score * min(count, 3))
                evidence.append(f"{keyword}({count})")

        # 唤醒分析
        for keyword, score in AROUSAL_HIGH.items():
            count = text.count(keyword)
            if count > 0:
                arousal_scores.append(score * min(count, 3))

        for keyword, score in AROUSAL_LOW.items():
            count = text.count(keyword)
            if count > 0:
                arousal_scores.append(score * min(count, 3))

        # 计算平均值（带权重，避免极端值主导）
        if valence_scores:
            valence = sum(valence_scores) / len(valence_scores)
            valence = max(-1.0, min(1.0, valence))
        else:
            valence = 0.0

        if arousal_scores:
            arousal = sum(arousal_scores) / len(arousal_scores)
            arousal = max(-1.0, min(1.0, arousal))
        else:
            arousal = 0.0

        # 置信度（基于匹配到的关键词数量）
        total_matches = len(valence_scores) + len(arousal_scores)
        confidence = min(1.0, total_matches / 10.0)

        # 映射情绪标签
        emotion_label = self._map_to_emotion_label(valence, arousal)

        return ValenceArousal(
            valence=round(valence, 4),
            arousal=round(arousal, 4),
            confidence=round(confidence, 4),
            emotion_label=emotion_label,
            evidence=evidence[:10],
        )

    def _map_to_emotion_label(self, valence: float, arousal: float) -> str:
        """将效价/唤醒坐标映射到情绪标签。"""
        for v_min, v_max, a_min, a_max, label in EMOTION_LABELS:
            if v_min <= valence <= v_max and a_min <= arousal <= a_max:
                return label
        return "neutral"

    def analyze_diary(self, diary_id: int, *, use_mood_prior: bool = False) -> ValenceArousal | None:
        """分析单篇日记的情绪。

        Args:
            diary_id: 日记 ID。
            use_mood_prior: 是否把条目上已有的 ``mood`` 当作效价先验做加权融合。
                **默认关闭** —— ``mood`` 是本方法的输出而非输入：一旦开启，
                回填后的条目每次重跑都会被上一轮结果再压 0.6 倍，效价反复衰减到 0。
                只有明确知道 ``mood`` 来自人工标注时才应开启。

        Returns:
            效价/唤醒坐标；日记不存在时返回 None。

        """
        conn = self.store.conn
        row = conn.execute(
            "SELECT id, title, content, mood FROM diary_entries WHERE id = ?",
            (diary_id,),
        ).fetchone()

        if row is None:
            return None

        text = (row["title"] or "") + " " + (row["content"] or "")
        result = self.analyze_text(text)

        # 可选：把条目已有的 mood 标注作为效价先验融合进去。
        # mood 是 MoodLevel 字符串（"happy" / "unknown"…）而非数字——早期版本在这里
        # float(row["mood"])，恒抛 ValueError 被吞掉，这段融合从未真正生效过。
        # 现在改为显式开关，默认关闭（见 analyze_diary 文档）。
        prior = MOOD_PRIOR_VALENCE.get(str(row["mood"] or "").lower()) if use_mood_prior else None
        if prior is not None:
            result.valence = round(result.valence * 0.6 + prior * 0.4, 4)
            result.confidence = round(min(1.0, result.confidence + 0.2), 4)
            result.emotion_label = self._map_to_emotion_label(result.valence, result.arousal)

        # 保存到数据库
        conn.execute(
            """
            INSERT OR REPLACE INTO diary_emotion_analyses
            (diary_id, valence, arousal, confidence, emotion_label, evidence)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                diary_id,
                result.valence,
                result.arousal,
                result.confidence,
                result.emotion_label,
                json.dumps(result.evidence, ensure_ascii=False),
            ),
        )

        # 回写到 diary_entries：分析表是明细，entries 侧才是画像/复盘的消费字段。
        # 缺了这一步，diary_entries.mood 永远停在 unknown，emotional_baseline 全废。
        self._write_entry_mood(
            diary_id,
            mood_level_for_emotion(result.emotion_label),
            result.valence,
            commit=True,
        )

        return result

    def _write_entry_mood(self, diary_id: int, mood: str, mood_score: float, *, commit: bool = True) -> None:
        """把情绪结论写回 ``diary_entries.mood`` / ``mood_score``。"""
        conn = self.store.conn
        conn.execute(
            """
            UPDATE diary_entries
            SET mood = ?, mood_score = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (mood, round(mood_score, 4), diary_id),
        )
        if commit:
            conn.commit()

    def backfill_entry_moods(self, *, only_unknown: bool = True) -> int:
        """把已有的分析结果回填到 ``diary_entries``。

        用于修复历史数据：分析表有数据、但条目侧仍是 unknown 的情况（本项目的
        存量 925 篇正是如此）。

        Args:
            only_unknown: 为 True 时只回填 ``mood = 'unknown'`` 的条目，
                保留人工或非分析来源写入的情绪标注。

        Returns:
            实际更新的条数。

        """
        conn = self.store.conn
        rows = conn.execute(
            """
            SELECT ea.diary_id, ea.valence, ea.emotion_label, de.mood AS entry_mood
            FROM diary_emotion_analyses ea
            JOIN diary_entries de ON de.id = ea.diary_id
            ORDER BY ea.diary_id
            """
        ).fetchall()

        updated = 0
        for row in rows:
            if only_unknown and (row["entry_mood"] or "unknown") != "unknown":
                continue
            self._write_entry_mood(
                int(row["diary_id"]),
                mood_level_for_emotion(row["emotion_label"] or "neutral"),
                float(row["valence"] or 0.0),
                commit=False,
            )
            updated += 1

        conn.commit()
        return updated

    def analyze_all_diaries(self) -> int:
        """分析所有日记的情绪。返回分析的数量。"""
        conn = self.store.conn
        rows = conn.execute("SELECT id FROM diary_entries ORDER BY id").fetchall()

        count = 0
        for row in rows:
            if self.analyze_diary(row["id"]):
                count += 1

        return count

    def get_emotion_trend(self, days: int = 30) -> list[EmotionTrendPoint]:
        """获取情绪趋势（按天聚合）。"""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        conn = self.store.conn
        rows = conn.execute(
            """
            SELECT
                de.entry_date as date,
                AVG(ea.valence) as valence,
                AVG(ea.arousal) as arousal,
                COUNT(*) as entry_count
            FROM diary_emotion_analyses ea
            JOIN diary_entries de ON ea.diary_id = de.id
            WHERE de.entry_date >= ?
            GROUP BY de.entry_date
            ORDER BY de.entry_date ASC
            """,
            (start_date.strftime("%Y-%m-%d"),),
        ).fetchall()

        trend = []
        for row in rows:
            valence = row["valence"] or 0
            arousal = row["arousal"] or 0
            label = self._map_to_emotion_label(valence, arousal)
            trend.append(
                EmotionTrendPoint(
                    date=row["date"],
                    valence=round(valence, 4),
                    arousal=round(arousal, 4),
                    emotion_label=label,
                    entry_count=row["entry_count"],
                )
            )

        return trend

    def forecast_emotion(self, days: int = 7) -> list[EmotionForecast]:
        """预测未来 N 天的情绪。基于线性回归 + 周期性。"""
        # 获取过去 30 天的趋势
        trend = self.get_emotion_trend(days=30)
        if len(trend) < 3:
            return []

        # 简单线性回归预测
        valences = [p.valence for p in trend]
        arousals = [p.arousal for p in trend]

        # 计算趋势斜率
        n = len(valences)
        x_mean = (n - 1) / 2
        y_valence_mean = sum(valences) / n
        y_arousal_mean = sum(arousals) / n

        valence_slope = (
            sum((i - x_mean) * (valences[i] - y_valence_mean) for i in range(n))
            / sum((i - x_mean) ** 2 for i in range(n))
            if n > 1
            else 0
        )

        arousal_slope = (
            sum((i - x_mean) * (arousals[i] - y_arousal_mean) for i in range(n))
            / sum((i - x_mean) ** 2 for i in range(n))
            if n > 1
            else 0
        )

        # 生成预测
        forecasts = []
        today = datetime.now()
        last_valence = valences[-1]
        last_arousal = arousals[-1]

        # 判断趋势
        if valence_slope > 0.01:
            trend_str = "improving"
        elif valence_slope < -0.01:
            trend_str = "declining"
        else:
            trend_str = "stable"

        for i in range(1, days + 1):
            forecast_date = (today + timedelta(days=i)).strftime("%Y-%m-%d")
            # 线性预测 + 均值回归（向长期均值靠拢）
            predicted_valence = last_valence + valence_slope * i * 0.3 + y_valence_mean * 0.1
            predicted_arousal = last_arousal + arousal_slope * i * 0.3 + y_arousal_mean * 0.1

            # 限制范围
            predicted_valence = max(-1.0, min(1.0, predicted_valence))
            predicted_arousal = max(-1.0, min(1.0, predicted_arousal))

            emotion = self._map_to_emotion_label(predicted_valence, predicted_arousal)
            confidence = max(0.3, 1.0 - i * 0.1)  # 越远置信度越低

            factors = []
            if valence_slope > 0.01:
                factors.append("近期情绪呈上升趋势")
            elif valence_slope < -0.01:
                factors.append("近期情绪呈下降趋势，注意调节")
            else:
                factors.append("近期情绪相对稳定")

            forecasts.append(
                EmotionForecast(
                    forecast_date=forecast_date,
                    predicted_valence=round(predicted_valence, 4),
                    predicted_arousal=round(predicted_arousal, 4),
                    predicted_emotion=emotion,
                    confidence=round(confidence, 4),
                    trend=trend_str,
                    factors=factors,
                )
            )

        return forecasts

    def assess_burnout(self, days: int = 30) -> BurnoutAssessment:
        """倦怠评估。结合情绪历史、写作一致性、负面关键词密度、睡眠/健康关注。"""
        assessment_date = datetime.now().strftime("%Y-%m-%d")
        result = BurnoutAssessment(assessment_date=assessment_date)

        # 获取情绪趋势
        trend = self.get_emotion_trend(days=days)
        if not trend:
            return result

        # 1. 情绪耗竭（效价持续偏低 + 唤醒偏低）
        recent_valences = [p.valence for p in trend[-7:]] if len(trend) >= 7 else [p.valence for p in trend]
        recent_arousals = [p.arousal for p in trend[-7:]] if len(trend) >= 7 else [p.arousal for p in trend]

        avg_valence = sum(recent_valences) / len(recent_valences)
        avg_arousal = sum(recent_arousals) / len(recent_arousals)

        # 情绪耗竭评分（0-100）：效价越低、唤醒越低，耗竭越高
        result.emotional_exhaustion = round(
            max(0, min(100, ((-avg_valence + 1) / 2 * 60 + (-avg_arousal + 1) / 2 * 40))),
            1,
        )

        # 2. 去人格化（负面情绪比例）
        negative_emotions = {"anxious", "angry", "sad", "stressed", "depressed", "bored"}
        negative_count = sum(1 for p in trend if p.emotion_label in negative_emotions)
        result.depersonalization = round(negative_count / len(trend) * 100, 1)

        # 3. 个人成就感降低（积极情绪比例的反向）
        positive_emotions = {"happy", "content", "excited", "enthusiastic", "ecstatic"}
        positive_count = sum(1 for p in trend if p.emotion_label in positive_emotions)
        result.reduced_accomplishment = round((1 - positive_count / len(trend)) * 100, 1)

        # 4. 写作一致性（写作频率波动越大，可能越倦怠）
        entry_counts = [p.entry_count for p in trend]
        if entry_counts:
            mean_count = sum(entry_counts) / len(entry_counts)
            if mean_count > 0:
                variance = sum((c - mean_count) ** 2 for c in entry_counts) / len(entry_counts)
                cv = math.sqrt(variance) / mean_count  # 变异系数
                result.writing_consistency = round(min(100, cv * 50), 1)

        # 5. 负面关键词密度 + 6. 睡眠/健康关注
        conn = self.store.conn
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = conn.execute(
            "SELECT content FROM diary_entries WHERE entry_date >= ?",
            (start_date,),
        ).fetchall()

        negative_keywords = [
            "累",
            "疲惫",
            "困",
            "压力",
            "焦虑",
            "烦",
            "不想",
            "无力",
            "空虚",
            "麻木",
        ]
        sleep_health_keywords = [
            "失眠",
            "睡不着",
            "熬夜",
            "生病",
            "不舒服",
            "头疼",
            "胃疼",
            "吃药",
            "医院",
        ]

        total_chars = 0
        negative_count = 0
        sleep_health_count = 0

        for row in rows:
            content = row["content"] or ""
            total_chars += len(content)
            for kw in negative_keywords:
                negative_count += content.count(kw)
            for kw in sleep_health_keywords:
                sleep_health_count += content.count(kw)

        if total_chars > 0:
            result.negative_keyword_density = round(negative_count / total_chars * 1000, 2)
            result.sleep_health_concern = round(sleep_health_count / total_chars * 1000, 2)

        # 综合倦怠评分（加权平均）
        result.overall_score = round(
            result.emotional_exhaustion * 0.3
            + result.depersonalization * 0.2
            + result.reduced_accomplishment * 0.15
            + result.writing_consistency * 0.1
            + min(100, result.negative_keyword_density * 10) * 0.15
            + min(100, result.sleep_health_concern * 20) * 0.1,
            1,
        )

        # 倦怠等级
        if result.overall_score < 30:
            result.level = "healthy"
        elif result.overall_score < 50:
            result.level = "mild"
        elif result.overall_score < 70:
            result.level = "moderate"
        else:
            result.level = "severe"

        # 警告和建议
        if result.emotional_exhaustion > 60:
            result.warnings.append("情绪耗竭程度较高，注意休息和情绪调节")
            result.recommendations.append("尝试每天安排 15 分钟放松时间，做一些让自己开心的小事")
        if result.depersonalization > 50:
            result.warnings.append("负面情绪出现频率较高")
            result.recommendations.append("记录每天三件好事，培养感恩心态")
        if result.sleep_health_concern > 2:
            result.warnings.append("睡眠/健康问题提及较多，注意身体信号")
            result.recommendations.append("优先保证睡眠，必要时咨询医生")
        if result.overall_score > 50:
            result.recommendations.append("考虑减少工作负荷，给自己安排一些恢复性活动")
            result.recommendations.append("如果持续感到倦怠，建议寻求专业心理咨询")

        if not result.warnings:
            result.warnings.append("当前状态良好，继续保持")

        return result

    def get_emotion_stats(self) -> dict[str, Any]:
        """获取情绪统计概览。"""
        conn = self.store.conn

        # 总体统计
        row = conn.execute(
            """
            SELECT
                COUNT(*) as total,
                AVG(valence) as avg_valence,
                AVG(arousal) as avg_arousal,
                AVG(confidence) as avg_confidence
            FROM diary_emotion_analyses
            """
        ).fetchone()

        # 情绪标签分布
        label_rows = conn.execute(
            """
            SELECT emotion_label, COUNT(*) as count
            FROM diary_emotion_analyses
            GROUP BY emotion_label
            ORDER BY count DESC
            """
        ).fetchall()

        label_distribution = {}
        for r in label_rows:
            cn_label = EMOTION_LABELS_CN.get(r["emotion_label"], r["emotion_label"])
            label_distribution[cn_label] = r["count"]

        return {
            "total_analyzed": row["total"] or 0,
            "avg_valence": round(row["avg_valence"] or 0, 4),
            "avg_arousal": round(row["avg_arousal"] or 0, 4),
            "avg_confidence": round(row["avg_confidence"] or 0, 4),
            "dominant_emotion": EMOTION_LABELS_CN.get(label_rows[0]["emotion_label"], "neutral")
            if label_rows
            else "neutral",
            "label_distribution": label_distribution,
        }

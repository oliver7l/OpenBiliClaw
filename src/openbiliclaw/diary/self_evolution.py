"""自进化日记系统核心模块。

实现夜间自我改进循环：用户画像自动构建与更新、漂移检测、夜间日志生成、
标签自动优化。让日记系统从被动记录工具升级为主动学习、自我进化的 AI 伙伴。

借鉴项目：
- OpenGriffin：夜间 4:30 AM 自我改进循环、漂移检测、可读的夜间日志
- Dear Agent：reflect 主动循环、模式发现
- Doppelganger AI：重要性评分、记忆分层
"""

from __future__ import annotations

import json
import logging
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import DiaryEntry
    from .store import DiaryStore

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════


@dataclass
class UserProfile:
    """用户画像：多维度描述用户的性格、偏好、关注、关系、情绪和价值观。"""

    profile_date: str  # 画像日期（YYYY-MM-DD）

    # 性格特质（从语言风格、情绪表达推断）
    personality: dict[str, float] = field(default_factory=dict)
    # 例如：{"introversion": 0.6, "emotional": 0.7, "optimism": 0.5, "analytical": 0.4}

    # 偏好习惯（作息、饮食、运动、娱乐）
    habits: dict[str, Any] = field(default_factory=dict)
    # 例如：{"sleep_pattern": "night_owl", "exercise_frequency": "weekly"}

    # 关注焦点分布（各领域的关注度百分比）
    focus_distribution: dict[str, float] = field(default_factory=dict)
    # 例如：{"work": 0.35, "family": 0.25, "health": 0.15, "finance": 0.1}

    # 人际关系（核心人物及亲密度）
    relationships: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 例如：{"艳艳": {"intimacy": 0.9, "trend": "stable", "recent_frequency": 5}}

    # 情绪基调
    emotional_baseline: dict[str, Any] = field(default_factory=dict)
    # 例如：{"average_mood": 0.3, "volatility": 0.4, "positive_ratio": 0.6}

    # 价值观（核心信念和人生态度）
    values: list[str] = field(default_factory=list)
    # 例如：["家庭最重要", "持续学习成长", "追求工作生活平衡"]

    # 写作习惯
    writing_pattern: dict[str, Any] = field(default_factory=dict)
    # 例如：{"average_length": 500, "writing_frequency": "daily", "preferred_time": "evening"}

    # 元数据
    total_entries_analyzed: int = 0
    last_updated: str = ""

    def to_dict(self) -> dict:
        """序列化为字典。"""
        return asdict(self)


@dataclass
class DriftEvent:
    """漂移事件：检测到的用户行为/情绪/关注/关系的显著变化。"""

    drift_id: str
    drift_type: str  # behavior / emotion / focus / relationship / writing
    severity: str  # info / warning / alert
    title: str
    description: str
    evidence: list[dict[str, Any]]  # 证据列表（日记引用、数据对比）
    current_value: Any
    previous_value: Any
    change_percent: float  # 变化百分比
    detected_at: str
    status: str = "new"  # new / acknowledged / dismissed

    def to_dict(self) -> dict:
        """序列化为字典。"""
        return asdict(self)


@dataclass
class NightlyLog:
    """夜间日志：系统每天夜间运行后生成的可读日志。"""

    log_date: str  # 日志日期（分析的是前一天的数据）
    generated_at: str

    # 今日摘要
    daily_summary: dict[str, Any] = field(default_factory=dict)
    # 例如：{"entry_count": 2, "total_words": 800, "average_mood": 0.4, "top_tags": [...]}

    # 学到了什么（关于用户的新信息）
    learnings: list[str] = field(default_factory=list)

    # 发现的模式
    patterns: list[dict[str, Any]] = field(default_factory=list)
    # 例如：[{"type": "emotion_pattern", "description": "...", "evidence": [...]}]

    # 漂移警报
    drifts: list[DriftEvent] = field(default_factory=list)

    # 系统建议（温和不说教）
    suggestions: list[str] = field(default_factory=list)

    # 自我优化（系统对自己的调整）
    self_optimizations: list[str] = field(default_factory=list)

    # 标签优化建议
    tag_optimizations: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """序列化为字典。"""
        d = asdict(self)
        d["drifts"] = [drift.to_dict() for drift in self.drifts]
        return d


@dataclass
class TagOptimization:
    """标签优化建议。"""

    new_tags: list[dict[str, Any]] = field(default_factory=list)
    # 例如：[{"tag": "自驾游", "count": 15, "sample_entries": [...]}]
    merge_suggestions: list[dict[str, Any]] = field(default_factory=list)
    # 例如：[{"from": ["工作", "上班"], "to": "工作", "reason": "语义高度重叠"}]
    hierarchy: dict[str, Any] = field(default_factory=dict)
    # 例如：{"工作": ["加班", "项目", "会议"], "家庭": ["乐乐", "艳艳", "妈妈"]}
    outdated_tags: list[str] = field(default_factory=list)
    # 例如：["旧标签A", "旧标签B"]（超过 90 天未使用）


# ═══════════════════════════════════════════
# 核心服务
# ═══════════════════════════════════════════


class SelfEvolutionService:
    """自进化服务：夜间自我改进循环的核心实现。"""

    # 关注领域关键词
    FOCUS_KEYWORDS: dict[str, list[str]] = {
        "work": [
            "工作",
            "上班",
            "加班",
            "会议",
            "项目",
            "同事",
            "老板",
            "公司",
            "代码",
            "bug",
            "需求",
            "面试",
            "offer",
            "离职",
            "跳槽",
            "升职",
            "加薪",
        ],
        "family": [
            "妈妈",
            "爸爸",
            "老公",
            "老婆",
            "孩子",
            "儿子",
            "女儿",
            "家",
            "家人",
            "乐乐",
            "艳艳",
            "狄胖胖",
            "童先海",
            "周英",
            "春节",
            "回家",
            "团聚",
        ],
        "health": [
            "身体",
            "生病",
            "医院",
            "医生",
            "药",
            "睡",
            "失眠",
            "运动",
            "健身",
            "跑步",
            "体检",
            "康复",
            "感冒",
            "发烧",
            "头疼",
        ],
        "finance": [
            "钱",
            "工资",
            "花钱",
            "买",
            "便宜",
            "贵",
            "投资",
            "理财",
            "贷款",
            "存款",
            "涨薪",
            "奖金",
            "省钱",
            "消费",
        ],
        "learning": [
            "学习",
            "读书",
            "看书",
            "课程",
            "考试",
            "证书",
            "培训",
            "考研",
            "留学",
            "知识",
            "技能",
            "成长",
            "进步",
        ],
        "travel": [
            "旅行",
            "旅游",
            "出去玩",
            "度假",
            "景点",
            "酒店",
            "飞机",
            "高铁",
            "开车",
            "自驾游",
            "机票",
            "攻略",
            "拍照",
        ],
        "entertainment": [
            "电影",
            "电视剧",
            "综艺",
            "游戏",
            "音乐",
            "演唱会",
            "逛街",
            "购物",
            "美食",
            "吃饭",
            "火锅",
            "烧烤",
            "咖啡",
            "奶茶",
        ],
        "emotion": [
            "开心",
            "难过",
            "生气",
            "焦虑",
            "紧张",
            "担心",
            "害怕",
            "幸福",
            "满足",
            "失望",
            "沮丧",
            "激动",
            "兴奋",
            "平静",
        ],
    }

    # 性格推断关键词
    PERSONALITY_KEYWORDS: dict[str, list[str]] = {
        "introversion": ["一个人", "独处", "安静", "内向", "社恐", "不想说话", "宅"],
        "extraversion": ["朋友", "聚会", "热闹", "社交", "出去玩", "人多", "聊天"],
        "emotional": ["感觉", "觉得", "心情", "感动", "难过", "开心", "哭", "笑", "情绪"],
        "analytical": ["分析", "思考", "原因", "逻辑", "数据", "研究", "对比", "总结"],
        "optimism": ["希望", "未来", "加油", "努力", "相信", "美好", "期待", "积极"],
        "pessimism": ["担心", "焦虑", "害怕", "绝望", "无力", "沮丧", "消极", "失望"],
    }

    def __init__(self, store: DiaryStore):
        """初始化自进化服务。

        Args:
            store: 日记存储服务

        """
        self.store = store
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """确保自进化相关的数据库表存在。"""
        conn = self.store.conn
        cursor = conn.cursor()

        # 用户画像表（存储当前画像和历史快照）
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS diary_user_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_date TEXT NOT NULL,
                profile_json TEXT NOT NULL,
                total_entries INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(profile_date)
            )
            """
        )

        # 漂移事件表
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS diary_drift_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                drift_id TEXT NOT NULL UNIQUE,
                drift_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                evidence_json TEXT DEFAULT '[]',
                current_value TEXT DEFAULT '',
                previous_value TEXT DEFAULT '',
                change_percent REAL DEFAULT 0,
                detected_at TEXT NOT NULL,
                status TEXT DEFAULT 'new',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # 夜间日志表
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS diary_nightly_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                log_date TEXT NOT NULL UNIQUE,
                log_json TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # 标签优化建议表
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS diary_tag_optimizations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                optimization_date TEXT NOT NULL,
                optimization_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.commit()
        logger.info("自进化相关数据库表已确保存在")

    # ─── 夜间循环主入口 ──────────────────────────────────────────────

    def run_nightly_cycle(self, target_date: str | None = None) -> NightlyLog:
        """执行完整的夜间自我改进循环。

        Args:
            target_date: 目标日期（分析该日期的数据），默认为昨天

        Returns:
            生成的夜间日志

        """
        if target_date is None:
            target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        logger.info(f"开始夜间自我改进循环，目标日期: {target_date}")

        # 1. 更新用户画像
        profile = self.update_user_profile(target_date)

        # 2. 漂移检测
        drifts = self.detect_drifts(target_date)

        # 3. 标签优化
        tag_opt = self.optimize_tags(target_date)

        # 4. 生成夜间日志
        nightly_log = self.generate_nightly_log(
            target_date=target_date,
            profile=profile,
            drifts=drifts,
            tag_optimization=tag_opt,
        )

        # 5. 保存夜间日志
        self._save_nightly_log(nightly_log)

        logger.info(f"夜间自我改进循环完成，生成了 {len(drifts)} 个漂移事件")
        return nightly_log

    # ─── 用户画像 ─────────────────────────────────────────────────────

    def update_user_profile(self, target_date: str) -> UserProfile:
        """增量更新用户画像。

        Args:
            target_date: 目标日期

        Returns:
            更新后的用户画像

        """
        # 获取所有日记（用于构建完整画像）
        all_entries = self.store.list_entries(limit=5000)

        # 获取目标日期的日记
        target_entries = [e for e in all_entries if e.entry_date <= target_date]

        if not target_entries:
            logger.warning(f"没有找到 {target_date} 及之前的日记，无法构建画像")
            return UserProfile(profile_date=target_date, last_updated=datetime.now().isoformat())

        # 构建画像
        profile = UserProfile(
            profile_date=target_date,
            total_entries_analyzed=len(target_entries),
            last_updated=datetime.now().isoformat(),
        )

        # 1. 性格特质推断
        profile.personality = self._infer_personality(target_entries)

        # 2. 关注焦点分布
        profile.focus_distribution = self._calculate_focus_distribution(target_entries)

        # 3. 人际关系
        profile.relationships = self._analyze_relationships(target_entries)

        # 4. 情绪基调
        profile.emotional_baseline = self._calculate_emotional_baseline(target_entries)

        # 5. 写作习惯
        profile.writing_pattern = self._analyze_writing_pattern(target_entries)

        # 6. 价值观提取（简化版：从高频主题中提取）
        profile.values = self._extract_values(target_entries)

        # 保存画像快照
        self._save_profile_snapshot(profile)

        return profile

    def _infer_personality(self, entries: list[DiaryEntry]) -> dict[str, float]:
        """从日记内容推断性格特质。"""
        personality: dict[str, float] = defaultdict(float)
        total_text = " ".join((e.title or "") + " " + (e.content or "") for e in entries)
        text_length = len(total_text) or 1

        for trait, keywords in self.PERSONALITY_KEYWORDS.items():
            count = sum(total_text.count(kw) for kw in keywords)
            # 归一化（每万字出现次数）
            personality[trait] = round(count / text_length * 10000, 2)

        # 计算对立维度的相对值
        if personality["introversion"] + personality["extraversion"] > 0:
            total = personality["introversion"] + personality["extraversion"]
            personality["introversion_score"] = round(personality["introversion"] / total, 2)
            personality["extraversion_score"] = round(personality["extraversion"] / total, 2)

        if personality["optimism"] + personality["pessimism"] > 0:
            total = personality["optimism"] + personality["pessimism"]
            personality["optimism_score"] = round(personality["optimism"] / total, 2)
            personality["pessimism_score"] = round(personality["pessimism"] / total, 2)

        return dict(personality)

    def _calculate_focus_distribution(self, entries: list[DiaryEntry]) -> dict[str, float]:
        """计算各领域的关注度分布。"""
        focus_counts: dict[str, int] = defaultdict(int)
        total_mentions = 0

        for entry in entries:
            content = (entry.title or "") + " " + (entry.content or "")
            for domain, keywords in self.FOCUS_KEYWORDS.items():
                count = sum(content.count(kw) for kw in keywords)
                if count > 0:
                    focus_counts[domain] += count
                    total_mentions += count

        # 转换为百分比
        if total_mentions > 0:
            return {
                domain: round(count / total_mentions, 4)
                for domain, count in sorted(focus_counts.items(), key=lambda x: -x[1])
            }
        return {}

    def _analyze_relationships(self, entries: list[DiaryEntry]) -> dict[str, dict[str, Any]]:
        """分析人际关系。"""
        # 常见人物名
        common_persons = [
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
        ]

        person_counts: dict[str, int] = defaultdict(int)
        person_dates: dict[str, list[str]] = defaultdict(list)

        for entry in entries:
            content = (entry.title or "") + " " + (entry.content or "")
            for person in common_persons:
                if person in content:
                    person_counts[person] += 1
                    person_dates[person].append(entry.entry_date)

        # 计算亲密度和趋势
        relationships: dict[str, dict[str, Any]] = {}
        max_count = max(person_counts.values()) if person_counts else 1

        # 按时间分三段计算频率趋势
        if entries:
            dates = sorted(e.entry_date for e in entries)
            if len(dates) >= 3:
                third = len(dates) // 3
                early_end = dates[third]
                middle_end = dates[2 * third]
            else:
                early_end = middle_end = dates[-1] if dates else ""
        else:
            early_end = middle_end = ""

        for person, count in person_counts.items():
            intimacy = round(count / max_count, 2)

            # 计算趋势（最近 vs 早期）
            person_date_list = person_dates[person]
            early_count = sum(1 for d in person_date_list if d <= early_end)
            recent_count = sum(1 for d in person_date_list if d > middle_end)

            if early_count > 0 and recent_count > 0:
                if recent_count > early_count * 1.2:
                    trend = "rising"
                elif recent_count < early_count * 0.8:
                    trend = "declining"
                else:
                    trend = "stable"
            else:
                trend = "stable"

            relationships[person] = {
                "intimacy": intimacy,
                "trend": trend,
                "total_mentions": count,
                "first_mentioned": min(person_date_list) if person_date_list else "",
                "last_mentioned": max(person_date_list) if person_date_list else "",
                "recent_30d_count": sum(
                    1
                    for d in person_date_list
                    if (datetime.now() - datetime.strptime(d, "%Y-%m-%d")).days <= 30
                ),
            }

        return relationships

    def _calculate_emotional_baseline(self, entries: list[DiaryEntry]) -> dict[str, Any]:
        """计算情绪基调。"""
        mood_scores = [e.mood_score for e in entries if e.mood_score is not None]
        moods = [e.mood.value for e in entries if e.mood]

        if not mood_scores:
            return {}

        avg_mood = round(sum(mood_scores) / len(mood_scores), 3)

        # 情绪波动性（标准差）
        if len(mood_scores) > 1:
            mean = sum(mood_scores) / len(mood_scores)
            variance = sum((x - mean) ** 2 for x in mood_scores) / len(mood_scores)
            volatility = round(math.sqrt(variance), 3)
        else:
            volatility = 0

        # 正负情绪比例
        positive_count = sum(1 for s in mood_scores if s > 0.1)
        negative_count = sum(1 for s in mood_scores if s < -0.1)
        neutral_count = len(mood_scores) - positive_count - negative_count

        # 主导情绪
        mood_counter = Counter(moods)
        dominant_mood = mood_counter.most_common(1)[0][0] if mood_counter else "unknown"

        return {
            "average_mood": avg_mood,
            "volatility": volatility,
            "positive_ratio": round(positive_count / len(mood_scores), 3),
            "negative_ratio": round(negative_count / len(mood_scores), 3),
            "neutral_ratio": round(neutral_count / len(mood_scores), 3),
            "dominant_mood": dominant_mood,
            "mood_distribution": dict(mood_counter.most_common()),
        }

    def _analyze_writing_pattern(self, entries: list[DiaryEntry]) -> dict[str, Any]:
        """分析写作习惯。"""
        if not entries:
            return {}

        word_counts = [e.word_count or 0 for e in entries]
        avg_length = round(sum(word_counts) / len(word_counts))

        # 写作频率
        dates = set(e.entry_date for e in entries)
        if len(dates) >= 2:
            date_objs = sorted(datetime.strptime(d, "%Y-%m-%d") for d in dates)
            span_days = (date_objs[-1] - date_objs[0]).days + 1
            frequency = round(len(dates) / span_days * 7, 1)  # 每周平均篇数
        else:
            frequency = 0

        # 偏好写作时间（从 created_at 推断，简化为时间段）
        # 由于 created_at 可能不准确，这里简化

        return {
            "average_length": avg_length,
            "writing_frequency_per_week": frequency,
            "total_entries": len(entries),
            "unique_days": len(dates),
            "max_single_entry": max(word_counts) if word_counts else 0,
            "min_single_entry": min(word_counts) if word_counts else 0,
        }

    def _extract_values(self, entries: list[DiaryEntry]) -> list[str]:
        """从日记中提取价值观（简化版）。"""
        # 基于高频主题和反复出现的观点提取
        values = []
        all_text = " ".join((e.title or "") + " " + (e.content or "") for e in entries)

        value_patterns = [
            ("家庭最重要", ["家", "家人", "家庭", "陪伴", "团聚"]),
            ("持续学习成长", ["学习", "成长", "进步", "读书", "知识", "技能"]),
            ("追求工作生活平衡", ["平衡", "工作", "生活", "休息", "放松"]),
            ("重视健康", ["健康", "身体", "运动", "锻炼", "睡眠"]),
            ("珍惜当下", ["当下", "现在", "珍惜", "享受", "美好"]),
            ("重视人际关系", ["朋友", "友情", "关系", "沟通", "理解"]),
            ("追求自由", ["自由", "独立", "自主", "选择"]),
            ("感恩心态", ["感恩", "感谢", "感激", "幸运", "幸福"]),
        ]

        for value, keywords in value_patterns:
            count = sum(all_text.count(kw) for kw in keywords)
            if count >= 5:  # 出现次数阈值
                values.append(value)

        return values[:8]  # 最多返回 8 个价值观

    # ─── 漂移检测 ─────────────────────────────────────────────────────

    def detect_drifts(self, target_date: str) -> list[DriftEvent]:
        """检测用户行为/情绪/关注/关系的显著变化。

        Args:
            target_date: 目标日期

        Returns:
            漂移事件列表

        """
        drifts: list[DriftEvent] = []

        # 获取当前画像
        current_profile = self.get_user_profile(target_date)
        if current_profile is None:
            logger.warning("没有当前画像，跳过漂移检测")
            return drifts

        # 获取 30 天前的画像作为对比基准
        comparison_date = (
            datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=30)
        ).strftime("%Y-%m-%d")
        previous_profile = self._get_closest_profile(comparison_date)

        if previous_profile is None:
            logger.info("没有历史画像可对比，跳过漂移检测（首次运行）")
            return drifts

        # 1. 情绪漂移检测
        drifts.extend(self._detect_emotion_drift(current_profile, previous_profile, target_date))

        # 2. 关注漂移检测
        drifts.extend(self._detect_focus_drift(current_profile, previous_profile, target_date))

        # 3. 关系漂移检测
        drifts.extend(
            self._detect_relationship_drift(current_profile, previous_profile, target_date)
        )

        # 4. 写作频率漂移检测
        drifts.extend(self._detect_writing_drift(current_profile, previous_profile, target_date))

        # 保存漂移事件
        for drift in drifts:
            self._save_drift_event(drift)

        return drifts

    def _detect_emotion_drift(
        self, current: UserProfile, previous: UserProfile, target_date: str
    ) -> list[DriftEvent]:
        """检测情绪漂移。"""
        drifts = []

        curr_mood = current.emotional_baseline.get("average_mood", 0)
        prev_mood = previous.emotional_baseline.get("average_mood", 0)

        if prev_mood != 0:
            change = curr_mood - prev_mood
            change_percent = abs(change / prev_mood * 100) if prev_mood != 0 else 0

            if change_percent >= 20:  # 变化超过 20%
                severity = "alert" if change_percent >= 50 else "warning"
                direction = "下降" if change < 0 else "上升"

                drift = DriftEvent(
                    drift_id=f"emotion-{target_date}",
                    drift_type="emotion",
                    severity=severity,
                    title=f"情绪基调{direction}",
                    description=(
                        f"平均情绪分从 {prev_mood:.2f} 变为 {curr_mood:.2f}，"
                        f"{direction}了 {abs(change):.2f}（{change_percent:.0f}%）。"
                    ),
                    evidence=[
                        {"type": "metric", "label": "30天前平均情绪分", "value": prev_mood},
                        {"type": "metric", "label": "当前平均情绪分", "value": curr_mood},
                    ],
                    current_value=curr_mood,
                    previous_value=prev_mood,
                    change_percent=change_percent,
                    detected_at=datetime.now().isoformat(),
                )
                drifts.append(drift)

        # 情绪波动性漂移
        curr_vol = current.emotional_baseline.get("volatility", 0)
        prev_vol = previous.emotional_baseline.get("volatility", 0)

        if prev_vol > 0 and curr_vol > prev_vol * 1.5:
            drift = DriftEvent(
                drift_id=f"emotion-volatility-{target_date}",
                drift_type="emotion",
                severity="warning",
                title="情绪波动加剧",
                description=(
                    f"情绪波动性从 {prev_vol:.3f} 上升到 {curr_vol:.3f}，"
                    f"增加了 {(curr_vol / prev_vol - 1) * 100:.0f}%。情绪起伏变大。"
                ),
                evidence=[
                    {"type": "metric", "label": "30天前波动性", "value": prev_vol},
                    {"type": "metric", "label": "当前波动性", "value": curr_vol},
                ],
                current_value=curr_vol,
                previous_value=prev_vol,
                change_percent=(curr_vol / prev_vol - 1) * 100,
                detected_at=datetime.now().isoformat(),
            )
            drifts.append(drift)

        return drifts

    def _detect_focus_drift(
        self, current: UserProfile, previous: UserProfile, target_date: str
    ) -> list[DriftEvent]:
        """检测关注焦点漂移。"""
        drifts = []

        curr_focus = current.focus_distribution
        prev_focus = previous.focus_distribution

        for domain in set(list(curr_focus.keys()) + list(prev_focus.keys())):
            curr_val = curr_focus.get(domain, 0)
            prev_val = prev_focus.get(domain, 0)

            if prev_val > 0.05:  # 之前关注度超过 5% 才检测
                change_percent = abs(curr_val - prev_val) / prev_val * 100

                if change_percent >= 30:  # 变化超过 30%
                    severity = "warning" if change_percent >= 50 else "info"
                    direction = "上升" if curr_val > prev_val else "下降"
                    domain_names = {
                        "work": "工作",
                        "family": "家庭",
                        "health": "健康",
                        "finance": "财务",
                        "learning": "学习",
                        "travel": "旅行",
                        "entertainment": "娱乐",
                        "emotion": "情感",
                    }
                    domain_name = domain_names.get(domain, domain)

                    drift = DriftEvent(
                        drift_id=f"focus-{domain}-{target_date}",
                        drift_type="focus",
                        severity=severity,
                        title=f"对{domain_name}的关注度{direction}",
                        description=(
                            f"对{domain_name}的关注度从 {prev_val * 100:.1f}% 变为 "
                            f"{curr_val * 100:.1f}%，{direction}了 {change_percent:.0f}%。"
                        ),
                        evidence=[
                            {
                                "type": "metric",
                                "label": "30天前关注度",
                                "value": f"{prev_val * 100:.1f}%",
                            },
                            {
                                "type": "metric",
                                "label": "当前关注度",
                                "value": f"{curr_val * 100:.1f}%",
                            },
                        ],
                        current_value=curr_val,
                        previous_value=prev_val,
                        change_percent=change_percent,
                        detected_at=datetime.now().isoformat(),
                    )
                    drifts.append(drift)

        return drifts

    def _detect_relationship_drift(
        self, current: UserProfile, previous: UserProfile, target_date: str
    ) -> list[DriftEvent]:
        """检测人际关系漂移。"""
        drifts = []

        curr_rels = current.relationships
        prev_rels = previous.relationships

        for person in set(list(curr_rels.keys()) + list(prev_rels.keys())):
            curr = curr_rels.get(person, {})
            prev = prev_rels.get(person, {})

            curr_count = curr.get("recent_30d_count", 0)
            prev_count = prev.get("recent_30d_count", 0)

            if prev_count >= 3 and curr_count != prev_count:
                change_percent = abs(curr_count - prev_count) / prev_count * 100

                if change_percent >= 40:  # 变化超过 40%
                    severity = "warning" if change_percent >= 60 else "info"
                    direction = "增加" if curr_count > prev_count else "减少"

                    drift = DriftEvent(
                        drift_id=f"relationship-{person}-{target_date}",
                        drift_type="relationship",
                        severity=severity,
                        title=f"与{person}的互动{direction}",
                        description=(
                            f"最近30天与{person}的互动从 {prev_count} 次变为 {curr_count} 次，"
                            f"{direction}了 {change_percent:.0f}%。"
                        ),
                        evidence=[
                            {"type": "metric", "label": "30天前互动次数", "value": prev_count},
                            {"type": "metric", "label": "当前互动次数", "value": curr_count},
                        ],
                        current_value=curr_count,
                        previous_value=prev_count,
                        change_percent=change_percent,
                        detected_at=datetime.now().isoformat(),
                    )
                    drifts.append(drift)

        return drifts

    def _detect_writing_drift(
        self, current: UserProfile, previous: UserProfile, target_date: str
    ) -> list[DriftEvent]:
        """检测写作频率漂移。"""
        drifts = []

        curr_freq = current.writing_pattern.get("writing_frequency_per_week", 0)
        prev_freq = previous.writing_pattern.get("writing_frequency_per_week", 0)

        if prev_freq >= 1 and curr_freq != prev_freq:
            change_percent = abs(curr_freq - prev_freq) / prev_freq * 100

            if change_percent >= 30:
                severity = "warning" if change_percent >= 50 else "info"
                direction = "增加" if curr_freq > prev_freq else "减少"

                drift = DriftEvent(
                    drift_id=f"writing-{target_date}",
                    drift_type="writing",
                    severity=severity,
                    title=f"写作频率{direction}",
                    description=(
                        f"每周平均写作篇数从 {prev_freq:.1f} 变为 {curr_freq:.1f}，"
                        f"{direction}了 {change_percent:.0f}%。"
                    ),
                    evidence=[
                        {"type": "metric", "label": "30天前频率", "value": f"{prev_freq:.1f}篇/周"},
                        {"type": "metric", "label": "当前频率", "value": f"{curr_freq:.1f}篇/周"},
                    ],
                    current_value=curr_freq,
                    previous_value=prev_freq,
                    change_percent=change_percent,
                    detected_at=datetime.now().isoformat(),
                )
                drifts.append(drift)

        return drifts

    # ─── 标签优化 ─────────────────────────────────────────────────────

    def optimize_tags(self, target_date: str) -> TagOptimization:
        """标签优化：发现新标签、合并建议、层级构建、过时标签。

        Args:
            target_date: 目标日期

        Returns:
            标签优化建议

        """
        entries = self.store.list_entries(limit=5000)
        entries = [e for e in entries if e.entry_date <= target_date]

        optimization = TagOptimization()

        if not entries:
            return optimization

        # 1. 发现高频未标记概念（新标签候选）
        all_text = " ".join((e.title or "") + " " + (e.content or "") for e in entries)
        existing_tags = set()
        for e in entries:
            if e.tags:
                existing_tags.update(e.tags)

        # 候选新概念（简化版：高频名词短语）
        candidate_concepts = [
            "自驾游",
            "地铁",
            "加班",
            "面试",
            "搬家",
            "装修",
            "健身",
            "减肥",
            "护肤",
            "网购",
            "追剧",
            "游戏",
            "摄影",
            "烘焙",
            "宠物",
            "养花",
            "手工",
            "冥想",
            "瑜伽",
            "跑步",
            "游泳",
        ]

        for concept in candidate_concepts:
            if concept not in existing_tags:
                count = all_text.count(concept)
                if count >= 3:  # 出现至少 3 次
                    # 找示例日记
                    sample_entries = []
                    for e in entries[:100]:
                        if concept in (e.content or ""):
                            sample_entries.append(
                                {
                                    "id": e.id,
                                    "date": e.entry_date,
                                    "title": e.title,
                                }
                            )
                            if len(sample_entries) >= 3:
                                break
                    optimization.new_tags.append(
                        {
                            "tag": concept,
                            "count": count,
                            "sample_entries": sample_entries,
                        }
                    )

        # 2. 相似标签合并建议（简化版：基于语义重叠）
        merge_candidates = [
            (["工作", "上班"], "工作", "语义高度重叠，都指职场相关"),
            (["开心", "快乐", "高兴"], "开心", "情绪类同义词，可合并"),
            (["难过", "伤心", "沮丧"], "难过", "情绪类同义词，可合并"),
        ]

        for from_tags, to_tag, reason in merge_candidates:
            existing_from = [t for t in from_tags if t in existing_tags]
            if len(existing_from) >= 2:
                optimization.merge_suggestions.append(
                    {
                        "from": existing_from,
                        "to": to_tag,
                        "reason": reason,
                    }
                )

        # 3. 标签层级构建（简化版）
        hierarchy: dict[str, list[str]] = {
            "工作": [],
            "家庭": [],
            "健康": [],
            "情感": [],
            "娱乐": [],
        }

        work_children = ["加班", "会议", "项目", "面试", "offer", "离职", "升职"]
        family_children = ["乐乐", "艳艳", "妈妈", "爸爸", "春节", "团聚"]
        health_children = ["运动", "健身", "跑步", "生病", "医院", "体检", "失眠"]
        emotion_children = ["开心", "难过", "焦虑", "生气", "幸福", "感动"]
        entertainment_children = ["电影", "电视剧", "游戏", "音乐", "美食", "旅行"]

        for tag in existing_tags:
            if tag in work_children:
                hierarchy["工作"].append(tag)
            elif tag in family_children:
                hierarchy["家庭"].append(tag)
            elif tag in health_children:
                hierarchy["健康"].append(tag)
            elif tag in emotion_children:
                hierarchy["情感"].append(tag)
            elif tag in entertainment_children:
                hierarchy["娱乐"].append(tag)

        # 移除空分类
        optimization.hierarchy = {k: v for k, v in hierarchy.items() if v}

        # 4. 过时标签（超过 90 天未使用）
        ninety_days_ago = (
            datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=90)
        ).strftime("%Y-%m-%d")

        tag_last_used: dict[str, str] = {}
        for e in entries:
            if e.tags:
                for tag in e.tags:
                    if tag not in tag_last_used or e.entry_date > tag_last_used[tag]:
                        tag_last_used[tag] = e.entry_date

        for tag, last_used in tag_last_used.items():
            if last_used < ninety_days_ago:
                optimization.outdated_tags.append(tag)

        # 保存优化建议
        self._save_tag_optimization(target_date, optimization)

        return optimization

    # ─── 夜间日志生成 ─────────────────────────────────────────────────

    def generate_nightly_log(
        self,
        target_date: str,
        profile: UserProfile,
        drifts: list[DriftEvent],
        tag_optimization: TagOptimization,
    ) -> NightlyLog:
        """生成可读的夜间日志。

        Args:
            target_date: 目标日期
            profile: 用户画像
            drifts: 漂移事件
            tag_optimization: 标签优化建议

        Returns:
            生成的夜间日志

        """
        # 获取目标日期的日记
        target_entries = self.store.list_entries(
            start_date=target_date,
            end_date=target_date,
            limit=100,
        )

        log = NightlyLog(
            log_date=target_date,
            generated_at=datetime.now().isoformat(),
        )

        # 1. 今日摘要
        total_words = sum(e.word_count or 0 for e in target_entries)
        avg_mood = (
            sum(e.mood_score or 0 for e in target_entries) / len(target_entries)
            if target_entries
            else 0
        )

        all_tags = []
        for e in target_entries:
            if e.tags:
                all_tags.extend(e.tags)
        top_tags = [t for t, _ in Counter(all_tags).most_common(5)]

        log.daily_summary = {
            "entry_count": len(target_entries),
            "total_words": total_words,
            "average_mood": round(avg_mood, 2),
            "top_tags": top_tags,
            "mood_distribution": dict(Counter(e.mood.value for e in target_entries if e.mood)),
        }

        # 2. 学到了什么
        learnings = []

        if profile.personality:
            top_traits = sorted(
                profile.personality.items(),
                key=lambda x: -x[1],
            )[:3]
            trait_names = {
                "introversion": "内向",
                "extraversion": "外向",
                "emotional": "感性",
                "analytical": "理性",
                "optimism": "乐观",
                "pessimism": "悲观",
            }
            if top_traits:
                learnings.append(
                    f"从语言风格看，你最突出的性格特质是"
                    f"{'、'.join(trait_names.get(t, t) for t, _ in top_traits)}。"
                )

        if profile.focus_distribution:
            top_focus = sorted(profile.focus_distribution.items(), key=lambda x: -x[1])[:2]
            focus_names = {
                "work": "工作",
                "family": "家庭",
                "health": "健康",
                "finance": "财务",
                "learning": "学习",
                "travel": "旅行",
                "entertainment": "娱乐",
                "emotion": "情感",
            }
            if top_focus:
                focus_strs = [f"{focus_names.get(f, f)}（{v * 100:.0f}%）" for f, v in top_focus]
                learnings.append(f"你最近最关注的领域是{'和'.join(focus_strs)}。")

        if profile.values:
            learnings.append(
                f"从反复出现的主题看，你重视的价值观包括：{'、'.join(profile.values[:3])}。"
            )

        log.learnings = learnings

        # 3. 发现的模式
        patterns = []

        if profile.emotional_baseline:
            eb = profile.emotional_baseline
            patterns.append(
                {
                    "type": "emotion_pattern",
                    "description": (
                        f"你的主导情绪是{eb.get('dominant_mood', '未知')}，"
                        f"平均情绪分 {eb.get('average_mood', 0):.2f}，"
                        f"积极情绪占比 {eb.get('positive_ratio', 0) * 100:.0f}%。"
                    ),
                }
            )

        if profile.relationships:
            top_rels = sorted(
                profile.relationships.items(),
                key=lambda x: -x[1].get("intimacy", 0),
            )[:3]
            if top_rels:
                rel_strs = []
                for name, rel in top_rels:
                    intimacy = rel.get("intimacy", 0)
                    rel_strs.append(f"{name}（亲密度{intimacy:.0%}）")
                patterns.append(
                    {
                        "type": "relationship_pattern",
                        "description": f"你生活中最重要的人是{'、'.join(rel_strs)}。",
                    }
                )

        if profile.writing_pattern:
            wp = profile.writing_pattern
            freq = wp.get("writing_frequency_per_week", 0)
            avg_len = wp.get("average_length", 0)
            patterns.append(
                {
                    "type": "writing_pattern",
                    "description": (f"你平均每周写 {freq:.1f} 篇日记，平均每篇 {avg_len} 字。"),
                }
            )

        log.patterns = patterns

        # 4. 漂移警报
        log.drifts = drifts

        # 5. 系统建议（温和不说教）
        suggestions = []

        # 基于情绪的建议
        if profile.emotional_baseline:
            avg_mood = profile.emotional_baseline.get("average_mood", 0)
            if avg_mood < -0.2:
                suggestions.append(
                    "最近情绪似乎偏低，要不要做点让自己开心的小事？比如听听喜欢的音乐，或者出去走走。"
                )
            elif avg_mood > 0.3:
                suggestions.append(
                    "最近状态不错呢！保持这份好心情，也可以记录一下是什么让你这么开心。"
                )

        # 基于漂移的建议
        for drift in drifts:
            if drift.drift_type == "emotion" and drift.severity in ("warning", "alert"):
                if "下降" in drift.title:
                    suggestions.append(
                        "注意到情绪有些变化，如果需要的话，可以多和信任的人聊聊，或者给自己一些放松的时间。"
                    )
            elif drift.drift_type == "relationship" and "减少" in drift.title:
                person_name = drift.title.replace("与", "").replace("的互动减少", "")
                suggestions.append(f"和{person_name}的联系变少了，要不要主动联系一下？")

        # 基于写作频率的建议
        if profile.writing_pattern:
            freq = profile.writing_pattern.get("writing_frequency_per_week", 0)
            if freq < 1:
                suggestions.append(
                    "最近写日记的频率降低了，哪怕只写几句话，记录当下的感受也是很有意义的。"
                )

        log.suggestions = suggestions[:5]  # 最多 5 条建议

        # 6. 自我优化
        self_opts = []

        if tag_optimization.new_tags:
            self_opts.append(
                f"发现了 {len(tag_optimization.new_tags)} 个潜在新标签："
                f"{'、'.join(t['tag'] for t in tag_optimization.new_tags[:3])}。"
            )

        if tag_optimization.outdated_tags:
            self_opts.append(
                f"发现 {len(tag_optimization.outdated_tags)} 个超过90天未使用的标签，"
                f"可以考虑归档或删除。"
            )

        if drifts:
            self_opts.append(
                f"检测到 {len(drifts)} 个显著变化，已记录到漂移事件库，后续分析会考虑这些变化趋势。"
            )

        self_opts.append(
            f"已更新用户画像，当前基于 {profile.total_entries_analyzed} 篇日记构建，"
            f"包含 {len(profile.relationships)} 个核心人物关系。"
        )

        log.self_optimizations = self_opts

        # 7. 标签优化
        log.tag_optimizations = {
            "new_tags_count": len(tag_optimization.new_tags),
            "merge_suggestions_count": len(tag_optimization.merge_suggestions),
            "outdated_tags_count": len(tag_optimization.outdated_tags),
            "hierarchy_levels": len(tag_optimization.hierarchy),
        }

        return log

    # ─── 查询接口 ─────────────────────────────────────────────────────

    def get_user_profile(self, target_date: str | None = None) -> UserProfile | None:
        """获取用户画像。

        Args:
            target_date: 目标日期，默认为今天

        Returns:
            用户画像，如果不存在返回 None

        """
        if target_date is None:
            target_date = datetime.now().strftime("%Y-%m-%d")

        conn = self.store.conn
        cursor = conn.cursor()

        # 找最近的画像（<= target_date）
        cursor.execute(
            """
            SELECT profile_json FROM diary_user_profiles
            WHERE profile_date <= ?
            ORDER BY profile_date DESC
            LIMIT 1
            """,
            (target_date,),
        )
        row = cursor.fetchone()

        if row is None:
            return None

        profile_dict = json.loads(row["profile_json"])
        return UserProfile(**profile_dict)

    def get_profile_history(self, limit: int = 30) -> list[dict[str, Any]]:
        """获取画像历史快照。

        Args:
            limit: 返回数量上限

        Returns:
            画像历史列表

        """
        conn = self.store.conn
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT profile_date, profile_json, total_entries, created_at
            FROM diary_user_profiles
            ORDER BY profile_date DESC
            LIMIT ?
            """,
            (limit,),
        )

        results = []
        for row in cursor.fetchall():
            profile_dict = json.loads(row["profile_json"])
            results.append(
                {
                    "profile_date": row["profile_date"],
                    "total_entries": row["total_entries"],
                    "created_at": row["created_at"],
                    "summary": {
                        "average_mood": profile_dict.get("emotional_baseline", {}).get(
                            "average_mood"
                        ),
                        "top_focus": list(profile_dict.get("focus_distribution", {}).keys())[:3],
                        "key_relationships": list(profile_dict.get("relationships", {}).keys())[:5],
                    },
                }
            )

        return results

    def get_drifts(
        self,
        drift_type: str | None = None,
        severity: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[DriftEvent]:
        """获取漂移事件列表。

        Args:
            drift_type: 按类型筛选
            severity: 按严重程度筛选
            status: 按状态筛选
            limit: 返回数量上限

        Returns:
            漂移事件列表

        """
        conn = self.store.conn
        cursor = conn.cursor()

        query = "SELECT * FROM diary_drift_events WHERE 1=1"
        params: list[Any] = []

        if drift_type:
            query += " AND drift_type = ?"
            params.append(drift_type)
        if severity:
            query += " AND severity = ?"
            params.append(severity)
        if status:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY detected_at DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)

        drifts = []
        for row in cursor.fetchall():
            drift = DriftEvent(
                drift_id=row["drift_id"],
                drift_type=row["drift_type"],
                severity=row["severity"],
                title=row["title"],
                description=row["description"],
                evidence=json.loads(row["evidence_json"]),
                current_value=row["current_value"],
                previous_value=row["previous_value"],
                change_percent=row["change_percent"],
                detected_at=row["detected_at"],
                status=row["status"],
            )
            drifts.append(drift)

        return drifts

    def get_nightly_logs(self, limit: int = 30) -> list[dict[str, Any]]:
        """获取夜间日志列表。

        Args:
            limit: 返回数量上限

        Returns:
            夜间日志列表（摘要）

        """
        conn = self.store.conn
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT log_date, log_json, generated_at
            FROM diary_nightly_logs
            ORDER BY log_date DESC
            LIMIT ?
            """,
            (limit,),
        )

        results = []
        for row in cursor.fetchall():
            log_dict = json.loads(row["log_json"])
            results.append(
                {
                    "log_date": row["log_date"],
                    "generated_at": row["generated_at"],
                    "summary": {
                        "entry_count": log_dict.get("daily_summary", {}).get("entry_count", 0),
                        "total_words": log_dict.get("daily_summary", {}).get("total_words", 0),
                        "drift_count": len(log_dict.get("drifts", [])),
                        "learning_count": len(log_dict.get("learnings", [])),
                        "suggestion_count": len(log_dict.get("suggestions", [])),
                    },
                }
            )

        return results

    def get_nightly_log(self, log_date: str) -> NightlyLog | None:
        """获取指定日期的夜间日志。

        Args:
            log_date: 日志日期

        Returns:
            夜间日志，如果不存在返回 None

        """
        conn = self.store.conn
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT log_json FROM diary_nightly_logs
            WHERE log_date = ?
            """,
            (log_date,),
        )
        row = cursor.fetchone()

        if row is None:
            return None

        log_dict = json.loads(row["log_json"])
        # 重建 DriftEvent 对象
        drifts = [DriftEvent(**d) for d in log_dict.get("drifts", [])]
        log_dict["drifts"] = drifts
        return NightlyLog(**log_dict)

    # ─── 内部保存方法 ─────────────────────────────────────────────────

    def _save_profile_snapshot(self, profile: UserProfile) -> None:
        """保存画像快照。"""
        conn = self.store.conn
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO diary_user_profiles
            (profile_date, profile_json, total_entries, created_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                profile.profile_date,
                json.dumps(profile.to_dict(), ensure_ascii=False),
                profile.total_entries_analyzed,
            ),
        )
        conn.commit()

    def _save_drift_event(self, drift: DriftEvent) -> None:
        """保存漂移事件。"""
        conn = self.store.conn
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR IGNORE INTO diary_drift_events
            (drift_id, drift_type, severity, title, description, evidence_json,
             current_value, previous_value, change_percent, detected_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                drift.drift_id,
                drift.drift_type,
                drift.severity,
                drift.title,
                drift.description,
                json.dumps(drift.evidence, ensure_ascii=False),
                str(drift.current_value),
                str(drift.previous_value),
                drift.change_percent,
                drift.detected_at,
                drift.status,
            ),
        )
        conn.commit()

    def _save_nightly_log(self, log: NightlyLog) -> None:
        """保存夜间日志。"""
        conn = self.store.conn
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO diary_nightly_logs
            (log_date, log_json, generated_at, created_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                log.log_date,
                json.dumps(log.to_dict(), ensure_ascii=False),
                log.generated_at,
            ),
        )
        conn.commit()

    def _save_tag_optimization(self, date: str, optimization: TagOptimization) -> None:
        """保存标签优化建议。"""
        conn = self.store.conn
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO diary_tag_optimizations
            (optimization_date, optimization_json, created_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            (
                date,
                json.dumps(asdict(optimization), ensure_ascii=False),
            ),
        )
        conn.commit()

    def _get_closest_profile(self, target_date: str) -> UserProfile | None:
        """获取最接近目标日期的画像（用于对比）。"""
        conn = self.store.conn
        cursor = conn.cursor()

        # 找 <= target_date 的最近画像
        cursor.execute(
            """
            SELECT profile_json FROM diary_user_profiles
            WHERE profile_date <= ?
            ORDER BY profile_date DESC
            LIMIT 1
            """,
            (target_date,),
        )
        row = cursor.fetchone()

        if row is None:
            return None

        profile_dict = json.loads(row["profile_json"])
        return UserProfile(**profile_dict)

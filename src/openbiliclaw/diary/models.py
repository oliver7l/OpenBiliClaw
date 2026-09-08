"""日记系统数据模型。

定义日记条目、分析结果、统计信息等核心数据结构，
使用 Pydantic 进行类型校验与序列化。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import StrEnum

from pydantic import BaseModel, Field


class MoodLevel(StrEnum):
    """情绪等级枚举。"""

    VERY_HAPPY = "very_happy"
    HAPPY = "happy"
    NEUTRAL = "neutral"
    SAD = "sad"
    VERY_SAD = "very_sad"
    ANGRY = "angry"
    ANXIOUS = "anxious"
    UNKNOWN = "unknown"


class DiaryEntry(BaseModel):
    """单条日记条目。"""

    id: int = Field(description="日记唯一 ID")
    entry_date: str = Field(description="日记日期，格式 YYYY-MM-DD")
    title: str = Field(default="", description="日记标题")
    content: str = Field(description="日记正文内容")
    source: str = Field(
        default="manual", description="来源：manual / import_lele / import_text / api"
    )
    tags: list[str] = Field(default_factory=list, description="标签列表")
    mood: MoodLevel = Field(default=MoodLevel.UNKNOWN, description="情绪等级")
    mood_score: float = Field(default=0.0, ge=-1.0, le=1.0, description="情绪分值 -1~1")
    word_count: int = Field(default=0, description="字数统计")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")
    analysis_id: int | None = Field(default=None, description="关联的分析记录 ID")


class DiaryEntryCreate(BaseModel):
    """创建日记条目请求。"""

    entry_date: str = Field(description="日记日期，格式 YYYY-MM-DD")
    title: str = Field(default="", description="日记标题")
    content: str = Field(min_length=1, description="日记正文内容")
    source: str = Field(default="manual", description="来源标识")
    tags: list[str] = Field(default_factory=list, description="标签列表")
    mood: MoodLevel = Field(default=MoodLevel.UNKNOWN, description="情绪等级")


class DiaryEntryUpdate(BaseModel):
    """更新日记条目请求。"""

    title: str | None = Field(default=None, description="日记标题")
    content: str | None = Field(default=None, description="日记正文内容")
    tags: list[str] | None = Field(default=None, description="标签列表")
    mood: MoodLevel | None = Field(default=None, description="情绪等级")
    entry_date: str | None = Field(default=None, description="日记日期")


class DiaryAnalysis(BaseModel):
    """日记分析记录。"""

    id: int = Field(description="分析唯一 ID")
    diary_id: int = Field(description="关联的日记 ID")
    summary: str = Field(default="", description="内容摘要")
    key_points: list[str] = Field(default_factory=list, description="关键要点")
    emotions: dict[str, float] = Field(default_factory=dict, description="情绪分布")
    themes: list[str] = Field(default_factory=list, description="主题标签")
    people_mentioned: list[str] = Field(default_factory=list, description="提到的人物")
    growth_insight: str = Field(default="", description="成长洞察")
    mood_score: float = Field(default=0.0, description="情绪分值")
    model_used: str = Field(default="", description="使用的模型")
    created_at: datetime = Field(description="分析创建时间")


class DiaryStats(BaseModel):
    """日记统计信息。"""

    total_entries: int = Field(description="日记总条数")
    total_words: int = Field(description="总字数")
    earliest_date: str = Field(description="最早日记日期")
    latest_date: str = Field(description="最新日记日期")
    avg_words_per_entry: float = Field(description="平均每篇字数")
    mood_distribution: dict[str, int] = Field(default_factory=dict, description="情绪分布统计")
    top_tags: list[tuple[str, int]] = Field(default_factory=list, description="热门标签")
    entries_by_month: dict[str, int] = Field(default_factory=dict, description="按月统计")
    analyzed_count: int = Field(default=0, description="已分析条数")


class DiaryFragment(BaseModel):
    """随手记碎片。

    借鉴 Night-Journal 和 echolog 的设计：白天随手记录碎片（文字+情绪+图片+语音），
    晚上 AI 自动聚合为完整日记。支持多种碎片类型，自动标签和情绪识别。
    """

    id: int = Field(description="碎片唯一 ID")
    content: str = Field(description="碎片内容")
    mood: MoodLevel = Field(default=MoodLevel.UNKNOWN, description="情绪标签")
    fragment_date: str = Field(description="碎片所属日期 YYYY-MM-DD")
    source: str = Field(default="manual", description="来源：manual / api / voice / image / bot")
    fragment_type: str = Field(default="text", description="碎片类型：text / image / voice / link")
    media_path: str = Field(default="", description="媒体文件路径（图片/语音）")
    media_description: str = Field(
        default="", description="媒体内容的 AI 描述（图片理解/语音转写）"
    )
    tags: list[str] = Field(default_factory=list, description="AI 自动提取的标签")
    created_at: datetime = Field(description="创建时间")


class DiaryFragmentCreate(BaseModel):
    """创建碎片请求。"""

    content: str = Field(min_length=1, description="碎片内容")
    mood: MoodLevel = Field(default=MoodLevel.UNKNOWN, description="情绪标签")
    fragment_date: str | None = Field(default=None, description="日期，默认今天")
    source: str = Field(default="manual", description="来源")
    fragment_type: str = Field(default="text", description="碎片类型：text / image / voice / link")
    media_path: str = Field(default="", description="媒体文件路径")
    media_description: str = Field(default="", description="媒体内容描述")
    tags: list[str] = Field(default_factory=list, description="标签列表")


class TagType(StrEnum):
    """标签类型枚举。"""

    EMOTION = "emotion"  # 情绪类：开心、焦虑、难过
    TOPIC = "topic"  # 主题类：工作、家庭、旅行
    EVENT = "event"  # 事件类：生日、面试、搬家
    LOCATION = "location"  # 地点类：深圳、北京、家里
    WORK = "work"  # 工作相关
    FAMILY = "family"  # 家庭相关
    HEALTH = "health"  # 健康相关
    FINANCE = "finance"  # 财务相关
    OTHER = "other"  # 其他


class DiaryTag(BaseModel):
    """日记标签。

    AI 自动从日记内容中提取的结构化标签，支持按类型分类。
    """

    id: int = Field(description="标签唯一 ID")
    name: str = Field(description="标签名称")
    type: TagType = Field(default=TagType.OTHER, description="标签类型")
    count: int = Field(default=0, description="被使用的次数")
    created_at: datetime = Field(description="创建时间")


class DiaryPerson(BaseModel):
    """日记中出现的人物。

    AI 自动识别并建立人物档案，记录出现频率和时间跨度。
    """

    id: int = Field(description="人物唯一 ID")
    name: str = Field(description="人物名称")
    aliases: list[str] = Field(default_factory=list, description="别名列表")
    relation: str = Field(default="", description="与作者的关系：家人/朋友/同事等")
    description: str = Field(default="", description="人物描述/备注")
    first_appeared: str = Field(default="", description="首次出现日期 YYYY-MM-DD")
    last_appeared: str = Field(default="", description="最后出现日期 YYYY-MM-DD")
    appearance_count: int = Field(default=0, description="出现次数")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")


class DiaryPersonDetail(DiaryPerson):
    """人物详情，包含相关日记列表。"""

    related_entries: list[dict] = Field(
        default_factory=list, description="相关日记列表（id, date, title, context）"
    )


class ExtractionResult(BaseModel):
    """AI 提取结果。"""

    tags: list[dict] = Field(
        default_factory=list, description="提取的标签列表 [{name, type, confidence}]"
    )
    persons: list[dict] = Field(
        default_factory=list, description="提取的人物列表 [{name, relation, context}]"
    )
    locations: list[str] = Field(default_factory=list, description="提取的地点")
    events: list[str] = Field(default_factory=list, description="提取的事件")

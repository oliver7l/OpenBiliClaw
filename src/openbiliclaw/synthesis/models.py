"""跨模块迭代合成数据模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SynthesisConfig(BaseModel):
    """合成引擎配置。"""

    # 数据库路径
    main_db_path: str = "data/openbiliclaw.db"
    chat_db_path: str = "data/chat_analysis.db"

    # 每次合成最多处理的新记录数
    max_new_diary_analyses: int = 50
    max_new_chat_insights: int = 30
    max_new_chat_topics: int = 20

    # 合成 LLM 参数
    llm_temperature: float = 0.3
    llm_max_tokens: int = 4096

    # 迭代频率（小时）
    min_interval_hours: float = 6.0


class SynthesisState(BaseModel):
    """增量合成状态，记录上次处理进度。"""

    last_diary_analysis_id: int = 0
    last_chat_insight_id: int = 0
    last_chat_topic_id: int = 0
    current_version: int = 0
    last_synthesis_at: str | None = None
    total_synthesis_runs: int = 0


class SynthesisVersion(BaseModel):
    """单次合成结果版本。"""

    id: int = 0
    version: int
    parent_version: int | None = None
    created_at: str = ""

    # 本次新增的数据范围
    new_diary_count: int = 0
    new_chat_insight_count: int = 0
    new_chat_topic_count: int = 0

    # 合成输出
    summary: str = ""
    patterns: list[str] = Field(default_factory=list)
    insights: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)

    # 跨模块关联
    cross_patterns: list[CrossModulePattern] = Field(default_factory=list)

    # LLM 元信息
    model_used: str = ""
    tokens_used: int = 0


class CrossModulePattern(BaseModel):
    """跨模块模式关联：发现日记与聊天中的共同主题。"""

    pattern: str
    diary_evidence: list[str] = Field(default_factory=list)
    chat_evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.5


class DiarySynthesisResult(BaseModel):
    """对单篇日记的合成增强结果（回写用）。"""

    diary_id: int
    version: int
    enhanced_tags: list[str] = Field(default_factory=list)
    enhanced_insight: str = ""
    cross_refs: list[int] = Field(default_factory=list, description="关联的聊天会话 ID")
    updated_at: str = ""


_SYNTHESIS_SYSTEM_PROMPT = """你是一位个人成长分析师，负责综合日记和聊天记录的分析结果，持续迭代优化个人认知画像。

## 你的任务
综合分析一段时期内的日记分析和聊天分析的结果，识别跨模块的模式、主题演化和个人成长趋势。

## 迭代规则
- 你收到的"前次合成结果"是之前版本的分析结论
- 请在此基础上**更新**而非重写：保留正确的旧结论，修正过时的，补充新增的
- 如果新旧信息矛盾，优先采信更新、更具体的数据
- 保持版本连续性：在版本号上递增

## 输出格式
请严格输出 JSON（不要使用 markdown 代码块）：

{
  "summary": "对本次合成周期的整体总结，2-3句话",
  "patterns": [
    "识别出的重复行为/思维模式，每条一句话"
  ],
  "insights": [
    "深度洞察，每条一句话，50-100字"
  ],
  "themes": [
    "跨模块出现的高频主题标签"
  ],
  "concerns": [
    "需要关注的潜在问题或风险信号"
  ],
  "cross_patterns": [
    {
      "pattern": "跨模块模式描述",
      "diary_evidence": ["日记层面的证据"],
      "chat_evidence": ["聊天层面的证据"],
      "confidence": 0.8
    }
  ]
}
"""


_SYNTHESIS_USER_PROMPT_TEMPLATE = """## 前次合成结果（版本 {prev_version}）
{prev_synthesis}

## 本次新增数据

### 新增日记分析（{diary_count} 条）
{diary_analyses}

### 新增聊天洞察（{chat_insight_count} 条）
{chat_insights}

### 新增聊天话题（{chat_topic_count} 个）
{chat_topics}

## 要求
请综合新旧数据，输出更新后的合成结果。格式要求见系统指令。
"""
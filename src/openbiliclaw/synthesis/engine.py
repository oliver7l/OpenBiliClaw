"""迭代合成引擎。

核心逻辑：
  1. 读取上次合成状态（last_xxx_id）
  2. 拉取各模块新增数据
  3. 加载前次合成结果
  4. LLM 合成：前次结果 + 新增数据 → 更新后的洞察
  5. 版本化存储新结果
  6. 更新合成状态
  7. 可选：将增强结果回写到源模块
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from .models import (
    _SYNTHESIS_SYSTEM_PROMPT,
    _SYNTHESIS_USER_PROMPT_TEMPLATE,
    CrossModulePattern,
    SynthesisConfig,
    SynthesisState,
    SynthesisVersion,
)
from .store import SynthesisStore

logger = logging.getLogger(__name__)

try:
    from obc_llm.generation import generate_structured
except ImportError:
    generate_structured = None


class SynthesisEngine:
    """跨模块迭代合成引擎。

    每次 run() 调用都是增量更新：
    - 只处理上次合成以来新增的日记分析和聊天洞察/话题
    - 包含前次合成结果作为上下文
    - LLM 在旧结果基础上更新而非重写
    - 生成新版本，保留完整演进历史
    """

    def __init__(
        self,
        config: SynthesisConfig | None = None,
        llm_service: Any = None,
        store: SynthesisStore | None = None,
    ):
        self._config = config or SynthesisConfig()
        self._llm_service = llm_service
        self._store = store or SynthesisStore(self._config.main_db_path)

    # ── 主入口 ─────────────────────────────────────────────────────

    async def run(self) -> SynthesisVersion | None:
        """执行一次迭代合成。

        Returns:
            新生成的合成版本，若无新数据或无 LLM 则返回 None。

        """
        if self._llm_service is None:
            logger.warning("synthesis: LLM service not configured, skipping")
            return None

        state = self._store.get_state()

        # 1. 拉取新增数据
        diary_analyses = self._store.get_new_diary_analyses(
            state.last_diary_analysis_id, self._config.max_new_diary_analyses
        )
        chat_insights = self._store.get_new_chat_insights(
            state.last_chat_insight_id, self._config.max_new_chat_insights
        )
        chat_topics = self._store.get_new_chat_topics(
            state.last_chat_topic_id, self._config.max_new_chat_topics
        )

        total_new = len(diary_analyses) + len(chat_insights) + len(chat_topics)
        if total_new == 0:
            logger.info("synthesis: no new data, skipping")
            return None

        logger.info(
            "synthesis: run %d new items (diary=%d, chat_insight=%d, chat_topic=%d)",
            total_new,
            len(diary_analyses),
            len(chat_insights),
            len(chat_topics),
        )

        # 2. 加载前次合成结果
        prev_version = self._store.get_latest_version()
        prev_synthesis_text = self._format_prev_synthesis(prev_version)
        prev_ver = prev_version.version if prev_version else 0

        # 3. 构建 LLM 提示
        user_input = _SYNTHESIS_USER_PROMPT_TEMPLATE.format(
            prev_version=prev_ver,
            prev_synthesis=prev_synthesis_text,
            diary_count=len(diary_analyses),
            diary_analyses=self._format_diary_analyses(diary_analyses),
            chat_insight_count=len(chat_insights),
            chat_insights=self._format_chat_insights(chat_insights),
            chat_topic_count=len(chat_topics),
            chat_topics=self._format_chat_topics(chat_topics),
        )

        # 4. LLM 合成
        try:
            result = await generate_structured(
                self._llm_service,
                system_instruction=_SYNTHESIS_SYSTEM_PROMPT,
                user_input=user_input,
                parse=lambda x: json.loads(x) if isinstance(x, str) else x,
                label="synthesis.iterative",
                temperature=self._config.llm_temperature,
                max_tokens=self._config.llm_max_tokens,
            )
        except Exception as exc:
            logger.exception("synthesis: LLM call failed: %s", exc)
            return None

        if not result:
            logger.warning("synthesis: LLM returned empty result")
            return None

        # 5. 构建版本
        new_version = SynthesisVersion(
            version=prev_ver + 1,
            parent_version=prev_ver if prev_ver > 0 else None,
            created_at=datetime.now(UTC).isoformat(),
            new_diary_count=len(diary_analyses),
            new_chat_insight_count=len(chat_insights),
            new_chat_topic_count=len(chat_topics),
            summary=result.get("summary", ""),
            patterns=result.get("patterns", []),
            insights=result.get("insights", []),
            themes=result.get("themes", []),
            concerns=result.get("concerns", []),
            cross_patterns=[CrossModulePattern(**p) for p in result.get("cross_patterns", [])],
        )

        # 6. 持久化
        row_id = self._store.save_version(new_version)
        new_version.id = row_id

        # 7. 更新状态
        new_state = SynthesisState(
            last_diary_analysis_id=max(
                state.last_diary_analysis_id,
                max((a["id"] for a in diary_analyses), default=0),
            ),
            last_chat_insight_id=max(
                state.last_chat_insight_id,
                max((i["id"] for i in chat_insights), default=0),
            ),
            last_chat_topic_id=max(
                state.last_chat_topic_id,
                max((t["id"] for t in chat_topics), default=0),
            ),
            current_version=new_version.version,
            last_synthesis_at=new_version.created_at,
            total_synthesis_runs=state.total_synthesis_runs + 1,
        )
        self._store.update_state(new_state)

        logger.info(
            "synthesis: version %d saved (run #%d)",
            new_version.version,
            new_state.total_synthesis_runs,
        )
        return new_version

    # ── 格式化辅助 ─────────────────────────────────────────────────

    @staticmethod
    def _format_prev_synthesis(prev: SynthesisVersion | None) -> str:
        if prev is None:
            return "（尚无前次合成结果）"
        parts = [
            f"版本: {prev.version}",
            f"创建时间: {prev.created_at}",
            f"摘要: {prev.summary}",
            f"模式: {'; '.join(prev.patterns) if prev.patterns else '无'}",
            f"洞察: {'; '.join(prev.insights) if prev.insights else '无'}",
            f"主题: {'; '.join(prev.themes) if prev.themes else '无'}",
            f"关注点: {'; '.join(prev.concerns) if prev.concerns else '无'}",
        ]
        if prev.cross_patterns:
            parts.append("跨模块模式:")
            for p in prev.cross_patterns:
                parts.append(f"  - {p.pattern} (置信度: {p.confidence})")
        return "\n".join(parts)

    @staticmethod
    def _format_diary_analyses(analyses: list[dict[str, Any]]) -> str:
        if not analyses:
            return "（无）"
        lines = []
        for a in analyses:
            themes = _safe_json_load(a.get("themes", "[]"))
            emotions = _safe_json_load(a.get("emotions", "{}"))
            lines.append(
                f"- 日记 #{a['diary_id']} ({a.get('entry_date', '?')}): "
                f"情绪={emotions}, "
                f"主题={themes}, "
                f"摘要={a.get('summary', '')[:120]}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_chat_insights(insights: list[dict[str, Any]]) -> str:
        if not insights:
            return "（无）"
        lines = []
        for i in insights:
            lines.append(
                f"- 会话 #{i.get('session_id', '?')} "
                f"[{i.get('insight_type', '?')}]: "
                f"{i.get('content', '')[:120]}"
                f" (置信度: {i.get('confidence', '?')})"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_chat_topics(topics: list[dict[str, Any]]) -> str:
        if not topics:
            return "（无）"
        lines = []
        for t in topics:
            keywords = _safe_json_load(t.get("keywords", "[]"))
            lines.append(
                f"- 会话 #{t.get('session_id', '?')}: "
                f"话题={t.get('topic', '?')}, "
                f"关键词={keywords}, "
                f"摘要={t.get('summary', '')[:120]}"
            )
        return "\n".join(lines)


def _safe_json_load(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    return value

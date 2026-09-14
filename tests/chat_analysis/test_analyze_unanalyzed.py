"""聊天分析「空转标记」回归测试。

背景（2026-09-15 实测发现，详见 ``docs/module-inventory-2026-09-15.md`` §2）：

``self_evolution/loop_engine.py`` 每 6 小时调一次 ``analyze_unanalyzed(limit=10)``，
而它构造 ``ChatAnalysisService`` 时**没传 llm_service**。LLM 缺失时
``_call_llm`` 返回 None、三个分析函数静默返回空结果、**不抛异常**——旧版 service
只要不抛异常就 ``mark_session_analyzed()``，于是会话被标记 ``analyzed=1`` 且
**永久不会再被扫描**。

生产库实测：220 个 ``analyzed=1`` 会话里 **219 个零产出**
（``chat_topics`` 25 行 / ``chat_insights`` 20 行全部只属于 session 1）。

这里锁住三条契约：
1. 没有可用 LLM → 直接跳过，**不标记**任何会话
2. 有 LLM 但分析结果为空 → **不标记**（保持未分析，下轮重试）
3. 有产出 → 才标记（防止「修过头」把正常路径也堵死）

刻意**不测**「重置历史数据」——那会触发 800 个会话 × 3 次 LLM 调用，属于
需要用户授权的运维动作。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openbiliclaw.chat_analysis.models import ChatSessionCreate
from openbiliclaw.chat_analysis.service import ChatAnalysisService


class _StubLLM:
    """最小可用的 LLM 替身：只需能被 ``_get_llm`` 认出来。

    ``_get_llm`` 只检查对象有没有 ``complete_structured_task`` 属性，
    所以这里不需要实现任何真实调用——分析本体由 monkeypatch 顶替。
    """

    async def complete_structured_task(self, **kwargs):  # noqa: ANN003, ANN201
        raise AssertionError("本测试不应真的调用 LLM")


@pytest.fixture()
def service(tmp_path: Path):
    """独立的临时库，绝不碰 ``data/chat_analysis.db``。"""
    return ChatAnalysisService(db_path=tmp_path / "chat.db")


def _create_session(service: ChatAnalysisService, title: str = "测试会话"):
    return service.store.create_session(ChatSessionCreate(title=title))


def _analyzed_in_db(service: ChatAnalysisService, session_id: int) -> int:
    """**直接查库**读 ``analyzed``，而不是看返回的模型字段。

    为什么不能信模型字段：``store._row_to_session`` 原先压根没映射 ``analyzed``，
    ``ChatSession.analyzed`` 恒为 False —— 于是「服务层错误地标记了已分析」这件事
    在读路径上**完全看不出来**（两个 bug 互相掩盖）。查库才是真值。
    """
    row = service.store.conn.execute(
        "SELECT analyzed FROM chat_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    return int(row[0]) if row else -1


async def test_no_llm_service_never_marks_analyzed(service: ChatAnalysisService) -> None:
    """没有 LLM 时必须原样留下会话 —— 而不是标记成「已分析但零产出」。

    修复前：``analyze_session`` 静默返回空但仍 ``mark_session_analyzed()``，
    于是每跑一轮污染 10 个会话，且这些会话永远不会再被处理。
    """
    session = _create_session(service)
    assert session.analyzed == 0

    result = await service.analyze_unanalyzed(limit=5)

    assert _analyzed_in_db(service, session.id) == 0, (
        "没有 LLM 却把会话标记成了已分析（会永久丢失分析机会，且此后不再被扫描）"
    )
    assert result.get("analyzed") == 0
    assert result.get("skipped_no_llm") == 1


async def test_empty_result_does_not_mark_analyzed(
    service: ChatAnalysisService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """有 LLM 但分析产出为空时同样不得标记。

    覆盖 LLM 存在却「调用失败 / 返回空 / 配额耗尽」的情况——这些路径下
    ``_call_llm`` 统一返回 None，症状与没有 LLM 完全一致。
    """
    service._llm_service = _StubLLM()
    session = _create_session(service)

    async def _empty(_session_id: int) -> dict:
        return {"topics": [], "insights": [], "summary": None, "topic_count": 0, "insight_count": 0}

    monkeypatch.setattr(service, "analyze_session", _empty)

    await service.analyze_unanalyzed(limit=5)

    assert _analyzed_in_db(service, session.id) == 0, (
        "分析结果为空却标记了已分析（下轮不会再重试，产出永久为 0）"
    )


async def test_non_empty_result_marks_analyzed(
    service: ChatAnalysisService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """反向守卫：真有产出时必须照常标记，防止把正常增量逻辑也堵死。"""
    service._llm_service = _StubLLM()
    session = _create_session(service)

    async def _productive(_session_id: int) -> dict:
        return {"topics": [], "insights": [], "summary": {"tldr": "x"}, "topic_count": 0, "insight_count": 0}

    monkeypatch.setattr(service, "analyze_session", _productive)

    result = await service.analyze_unanalyzed(limit=5)

    assert _analyzed_in_db(service, session.id) == 1, (
        "有产出却没标记已分析（增量分析会无限重复同一批）"
    )
    assert result.get("analyzed") == 1


async def test_get_session_reports_analyzed_flag(service: ChatAnalysisService) -> None:
    """读路径必须如实反映 ``analyzed``。

    修复前 ``store._row_to_session`` 完全没有映射 ``analyzed`` /
    ``last_analyzed_at``，``ChatSession.analyzed`` 恒为 False —— 库里明明是 1，
    界面与 API 却一律显示「未分析」。这个 bug 还**掩盖了上一个 bug**
    （服务层错误地把 219 个会话标记成已分析，但读出来全显示未分析）。
    """
    session = _create_session(service)
    assert service.store.get_session(session.id) is not None
    assert service.store.get_session(session.id).analyzed is False

    service.store.mark_session_analyzed(session.id)

    after = service.store.get_session(session.id)
    assert after is not None
    assert after.analyzed is True, "库里已置 1，但读出来的模型仍报未分析"
    assert after.last_analyzed_at, "last_analyzed_at 也应一并映射出来"

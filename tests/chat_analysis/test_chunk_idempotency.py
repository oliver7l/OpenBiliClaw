"""分析片段导入幂等性回归测试（2026-09-15）。

背景：``importer.py`` 原先对 ``chat_analysis_chunks`` 做**无条件 INSERT**，
且该表没有任何唯一约束 —— 同一份分析文件被导入两次就会产生两行。

**注意口径**（这是本次最值钱的一条教训）：
最初把「``(session_title, start_line, end_line)`` 相同的 413 组 / 419 行」
误判成重复数据。逐列比对后发现**它们内容各不相同**（几乎都落在默认区间
``(0, 0)`` 上，但 ``analysis_content`` / ``analysis_file`` 不同）——
按全部内容列分组，真重复为 **0**。若照最初的结论删除，会删掉 419 行真实数据。

所以正确的护栏是给**真正的来源标识** ``analysis_file`` 加唯一索引
（实测 832 行 / 832 个不同值），而不是按「标题+行区间」去重。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openbiliclaw.chat_analysis.models import ChatAnalysisChunkCreate
from openbiliclaw.chat_analysis.service import ChatAnalysisService


@pytest.fixture()
def store(tmp_path: Path):
    return ChatAnalysisService(db_path=tmp_path / "chat.db").store


def _chunk(analysis_file: str, content: str = "分析内容", title: str = "某群") -> ChatAnalysisChunkCreate:
    return ChatAnalysisChunkCreate(
        session_title=title,
        start_line=0,
        end_line=3000,
        analysis_content=content,
        analysis_file=analysis_file,
        model_used="deepseek",
    )


def test_same_analysis_file_inserted_once(store) -> None:
    """同一来源文件写两次 → 只留一行，且第二次返回既有行（不抛错）。"""
    first = store.create_analysis_chunk(_chunk("chat-analysis://群A/第0-3000行"))
    second = store.create_analysis_chunk(
        _chunk("chat-analysis://群A/第0-3000行", content="换了内容的重复导入")
    )

    assert store.count_analysis_chunks() == 1, "同一 analysis_file 被写入了多行"
    assert second.id == first.id, "重复导入应回读既有行"
    assert second.analysis_content == first.analysis_content, "内容应保持首次写入的值"


def test_different_analysis_files_both_kept(store) -> None:
    """反向守卫：不同来源必须都保留（唯一键不能宽到误杀）。"""
    store.create_analysis_chunk(_chunk("chat-analysis://群A/第0-3000行"))
    store.create_analysis_chunk(_chunk("chat-analysis://群A/第3000-6000行"))

    assert store.count_analysis_chunks() == 2


def test_same_line_range_but_different_source_kept(store) -> None:
    """**本次教训的回归**：标题与行区间相同、但来源不同 → 必须都保留。

    这类行曾被误判为「重复」共 419 行；实际上它们是不同分析文件产出的不同内容。
    """
    store.create_analysis_chunk(
        _chunk("chat-analysis://群A/analysis-1.txt", content="第一份分析", title="群A")
    )
    store.create_analysis_chunk(
        _chunk("chat-analysis://群A/analysis-2.txt", content="第二份分析", title="群A")
    )

    assert store.count_analysis_chunks(session_title="群A") == 2, (
        "标题+行区间相同但来源不同的记录被误当重复处理"
    )


def test_empty_analysis_file_is_not_deduped(store) -> None:
    """空 ``analysis_file`` 不参与唯一约束（部分索引排除空串）。

    否则第二条空值行会被误拒——而空来源在历史数据里是可能的。
    """
    store.create_analysis_chunk(_chunk("", content="内容一"))
    store.create_analysis_chunk(_chunk("", content="内容二"))

    assert store.count_analysis_chunks() == 2


def test_has_analysis_chunk(store) -> None:
    """导入方据此判断是否跳过，因此必须准确。"""
    assert store.has_analysis_chunk("chat-analysis://群A/第0-3000行") is False
    store.create_analysis_chunk(_chunk("chat-analysis://群A/第0-3000行"))
    assert store.has_analysis_chunk("chat-analysis://群A/第0-3000行") is True
    assert store.has_analysis_chunk("") is False

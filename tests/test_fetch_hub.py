"""Fetch Hub 离线单测：URL 路由、降级编排、健康度落库、草稿生成（全部 mock，不联网）。"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "scripts" / "content_library"))

from fetchhub import core  # noqa: E402
from fetchhub.draft import write_draft  # noqa: E402
from fetchhub.v2ex import parse_topic_id  # noqa: E402


def _doc(url="https://example.com/x", via="ch-b", platform="v2ex"):
    return core.UnifiedDoc(
        url=url,
        platform=platform,
        title="标题",
        author="作者",
        published_at="2026-09-14 10:00",
        content_md="正文内容",
        replies=[core.Reply("张三", "2026-09-14 11:00", "回复")],
        images=[],
        fetched_via=via,
        fetched_at="2026-09-14 12:00:00",
        confidence="full",
    )


# ---- URL 路由 ----


def test_detect_platform():
    assert core.detect_platform("https://www.v2ex.com/t/1241165") == "v2ex"
    assert core.detect_platform("https://www.zhihu.com/question/1/answer/2") == "zhihu"
    assert core.detect_platform("https://zhuanlan.zhihu.com/p/123") == "zhihu"
    assert core.detect_platform("https://www.xiaohongshu.com/discovery/item/abc") == "xhs"
    assert core.detect_platform("https://xhslink.cn/o/xyz") == "xhs"
    assert core.detect_platform("https://www.bilibili.com/video/BV1xx") == "bilibili"
    assert core.detect_platform("https://www.bilibili.com/bangumi/play/ep123") == "bilibili"
    assert core.detect_platform("https://b23.tv/abc") == "bilibili"
    assert core.detect_platform("https://example.com/page") == "generic"


def test_parse_topic_id():
    assert parse_topic_id("https://www.v2ex.com/t/1241165#reply19") == "1241165"
    assert parse_topic_id("https://www.v2ex.com/t/1240830?p=2") == "1240830"
    with pytest.raises(core.ChannelError) as ei:
        parse_topic_id("https://www.v2ex.com/recent?p=7")
    assert ei.value.kind == "parse"


# ---- 降级链声明 ----


def test_channel_chains_declared_and_agentlimb_last():
    for platform, chain in core.CHANNEL_CHAINS.items():
        assert chain, f"{platform} 降级链为空"
        assert "agentlimb" in chain[-1], f"{platform} 链尾必须是 AgentLimb 兜底"


# ---- 降级编排 ----


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "content.db"
    monkeypatch.setattr(core, "CONTENT_DB", db)
    return db


def test_fetch_degrades_to_next_channel(tmp_db, monkeypatch):
    calls = []

    def ch_a(url):
        calls.append("a")
        raise core.ChannelError("challenge", "被挡", "a")

    def ch_b(url):
        calls.append("b")
        return _doc(via="b")

    monkeypatch.setattr(core, "CHANNEL_CHAINS", {"v2ex": ["a", "b"]})
    monkeypatch.setattr(core, "_dispatch", lambda ch, url: {"a": ch_a, "b": ch_b}[ch](url))

    doc, attempts = core.fetch("https://www.v2ex.com/t/1")
    assert calls == ["a", "b"]
    assert doc.fetched_via == "b"
    assert [a["ok"] for a in attempts] == [False, True]

    # 健康度落库：每次尝试一行
    rows = sqlite3.connect(tmp_db).execute("SELECT channel, ok FROM fetch_log ORDER BY id").fetchall()
    assert rows == [("a", 0), ("b", 1)]


def test_fetch_no_fallback_only_first_channel(tmp_db, monkeypatch):
    calls = []

    def ch_a(url):
        calls.append("a")
        raise core.ChannelError("parse", "空", "a")

    def ch_b(url):  # pragma: no cover - 不应被调用
        calls.append("b")
        return _doc(via="b")

    monkeypatch.setattr(core, "CHANNEL_CHAINS", {"v2ex": ["a", "b"]})
    monkeypatch.setattr(core, "_dispatch", lambda ch, url: {"a": ch_a, "b": ch_b}[ch](url))

    with pytest.raises(core.AllChannelsFailed):
        core.fetch("https://www.v2ex.com/t/1", no_fallback=True)
    assert calls == ["a"]


def test_fetch_forced_channel_skips_chain(tmp_db, monkeypatch):
    monkeypatch.setattr(core, "CHANNEL_CHAINS", {"v2ex": ["a"]})
    monkeypatch.setattr(core, "_dispatch", lambda ch, url: _doc(via=ch))
    doc, attempts = core.fetch("https://www.v2ex.com/t/1", forced_channel="z")
    assert doc.fetched_via == "z"
    assert len(attempts) == 1


def test_fetch_unexpected_exception_still_degrades(tmp_db, monkeypatch):
    def ch_a(url):
        raise RuntimeError("boom")

    def ch_b(url):
        return _doc(via="b")

    monkeypatch.setattr(core, "CHANNEL_CHAINS", {"v2ex": ["a", "b"]})
    monkeypatch.setattr(core, "_dispatch", lambda ch, url: {"a": ch_a, "b": ch_b}[ch](url))
    doc, attempts = core.fetch("https://www.v2ex.com/t/1")
    assert doc.fetched_via == "b"
    assert attempts[0]["error_kind"] == "error"


def test_fetch_empty_result_treated_as_parse_error(tmp_db, monkeypatch):
    def ch_a(url):
        d = _doc()
        d.title = ""
        d.content_md = ""
        return d

    def ch_b(url):
        return _doc(via="b")

    monkeypatch.setattr(core, "CHANNEL_CHAINS", {"v2ex": ["a", "b"]})
    monkeypatch.setattr(core, "_dispatch", lambda ch, url: {"a": ch_a, "b": ch_b}[ch](url))
    doc, attempts = core.fetch("https://www.v2ex.com/t/1")
    assert doc.fetched_via == "b"
    assert attempts[0]["error_kind"] == "parse"


def test_unknown_channel_raises_config(tmp_db, monkeypatch):
    monkeypatch.setattr(core, "CHANNEL_CHAINS", {"v2ex": ["nope"]})
    with pytest.raises(core.AllChannelsFailed) as ei:
        core.fetch("https://www.v2ex.com/t/1")
    assert ei.value.attempts[0]["error_kind"] == "config"


# ---- 健康度 ----


def test_health_aggregates(tmp_db):
    core.log_fetch("u", "v2ex", "c1", True, None, 100)
    core.log_fetch("u", "v2ex", "c1", False, "challenge", 200, "x")
    core.log_fetch("u", "zhihu", "c2", True, None, 50)
    rows = {r["channel"]: r for r in core.health(days=7)}
    assert rows["c1"]["total"] == 2 and rows["c1"]["ok"] == 1
    assert rows["c1"]["rate"] == pytest.approx(0.5)
    assert rows["c2"]["rate"] == 1.0


def test_health_empty_when_no_db(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "CONTENT_DB", tmp_path / "missing.db")
    assert core.health() == []


# ---- UnifiedDoc 序列化 ----


def test_unified_doc_to_json_roundtrip():
    d = _doc()
    parsed = json.loads(d.to_json())
    assert parsed["title"] == "标题"
    assert parsed["replies"][0]["author"] == "张三"
    assert parsed["fetched_via"] == "ch-b"


# ---- 草稿生成 ----


def test_write_draft(tmp_path):
    doc = _doc(url="https://www.v2ex.com/t/999", via="agentlimb-v2ex")
    doc.extra = {"node": "职场话题", "reply_count": 7}
    path = write_draft(doc, project_root=tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "# 标题" in text
    assert "来源：v2ex" in text
    assert "TODO（必须人写" in text
    assert "## 平台附加信息" in text
    assert "- node: 职场话题" in text
    assert "> **张三**" in text
    # 不应出现重复分隔线
    assert "---\n---" not in text


def test_write_draft_dedupes_filename(tmp_path):
    doc = _doc()
    p1 = write_draft(doc, project_root=tmp_path)
    p2 = write_draft(doc, project_root=tmp_path)
    assert p1 != p2 and p1.exists() and p2.exists()

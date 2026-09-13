"""对话归档模块单元测试。

覆盖：
  - 存储层 ConversationArchiveStore（建表、upsert 幂等、批量、列表/排序、FTS 全文搜索、详情、计数、统计）
  - API 路由（列表 / 详情 / 统计 / 创建 / 批量导入 / 无数据库退化）

不依赖真实主库或 LLM：存储层用临时文件触发自带建表；API 用临时文件库
（``check_same_thread=False``，兼容 TestClient 子线程）+ 注入式 ``RuntimeContext``。
"""

from __future__ import annotations

import sqlite3
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# 模块级存储端点依赖全局 _service 记忆体，需要可重置
import openbiliclaw.api.conversation_archive_routes as ca_routes
from openbiliclaw.conversation_archive.store import (
    _SCHEMA_SQL,
    ConversationArchiveStore,
)

# ── fixtures ──────────────────────────────────────────────────────────

@pytest.fixture()
def store(tmp_path: Path) -> ConversationArchiveStore:
    """db_path 模式：首次访问 conn 时自动执行建表。"""
    return ConversationArchiveStore(db_path=tmp_path / "ca.db")


@pytest.fixture()
def sample_records() -> list[dict]:
    return [
        {
            "seq": 1,
            "kind": "zhihu_eval",
            "user_question": "如何评价 DeepSeek V4.1 Flash？",
            "question_title": "如何评价 DeepSeek V4.1 Flash？",
            "source_url": "https://www.zhihu.com/question/1/answer/1",
            "source_type": "answer",
            "author": "苏剑林",
            "headline": "科学空间博主",
            "voteup_count": 197,
            "comment_count": 4,
            "published_at": "2026-09-11",
            "tags": ["DeepSeek", "大模型架构"],
            "extracted_original_md": "Prefill 与 Decode 不对称是天然存在的结构，常规架构下每层都要算自己的 KV Cache，"
            "所以省下的只是很小一部分；真正的收益来自把这种不对称放大成结构级优势。",
            "my_analysis_md": (
                "洞见：真正的杠杆在于结构性放大，而不是发明新东西；可商榷的是收益的绝对量级"
                "在超长上下文下才显著。这与你在腾讯微视讲召回排序错位时用的叙事一致："
                "先点破常识，再讲结构性放大。"
            ),
        },
        {
            "seq": 2,
            "kind": "zhihu_eval",
            "user_question": "怎么理解推荐系统的两阶段架构？",
            "question_title": "怎么理解推荐系统的两阶段架构？",
            "source_url": "https://www.zhihu.com/question/2/answer/2",
            "source_type": "answer",
            "author": "王喆",
            "headline": "推荐系统作者",
            "voteup_count": 88,
            "comment_count": 12,
            "published_at": "2026-09-10",
            "tags": ["推荐系统", "召回"],
            "extracted_original_md": "召回决定推荐系统的天花板，排序只是在逼近这个天花板；"
            "两阶段架构的本质是把候选生成的广度与打分精度解耦，各自优化。",
            "my_analysis_md": (
                "可商榷：端到端重排正在抹平两阶段边界，但工业界仍以级联为主流，成本是核心约束。"
                "生成式重排（G-E 框架）在淘宝每平每屋已落地，说明方向成立但工程门槛高。"
            ),
        },
    ]


def _make_fake_db(tmp_path: Path) -> object:
    """构造一个最小 database 替身，供 API 注入（database= 模式）。

    必须用文件库 + check_same_thread=False：TestClient 在独立 portal 线程里执行
    同步端点，而 sqlite3 默认禁止跨线程复用连接。
    """
    conn = sqlite3.connect(tmp_path / "ca_api.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_SQL)
    db = types.SimpleNamespace(conn=conn)
    return db


@pytest.fixture()
def api_client(tmp_path: Path) -> TestClient:
    """注册对话归档路由的 FastAPI 测试客户端，数据库用文件替身。"""
    ca_routes._service = None  # 清空模块级记忆体，确保每次用新库
    app = FastAPI()
    ctx = types.SimpleNamespace(database=_make_fake_db(tmp_path))
    ca_routes.register_conversation_archive_routes(app, ctx)
    return TestClient(app)


# ── 存储层：写入 ──────────────────────────────────────────────────────

def test_upsert_creates_and_returns_id(store: ConversationArchiveStore) -> None:
    rid = store.upsert_item({"seq": 1, "user_question": "Q1"})
    assert isinstance(rid, int) and rid >= 1
    assert store.count_items() == 1


def test_upsert_is_idempotent_on_seq(store: ConversationArchiveStore) -> None:
    id1 = store.upsert_item({"seq": 1, "user_question": "旧问题"})
    id2 = store.upsert_item({"seq": 1, "user_question": "新问题"})
    assert id1 == id2  # 同 seq 复用同一行
    assert store.count_items() == 1
    item = store.get_item(id1)
    assert item is not None and item["user_question"] == "新问题"


def test_upsert_many_and_counts(store: ConversationArchiveStore, sample_records: list[dict]) -> None:
    n = store.upsert_many(sample_records)
    assert n == 2
    assert store.count_items() == 2


# ── 存储层：读取 ──────────────────────────────────────────────────────

def test_list_default_returns_all_sorted_by_seq(
    store: ConversationArchiveStore, sample_records: list[dict]
) -> None:
    store.upsert_many(sample_records)
    items = store.list_items()
    assert [it["seq"] for it in items] == [1, 2]


def test_list_sort_by_voteup_desc(
    store: ConversationArchiveStore, sample_records: list[dict]
) -> None:
    store.upsert_many(sample_records)
    items = store.list_items(sort_by="voteup_count", sort_order="DESC")
    assert items[0]["voteup_count"] == 197
    assert items[1]["voteup_count"] == 88


def test_list_pagination(store: ConversationArchiveStore, sample_records: list[dict]) -> None:
    store.upsert_many(sample_records)
    page = store.list_items(limit=1, offset=1, sort_by="seq", sort_order="ASC")
    assert len(page) == 1
    assert page[0]["seq"] == 2


def test_list_fts_search_hits_matching_row(
    store: ConversationArchiveStore, sample_records: list[dict]
) -> None:
    store.upsert_many(sample_records)
    hits = store.list_items(search="推荐系统")
    assert len(hits) == 1
    assert hits[0]["seq"] == 2


def test_get_item_found_and_missing(store: ConversationArchiveStore) -> None:
    rid = store.upsert_item({"seq": 7, "user_question": "Q7"})
    found = store.get_item(rid)
    assert found is not None and found["seq"] == 7
    assert store.get_item(99999) is None


def test_tags_roundtrip_as_json(store: ConversationArchiveStore) -> None:
    store.upsert_item({"seq": 3, "user_question": "Q3", "tags": ["a", "b"]})
    item = store.list_items()[0]
    assert item["tags"] == ["a", "b"]


# ── 存储层：统计 ──────────────────────────────────────────────────────

def test_stats_aggregates(
    store: ConversationArchiveStore, sample_records: list[dict]
) -> None:
    store.upsert_many(sample_records)
    stats = store.get_stats()
    assert stats["total"] == 2
    assert stats["by_kind"].get("zhihu_eval") == 2
    # 两条都带长度 >50 的原文与分析
    assert stats["with_original"] == 2
    assert stats["with_analysis"] == 2
    authors = {a["author"]: a["count"] for a in stats["by_author"]}
    assert authors == {"苏剑林": 1, "王喆": 1}


# ── API 层 ────────────────────────────────────────────────────────────

def test_api_list(api_client: TestClient, sample_records: list[dict]) -> None:
    # 预先灌入数据：直接走同一 store 不便（路由内部 new 了一个），改为走 import 端点
    resp = api_client.post("/api/conversation-archive/import", json={"items": sample_records})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    lst = api_client.get("/api/conversation-archive")
    body = lst.json()
    assert body["ok"] is True
    assert body["total"] == 2
    assert len(body["items"]) == 2


def test_api_get_detail(api_client: TestClient, sample_records: list[dict]) -> None:
    api_client.post("/api/conversation-archive/import", json={"items": sample_records})
    # seq=1 的行 id 为 1
    detail = api_client.get("/api/conversation-archive/1")
    assert detail.status_code == 200
    assert detail.json()["item"]["seq"] == 1
    # 不存在
    miss = api_client.get("/api/conversation-archive/999")
    assert miss.status_code == 404
    assert miss.json()["ok"] is False


def test_api_stats(api_client: TestClient, sample_records: list[dict]) -> None:
    api_client.post("/api/conversation-archive/import", json={"items": sample_records})
    stats = api_client.get("/api/conversation-archive/stats")
    body = stats.json()
    assert body["ok"] is True
    assert body["stats"]["total"] == 2


def test_api_create_requires_seq(api_client: TestClient) -> None:
    bad = api_client.post("/api/conversation-archive", json={"user_question": "x"})
    assert bad.status_code == 400
    assert bad.json()["ok"] is False


def test_api_create_upserts(api_client: TestClient) -> None:
    resp = api_client.post(
        "/api/conversation-archive",
        json={"seq": 100, "user_question": "新建问题", "tags": ["new"]},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["ok"] is True
    assert body["item"]["seq"] == 100
    assert body["item"]["tags"] == ["new"]


def test_api_import_rejects_non_list(api_client: TestClient) -> None:
    resp = api_client.post("/api/conversation-archive/import", json={"items": "nope"})
    assert resp.status_code == 400


def test_api_503_when_database_unavailable() -> None:
    ca_routes._service = None
    app = FastAPI()
    ctx = types.SimpleNamespace(database=None)
    ca_routes.register_conversation_archive_routes(app, ctx)
    client = TestClient(app)
    resp = client.get("/api/conversation-archive")
    assert resp.status_code == 503
    assert resp.json()["ok"] is False

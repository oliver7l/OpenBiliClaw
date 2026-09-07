"""日记知识图谱模块单元测试。

覆盖 KnowledgeGraphService 的核心功能：标签关联网络、人物关系图谱、
混合知识网络、人物关系分析、知识节点详情、网络统计。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from openbiliclaw.diary.knowledge_graph import (
    GraphEdge,
    GraphNode,
    KnowledgeGraph,
    KnowledgeGraphService,
    KnowledgeNodeDetail,
    PersonRelation,
)
from openbiliclaw.diary.models import DiaryEntryCreate, MoodLevel
from openbiliclaw.diary.store import DiaryStore


@pytest.fixture
def temp_store() -> DiaryStore:
    """创建临时数据库和 DiaryStore。"""
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "test_diary.db")
    store = DiaryStore(db_path=db_path)
    store.initialize()
    yield store
    # 清理
    try:
        os.unlink(db_path)
        os.rmdir(tmp)
    except OSError:
        pass


@pytest.fixture
def sample_entries(temp_store: DiaryStore) -> list[int]:
    """插入示例日记数据，返回日记 ID 列表。"""
    entries = [
        DiaryEntryCreate(
            entry_date="2024-01-01",
            title="和艳艳一起过元旦",
            content="今天和艳艳、乐乐一起去公园玩，很开心。妈妈也来了。",
            source="test",
            tags=["节日", "家庭"],
            mood=MoodLevel.HAPPY,
        ),
        DiaryEntryCreate(
            entry_date="2024-01-02",
            title="工作加班",
            content="今天加班到很晚，和同事一起完成项目。有点累但有成就感。",
            source="test",
            tags=["工作", "加班"],
            mood=MoodLevel.NEUTRAL,
        ),
        DiaryEntryCreate(
            entry_date="2024-01-03",
            title="和艳艳吵架",
            content="今天和艳艳因为小事吵架了，心情很不好。乐乐在旁边看着。",
            source="test",
            tags=["感情", "吵架"],
            mood=MoodLevel.SAD,
        ),
        DiaryEntryCreate(
            entry_date="2024-01-04",
            title="家庭聚餐",
            content="今天妈妈做了好吃的，爸爸也来了。艳艳和乐乐都很开心。",
            source="test",
            tags=["家庭", "美食"],
            mood=MoodLevel.VERY_HAPPY,
        ),
        DiaryEntryCreate(
            entry_date="2024-01-05",
            title="和朋友聚会",
            content="今天和老朋友聚会，聊了很多往事。朋友带了他老婆一起来。",
            source="test",
            tags=["朋友", "聚会"],
            mood=MoodLevel.HAPPY,
        ),
    ]
    ids = []
    for entry in entries:
        entry_id = temp_store.create_entry(entry)
        ids.append(entry_id)
    return ids


class TestDataStructures:
    """测试数据结构。"""

    def test_graph_node_creation(self):
        """测试 GraphNode 创建。"""
        node = GraphNode(
            id="tag:test",
            label="测试标签",
            type="tag",
            size=10,
            x=100.0,
            y=200.0,
        )
        assert node.id == "tag:test"
        assert node.label == "测试标签"
        assert node.type == "tag"
        assert node.size == 10
        assert node.x == 100.0
        assert node.y == 200.0

    def test_graph_edge_creation(self):
        """测试 GraphEdge 创建。"""
        edge = GraphEdge(
            source="tag:a",
            target="tag:b",
            weight=5,
            type="co-occurrence",
        )
        assert edge.source == "tag:a"
        assert edge.target == "tag:b"
        assert edge.weight == 5
        assert edge.type == "co-occurrence"

    def test_knowledge_graph_to_dict(self):
        """测试 KnowledgeGraph 序列化。"""
        graph = KnowledgeGraph(
            nodes=[
                GraphNode(id="tag:a", label="A", type="tag", size=5),
                GraphNode(id="tag:b", label="B", type="tag", size=3),
            ],
            edges=[
                GraphEdge(source="tag:a", target="tag:b", weight=2, type="co-occurrence"),
            ],
        )
        d = graph.to_dict()
        assert "nodes" in d
        assert "edges" in d
        assert len(d["nodes"]) == 2
        assert len(d["edges"]) == 1
        assert d["nodes"][0]["id"] == "tag:a"

    def test_person_relation_creation(self):
        """测试 PersonRelation 创建。"""
        rel = PersonRelation(
            person="艳艳",
            related_person="乐乐",
            relation_type="family",
            co_occurrence_count=10,
            first_appeared="2024-01-01",
            last_appeared="2024-01-05",
        )
        assert rel.person == "艳艳"
        assert rel.related_person == "乐乐"
        assert rel.relation_type == "family"
        assert rel.co_occurrence_count == 10


class TestKnowledgeGraphService:
    """测试 KnowledgeGraphService。"""

    def test_service_initialization(self, temp_store: DiaryStore):
        """测试服务初始化。"""
        service = KnowledgeGraphService(temp_store)
        assert service.store is temp_store

    def test_build_tag_network(self, temp_store: DiaryStore, sample_entries: list[int]):
        """测试标签关联网络构建。"""
        service = KnowledgeGraphService(temp_store)
        graph = service.build_tag_network(min_count=1, max_nodes=50)

        assert isinstance(graph, KnowledgeGraph)
        assert len(graph.nodes) > 0
        # 所有节点类型都是 tag
        assert all(n.type == "tag" for n in graph.nodes)
        # 节点大小 > 0
        assert all(n.size > 0 for n in graph.nodes)

    def test_build_tag_network_with_min_count(self, temp_store: DiaryStore, sample_entries: list[int]):
        """测试带最小出现次数的标签网络。"""
        service = KnowledgeGraphService(temp_store)
        graph = service.build_tag_network(min_count=100, max_nodes=50)
        # 没有标签出现 100 次以上，应该为空
        assert len(graph.nodes) == 0

    def test_build_person_network(self, temp_store: DiaryStore, sample_entries: list[int]):
        """测试人物关系图谱构建。"""
        service = KnowledgeGraphService(temp_store)
        graph = service.build_person_network(min_count=1, max_nodes=30)

        assert isinstance(graph, KnowledgeGraph)
        # 示例数据中包含艳艳、乐乐、妈妈、爸爸、朋友等人物
        person_labels = {n.label for n in graph.nodes}
        assert "艳艳" in person_labels
        assert "乐乐" in person_labels

    def test_build_mixed_network(self, temp_store: DiaryStore, sample_entries: list[int]):
        """测试混合知识网络构建。"""
        service = KnowledgeGraphService(temp_store)
        graph = service.build_mixed_network(min_count=1, max_nodes=60)

        assert isinstance(graph, KnowledgeGraph)
        # 混合网络应该包含 tag 和 person 两种类型
        node_types = {n.type for n in graph.nodes}
        assert "tag" in node_types
        assert "person" in node_types
        # 边应该包含 co-occurrence 和 tag-person 两种类型
        edge_types = {e.type for e in graph.edges}
        assert "co-occurrence" in edge_types

    def test_analyze_person_relations(self, temp_store: DiaryStore, sample_entries: list[int]):
        """测试人物关系分析。"""
        service = KnowledgeGraphService(temp_store)
        relations = service.analyze_person_relations("艳艳")

        assert isinstance(relations, list)
        assert len(relations) > 0
        # 所有关系都是 PersonRelation 类型
        assert all(isinstance(r, PersonRelation) for r in relations)
        # 艳艳和乐乐应该有关系
        related_persons = {r.related_person for r in relations}
        assert "乐乐" in related_persons

    def test_analyze_person_relations_nonexistent(self, temp_store: DiaryStore):
        """测试不存在的人物关系分析。"""
        service = KnowledgeGraphService(temp_store)
        relations = service.analyze_person_relations("不存在的人")
        assert len(relations) == 0

    def test_get_node_detail_tag(self, temp_store: DiaryStore, sample_entries: list[int]):
        """测试获取标签节点详情。"""
        service = KnowledgeGraphService(temp_store)
        detail = service.get_node_detail("tag:家庭", limit=10)

        assert detail is not None
        assert isinstance(detail, KnowledgeNodeDetail)
        assert detail.node_type == "tag"
        assert detail.node_label == "家庭"
        assert detail.total_appearances > 0
        assert len(detail.related_entries) > 0

    def test_get_node_detail_person(self, temp_store: DiaryStore, sample_entries: list[int]):
        """测试获取人物节点详情。"""
        service = KnowledgeGraphService(temp_store)
        detail = service.get_node_detail("person:艳艳", limit=10)

        assert detail is not None
        assert isinstance(detail, KnowledgeNodeDetail)
        assert detail.node_type == "person"
        assert detail.node_label == "艳艳"
        assert detail.total_appearances > 0

    def test_get_node_detail_nonexistent(self, temp_store: DiaryStore):
        """测试获取不存在的节点详情。"""
        service = KnowledgeGraphService(temp_store)
        detail = service.get_node_detail("tag:不存在的标签")
        assert detail is None

    def test_get_network_stats(self, temp_store: DiaryStore, sample_entries: list[int]):
        """测试获取网络统计。"""
        service = KnowledgeGraphService(temp_store)
        stats = service.get_network_stats()

        assert isinstance(stats, dict)
        assert "total_entries" in stats
        assert "total_tags" in stats
        assert "total_persons" in stats
        assert "tag_relations" in stats
        assert "person_relations" in stats
        assert "avg_tags_per_entry" in stats
        assert "top_tags" in stats
        assert "top_persons" in stats
        assert stats["total_entries"] == 5

    def test_date_range_filter(self, temp_store: DiaryStore, sample_entries: list[int]):
        """测试日期范围筛选。"""
        service = KnowledgeGraphService(temp_store)
        # 只查 2024-01-01 到 2024-01-02
        graph = service.build_tag_network(
            start_date="2024-01-01",
            end_date="2024-01-02",
            min_count=1,
        )
        # 应该只有前两天的标签
        assert len(graph.nodes) > 0

    def test_empty_database(self, temp_store: DiaryStore):
        """测试空数据库。"""
        service = KnowledgeGraphService(temp_store)
        stats = service.get_network_stats()
        assert stats["total_entries"] == 0

        graph = service.build_tag_network()
        assert len(graph.nodes) == 0

        detail = service.get_node_detail("tag:test")
        assert detail is None


class TestEdgeCases:
    """测试边界情况。"""

    def test_max_nodes_limit(self, temp_store: DiaryStore, sample_entries: list[int]):
        """测试最大节点数限制。"""
        service = KnowledgeGraphService(temp_store)
        graph = service.build_tag_network(min_count=1, max_nodes=2)
        assert len(graph.nodes) <= 2

    def test_node_size_scaling(self, temp_store: DiaryStore, sample_entries: list[int]):
        """测试节点大小缩放（对数缩放）。"""
        service = KnowledgeGraphService(temp_store)
        graph = service.build_person_network(min_count=1, max_nodes=30)
        # 所有节点大小应该在合理范围内
        for node in graph.nodes:
            assert node.size >= 5
            assert node.size <= 50

    def test_person_relation_type_inference(self, temp_store: DiaryStore, sample_entries: list[int]):
        """测试人物关系类型推断。"""
        service = KnowledgeGraphService(temp_store)
        relations = service.analyze_person_relations("艳艳")
        # 乐乐应该被推断为 family
        lele_rel = next((r for r in relations if r.related_person == "乐乐"), None)
        assert lele_rel is not None
        assert lele_rel.relation_type in ("family", "partner", "other")

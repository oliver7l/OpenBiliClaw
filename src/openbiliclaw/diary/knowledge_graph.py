"""日记知识图谱模块：标签关联网络 + 人物关系图谱 + 知识节点详情。

基于历史日记数据构建个人知识网络，可视化标签之间的关联、
人物之间的关系，以及知识节点的时间演变。参考 Personal-Knowledge-Wiki、
memex 等项目的设计理念。
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import DiaryEntry
    from .store import DiaryStore

logger = logging.getLogger(__name__)


@dataclass
class GraphNode:
    """知识图谱节点。"""

    id: str
    label: str
    type: str  # tag / person / theme
    size: int = 10  # 节点大小（基于出现次数）
    x: float = 0.0
    y: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class GraphEdge:
    """知识图谱边（关联关系）。"""

    source: str
    target: str
    weight: int = 1  # 边权重（共现次数）
    type: str = "co-occurrence"  # co-occurrence / relation / theme


@dataclass
class KnowledgeGraph:
    """知识图谱数据结构。"""

    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)

    def to_dict(self) -> dict:
        """转换为字典（用于 JSON 序列化）。"""
        return {
            "nodes": [
                {
                    "id": n.id,
                    "label": n.label,
                    "type": n.type,
                    "size": n.size,
                    "x": n.x,
                    "y": n.y,
                    "metadata": n.metadata,
                }
                for n in self.nodes
            ],
            "edges": [
                {
                    "source": e.source,
                    "target": e.target,
                    "weight": e.weight,
                    "type": e.type,
                }
                for e in self.edges
            ],
        }


@dataclass
class PersonRelation:
    """人物关系。"""

    person: str
    related_person: str
    relation_type: str  # family / friend / colleague / partner / other
    co_occurrence_count: int
    first_appeared: str
    last_appeared: str
    description: str = ""


@dataclass
class KnowledgeNodeDetail:
    """知识节点详情。"""

    node_id: str
    node_label: str
    node_type: str
    total_appearances: int
    related_entries: list[dict] = field(default_factory=list)
    related_nodes: list[dict] = field(default_factory=list)
    timeline: list[dict] = field(default_factory=list)


class KnowledgeGraphService:
    """知识图谱服务：构建标签关联网络、人物关系图谱、知识节点详情。"""

    def __init__(self, store: DiaryStore):
        self.store = store

    # ═══════════════════════════════════════════
    # 标签关联网络
    # ═══════════════════════════════════════════

    def build_tag_network(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        min_count: int = 2,
        max_nodes: int = 50,
    ) -> KnowledgeGraph:
        """构建标签关联网络。

        Args:
            start_date: 开始日期
            end_date: 结束日期
            min_count: 最小出现次数
            max_nodes: 最大节点数

        Returns:
            KnowledgeGraph 标签关联网络
        """
        entries = self._get_entries_in_range(start_date, end_date)
        if not entries:
            return KnowledgeGraph()

        # 统计标签出现次数
        tag_counter: Counter[str] = Counter()
        # 统计标签共现
        co_occurrence: dict[tuple[str, str], int] = defaultdict(int)

        for entry in entries:
            tags = [t for t in (entry.tags or []) if t]
            for tag in tags:
                tag_counter[tag] += 1
            # 统计共现（两两组合）
            for i in range(len(tags)):
                for j in range(i + 1, len(tags)):
                    pair = tuple(sorted([tags[i], tags[j]]))
                    co_occurrence[pair] += 1

        # 筛选高频标签
        top_tags = [tag for tag, count in tag_counter.most_common(max_nodes) if count >= min_count]
        top_tag_set = set(top_tags)

        # 构建节点
        nodes = []
        for tag in top_tags:
            count = tag_counter[tag]
            # 节点大小基于出现次数（对数缩放）
            size = max(10, min(50, 10 + count * 2))
            nodes.append(
                GraphNode(
                    id=f"tag:{tag}",
                    label=tag,
                    type="tag",
                    size=size,
                    metadata={"count": count},
                )
            )

        # 构建边（共现关系）
        edges = []
        for (tag1, tag2), count in co_occurrence.items():
            if tag1 in top_tag_set and tag2 in top_tag_set and count >= 1:
                edges.append(
                    GraphEdge(
                        source=f"tag:{tag1}",
                        target=f"tag:{tag2}",
                        weight=count,
                        type="co-occurrence",
                    )
                )

        # 按权重排序，取前 100 条边
        edges.sort(key=lambda e: e.weight, reverse=True)
        edges = edges[:100]

        return KnowledgeGraph(nodes=nodes, edges=edges)

    # ═══════════════════════════════════════════
    # 人物关系图谱
    # ═══════════════════════════════════════════

    def build_person_network(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        min_count: int = 1,
        max_nodes: int = 30,
    ) -> KnowledgeGraph:
        """构建人物关系图谱。

        Args:
            start_date: 开始日期
            end_date: 结束日期
            min_count: 最小出现次数
            max_nodes: 最大节点数

        Returns:
            KnowledgeGraph 人物关系图谱
        """
        entries = self._get_entries_in_range(start_date, end_date)
        if not entries:
            return KnowledgeGraph()

        # 从日记内容中识别人物（基于人物表 + 关键词）
        person_names = self._get_person_names()

        # 统计人物出现次数
        person_counter: Counter[str] = Counter()
        # 统计人物共现
        co_occurrence: dict[tuple[str, str], int] = defaultdict(int)
        # 人物首次/末次出现
        first_appeared: dict[str, str] = {}
        last_appeared: dict[str, str] = {}

        for entry in entries:
            content = (entry.title or "") + " " + entry.content
            found_persons = []
            for person in person_names:
                if person in content:
                    found_persons.append(person)
                    person_counter[person] += 1
                    if person not in first_appeared or entry.entry_date < first_appeared[person]:
                        first_appeared[person] = entry.entry_date
                    if person not in last_appeared or entry.entry_date > last_appeared[person]:
                        last_appeared[person] = entry.entry_date
            # 统计共现
            for i in range(len(found_persons)):
                for j in range(i + 1, len(found_persons)):
                    pair = tuple(sorted([found_persons[i], found_persons[j]]))
                    co_occurrence[pair] += 1

        # 筛选高频人物
        top_persons = [
            p for p, count in person_counter.most_common(max_nodes) if count >= min_count
        ]
        top_person_set = set(top_persons)

        # 构建节点
        nodes = []
        for person in top_persons:
            count = person_counter[person]
            size = max(10, min(50, 10 + count * 2))
            nodes.append(
                GraphNode(
                    id=f"person:{person}",
                    label=person,
                    type="person",
                    size=size,
                    metadata={
                        "count": count,
                        "first_appeared": first_appeared.get(person, ""),
                        "last_appeared": last_appeared.get(person, ""),
                    },
                )
            )

        # 构建边（共现关系）
        edges = []
        for (p1, p2), count in co_occurrence.items():
            if p1 in top_person_set and p2 in top_person_set and count >= 1:
                edges.append(
                    GraphEdge(
                        source=f"person:{p1}",
                        target=f"person:{p2}",
                        weight=count,
                        type="co-occurrence",
                    )
                )

        edges.sort(key=lambda e: e.weight, reverse=True)
        edges = edges[:80]

        return KnowledgeGraph(nodes=nodes, edges=edges)

    # ═══════════════════════════════════════════
    # 混合知识网络（标签 + 人物）
    # ═══════════════════════════════════════════

    def build_mixed_network(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        min_count: int = 2,
        max_nodes: int = 60,
    ) -> KnowledgeGraph:
        """构建混合知识网络（标签 + 人物 + 标签-人物关联）。

        Args:
            start_date: 开始日期
            end_date: 结束日期
            min_count: 最小出现次数
            max_nodes: 最大节点数

        Returns:
            KnowledgeGraph 混合知识网络
        """
        tag_graph = self.build_tag_network(start_date, end_date, min_count, max_nodes // 2)
        person_graph = self.build_person_network(start_date, end_date, 1, max_nodes // 2)

        # 合并节点
        all_nodes = tag_graph.nodes + person_graph.nodes
        all_edges = tag_graph.edges + person_graph.edges

        # 构建标签-人物关联边
        entries = self._get_entries_in_range(start_date, end_date)
        person_names = self._get_person_names()
        tag_person_cooccurrence: dict[tuple[str, str], int] = defaultdict(int)

        for entry in entries:
            content = (entry.title or "") + " " + entry.content
            tags = [t for t in (entry.tags or []) if t]
            persons = [p for p in person_names if p in content]
            for tag in tags:
                for person in persons:
                    pair = (tag, person)
                    tag_person_cooccurrence[pair] += 1

        # 添加标签-人物边
        tag_ids = {n.id for n in tag_graph.nodes}
        person_ids = {n.id for n in person_graph.nodes}
        for (tag, person), count in tag_person_cooccurrence.items():
            tag_id = f"tag:{tag}"
            person_id = f"person:{person}"
            if tag_id in tag_ids and person_id in person_ids and count >= 1:
                all_edges.append(
                    GraphEdge(
                        source=tag_id,
                        target=person_id,
                        weight=count,
                        type="tag-person",
                    )
                )

        return KnowledgeGraph(nodes=all_nodes, edges=all_edges)

    # ═══════════════════════════════════════════
    # 人物关系分析
    # ═══════════════════════════════════════════

    def analyze_person_relations(
        self,
        person_name: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[PersonRelation]:
        """分析某个人物与其他人物的关系。

        Args:
            person_name: 人物名称
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            人物关系列表
        """
        entries = self._get_entries_in_range(start_date, end_date)
        if not entries:
            return []

        person_names = self._get_person_names()
        relations: dict[str, dict] = {}

        for entry in entries:
            content = (entry.title or "") + " " + entry.content
            if person_name not in content:
                continue
            for other in person_names:
                if other == person_name:
                    continue
                if other in content:
                    if other not in relations:
                        relations[other] = {
                            "count": 0,
                            "first": entry.entry_date,
                            "last": entry.entry_date,
                        }
                    relations[other]["count"] += 1
                    if entry.entry_date < relations[other]["first"]:
                        relations[other]["first"] = entry.entry_date
                    if entry.entry_date > relations[other]["last"]:
                        relations[other]["last"] = entry.entry_date

        # 转换为 PersonRelation 列表
        result = []
        for other, data in relations.items():
            relation_type = self._infer_relation_type(person_name, other, data["count"])
            result.append(
                PersonRelation(
                    person=person_name,
                    related_person=other,
                    relation_type=relation_type,
                    co_occurrence_count=data["count"],
                    first_appeared=data["first"],
                    last_appeared=data["last"],
                )
            )

        result.sort(key=lambda r: r.co_occurrence_count, reverse=True)
        return result

    def _infer_relation_type(self, person1: str, person2: str, co_count: int) -> str:
        """推断人物关系类型。"""
        # 基于关键词的简单推断
        family_keywords = [
            "妈妈",
            "爸爸",
            "老公",
            "老婆",
            "儿子",
            "女儿",
            "宝宝",
            "乐乐",
            "家人",
            "父母",
        ]
        partner_keywords = ["艳艳", "老公", "老婆", "男朋友", "女朋友", "爱人"]
        friend_keywords = ["朋友", "闺蜜", "哥们", "兄弟"]
        colleague_keywords = ["同事", "老板", "领导", "客户"]

        combined = person1 + person2
        if any(kw in combined for kw in partner_keywords):
            return "partner"
        if any(kw in combined for kw in family_keywords):
            return "family"
        if any(kw in combined for kw in friend_keywords):
            return "friend"
        if any(kw in combined for kw in colleague_keywords):
            return "colleague"
        # 基于共现次数推断
        if co_count >= 20:
            return "family"
        if co_count >= 10:
            return "friend"
        return "other"

    # ═══════════════════════════════════════════
    # 知识节点详情
    # ═══════════════════════════════════════════

    def get_node_detail(
        self,
        node_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 20,
    ) -> KnowledgeNodeDetail | None:
        """获取知识节点详情。

        Args:
            node_id: 节点 ID（格式：tag:xxx 或 person:xxx）
            start_date: 开始日期
            end_date: 结束日期
            limit: 相关日记数量上限

        Returns:
            KnowledgeNodeDetail 节点详情，节点不存在返回 None
        """
        if ":" not in node_id:
            return None

        node_type, node_label = node_id.split(":", 1)
        entries = self._get_entries_in_range(start_date, end_date)
        if not entries:
            return None

        # 筛选相关日记
        related_entries = []
        for entry in entries:
            content = (entry.title or "") + " " + entry.content
            if node_type == "tag" and (
                node_label in (entry.tags or []) or node_label in content
            ) or node_type == "person" and node_label in content:
                related_entries.append(entry)

        if not related_entries:
            return None

        # 按日期排序
        related_entries.sort(key=lambda e: e.entry_date, reverse=True)

        # 构建相关日记列表
        related_entries_data = [
            {
                "id": e.id,
                "date": e.entry_date,
                "title": e.title or e.content[:50],
                "content_preview": e.content[:200] + ("..." if len(e.content) > 200 else ""),
                "mood": e.mood.value if e.mood else "unknown",
                "tags": e.tags or [],
            }
            for e in related_entries[:limit]
        ]

        # 统计相关节点
        related_nodes: Counter[str] = Counter()
        for entry in related_entries:
            for tag in entry.tags or []:
                if tag != node_label:
                    related_nodes[f"tag:{tag}"] += 1

        related_nodes_data = [
            {"id": nid, "label": nid.split(":", 1)[1], "count": count}
            for nid, count in related_nodes.most_common(10)
        ]

        # 时间线（按月统计）
        timeline: dict[str, int] = defaultdict(int)
        for entry in related_entries:
            month = entry.entry_date[:7]
            timeline[month] += 1

        timeline_data = [
            {"month": month, "count": count} for month, count in sorted(timeline.items())
        ]

        return KnowledgeNodeDetail(
            node_id=node_id,
            node_label=node_label,
            node_type=node_type,
            total_appearances=len(related_entries),
            related_entries=related_entries_data,
            related_nodes=related_nodes_data,
            timeline=timeline_data,
        )

    # ═══════════════════════════════════════════
    # 知识网络统计
    # ═══════════════════════════════════════════

    def get_network_stats(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """获取知识网络统计信息。

        Returns:
            统计信息字典
        """
        entries = self._get_entries_in_range(start_date, end_date)
        if not entries:
            return {
                "total_entries": 0,
                "total_tags": 0,
                "total_persons": 0,
                "tag_relations": 0,
                "person_relations": 0,
                "avg_tags_per_entry": 0,
                "top_tags": [],
                "top_persons": [],
            }

        # 统计标签
        all_tags: Counter[str] = Counter()
        for entry in entries:
            for tag in entry.tags or []:
                if tag:
                    all_tags[tag] += 1

        # 统计人物
        person_names = self._get_person_names()
        person_counter: Counter[str] = Counter()
        for entry in entries:
            content = (entry.title or "") + " " + entry.content
            for person in person_names:
                if person in content:
                    person_counter[person] += 1

        # 统计标签共现关系数
        tag_relations = 0
        for entry in entries:
            tags = [t for t in (entry.tags or []) if t]
            tag_relations += len(tags) * (len(tags) - 1) // 2

        # 统计人物共现关系数
        person_relations = 0
        for entry in entries:
            content = (entry.title or "") + " " + entry.content
            persons = [p for p in person_names if p in content]
            person_relations += len(persons) * (len(persons) - 1) // 2

        return {
            "total_entries": len(entries),
            "total_tags": len(all_tags),
            "total_persons": len(person_counter),
            "tag_relations": tag_relations,
            "person_relations": person_relations,
            "avg_tags_per_entry": round(sum(len(e.tags or []) for e in entries) / len(entries), 2),
            "top_tags": [{"tag": tag, "count": count} for tag, count in all_tags.most_common(10)],
            "top_persons": [
                {"person": person, "count": count}
                for person, count in person_counter.most_common(10)
            ],
        }

    # ═══════════════════════════════════════════
    # 内部辅助方法
    # ═══════════════════════════════════════════

    def _get_entries_in_range(
        self, start_date: str | None, end_date: str | None
    ) -> list[DiaryEntry]:
        """获取指定日期范围内的日记。"""
        all_entries = self.store.list_entries(limit=5000)
        if start_date and end_date:
            return [e for e in all_entries if start_date <= e.entry_date <= end_date]
        if start_date:
            return [e for e in all_entries if e.entry_date >= start_date]
        if end_date:
            return [e for e in all_entries if e.entry_date <= end_date]
        return all_entries

    def _get_person_names(self) -> list[str]:
        """获取人物名称列表（从人物表 + 常见人物名）。"""
        try:
            persons = self.store.list_persons(limit=100)
            names = [p.name for p in persons]
        except Exception:
            names = []

        # 添加常见人物名（从用户数据中观察到的）
        common_names = [
            "艳艳",
            "乐乐",
            "妈妈",
            "爸爸",
            "老公",
            "老婆",
            "狄胖胖",
            "童先海",
            "周英",
        ]
        for name in common_names:
            if name not in names:
                names.append(name)

        return names

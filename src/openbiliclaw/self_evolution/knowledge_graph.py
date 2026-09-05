"""Personal knowledge graph module.

Extracts entities and relationships from the user's content library
and builds a queryable knowledge graph.  Provides:
- Entity extraction (people, organizations, concepts, technologies)
- Relationship extraction (related-to, mentions, part-of)
- Graph storage and querying
- Entity popularity and centrality ranking
- Subgraph extraction for visualization
- Entity co-occurrence analysis
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from openbiliclaw.self_evolution.insight_report import extract_topics

logger = logging.getLogger("self_evolution.knowledge_graph")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Entity:
    """A node in the knowledge graph."""

    entity_id: str
    name: str
    entity_type: str  # "person" | "org" | "concept" | "tech" | "product" | "topic"
    description: str = ""
    mention_count: int = 0
    first_seen: str = ""
    last_seen: str = ""
    aliases: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "entity_type": self.entity_type,
            "description": self.description,
            "mention_count": self.mention_count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "aliases": self.aliases,
            "attributes": self.attributes,
        }


@dataclass
class Relationship:
    """An edge in the knowledge graph."""

    source_id: str
    target_id: str
    relation_type: str  # "related" | "mentions" | "part_of" | "causes" | "uses"
    weight: float = 1.0
    evidence_count: int = 0
    first_seen: str = ""
    last_seen: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation_type": self.relation_type,
            "weight": self.weight,
            "evidence_count": self.evidence_count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


@dataclass
class KnowledgeGraph:
    """A complete knowledge graph."""

    entities: dict[str, Entity] = field(default_factory=dict)
    relationships: list[Relationship] = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": [e.to_dict() for e in self.entities.values()],
            "relationships": [r.to_dict() for r in self.relationships],
            "generated_at": self.generated_at,
            "stats": {
                "entity_count": len(self.entities),
                "relationship_count": len(self.relationships),
            },
        }

    def get_subgraph(self, entity_id: str, *, depth: int = 2, max_nodes: int = 50) -> dict[str, Any]:
        """Get a subgraph around a specific entity."""
        if entity_id not in self.entities:
            return {"entities": [], "relationships": [], "error": "Entity not found"}

        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(entity_id, 0)]
        sub_entities: list[Entity] = []
        sub_relationships: list[Relationship] = []

        while queue and len(sub_entities) < max_nodes:
            current, d = queue.pop(0)
            if current in visited or d > depth:
                continue
            visited.add(current)

            if current in self.entities:
                sub_entities.append(self.entities[current])

            # Find all relationships involving this entity
            for rel in self.relationships:
                if rel.source_id == current and rel.target_id not in visited:
                    sub_relationships.append(rel)
                    queue.append((rel.target_id, d + 1))
                elif rel.target_id == current and rel.source_id not in visited:
                    sub_relationships.append(rel)
                    queue.append((rel.source_id, d + 1))

        return {
            "center": entity_id,
            "depth": depth,
            "entities": [e.to_dict() for e in sub_entities],
            "relationships": [r.to_dict() for r in sub_relationships],
        }

    def get_top_entities(self, *, limit: int = 20) -> list[Entity]:
        """Get the most mentioned entities."""
        return sorted(self.entities.values(), key=lambda e: -e.mention_count)[:limit]

    def get_related_entities(self, entity_id: str, *, limit: int = 10) -> list[tuple[Entity, float]]:
        """Get entities most related to a given entity."""
        related: dict[str, float] = defaultdict(float)

        for rel in self.relationships:
            if rel.source_id == entity_id:
                related[rel.target_id] += rel.weight
            elif rel.target_id == entity_id:
                related[rel.source_id] += rel.weight

        result = []
        for eid, weight in sorted(related.items(), key=lambda x: -x[1])[:limit]:
            if eid in self.entities:
                result.append((self.entities[eid], weight))

        return result


# ---------------------------------------------------------------------------
# Graph Builder
# ---------------------------------------------------------------------------


class KnowledgeGraphBuilder:
    """Build a personal knowledge graph from content.

    Args:
        db_path: Path to the SQLite database.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _get_conn(self) -> Any:
        import sqlite3

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def build(
        self,
        *,
        limit: int = 1000,
        min_mentions: int = 2,
        entity_types: list[str] | None = None,
    ) -> KnowledgeGraph:
        """Build a knowledge graph from the content library.

        Args:
            limit: Maximum articles to process.
            min_mentions: Minimum mentions for an entity to be included.
            entity_types: Types of entities to include (default: all).

        Returns:
            A KnowledgeGraph with entities and relationships.
        """
        if entity_types is None:
            entity_types = ["person", "org", "concept", "tech", "product", "topic"]

        graph = KnowledgeGraph(generated_at=datetime.now().isoformat())

        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                SELECT id, title, url, source_type, tags, content_text, ai_summary, author, created_at
                FROM articles
                WHERE content_text IS NOT NULL AND length(content_text) > 100
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

            # Entity co-occurrence matrix
            co_occurrence: dict[tuple[str, str], int] = defaultdict(int)
            article_entities: list[list[str]] = []

            for row in rows:
                text = " ".join(
                    filter(None, [
                        row["title"] or "",
                        row["tags"] or "",
                        row["ai_summary"] or "",
                        (row["content_text"] or "")[:1000],
                        row["author"] or "",
                    ])
                )

                # Extract entities (simplified: use topic extraction + known entity patterns)
                entities = self._extract_entities(text, entity_types)

                # Update entity mentions
                for entity_name, entity_type in entities:
                    eid = self._entity_id(entity_name)
                    if eid not in graph.entities:
                        graph.entities[eid] = Entity(
                            entity_id=eid,
                            name=entity_name,
                            entity_type=entity_type,
                            first_seen=row["created_at"] or "",
                            last_seen=row["created_at"] or "",
                        )
                    entity = graph.entities[eid]
                    entity.mention_count += 1
                    if row["created_at"]:
                        if not entity.first_seen or row["created_at"] < entity.first_seen:
                            entity.first_seen = row["created_at"]
                        if not entity.last_seen or row["created_at"] > entity.last_seen:
                            entity.last_seen = row["created_at"]

                # Track co-occurrences within this article
                entity_ids = list(set([self._entity_id(name) for name, _ in entities]))
                article_entities.append(entity_ids)

                for i, e1 in enumerate(entity_ids):
                    for e2 in entity_ids[i + 1:]:
                        key = tuple(sorted([e1, e2]))
                        co_occurrence[key] += 1

            # Build relationships from co-occurrences
            for (e1, e2), count in co_occurrence.items():
                if count >= min_mentions and e1 in graph.entities and e2 in graph.entities:
                    graph.relationships.append(
                        Relationship(
                            source_id=e1,
                            target_id=e2,
                            relation_type="related",
                            weight=min(count / 5.0, 1.0),
                            evidence_count=count,
                        )
                    )

            # Filter out entities with too few mentions
            if min_mentions > 1:
                graph.entities = {
                    eid: e for eid, e in graph.entities.items()
                    if e.mention_count >= min_mentions
                }
                # Also filter relationships
                graph.relationships = [
                    r for r in graph.relationships
                    if r.source_id in graph.entities and r.target_id in graph.entities
                ]

        finally:
            conn.close()

        return graph

    def _extract_entities(
        self, text: str, entity_types: list[str]
    ) -> list[tuple[str, str]]:
        """Extract entities from text.

        Simplified extraction using:
        1. Known topic list (from insight_report)
        2. Capitalized terms (organizations, products)
        3. Author names (if provided)

        Returns:
            List of (entity_name, entity_type) tuples.
        """
        entities: list[tuple[str, str]] = []
        seen: set[str] = set()

        # 1. Extract topics (concepts)
        if "concept" in entity_types or "topic" in entity_types:
            topics = extract_topics(text, top_k=5)
            for topic in topics:
                if topic.lower() not in seen:
                    seen.add(topic.lower())
                    entities.append((topic, "concept"))

        # 2. Extract capitalized English terms (orgs, products, tech)
        if any(t in entity_types for t in ["org", "product", "tech"]):
            import re
            # Acronyms and CamelCase terms
            for match in re.findall(r"\b[A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)*\b", text):
                if len(match) >= 2 and match.lower() not in seen:
                    # Heuristic: all caps = tech/acronym, CamelCase = product/org
                    if match.isupper():
                        etype = "tech"
                    elif match[0].isupper() and any(c.islower() for c in match):
                        etype = "product"
                    else:
                        etype = "org"
                    if etype in entity_types:
                        seen.add(match.lower())
                        entities.append((match, etype))

        return entities[:10]  # Limit per article

    def _entity_id(self, name: str) -> str:
        """Generate a stable entity ID from name."""
        import hashlib
        return "ent-" + hashlib.md5(name.lower().encode()).hexdigest()[:12]

    def save_graph(self, graph: KnowledgeGraph) -> None:
        """Save graph to database."""
        import json as json_mod

        conn = self._get_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_graph (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    generated_at TEXT,
                    graph_json TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO knowledge_graph (generated_at, graph_json) VALUES (?, ?)",
                (graph.generated_at, json_mod.dumps(graph.to_dict(), ensure_ascii=False)),
            )
            conn.commit()
        finally:
            conn.close()

    def load_latest_graph(self) -> KnowledgeGraph | None:
        """Load the most recent saved graph."""
        import json as json_mod

        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT graph_json FROM knowledge_graph ORDER BY generated_at DESC LIMIT 1"
            ).fetchone()

            if not row:
                return None

            data = json_mod.loads(row["graph_json"])
            graph = KnowledgeGraph(generated_at=data.get("generated_at", ""))

            for e_data in data.get("entities", []):
                entity = Entity(
                    entity_id=e_data["entity_id"],
                    name=e_data["name"],
                    entity_type=e_data["entity_type"],
                    description=e_data.get("description", ""),
                    mention_count=e_data.get("mention_count", 0),
                    first_seen=e_data.get("first_seen", ""),
                    last_seen=e_data.get("last_seen", ""),
                    aliases=e_data.get("aliases", []),
                    attributes=e_data.get("attributes", {}),
                )
                graph.entities[entity.entity_id] = entity

            for r_data in data.get("relationships", []):
                graph.relationships.append(
                    Relationship(
                        source_id=r_data["source_id"],
                        target_id=r_data["target_id"],
                        relation_type=r_data["relation_type"],
                        weight=r_data.get("weight", 1.0),
                        evidence_count=r_data.get("evidence_count", 0),
                        first_seen=r_data.get("first_seen", ""),
                        last_seen=r_data.get("last_seen", ""),
                    )
                )

            return graph

        finally:
            conn.close()

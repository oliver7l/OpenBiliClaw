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
from openbiliclaw.storage.database import open_db_conn

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from openbiliclaw.self_evolution.insight_report import extract_topics

logger = logging.getLogger("self_evolution.knowledge_graph")


# ---------------------------------------------------------------------------
# Entity canonicalization helpers
# ---------------------------------------------------------------------------

# Common alias mappings (lowercase normalized form -> canonical name)
_COMMON_ALIASES: dict[str, str] = {
    "js": "JavaScript",
    "javascript": "JavaScript",
    "ai": "人工智能",
    "人工智能": "人工智能",
    "llm": "大语言模型",
    "大语言模型": "大语言模型",
    "大模型": "大语言模型",
    "ml": "机器学习",
    "机器学习": "机器学习",
    "dl": "深度学习",
    "深度学习": "深度学习",
    "nlp": "自然语言处理",
    "自然语言处理": "自然语言处理",
    "cv": "计算机视觉",
    "计算机视觉": "计算机视觉",
    "rl": "强化学习",
    "强化学习": "强化学习",
    "rag": "检索增强生成",
    "检索增强生成": "检索增强生成",
    "sql": "SQL",
    "python": "Python",
    "java": "Java",
    "cpp": "C++",
    "c++": "C++",
    "golang": "Go",
    "go": "Go",
    "rust": "Rust",
    "typescript": "TypeScript",
    "ts": "TypeScript",
    "html": "HTML",
    "css": "CSS",
    "api": "API",
    "cli": "CLI",
    "ui": "UI",
    "ux": "UX",
    "db": "数据库",
    "数据库": "数据库",
    "ctr": "CTR",
    "cvr": "CVR",
    "roi": "ROI",
    "roas": "ROAS",
    "dsp": "DSP",
    "rtb": "RTB",
    "ssp": "SSP",
    "dmp": "DMP",
    "cdn": "CDN",
    "tcp": "TCP",
    "ip": "IP",
    "http": "HTTP",
    "https": "HTTPS",
    "json": "JSON",
    "xml": "XML",
    "yaml": "YAML",
    "git": "Git",
    "docker": "Docker",
    "k8s": "Kubernetes",
    "kubernetes": "Kubernetes",
    "aws": "AWS",
    "gcp": "GCP",
    "azure": "Azure",
    "v2ex": "V2EX",
    "b站": "B站",
    "bilibili": "B站",
    "知乎": "知乎",
    "小红书": "小红书",
    "抖音": "抖音",
    "youtube": "YouTube",
    "github": "GitHub",
    "chatgpt": "ChatGPT",
    "gpt": "GPT",
    "claude": "Claude",
    "gemini": "Gemini",
    "deepseek": "DeepSeek",
    "qwen": "通义千问",
    "通义千问": "通义千问",
    "doubao": "豆包",
    "豆包": "豆包",
    "百度": "百度",
    "阿里": "阿里巴巴",
    "阿里巴巴": "阿里巴巴",
    "腾讯": "腾讯",
    "字节": "字节跳动",
    "字节跳动": "字节跳动",
    "美团": "美团",
    "京东": "京东",
    "拼多多": "拼多多",
    "华为": "华为",
    "小米": "小米",
    "苹果": "Apple",
    "apple": "Apple",
    "谷歌": "Google",
    "google": "Google",
    "微软": "Microsoft",
    "microsoft": "Microsoft",
    "meta": "Meta",
    "facebook": "Meta",
    "亚马逊": "Amazon",
    "amazon": "Amazon",
    "netflix": "Netflix",
    "特斯拉": "Tesla",
    "tesla": "Tesla",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "huggingface": "Hugging Face",
    "hugging face": "Hugging Face",
    "pytorch": "PyTorch",
    "tensorflow": "TensorFlow",
    "pandas": "Pandas",
    "numpy": "NumPy",
    "scikit-learn": "scikit-learn",
    "sklearn": "scikit-learn",
    "fastapi": "FastAPI",
    "flask": "Flask",
    "django": "Django",
    "react": "React",
    "vue": "Vue",
    "angular": "Angular",
    "nextjs": "Next.js",
    "next.js": "Next.js",
    "nuxt": "Nuxt",
    "svelte": "Svelte",
    "tailwind": "Tailwind CSS",
    "tailwindcss": "Tailwind CSS",
    "bootstrap": "Bootstrap",
    "vite": "Vite",
    "webpack": "Webpack",
    "nodejs": "Node.js",
    "node.js": "Node.js",
    "deno": "Deno",
    "bun": "Bun",
    "redis": "Redis",
    "mongodb": "MongoDB",
    "postgresql": "PostgreSQL",
    "postgres": "PostgreSQL",
    "mysql": "MySQL",
    "sqlite": "SQLite",
    "elasticsearch": "Elasticsearch",
    "kafka": "Kafka",
    "rabbitmq": "RabbitMQ",
    "nginx": "Nginx",
    "linux": "Linux",
    "unix": "Unix",
    "macos": "macOS",
    "windows": "Windows",
    "android": "Android",
    "ios": "iOS",
    "鸿蒙": "HarmonyOS",
    "harmonyos": "HarmonyOS",
}


def _normalize_entity_name(name: str) -> str:
    """Normalize entity name for fuzzy matching.

    Strips punctuation, collapses whitespace, lowercases.
    Used for Tier-2 matching in canonicalization.
    """
    import re

    s = name.lower().strip()
    s = re.sub(r"[^\w\s]", "", s)  # strip punctuation
    s = re.sub(r"\s+", " ", s)  # collapse whitespace
    return s


def _canonicalize_name(name: str) -> str:
    """Return the canonical name for an entity, handling common aliases.

    Three-tier matching:
    1. Exact match in _COMMON_ALIASES (case-insensitive)
    2. Normalized name match in _COMMON_ALIASES
    3. Return original name (capitalized first letter)

    Returns the canonical display name.
    """
    # Tier 1: exact case-insensitive match
    lower = name.lower().strip()
    if lower in _COMMON_ALIASES:
        return _COMMON_ALIASES[lower]

    # Tier 2: normalized match
    normalized = _normalize_entity_name(name)
    if normalized in _COMMON_ALIASES:
        return _COMMON_ALIASES[normalized]

    # Tier 3: return original with first letter capitalized
    if name and name[0].islower() and len(name) > 1:
        return name[0].upper() + name[1:]
    return name


def _merge_descriptions(existing: str, new: str, max_length: int = 1000) -> str:
    """Merge two entity descriptions, avoiding duplicates.

    If the new description adds meaningful info, append it.
    Returns the merged description, truncated to max_length.
    """
    if not new:
        return existing or ""
    if not existing:
        return new[:max_length]

    # If new description is a substring of existing (case-insensitive), skip
    if new.lower().strip() in existing.lower():
        return existing

    # Append with separator, truncate
    merged = f"{existing.rstrip('.')}. {new.strip()}"
    return merged[:max_length]


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

    def get_subgraph(
        self, entity_id: str, *, depth: int = 2, max_nodes: int = 50
    ) -> dict[str, Any]:
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

    def get_related_entities(
        self, entity_id: str, *, limit: int = 10
    ) -> list[tuple[Entity, float]]:
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
        from contextlib import suppress as _suppress
        from pathlib import Path as _Path

        conn = open_db_conn(self.db_path)
        conn.row_factory = sqlite3.Row
        # v0.4.0+: articles 表迁移到 content.db，主库连接需 ATTACH content，
        # 否则裸 FROM articles 报 no such table: articles。
        _content_path = _Path(str(self.db_path)).with_name("content.db")
        if _content_path.exists():
            with _suppress(Exception):
                conn.execute("ATTACH DATABASE ? AS content", (str(_content_path),))
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
                SELECT id, title, url, source_type, tags, content_text,
                       ai_summary, author, created_at
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
                    filter(
                        None,
                        [
                            row["title"] or "",
                            row["tags"] or "",
                            row["ai_summary"] or "",
                            (row["content_text"] or "")[:1000],
                            row["author"] or "",
                        ],
                    )
                )

                # Extract entities (simplified: use topic extraction + known entity patterns)
                entities = self._extract_entities(text, entity_types)

                # Update entity mentions (with canonicalization)
                for entity_name, entity_type in entities:
                    # Canonicalize: handle aliases like "JS" -> "JavaScript"
                    canonical_name = _canonicalize_name(entity_name)
                    eid = self._entity_id(canonical_name)

                    if eid not in graph.entities:
                        graph.entities[eid] = Entity(
                            entity_id=eid,
                            name=canonical_name,
                            entity_type=entity_type,
                            first_seen=row["created_at"] or "",
                            last_seen=row["created_at"] or "",
                            aliases=[entity_name] if entity_name != canonical_name else [],
                        )
                    entity = graph.entities[eid]
                    entity.mention_count += 1

                    # Track alias if it's a new surface form
                    if entity_name != canonical_name and entity_name not in entity.aliases:
                        entity.aliases.append(entity_name)

                    if row["created_at"]:
                        if not entity.first_seen or row["created_at"] < entity.first_seen:
                            entity.first_seen = row["created_at"]
                        if not entity.last_seen or row["created_at"] > entity.last_seen:
                            entity.last_seen = row["created_at"]

                # Track co-occurrences within this article (use canonical IDs)
                entity_ids = list(
                    set([self._entity_id(_canonicalize_name(name)) for name, _ in entities])
                )
                article_entities.append(entity_ids)

                for i, e1 in enumerate(entity_ids):
                    for e2 in entity_ids[i + 1 :]:
                        a, b = sorted([e1, e2])
                        co_occurrence[(a, b)] += 1

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
                    eid: e for eid, e in graph.entities.items() if e.mention_count >= min_mentions
                }
                # Also filter relationships
                graph.relationships = [
                    r
                    for r in graph.relationships
                    if r.source_id in graph.entities and r.target_id in graph.entities
                ]

        finally:
            conn.close()

        return graph

    def _extract_entities(self, text: str, entity_types: list[str]) -> list[tuple[str, str]]:
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
                CREATE TABLE IF NOT EXISTS knowledge.knowledge_graph (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    generated_at TEXT,
                    graph_json TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO knowledge.knowledge_graph (generated_at, graph_json) VALUES (?, ?)",
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
                "SELECT graph_json FROM knowledge.knowledge_graph ORDER BY generated_at DESC LIMIT 1"
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

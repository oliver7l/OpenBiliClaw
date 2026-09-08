"""Knowledge Forge API 路由测试。

使用临时 SQLite 库 + 临时 config，验证 §4 端点的查询与触发逻辑。
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolate_runtime_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """隔离本地配置，避免读到开发机真实 config.toml。"""
    from openbiliclaw.config import Config, save_config

    project_root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    cfg = Config()
    cfg.llm.default_provider = "ollama"
    cfg.llm.ollama.model = "llama3"
    save_config(cfg, project_root / "config.toml")


def _make_kf_db(tmp_path: Path) -> Path:
    """建一个带 KF 表结构与少量数据的临时库。"""
    db = tmp_path / "kf_test.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY, source_type TEXT, title TEXT, url TEXT,
            author TEXT, tags TEXT, content_text TEXT, content_cleaned TEXT,
            summary_detailed TEXT, summary_compact TEXT, summary_ultra_compact TEXT,
            summary_quality REAL, summary_version INTEGER, summary_generated_at TEXT,
            ai_summary TEXT, published_at TEXT, created_at TEXT
        );
        CREATE TABLE entities (
            id INTEGER PRIMARY KEY, name TEXT UNIQUE, type TEXT, description TEXT,
            article_count INTEGER DEFAULT 0, first_seen_at TEXT, last_updated_at TEXT, metadata TEXT
        );
        CREATE TABLE article_entities (
            article_id INTEGER, entity_id INTEGER, relevance REAL, context TEXT,
            PRIMARY KEY (article_id, entity_id)
        );
        CREATE TABLE article_relations (
            article_id_a INTEGER, article_id_b INTEGER, relation_type TEXT,
            confidence REAL, description TEXT, created_at TEXT,
            PRIMARY KEY (article_id_a, article_id_b, relation_type)
        );
        CREATE TABLE audit_tasks (
            id INTEGER PRIMARY KEY, task_type TEXT, status TEXT, started_at TEXT,
            completed_at TEXT, total_articles INTEGER, issues_found INTEGER,
            report_path TEXT, created_at TEXT
        );
        CREATE TABLE audit_issues (
            id INTEGER PRIMARY KEY, article_id INTEGER, issue_type TEXT, severity TEXT,
            description TEXT, details TEXT, status TEXT, fix_suggestion TEXT,
            fixed_at TEXT, created_at TEXT
        );
        CREATE TABLE article_quality_scores (
            article_id INTEGER PRIMARY KEY, overall_score REAL, completeness_score REAL,
            content_score REAL, link_score REAL, uniqueness_score REAL, last_audited_at TEXT
        );
        CREATE TABLE gap_analysis_tasks (
            id INTEGER PRIMARY KEY, status TEXT, started_at TEXT, completed_at TEXT,
            report_path TEXT, total_topics INTEGER, gaps_found INTEGER, created_at TEXT
        );
        CREATE TABLE gap_records (
            id INTEGER PRIMARY KEY, task_id INTEGER, gap_type TEXT, entity_id INTEGER,
            severity TEXT, description TEXT, current_count INTEGER, suggested_count INTEGER,
            suggestion TEXT, status TEXT, created_at TEXT
        );
        """
    )
    conn.executemany(
        """INSERT INTO articles (id, source_type, title, url, author, tags, content_text,
                                 summary_detailed, summary_compact, summary_ultra_compact,
                                 summary_quality)
           VALUES (?, ?, ?, ?, '作者A', '["AI"]', '正文内容足够长。' * 50,
                   '详细摘要内容', '精简摘要', '极简摘要', 0.85)""",
        [(1, "zhihu", "推荐系统双塔", "http://a/1"), (2, "zhihu", "推荐系统粗排", "http://a/2")],
    )
    conn.executemany(
        "INSERT INTO entities (id, name, type, article_count) VALUES (?, ?, ?, ?)",
        [
            (1, "推荐系统", "topic", 2),
            (2, "作者A", "author", 2),
            (3, "双塔模型", "concept", 1),
        ],
    )
    conn.executemany(
        "INSERT INTO article_entities (article_id, entity_id, relevance) VALUES (?, ?, ?)",
        [(1, 1, 0.9), (2, 1, 0.8), (1, 3, 0.7)],
    )
    conn.execute(
        "INSERT INTO article_relations (article_id_a, article_id_b, relation_type,"
        " confidence, description) VALUES (1, 2, 'same_topic', 0.7, '共享主题')"
    )
    conn.execute(
        "INSERT INTO audit_tasks (task_type, status, started_at, created_at) "
        "VALUES ('full_audit', 'completed', '2026-09-08 10:00:00', '2026-09-08 10:00:00')"
    )
    conn.execute(
        "INSERT INTO audit_issues (article_id, issue_type, severity, description,"
        " status, created_at) VALUES (1, 'missing_tags', 'low', '缺标签', 'open',"
        " '2026-09-08 10:00:00')"
    )
    conn.execute("INSERT INTO article_quality_scores (article_id, overall_score) VALUES (1, 85.0)")
    conn.execute(
        "INSERT INTO gap_records (gap_type, severity, description, status, created_at) "
        "VALUES ('topic_coverage', 'high', '主题覆盖不足', 'open', '2026-09-08 10:00:00')"
    )
    conn.commit()
    conn.close()
    return db


class TestKnowledgeForgeApi:
    def _client(self, tmp_path: Path, db: Path) -> TestClient:
        """起一个带指定 KF 库的 app（database 注入可解析 db_path 的假对象）。"""
        from openbiliclaw.api.app import create_app

        class FakeDatabase:
            db_path = str(db)

        app = create_app(memory_manager=object(), database=FakeDatabase(), soul_engine=object())
        return TestClient(app)

    def test_summary_get(self, tmp_path: Path) -> None:
        db = _make_kf_db(tmp_path)
        client = self._client(tmp_path, db)
        r = client.get("/api/articles/1/summary?level=compact")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] and data["summary"] == "精简摘要"
        assert data["quality"] == 0.85

    def test_summary_get_invalid_level(self, tmp_path: Path) -> None:
        db = _make_kf_db(tmp_path)
        client = self._client(tmp_path, db)
        r = client.get("/api/articles/1/summary?level=bad")
        assert r.status_code == 400

    def test_summary_get_not_found(self, tmp_path: Path) -> None:
        db = _make_kf_db(tmp_path)
        client = self._client(tmp_path, db)
        r = client.get("/api/articles/999/summary")
        assert r.status_code == 404

    def test_entities_list(self, tmp_path: Path) -> None:
        db = _make_kf_db(tmp_path)
        client = self._client(tmp_path, db)
        r = client.get("/api/entities?type=topic")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1 and items[0]["name"] == "推荐系统"

    def test_entity_articles(self, tmp_path: Path) -> None:
        db = _make_kf_db(tmp_path)
        client = self._client(tmp_path, db)
        r = client.get("/api/entities/1/articles")
        assert r.status_code == 200
        assert r.json()["total"] == 2

    def test_authors_topics_concepts(self, tmp_path: Path) -> None:
        db = _make_kf_db(tmp_path)
        client = self._client(tmp_path, db)
        # 作者/主题/概念统一通过 /api/entities?type= 查询（设计文档 4.2 主入口）
        assert client.get("/api/entities?type=author").json()["total"] == 1
        assert client.get("/api/entities?type=topic").json()["total"] == 1
        assert client.get("/api/entities?type=concept").json()["total"] == 1

    def test_article_related(self, tmp_path: Path) -> None:
        db = _make_kf_db(tmp_path)
        client = self._client(tmp_path, db)
        r = client.get("/api/articles/1/related")
        assert r.status_code == 200
        items = r.json()["items"]
        assert any(i["article_id"] == 2 for i in items)

    def test_article_contradictions_empty(self, tmp_path: Path) -> None:
        db = _make_kf_db(tmp_path)
        client = self._client(tmp_path, db)
        r = client.get("/api/articles/1/contradictions")
        assert r.status_code == 200 and r.json()["items"] == []

    def test_knowledge_graph(self, tmp_path: Path) -> None:
        db = _make_kf_db(tmp_path)
        client = self._client(tmp_path, db)
        r = client.get("/api/knowledge-graph")
        assert r.status_code == 200
        data = r.json()
        assert len(data["nodes"]) == 3 and len(data["edges"]) == 3

    def test_gap_records(self, tmp_path: Path) -> None:
        db = _make_kf_db(tmp_path)
        client = self._client(tmp_path, db)
        r = client.get("/api/gap-records?severity=high")
        assert r.status_code == 200
        assert r.json()["total"] == 1
        # resolve
        r2 = client.post("/api/gap-records/1/resolve")
        assert r2.status_code == 200
        conn = sqlite3.connect(db)
        status = conn.execute("SELECT status FROM gap_records WHERE id=1").fetchone()[0]
        conn.close()
        assert status == "resolved"

    def test_audit_issues_and_fix(self, tmp_path: Path) -> None:
        db = _make_kf_db(tmp_path)
        client = self._client(tmp_path, db)
        r = client.get("/api/audit/issues?issue_type=missing_tags")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1 and items[0]["article_id"] == 1
        r2 = client.post("/api/audit/issues/1/fix")
        assert r2.status_code == 200
        conn = sqlite3.connect(db)
        status = conn.execute("SELECT status FROM audit_issues WHERE id=1").fetchone()[0]
        conn.close()
        assert status == "fixed"

    def test_quality_score(self, tmp_path: Path) -> None:
        db = _make_kf_db(tmp_path)
        client = self._client(tmp_path, db)
        r = client.get("/api/articles/1/quality-score")
        assert r.status_code == 200
        assert r.json()["score"]["overall_score"] == 85.0

    def test_audit_run_requires_confirm(self, tmp_path: Path) -> None:
        db = _make_kf_db(tmp_path)
        client = self._client(tmp_path, db)
        r = client.post("/api/audit/run")
        assert r.status_code == 400  # 无 confirm 拒绝


# --------------------------------------------------------------------------- #
# 3.2 实体页增强 + §5 前端数据端点
# --------------------------------------------------------------------------- #
class TestEntityEnhanceAndPages:
    def test_entity_detail_has_timeline_and_related(self, tmp_path: Path) -> None:
        """实体详情含引用时间线 + 相关实体共现。"""
        db = _make_kf_db(tmp_path)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE articles SET published_at = CASE id WHEN 1 THEN '2026-08-01' "
                "WHEN 2 THEN '2026-09-01' END"
            )
        client = TestKnowledgeForgeApi()._client(tmp_path, db)
        r = client.get("/api/entities/1")
        assert r.status_code == 200
        e = r.json()["entity"]
        assert e["timeline"] == [
            {"month": "2026-08", "count": 1},
            {"month": "2026-09", "count": 1},
        ]
        assert any(x["id"] == 3 and x["co_occur"] == 1 for x in e["related"])

    def test_entity_timeline_months_all(self, tmp_path: Path) -> None:
        """months=0 返回全量时间线；months=2 只返回最近 2 个月。"""
        db = _make_kf_db(tmp_path)
        client = TestKnowledgeForgeApi()._client(tmp_path, db)
        full = client.get("/api/entities/1?months=0").json()["entity"]["timeline"]
        recent = client.get("/api/entities/1?months=2").json()["entity"]["timeline"]
        assert len(full) >= len(recent)
        assert len(recent) <= 2

    def test_contradiction_resolve_and_filter(self, tmp_path: Path) -> None:
        """标记误报后列表不再展示；confirmed 保留展示并带 status。"""
        db = _make_kf_db(tmp_path)
        with sqlite3.connect(db) as conn:
            conn.executemany(
                "INSERT INTO article_relations"
                " (article_id_a, article_id_b, relation_type, confidence)"
                " VALUES (?, ?, 'contradiction', ?)",
                [(1, 2, 0.8), (2, 1, 0.6)],
            )
        client = TestKnowledgeForgeApi()._client(tmp_path, db)
        assert client.get("/api/contradictions").json()["total"] == 2
        # 按 rowid 定位（fixture 预置 same_topic 占 rowid=1；contradiction 为 2/3）
        r = client.post("/api/contradictions/2/resolve?action=false_positive")
        assert r.json()["ok"] and r.json()["status"] == "false_positive"
        r2 = client.post("/api/contradictions/3/resolve?action=confirmed")
        assert r2.json()["ok"] and r2.json()["status"] == "confirmed"
        data = client.get("/api/contradictions").json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "confirmed"
        assert data["items"][0]["relation_id"] == 3

    def test_entity_related_prefers_persisted(self, tmp_path: Path) -> None:
        """entity_relations 持久化行优先于实时共现计算。"""
        db = _make_kf_db(tmp_path)
        with sqlite3.connect(db) as conn:
            conn.executescript(
                """
                CREATE TABLE entity_relations (
                    entity_id_a INTEGER, entity_id_b INTEGER, relation_type TEXT,
                    confidence REAL, description TEXT, co_occur INTEGER,
                    PRIMARY KEY (entity_id_a, entity_id_b, relation_type)
                );
                """
            )
            conn.execute(
                "INSERT INTO entity_relations (entity_id_a, entity_id_b, relation_type,"
                " confidence, description, co_occur) VALUES"
                " (1, 2, 'co_occur', 1.0, '共现 9 篇', 9)"
            )
        client = TestKnowledgeForgeApi()._client(tmp_path, db)
        e = client.get("/api/entities/1").json()["entity"]
        related = e["related"]
        assert any(x["id"] == 2 and x["co_occur"] == 9 for x in related)

    def test_audit_summary_endpoint(self, tmp_path: Path) -> None:
        db = _make_kf_db(tmp_path)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO audit_issues (article_id, issue_type, severity, status, created_at) "
                "VALUES (2, 'duplicate', 'high', 'open', '2026-09-08 10:00:00')"
            )
        client = TestKnowledgeForgeApi()._client(tmp_path, db)
        r = client.get("/api/audit/summary")
        assert r.status_code == 200
        s = r.json()["summary"]
        assert s["low"] == 1 and s["high"] == 1 and s["open"] == 2

    def test_contradictions_list_endpoint(self, tmp_path: Path) -> None:
        db = _make_kf_db(tmp_path)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO article_relations (article_id_a, article_id_b, relation_type, "
                "confidence, description) VALUES (1, 2, 'contradiction', 0.93, '两文观点相反')"
            )
        client = TestKnowledgeForgeApi()._client(tmp_path, db)
        r = client.get("/api/contradictions")
        assert r.status_code == 200
        d = r.json()
        assert d["total"] == 1
        item = d["items"][0]
        assert item["title_a"] == "推荐系统双塔" and item["title_b"] == "推荐系统粗排"
        assert item["confidence"] == 0.93

    def test_frontend_pages_mounted(self, tmp_path: Path) -> None:
        """§5 各页面可访问（静态页 200 + 注入类型）。"""
        db = _make_kf_db(tmp_path)
        client = TestKnowledgeForgeApi()._client(tmp_path, db)
        for path, marker in (
            ("/authors", '__ENTITY_TYPE__="author"'),
            ("/topics", '__ENTITY_TYPE__="topic"'),
            ("/concepts", '__ENTITY_TYPE__="concept"'),
            ("/audit", "质量审计"),
            ("/gap-analysis", "缺口分析"),
            ("/contradictions", "矛盾报告"),
            ("/knowledge-graph", "echarts"),
        ):
            r = client.get(path)
            assert r.status_code == 200, path
            assert marker in r.text, path

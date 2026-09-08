"""Knowledge Forge 新管线测试：低质量检测 / 自动修复 / 批量处理 / 定时任务。

LLM 调用统一用 FakeLlm 注入（monkeypatch utils.get_llm_client）。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest


# --------------------------------------------------------------------------- #
# 工具：临时库 + Fake LLM
# --------------------------------------------------------------------------- #
def _make_db() -> Path:
    d = tempfile.mkdtemp()
    db = Path(d) / "kf.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY, source_type TEXT, source_name TEXT, title TEXT,
            url TEXT, author TEXT, tags TEXT, content_text TEXT, content_cleaned TEXT,
            content_clean_score REAL, content_clean_log TEXT, content_verified INTEGER,
            content_verify_result TEXT, content_hash TEXT, ai_summary TEXT,
            summary_detailed TEXT, summary_compact TEXT, summary_ultra_compact TEXT,
            summary_quality REAL, summary_version INTEGER, summary_generated_at TEXT,
            status TEXT, published_at TEXT, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE audit_issues (
            id INTEGER PRIMARY KEY, article_id INTEGER, issue_type TEXT, severity TEXT,
            description TEXT, details TEXT, status TEXT, fix_suggestion TEXT,
            fixed_at TEXT, created_at TEXT
        );
        CREATE TABLE audit_tasks (
            id INTEGER PRIMARY KEY, task_type TEXT, status TEXT, started_at TEXT,
            completed_at TEXT, total_articles INTEGER, issues_found INTEGER,
            issues_fixed INTEGER, report_path TEXT, details TEXT, created_at TEXT
        );
        CREATE TABLE entities (
            id INTEGER PRIMARY KEY, name TEXT UNIQUE, type TEXT, description TEXT,
            article_count INTEGER DEFAULT 0, first_seen_at TEXT, last_updated_at TEXT, metadata TEXT
        );
        CREATE TABLE article_entities (
            article_id INTEGER, entity_id INTEGER, relevance REAL, context TEXT,
            PRIMARY KEY (article_id, entity_id)
        );
        CREATE TABLE article_quality_scores (
            article_id INTEGER PRIMARY KEY, overall_score REAL, completeness_score REAL,
            content_score REAL, link_score REAL, uniqueness_score REAL, last_audited_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    return db


class FakeLlm:
    """按 system_instruction 关键词分派返回的 Fake LLM。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def complete(self, spec: object, **kw: object) -> SimpleNamespace:
        system = str(kw.get("system_instruction", ""))
        user = str(kw.get("user_input", ""))
        self.calls.append((system, user))
        if "内容质量审核员" in system:
            return SimpleNamespace(content=json.dumps({"low_quality": True, "reason": "广告内容"}))
        if "标签提取助手" in system:
            return SimpleNamespace(content=json.dumps(["AI", "推荐"]))
        if "实体提取" in system or "技术概念" in system:
            return SimpleNamespace(content=json.dumps(["双塔模型", "召回"]))
        if "评估以下三层摘要" in system:
            return SimpleNamespace(content="0.85")
        if "压缩为超精简版" in system:
            return SimpleNamespace(content="一句话超精简摘要")
        if "压缩为精简版" in system:
            return SimpleNamespace(content="精简摘要内容")
        if "详细摘要" in system:
            return SimpleNamespace(content="详细摘要内容，包含核心观点与关键数据。")
        if "主题" in system:
            return SimpleNamespace(content=json.dumps(["推荐系统"]))
        return SimpleNamespace(content="{}")


@pytest.fixture(autouse=True)
def _fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLlm:
    """注入 Fake LLM：同时 patch utils 及所有模块顶部已绑定的 get_llm_client 引用。

    各模块（auto_fixer/entity_extractor/summary_engine/low_quality_detector）在模块
    顶部 ``from openbiliclaw.knowledge_forge.utils import get_llm_client``，monkeypatch
    仅改 utils 属性不会覆盖已绑定引用，需逐一 patch（raising=False 容忍未加载模块）。
    """
    fake = FakeLlm()
    for mod_name in (
        "openbiliclaw.knowledge_forge.utils",
        "openbiliclaw.knowledge_forge.auto_fixer",
        "openbiliclaw.knowledge_forge.entity_extractor",
        "openbiliclaw.knowledge_forge.summary_engine",
        "openbiliclaw.knowledge_forge.low_quality_detector",
        "openbiliclaw.knowledge_forge.contradiction_detector",
    ):
        try:
            mod = __import__(mod_name, fromlist=["get_llm_client"])
            monkeypatch.setattr(mod, "get_llm_client", lambda **kw: fake, raising=False)
        except Exception:  # noqa: BLE001 — 模块未加载时跳过
            pass
    return fake


# --------------------------------------------------------------------------- #
# 低质量内容检测
# --------------------------------------------------------------------------- #
class TestLowQualityDetector:
    def test_fetch_candidates_prefers_suspects(self) -> None:
        """优先从疑似问题文章抽样，不足随机补足。"""
        from openbiliclaw.knowledge_forge.low_quality_detector import LowQualityDetector

        db = _make_db()
        conn = sqlite3.connect(db)
        conn.executemany(
            "INSERT INTO articles (id, title, content_text) VALUES (?, ?, ?)",
            [
                (1, "疑似污染文", "正文内容" * 20),
                (2, "正常文", "正常正文内容" * 30),
                (3, "正常文2", "更多正常正文内容" * 30),
            ],
        )
        conn.execute(
            "INSERT INTO audit_issues (article_id, issue_type, severity, status) "
            "VALUES (1, 'content_contamination', 'high', 'open')"
        )
        conn.commit()
        conn.close()

        d = LowQualityDetector(db_path=db)
        cands = d._fetch_candidates(limit=2, ratio=1.0)
        ids = [c["id"] for c in cands]
        assert 1 in ids  # 疑似优先

    def test_detect_writes_low_quality_issue(self, _fake_llm: FakeLlm) -> None:
        """LLM 判定低质量 → 写入 audit_issues(low_quality)。"""
        from openbiliclaw.knowledge_forge.low_quality_detector import LowQualityDetector

        db = _make_db()
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO articles (id, title, content_text, content_cleaned) "
            "VALUES (1, '广告文', '正文内容' * 20, '正文内容' * 20)"
        )
        conn.commit()
        conn.close()

        import asyncio

        stats = asyncio.run(LowQualityDetector(db_path=db).detect(limit=1))
        assert stats["low_quality"] == 1
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT issue_type, severity, status FROM audit_issues WHERE article_id=1"
        ).fetchone()
        task = conn.execute(
            "SELECT task_type, status FROM audit_tasks WHERE task_type='low_quality_check'"
        ).fetchone()
        conn.close()
        assert row == ("low_quality", "high", "open")
        assert task == ("low_quality_check", "completed")


# --------------------------------------------------------------------------- #
# 自动修复
# --------------------------------------------------------------------------- #
class TestAutoFixer:
    def test_auto_fix_disabled_by_default(self) -> None:
        """未开启时直接返回 disabled。"""
        from openbiliclaw.knowledge_forge.auto_fixer import IssueFixer

        db = _make_db()
        stats = asyncio_run(IssueFixer(db_path=db).fix_all())
        assert stats.get("disabled") is True

    def test_fix_clean_and_hash_and_duplicate(self) -> None:
        """污染重清理 + 补哈希 + 重复标记，issue 全部转 fixed。"""
        from openbiliclaw.knowledge_forge.auto_fixer import IssueFixer

        db = _make_db()
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO articles (id, title, content_text, content_cleaned, content_hash, status) "
            "VALUES (1, 'A', '<p>正文内容</p>', '', '', 'unread')"
        )
        conn.execute(
            "INSERT INTO articles (id, title, content_text, status) "
            "VALUES (2, 'B', '重复正文', 'unread')"
        )
        conn.executemany(
            "INSERT INTO audit_issues (article_id, issue_type, severity, status) VALUES (?,?,?,?)",
            [
                (1, "content_contamination", "high", "open"),
                (1, "missing_hash", "low", "open"),
                (1, "duplicate", "high", "open"),
            ],
        )
        conn.execute(
            "UPDATE audit_issues SET details = ? WHERE issue_type = 'duplicate'",
            (json.dumps({"duplicate_ids": [2]}),),
        )
        conn.commit()
        conn.close()

        stats = asyncio_run(IssueFixer(db_path=db).fix_all(enable=True))
        assert stats["fixed"] == 3

        conn = sqlite3.connect(db)
        art = conn.execute(
            "SELECT content_cleaned, content_hash FROM articles WHERE id=1"
        ).fetchone()
        dup = conn.execute("SELECT status FROM articles WHERE id=2").fetchone()[0]
        fixed = conn.execute("SELECT COUNT(*) FROM audit_issues WHERE status='fixed'").fetchone()[0]
        conn.close()
        assert art[0] and "正文内容" in art[0]  # 已清理（HTML 剥离）
        assert len(art[1]) == 32  # sha256 前 32 位
        assert dup == "duplicate"
        assert fixed == 3

    def test_fix_tags_uses_llm(self, _fake_llm: FakeLlm) -> None:
        """缺失标签经 LLM 补充。"""
        from openbiliclaw.knowledge_forge.auto_fixer import IssueFixer

        db = _make_db()
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO articles (id, title, content_text, tags) VALUES (1, 'T', '正文', '[]')"
        )
        conn.execute(
            "INSERT INTO audit_issues (article_id, issue_type, severity, status) "
            "VALUES (1, 'missing_tags', 'low', 'open')"
        )
        conn.commit()
        conn.close()

        stats = asyncio_run(IssueFixer(db_path=db).fix_all(enable=True))
        assert stats["fixed"] == 1
        conn = sqlite3.connect(db)
        tags = conn.execute("SELECT tags FROM articles WHERE id=1").fetchone()[0]
        conn.close()
        assert "AI" in tags and "推荐" in tags


# --------------------------------------------------------------------------- #
# 历史文章批量处理
# --------------------------------------------------------------------------- #
class TestBatchProcessor:
    def test_backfill_priority_high(self, _fake_llm: FakeLlm) -> None:
        """高价值优先：已清理且缺摘要的长文被选中并补齐。"""
        from openbiliclaw.knowledge_forge.batch_processor import BatchProcessor

        db = _make_db()
        conn = sqlite3.connect(db)
        # 1: 已清理缺摘要（高价值，应被选中）
        conn.execute(
            "INSERT INTO articles (id, title, content_text, content_cleaned, summary_compact) "
            "VALUES (?, ?, ?, ?, ?)",
            (1, "高价值文", "正文" * 300, "清理后正文" * 300, ""),
        )
        # 2: 未清理（priority=high 时跳过）
        conn.execute(
            "INSERT INTO articles (id, title, content_text, content_cleaned) VALUES (?, ?, ?, ?)",
            (2, "未清理文", "短正文", ""),
        )
        conn.commit()
        conn.close()

        stats = asyncio_run(
            BatchProcessor(db_path=db).backfill(limit=5, batch_size=2, priority="high")
        )
        assert stats["processed"] == 1
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT summary_compact, tags, content_cleaned FROM articles WHERE id=1"
        ).fetchone()
        score = conn.execute(
            "SELECT overall_score FROM article_quality_scores WHERE article_id=1"
        ).fetchone()
        entities = conn.execute(
            "SELECT COUNT(*) FROM article_entities WHERE article_id=1"
        ).fetchone()[0]
        task = conn.execute(
            "SELECT status, issues_found FROM audit_tasks WHERE task_type='batch_backfill'"
        ).fetchone()
        conn.close()
        assert row[0]  # 摘要已生成
        assert row[1] and row[1] != "[]"  # 标签已补
        assert score and score[0] > 0  # 质量分已写
        assert entities > 0  # 实体已提取
        assert task == ("completed", 1)

    def test_backfill_all_cleans_uncached(self, _fake_llm: FakeLlm) -> None:
        """priority=all 时未清理文章也被补齐。"""
        from openbiliclaw.knowledge_forge.batch_processor import BatchProcessor

        db = _make_db()
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO articles (id, title, content_text, content_cleaned) "
            "VALUES (1, '未清理文', '<p>正文内容</p>', '')"
        )
        conn.commit()
        conn.close()

        stats = asyncio_run(
            BatchProcessor(db_path=db).backfill(limit=5, batch_size=2, priority="all")
        )
        assert stats["processed"] == 1
        conn = sqlite3.connect(db)
        cleaned = conn.execute("SELECT content_cleaned FROM articles WHERE id=1").fetchone()[0]
        conn.close()
        assert cleaned and "<p>" not in cleaned  # 已清理且剥离 HTML


# --------------------------------------------------------------------------- #
# 定时组合任务
# --------------------------------------------------------------------------- #
class TestScheduler:
    def test_run_scheduled_records_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """组合任务按序执行并落库 scheduled_run。"""
        from openbiliclaw.knowledge_forge import dead_link_checker, gap_analyst, quality_auditor
        from openbiliclaw.knowledge_forge.schedule import Scheduler

        db = _make_db()

        class FakeQualityAuditor:
            def __init__(self, **kw: object) -> None:
                pass

            def run_full_audit(self) -> dict[str, object]:
                return {"total": 100, "issues_found": 5}

        class FakeGapAnalyst:
            def __init__(self, **kw: object) -> None:
                pass

            def run(self) -> dict[str, object]:
                return {"gaps_found": 3, "high": 1}

        class FakeDeadLinkChecker:
            def __init__(self, **kw: object) -> None:
                pass

            async def check(self, **kw: object) -> dict[str, object]:
                return {"checked": 10, "dead": 1}

        monkeypatch.setattr(quality_auditor, "QualityAuditor", FakeQualityAuditor)
        monkeypatch.setattr(gap_analyst, "GapAnalyst", FakeGapAnalyst)
        monkeypatch.setattr(dead_link_checker, "DeadLinkChecker", FakeDeadLinkChecker)

        stats = asyncio_run(Scheduler(db_path=db).run_scheduled(include_dead_link=True))
        assert stats["steps"]["audit"]["issues_found"] == 5
        assert "质量审计" in stats["report"] and "缺口分析" in stats["report"]
        conn = sqlite3.connect(db)
        task = conn.execute(
            "SELECT task_type, status, issues_found FROM audit_tasks "
            "WHERE task_type='scheduled_run'"
        ).fetchone()
        conn.close()
        assert task == ("scheduled_run", "completed", 9)

    def test_build_schedule_guide(self) -> None:
        """生成 crontab 接入说明。"""
        from openbiliclaw.knowledge_forge.schedule import build_schedule_guide

        guide = build_schedule_guide()
        assert "crontab" in guide and "run-scheduled" in guide


def asyncio_run(coro: object) -> object:
    import asyncio

    return asyncio.run(coro)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 自动补充闭环
# --------------------------------------------------------------------------- #
class TestGapFiller:
    def test_parse_topic(self) -> None:
        from openbiliclaw.knowledge_forge.gap_filler import GapFiller

        f = GapFiller(db_path=_make_db())
        assert f._parse_topic({"description": "主题「RL」仅 2 篇，覆盖不足"}) == "RL"
        assert f._parse_topic({"description": "无主题标记"}) == ""

    def test_fetch_open_gaps_filters(self) -> None:
        from openbiliclaw.knowledge_forge.gap_filler import GapFiller

        db = _make_db()
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE gap_records (
                id INTEGER PRIMARY KEY, task_id INTEGER, gap_type TEXT, entity_id INTEGER,
                severity TEXT, description TEXT, current_count INTEGER, suggested_count INTEGER,
                suggestion TEXT, status TEXT, created_at TEXT
            );
            """
        )
        conn.executemany(
            "INSERT INTO gap_records (gap_type, severity, description, current_count, status) "
            "VALUES (?,?,?,?,?)",
            [
                ("topic_coverage", "high", "主题「RL」仅 2 篇，覆盖不足", 2, "open"),
                ("topic_coverage", "high", "主题「算法面试」仅 2 篇，覆盖不足", 2, "resolved"),
                ("platform_gap", "high", "平台缺口", 0, "open"),
            ],
        )
        conn.commit()
        conn.close()

        f = GapFiller(db_path=db)
        gaps = f._fetch_open_gaps(limit=5)
        assert len(gaps) == 1 and gaps[0]["description"].startswith("主题「RL」")

    def test_fill_loop_with_mocks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Mock 搜索与提取：缺口被填补并标记 resolved。"""
        from openbiliclaw.knowledge_forge import gap_filler as gf
        from openbiliclaw.knowledge_forge.gap_filler import GapFiller

        db = _make_db()
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE gap_records (
                id INTEGER PRIMARY KEY, task_id INTEGER, gap_type TEXT, entity_id INTEGER,
                severity TEXT, description TEXT, current_count INTEGER, suggested_count INTEGER,
                suggestion TEXT, status TEXT, created_at TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO gap_records (gap_type, severity, description, current_count, status) "
            "VALUES ('topic_coverage', 'high', '主题「RL」仅 2 篇，覆盖不足', 2, 'open')"
        )
        conn.commit()
        conn.close()

        class FakeBili:
            def search_cooldown_remaining(self) -> float:
                return 0.0

            async def search(self, keyword: str, **kw: object) -> list[dict[str, object]]:
                assert keyword == "RL"
                return [
                    {"bvid": "BV1AAA", "title": "RL 入门"},
                    {"bvid": "BV1BBB", "title": "RL 进阶"},
                ]

        calls: dict[str, int] = {"extract": 0}

        async def fake_extract(self: object, url: str, *, dry_run: bool = False) -> bool:
            calls["extract"] += 1
            return True

        monkeypatch.setattr(gf, "BilibiliAPIClient", lambda **kw: FakeBili())
        monkeypatch.setattr(GapFiller, "_extract_and_save", fake_extract)

        import asyncio

        stats = asyncio.run(GapFiller(db_path=db).fill(limit_gaps=5, fill_per_gap=2))
        assert stats["gaps_filled"] == 1
        assert stats["articles_filled"] == 2
        conn = sqlite3.connect(db)
        status = conn.execute("SELECT status, description FROM gap_records WHERE id=1").fetchone()
        conn.close()
        assert status[0] == "resolved"
        assert "RL" in status[1]

    def test_fill_skips_cooldown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """B 站搜索冷却期内跳过搜索。"""
        from openbiliclaw.knowledge_forge import gap_filler as gf
        from openbiliclaw.knowledge_forge.gap_filler import GapFiller

        db = _make_db()
        conn = sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE gap_records (id INTEGER PRIMARY KEY, gap_type TEXT, severity TEXT,"
            " entity_id INTEGER, description TEXT, current_count INTEGER, status TEXT,"
            " suggestion TEXT)"
        )
        conn.execute(
            "INSERT INTO gap_records (gap_type, severity, description, current_count, status) "
            "VALUES ('topic_coverage', 'high', '主题「RL」仅 2 篇', 2, 'open')"
        )
        conn.commit()
        conn.close()

        class FakeBiliCooldown:
            def search_cooldown_remaining(self) -> float:
                return 999.0

            async def search(self, keyword: str, **kw: object) -> list[dict[str, object]]:
                raise AssertionError("冷却期不应搜索")

        monkeypatch.setattr(gf, "BilibiliAPIClient", lambda **kw: FakeBiliCooldown())
        import asyncio

        stats = asyncio.run(GapFiller(db_path=db).fill(limit_gaps=5))
        assert stats["search_skipped_cooldown"] == 1
        assert stats["articles_filled"] == 0


# --------------------------------------------------------------------------- #
# 实体描述自动更新（3.2）
# --------------------------------------------------------------------------- #
class TestEntityDescriptionUpdater:
    def _seed(self, db: str, *, with_article: bool = True) -> None:
        import sqlite3

        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO entities (name, type, description, article_count, last_updated_at) "
            "VALUES ('RL', 'concept', '', 5, NULL)"
        )
        conn.execute(
            "INSERT INTO entities (name, type, description, article_count, last_updated_at) "
            "VALUES ('张佳玮', 'author', '已有简介', 3, '2026-09-01 00:00:00')"
        )
        if with_article:
            conn.execute(
                "INSERT INTO articles (id, title, url, summary_compact, published_at, created_at) "
                "VALUES (10, '强化学习入门', 'http://x/10', 'Agent 与环境交互学习', '2026-08-01', "
                "datetime('now'))"
            )
            conn.execute(
                "INSERT INTO article_entities (article_id, entity_id, relevance)"
                " VALUES (10, 1, 0.9)"
            )
        conn.commit()
        conn.close()

    def test_candidates_filters_fresh(self) -> None:
        from openbiliclaw.knowledge_forge.entity_description_updater import (
            EntityDescriptionUpdater,
        )

        db = _make_db()
        self._seed(db)
        updater = EntityDescriptionUpdater(db_path=db)
        candidates = updater._fetch_candidates(limit=10, entity_type="", refresh_days=30)
        assert [c["id"] for c in candidates] == [1]

    def test_run_updates_description(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openbiliclaw.knowledge_forge import entity_description_updater as edu
        from openbiliclaw.knowledge_forge.entity_description_updater import (
            EntityDescriptionUpdater,
        )

        db = _make_db()
        self._seed(db)
        calls: dict[str, int] = {"chat": 0}

        class FakeLlm:
            async def complete(self, spec: object, **kw: object) -> object:
                calls["chat"] += 1
                assert "RL" in kw.get("user_input", "")
                desc = (
                    '{"description": "强化学习（RL），机器学习范式之一，Agent '
                    '通过与环境交互最大化累积奖励。"}'
                )
                return type("R", (), {"content": desc})()

        monkeypatch.setattr(edu, "get_llm_client", lambda **kw: FakeLlm())
        import asyncio

        stats = asyncio.run(EntityDescriptionUpdater(db_path=db).run(limit=10, refresh_days=30))
        assert stats["updated"] == 1 and calls["chat"] == 1
        conn = sqlite3.connect(db)
        desc = conn.execute(
            "SELECT description, last_updated_at FROM entities WHERE id=1"
        ).fetchone()
        conn.close()
        assert desc[0].startswith("强化学习")
        assert desc[1]

    def test_run_dry_run_no_write(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openbiliclaw.knowledge_forge import entity_description_updater as edu
        from openbiliclaw.knowledge_forge.entity_description_updater import (
            EntityDescriptionUpdater,
        )

        db = _make_db()
        self._seed(db)

        class FakeLlm:
            async def complete(self, spec: object, **kw: object) -> object:
                return type(
                    "R",
                    (),
                    {"content": '{"description": "强化学习（RL）领域的研究与工程实践简介"}'},
                )()

        monkeypatch.setattr(edu, "get_llm_client", lambda **kw: FakeLlm())
        import asyncio

        stats = asyncio.run(
            EntityDescriptionUpdater(db_path=db).run(limit=10, refresh_days=30, dry_run=True)
        )
        assert stats["updated"] == 1 and stats["dry_run"]
        conn = sqlite3.connect(db)
        desc = conn.execute("SELECT description FROM entities WHERE id=1").fetchone()[0]
        conn.close()
        assert not desc


# --------------------------------------------------------------------------- #
# 实体间关联持久化（3.2）
# --------------------------------------------------------------------------- #
class TestEntityRelationBuilder:
    def test_compute_co_occurrences(self) -> None:
        from openbiliclaw.knowledge_forge.entity_relation_builder import (
            EntityRelationBuilder,
        )

        db = _make_db()
        conn = sqlite3.connect(db)
        conn.executemany(
            "INSERT INTO article_entities (article_id, entity_id) VALUES (?, ?)",
            [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2)],
        )
        conn.commit()
        conn.close()

        pairs = EntityRelationBuilder(db_path=db)._compute_co_occurrences()
        assert pairs[(1, 2)] == 2  # 实体1与2共现2篇文章
        assert pairs[(1, 3)] == 1
        assert pairs[(2, 3)] == 1

    def test_build_writes_relations(self) -> None:
        from openbiliclaw.knowledge_forge.entity_relation_builder import (
            EntityRelationBuilder,
        )

        db = _make_db()
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE entity_relations (entity_id_a INTEGER, entity_id_b INTEGER,"
            " relation_type TEXT, confidence REAL, description TEXT, co_occur INTEGER,"
            " PRIMARY KEY (entity_id_a, entity_id_b, relation_type))"
        )
        conn.executemany(
            "INSERT INTO article_entities (article_id, entity_id) VALUES (?, ?)",
            [(1, 1), (1, 2), (2, 1), (2, 2)],
        )
        conn.commit()
        conn.close()

        stats = EntityRelationBuilder(db_path=db).build(min_co_occur=1)
        assert stats["pairs_written"] == 1
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT entity_id_a, entity_id_b, co_occur, confidence FROM entity_relations"
        ).fetchone()
        conn.close()
        assert (row[0], row[1]) in ((1, 2), (2, 1))
        assert row[2] == 2 and row[3] == 0.2

    def test_build_min_co_occur_filter(self) -> None:
        from openbiliclaw.knowledge_forge.entity_relation_builder import (
            EntityRelationBuilder,
        )

        db = _make_db()
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE entity_relations (entity_id_a INTEGER, entity_id_b INTEGER,"
            " relation_type TEXT, confidence REAL, description TEXT, co_occur INTEGER,"
            " PRIMARY KEY (entity_id_a, entity_id_b, relation_type))"
        )
        conn.executemany(
            "INSERT INTO article_entities (article_id, entity_id) VALUES (?, ?)",
            [(1, 1), (1, 2), (2, 1), (2, 3)],
        )
        conn.commit()
        conn.close()

        stats = EntityRelationBuilder(db_path=db).build(min_co_occur=2)
        # (1,2) 共现1 < 2 跳过；(1,3) 共现1 < 2 跳过；无写入
        assert stats["pairs_written"] == 0 and stats["skipped_below_min"] == 2


# --------------------------------------------------------------------------- #
# 死链/低质量建议生成 + 定时任务增强
# --------------------------------------------------------------------------- #
class TestAutoFixSuggestion:
    def test_suggest_only_types_write_suggestion(self) -> None:
        """dead_link/too_short/low_quality/missing_author 只写建议不改数据。"""
        from openbiliclaw.knowledge_forge.auto_fixer import IssueFixer

        db = _make_db()
        conn = sqlite3.connect(db)
        conn.executemany(
            "INSERT INTO audit_issues (id, article_id, issue_type, severity, details, status) "
            "VALUES (?, ?, ?, ?, ?, 'open')",
            [
                (1, 1, "dead_link", "high", "{}"),
                (2, 2, "low_quality", "high", "{}"),
            ],
        )
        conn.commit()
        conn.close()

        import asyncio

        stats = asyncio.run(
            IssueFixer(db_path=db).fix_all(
                issue_types=["dead_link", "low_quality"], enable=True
            )
        )
        assert stats["suggested"] == 2 and stats["fixed"] == 0
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT issue_type, fix_suggestion FROM audit_issues ORDER BY id"
        ).fetchall()
        conn.close()
        assert rows[0][1] and "重新抓取" in rows[0][1]
        assert rows[1][1] and "低质量" in rows[1][1]

    def test_suggest_only_dry_run_no_write(self) -> None:
        from openbiliclaw.knowledge_forge.auto_fixer import IssueFixer

        db = _make_db()
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO audit_issues (id, article_id, issue_type, severity, details, status) "
            "VALUES (1, 1, 'dead_link', 'high', '{}', 'open')"
        )
        conn.commit()
        conn.close()

        import asyncio

        stats = asyncio.run(
            IssueFixer(db_path=db).fix_all(
                issue_types=["dead_link"], enable=True, dry_run=True
            )
        )
        assert stats["suggested"] == 1
        conn = sqlite3.connect(db)
        sug = conn.execute(
            "SELECT fix_suggestion FROM audit_issues WHERE id=1"
        ).fetchone()[0]
        conn.close()
        assert not sug


class TestSchedulerEnhanced:
    def test_run_scheduled_includes_entity_relations(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """定时任务组合含实体共现刷新步骤。"""
        from openbiliclaw.knowledge_forge import schedule as sch

        db = _make_db()
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE entity_relations (
                entity_id_a INTEGER, entity_id_b INTEGER, relation_type TEXT,
                confidence REAL, description TEXT, co_occur INTEGER,
                PRIMARY KEY (entity_id_a, entity_id_b, relation_type)
            );
            """
        )
        conn.executemany(
            "INSERT INTO article_entities (article_id, entity_id) VALUES (?, ?)",
            [(1, 1), (1, 2)],
        )
        conn.commit()
        conn.close()

        captured: dict[str, object] = {}

        async def fake_gap(self: object) -> dict[str, object]:
            return {"gaps_found": 0, "high": 0}

        async def fake_fill(self: object, **kw: object) -> dict[str, object]:
            captured["fill"] = True
            return {"gaps_filled": 0, "articles_filled": 0}

        monkeypatch.setattr(
            "openbiliclaw.knowledge_forge.gap_analyst.GapAnalyst.run", fake_gap
        )
        monkeypatch.setattr(
            "openbiliclaw.knowledge_forge.gap_filler.GapFiller.fill", fake_fill
        )

        import asyncio

        stats = asyncio.run(
            sch.Scheduler(db_path=db).run_scheduled(
                include_gap_fill=True, dry_run=True
            )
        )
        assert "entity_relations" in stats["steps"]
        assert stats["steps"]["entity_relations"]["pairs_written"] == 1
        assert captured.get("fill") is True
        assert "自动补充" in stats["report"]

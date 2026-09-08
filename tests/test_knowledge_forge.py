"""Knowledge Forge 核心工具与内容清理器测试。"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from openbiliclaw.knowledge_forge.content_cleaner import ContentCleaner
from openbiliclaw.knowledge_forge.utils import (
    compress_whitespace,
    content_hash,
    decode_html_entities,
    hamming_distance,
    has_encoding_error,
    simhash,
    simhash_similarity,
    strip_html_tags,
    tokenize,
)


# --------------------------------------------------------------------------- #
# utils
# --------------------------------------------------------------------------- #
class TestTokenize:
    def test_zh_ngram(self) -> None:
        tokens = tokenize("推荐系统")
        assert "推荐" in tokens and "荐系" in tokens and "系统" in tokens

    def test_en_word(self) -> None:
        tokens = tokenize("LLM Ranking")
        assert "llm" in tokens and "ranking" in tokens


class TestSimhash:
    def test_near_duplicate_high_similarity(self) -> None:
        a = simhash("推荐系统的粗排模型使用双塔结构进行高效召回排序")
        b = simhash("推荐系统的粗排模型使用双塔结构进行高效召回排序！")
        assert simhash_similarity(a, b) > 0.9

    def test_different_text_low_similarity(self) -> None:
        a = simhash("量化投资中的因子挖掘与回测过拟合问题探讨")
        b = simhash("张三今天去菜市场买了两斤苹果和一斤香蕉")
        assert simhash_similarity(a, b) < 0.75

    def test_hamming_distance(self) -> None:
        assert hamming_distance(0b1010, 0b1000) == 1

    def test_content_hash(self) -> None:
        assert content_hash("abc") == content_hash("abc")
        assert content_hash("abc") != content_hash("abd")


class TestHtmlCleaning:
    def test_strip_tags(self) -> None:
        assert strip_html_tags("<p>hello <b>world</b></p>") == "hello world"

    def test_strip_script(self) -> None:
        assert strip_html_tags("正文<script>alert(1)</script>尾部") == "正文尾部"

    def test_decode_entities(self) -> None:
        assert decode_html_entities("a&amp;b &#39;c&#39;") == "a&b 'c'"

    def test_compress_whitespace(self) -> None:
        assert compress_whitespace("a\n\n\n\nb  c") == "a\nb c"

    def test_encoding_error(self) -> None:
        assert not has_encoding_error("正常文本")
        assert has_encoding_error("乱\ufffd码")


# --------------------------------------------------------------------------- #
# ContentCleaner
# --------------------------------------------------------------------------- #
class TestContentCleaner:
    def test_zhihu_comment_truncation(self) -> None:
        cleaner = ContentCleaner()
        body = "正文第一段内容。\n正文第二段继续介绍。\n正文第三段。\n一看就是t0的老哥\n后面都是评论"
        result = cleaner.clean(body, title="标题", source_type="zhihu")
        assert "t0的老哥" not in result.cleaned_text
        assert result.truncated_at is not None
        assert result.operations and any(o["op"] == "zhihu_comment" for o in result.operations)

    def test_zhihu_recommendation_truncation(self) -> None:
        cleaner = ContentCleaner()
        body = (
            "正文内容很长的一段，这里继续展开介绍细节和背景知识。\n" * 10
            + "一、价值链分析的新框架\n"
            + "这是推荐内容，应该被截断"
        )
        result = cleaner.clean(body, title="标题", source_type="zhihu")
        assert "价值链分析" not in result.cleaned_text

    def test_xhs_ad_removal(self) -> None:
        cleaner = ContentCleaner()
        body = "这是一篇正常的小红书笔记内容。\n点击链接购买同款\n更多优惠请私信我"
        result = cleaner.clean(body, source_type="xiaohongshu")
        assert "点击链接" not in result.cleaned_text

    def test_bilibili_danmaku_removal(self) -> None:
        cleaner = ContentCleaner()
        body = "视频正文第一句。\n[00:01:23] 哈哈哈哈\n视频正文第二句。"
        result = cleaner.clean(body, source_type="bilibili")
        assert "哈哈哈哈" not in result.cleaned_text

    def test_html_clean(self) -> None:
        cleaner = ContentCleaner()
        result = cleaner.clean("<p>正文内容<b>加粗</b></p>", title="标题")
        assert result.cleaned_text == "正文内容加粗"

    def test_too_short_flagged(self) -> None:
        cleaner = ContentCleaner()
        result = cleaner.clean("太短了", title="标题")
        assert not result.verified
        assert any("content_too_short" in i for i in result.verify_issues)

    def test_score_in_range(self) -> None:
        cleaner = ContentCleaner()
        result = cleaner.clean("正文内容足够长。" * 50, title="标题")
        assert 0 <= result.clean_score <= 100

    def test_general_ad_marked_not_removed(self) -> None:
        """通用广告只标记不删除（保守策略）。"""
        cleaner = ContentCleaner()
        body = "这是正文内容。\n欢迎扫码关注公众号获取更多。"
        result = cleaner.clean(body, source_type="zhihu")
        assert "扫码关注" in result.cleaned_text  # 保守：不删
        assert any(o["op"] == "ad_suspect" for o in result.operations)


# --------------------------------------------------------------------------- #
# 存储层集成（临时库）
# --------------------------------------------------------------------------- #
class TestStorageIntegration:
    def _make_db(self) -> Path:
        d = tempfile.mkdtemp()
        return Path(d) / "kf.db"

    def test_summary_persist_and_skip(self) -> None:
        """summary_engine 落库 + 幂等跳过（无 LLM，仅验证 SQL 路径）。"""
        from openbiliclaw.knowledge_forge.summary_engine import SummaryEngine

        db = self._make_db()
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE articles (
                id INTEGER PRIMARY KEY, title TEXT, author TEXT, source_type TEXT,
                content_text TEXT, content_cleaned TEXT, tags TEXT,
                summary_detailed TEXT, summary_compact TEXT, summary_ultra_compact TEXT,
                summary_quality REAL, summary_version INTEGER, summary_generated_at TEXT,
                ai_summary TEXT, content_hash TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO articles (id, title, content_text) VALUES (1, 't', '正文内容' * 100)"
        )
        conn.commit()
        conn.close()

        engine = SummaryEngine(db_path=db)
        # 无 LLM：generate 应返回 content_too_short 或错误，但不应崩溃
        row = engine._fetch_row(1)
        assert row is not None

        # 已有摘要时 _has_summary 返回 True
        conn = sqlite3.connect(db)
        conn.execute(
            "UPDATE articles SET summary_detailed='d', summary_compact='c' WHERE id=1"
        )
        conn.commit()
        conn.close()
        assert engine._has_summary(engine._fetch_row(1)) is True

    def test_audit_duplicate_detection(self) -> None:
        """quality_auditor：URL 重复检测写 audit_issues。"""
        from openbiliclaw.knowledge_forge.quality_auditor import QualityAuditor

        db = self._make_db()
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE articles (
                id INTEGER PRIMARY KEY, source_type TEXT, title TEXT, url TEXT,
                author TEXT, tags TEXT, content_text TEXT, content_cleaned TEXT,
                ai_summary TEXT, content_hash TEXT, published_at TEXT, created_at TEXT, status TEXT
            );
            CREATE TABLE audit_tasks (
                id INTEGER PRIMARY KEY, task_type TEXT, status TEXT, started_at TEXT,
                completed_at TEXT, total_articles INTEGER, issues_found INTEGER,
                issues_fixed INTEGER, report_path TEXT, created_at TEXT
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
            CREATE TABLE audit_config (
                id INTEGER PRIMARY KEY, config_key TEXT UNIQUE, config_value TEXT, description TEXT
            );
            """
        )
        conn.executemany(
            "INSERT INTO articles (id, source_type, title, url, content_text, ai_summary, tags, author) "
            "VALUES (?, 'zhihu', 'a', 'http://same', '正文内容足够长。' * 30, '摘要内容足够长。' * 20, '[\"t\"]', '作者')",
            [(1,), (2,)],
        )
        conn.commit()
        conn.close()

        auditor = QualityAuditor(db_path=db)
        stats = auditor.run_full_audit()
        assert stats["duplicate_groups"] >= 1
        assert stats["issues_found"] >= 1

        conn = sqlite3.connect(db)
        n = conn.execute(
            "SELECT COUNT(*) FROM audit_issues WHERE issue_type='duplicate'"
        ).fetchone()[0]
        conn.close()
        assert n >= 1

    def test_entity_upsert_unique_name(self) -> None:
        """entity_extractor：同名不同 type 不违反 UNIQUE 约束。"""
        from openbiliclaw.knowledge_forge.entity_extractor import EntityExtractor

        db = self._make_db()
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE entities (
                id INTEGER PRIMARY KEY, name TEXT UNIQUE, type TEXT, description TEXT,
                article_count INTEGER DEFAULT 0, first_seen_at TEXT, last_updated_at TEXT, metadata TEXT
            );
            CREATE TABLE article_entities (
                article_id INTEGER, entity_id INTEGER, relevance REAL, context TEXT,
                PRIMARY KEY (article_id, entity_id)
            );
            """
        )
        conn.commit()
        conn.close()

        extractor = EntityExtractor(db_path=db)
        e1 = extractor._upsert_entity("AgenticRec", "topic")
        e2 = extractor._upsert_entity("AgenticRec", "concept")  # 同名不同 type → 复用
        assert e1 == e2
        conn = sqlite3.connect(db)
        n = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        conn.close()
        assert n == 1

    def test_gap_analysis_runs(self) -> None:
        """gap_analyst：最小表结构下可运行。"""
        from openbiliclaw.knowledge_forge.gap_analyst import GapAnalyst

        db = self._make_db()
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE articles (
                id INTEGER PRIMARY KEY, source_type TEXT, title TEXT, tags TEXT,
                content_text TEXT, published_at TEXT, created_at TEXT
            );
            CREATE TABLE entities (
                id INTEGER PRIMARY KEY, name TEXT UNIQUE, type TEXT, article_count INTEGER
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
            "INSERT INTO articles (id, source_type, title, tags, content_text, published_at) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, "zhihu", "a", '["冷门主题"]', "正文", "2026-01-01"),
                (2, "zhihu", "b", '["冷门主题"]', "正文", "2026-01-01"),
            ],
        )
        conn.commit()
        conn.close()

        result = GapAnalyst(db_path=db).run()
        assert result["gaps_found"] >= 1
        assert "高" in result["report"] or "中" in result["report"] or "低" in result["report"]


# --------------------------------------------------------------------------- #
# ContradictionDetector（无 LLM 部分）
# --------------------------------------------------------------------------- #
class TestContradictionDetector:
    def _make_db(self) -> Path:
        import tempfile

        d = tempfile.mkdtemp()
        return Path(d) / "kf.db"

    def test_candidate_pairs_shared_tags(self) -> None:
        """候选配对：共享标签的配对被找到，已检测过的跳过。"""
        from openbiliclaw.knowledge_forge.contradiction_detector import (
            ContradictionDetector,
        )

        db = self._make_db()
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE articles (
                id INTEGER PRIMARY KEY, source_type TEXT, title TEXT, url TEXT,
                author TEXT, tags TEXT, content_text TEXT, content_cleaned TEXT,
                summary_compact TEXT, ai_summary TEXT, published_at TEXT, created_at TEXT
            );
            CREATE TABLE article_relations (
                article_id_a INTEGER, article_id_b INTEGER, relation_type TEXT,
                confidence REAL, description TEXT, created_at TEXT,
                PRIMARY KEY (article_id_a, article_id_b, relation_type)
            );
            """
        )
        conn.executemany(
            "INSERT INTO articles (id, title, tags, content_text) VALUES (?, ?, ?, ?)",
            [
                (1, "A", '["AI", "推荐"]', "正文内容足够长。"),
                (2, "B", '["AI", "推荐"]', "正文内容足够长。"),
                (3, "C", '["AI"]', "正文内容足够长。"),
                (4, "D", '["AI"]', "正文内容足够长。"),
            ],
        )
        # 1-2 已检测过矛盾 → 应被跳过
        conn.execute(
            "INSERT INTO article_relations (article_id_a, article_id_b, relation_type) VALUES (1, 2, 'contradiction')"
        )
        conn.commit()
        conn.close()

        d = ContradictionDetector(db_path=db)
        pairs = d._fetch_candidate_pairs()
        keys = {(p["a_id"], p["b_id"]) for p in pairs}
        assert (1, 2) not in keys  # 已检测跳过
        assert (3, 4) in keys  # 共享单标签配对
        # 所有配对共享 ≥1 个标签
        for p in pairs:
            assert len(p["shared_tags"]) >= 1

    def test_parse_judgement_json(self) -> None:
        """LLM 输出 JSON 解析：围栏、缩进、异常值容错。"""
        from openbiliclaw.knowledge_forge.contradiction_detector import (
            ContradictionDetector,
        )

        d = ContradictionDetector(db_path=self._make_db())
        # 正常
        r = d._parse_judgement('{"contradiction": true, "confidence": 0.85, "description": "观点冲突"}')
        assert r is not None and r["contradiction"] is True
        # 围栏包裹
        r2 = d._parse_judgement('```json\n{"contradiction": false, "confidence": 0.3}\n```')
        assert r2 is not None and r2["contradiction"] is False
        # 置信度低于阈值 → 视为不矛盾
        r3 = d._parse_judgement('{"contradiction": true, "confidence": 0.5}')
        assert r3 is not None and r3["contradiction"] is False
        # 垃圾输入
        assert d._parse_judgement("") is None
        assert d._parse_judgement("抱歉我无法判断") is None

    def test_write_and_report(self) -> None:
        """矛盾关系落库 + 报告生成。"""
        from openbiliclaw.knowledge_forge.contradiction_detector import (
            ContradictionDetector,
        )

        db = self._make_db()
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE articles (
                id INTEGER PRIMARY KEY, title TEXT, url TEXT, content_text TEXT
            );
            CREATE TABLE article_relations (
                article_id_a INTEGER, article_id_b INTEGER, relation_type TEXT,
                confidence REAL, description TEXT, created_at TEXT,
                PRIMARY KEY (article_id_a, article_id_b, relation_type)
            );
            """
        )
        conn.executemany(
            "INSERT INTO articles (id, title, content_text) VALUES (?, ?, '正文')",
            [(1, "甲"), (2, "乙")],
        )
        conn.commit()
        conn.close()

        d = ContradictionDetector(db_path=db)
        d._write_relation(1, 2, True, 0.9, "观点冲突")
        report = d.generate_report()
        assert "观点冲突" in report and "甲" in report and "乙" in report


# --------------------------------------------------------------------------- #
# DeadLinkChecker（本地 HTTP mock）
# --------------------------------------------------------------------------- #
class TestDeadLinkChecker:
    def _make_db(self) -> Path:
        import tempfile

        d = tempfile.mkdtemp()
        return Path(d) / "kf.db"

    def _make_server(self, port: int) -> None:
        """在子线程起本地 HTTP 服务（port 由调用方传入）。"""
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class H(BaseHTTPRequestHandler):
            def do_HEAD(self) -> None:
                self._r()

            def do_GET(self) -> None:
                self._r()

            def _r(self) -> None:
                if self.path == "/alive":
                    self.send_response(200)
                elif self.path == "/dead":
                    self.send_response(404)
                elif self.path == "/gone":
                    self.send_response(410)
                elif self.path == "/move":
                    self.send_response(302)
                    self.send_header("Location", "/alive")
                else:
                    self.send_response(200)
                self.end_headers()

            def log_message(self, *args: object) -> None:  # noqa: A002
                pass

        self._srv = HTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def test_check_states(self) -> None:
        """死链检测：alive/dead(404,410)/redirect 分类正确且落库。"""
        import asyncio
        import socket

        from openbiliclaw.knowledge_forge.dead_link_checker import DeadLinkChecker

        # 找空闲端口
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self._make_server(port)

        db = self._make_db()
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE articles (id INTEGER PRIMARY KEY, source_type TEXT, url TEXT);
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
            """
        )
        conn.executemany(
            "INSERT INTO articles (id, source_type, url) VALUES (?, ?, ?)",
            [
                (1, "zhihu", f"http://127.0.0.1:{port}/alive"),
                (2, "zhihu", f"http://127.0.0.1:{port}/dead"),
                (3, "bilibili", f"http://127.0.0.1:{port}/gone"),
                (4, "zhihu", f"http://127.0.0.1:{port}/move"),
            ],
        )
        conn.commit()
        conn.close()

        checker = DeadLinkChecker(db_path=db)
        stats = asyncio.run(checker.check())
        assert stats["checked"] == 4
        assert stats["alive"] == 1
        assert stats["dead"] == 2  # 404 + 410
        assert stats["redirect"] == 1

        conn = sqlite3.connect(db)
        n_dead = conn.execute(
            "SELECT COUNT(*) FROM audit_issues WHERE issue_type='dead_link' AND severity='high'"
        ).fetchone()[0]
        task = conn.execute(
            "SELECT status, total_articles FROM audit_tasks WHERE task_type='dead_link_check'"
        ).fetchone()
        conn.close()
        assert n_dead == 2
        assert task[0] == "completed" and task[1] == 4
        self._srv.shutdown()

    def test_fetch_candidates_skips_recent(self) -> None:
        """候选提取：过去 7 天检查过且有效的文章被跳过。"""
        import asyncio

        from openbiliclaw.knowledge_forge.dead_link_checker import DeadLinkChecker

        db = self._make_db()
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE articles (id INTEGER PRIMARY KEY, source_type TEXT, url TEXT);
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
            """
        )
        conn.executemany(
            "INSERT INTO articles (id, source_type, url) VALUES (?, 'zhihu', ?)",
            [(1, "http://a.com/1"), (2, "http://a.com/2")],
        )
        # 文章1 昨天检查过且 alive → 应跳过
        conn.execute(
            "INSERT INTO audit_issues (article_id, issue_type, severity, description, details, status, created_at)"
            " VALUES (1, 'dead_link', 'low', '链接有效',"
            ' \'{"status_group":"alive"}\', \'open\', datetime(\'now\',\'localtime\',\'-1 day\'))'
        )
        conn.commit()
        conn.close()

        checker = DeadLinkChecker(db_path=db)
        result = asyncio.run(checker.check(limit=10))
        assert result["checked"] == 1  # 只检查文章2
        assert result["skipped_recent"] == 1

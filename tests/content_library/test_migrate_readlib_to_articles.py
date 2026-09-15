"""`scripts/content_library/migrate_readlib_to_articles.py` 的回归。

用**合成库**跑（不碰真实 data/*.db）：源库只有 `read_archive` + `notes`，目标库只有
`articles`，列按真实 schema 裁剪到脚本用到的那些。

重点锁三件事：
1. **默认 dry-run 一个字节都不写**（写库动作必须先 `--apply`）；
2. **绝不覆盖已有文章**（默认跳过；`--fill-blank` 只补空）；
3. **不伪造 URL**（`notes.source_url = "original"` 这种 → 报不可迁移，而不是编一个）。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "content_library"
    / "migrate_readlib_to_articles.py"
)
_SPEC = importlib.util.spec_from_file_location("obc_migrate_readlib", _SCRIPT_PATH)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - 路径固定存在
    raise RuntimeError(f"无法加载脚本：{_SCRIPT_PATH}")
_MODULE = importlib.util.module_from_spec(_SPEC)
# ⚠️ 必须先登记进 sys.modules 再 exec_module：脚本里有 @dataclass，
# dataclasses 解析类型注解时会去 sys.modules 里找本模块，否则收集阶段就挂。
sys.modules[_MODULE.__name__] = _MODULE
_SPEC.loader.exec_module(_MODULE)

_SOURCE_DDL = """
CREATE TABLE read_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    source_name TEXT DEFAULT '',
    title TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    author TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    content_text TEXT DEFAULT '',
    published_at TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '',
    content_md TEXT NOT NULL DEFAULT '',
    note_type TEXT NOT NULL DEFAULT 'manual',
    source_platform TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    source_ref TEXT DEFAULT '',
    author TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    metadata TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_TARGET_DDL = """
CREATE TABLE articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    source_name TEXT DEFAULT '',
    title TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    author TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    content_text TEXT DEFAULT '',
    published_at TEXT DEFAULT '',
    status TEXT DEFAULT 'unread',
    tags TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _make_dbs(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "openbiliclaw.db"
    target = tmp_path / "content.db"
    with sqlite3.connect(source) as conn:
        conn.executescript(_SOURCE_DDL)
    with sqlite3.connect(target) as conn:
        conn.executescript(_TARGET_DDL)
    return source, target


def _seed_archive(
    db: Path,
    *,
    url: str,
    title: str = "一篇知乎回答",
    source_name: str = "知乎",
    author: str = "某人",
    summary: str = "摘要",
    content_text: str = "正文",
    published_at: str = "2026-06-25 19:56",
) -> int:
    with sqlite3.connect(db) as conn:
        cursor = conn.execute(
            """INSERT INTO read_archive
               (source_type, source_name, title, url, author, summary, content_text, published_at)
               VALUES ('read-archive', ?, ?, ?, ?, ?, ?, ?)""",
            (source_name, title, url, author, summary, content_text, published_at),
        )
        return int(cursor.lastrowid or 0)


def _seed_note(
    db: Path,
    *,
    url: str,
    title: str = "一篇小红书笔记",
    content_md: str = "笔记正文",
    note_type: str = "import",
    platform: str = "xiaohongshu",
    tags: str = "[]",
) -> int:
    with sqlite3.connect(db) as conn:
        cursor = conn.execute(
            """INSERT INTO notes (title, content_md, note_type, source_platform, source_url, tags)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (title, content_md, note_type, platform, url, tags),
        )
        return int(cursor.lastrowid or 0)


def _seed_article(db: Path, *, url: str, title: str = "已存在的文章", **extra: Any) -> None:
    columns = ["source_type", "title", "url", *extra]
    values: list[Any] = ["zhihu", title, url, *extra.values()]
    placeholders = ", ".join("?" for _ in columns)
    with sqlite3.connect(db) as conn:
        conn.execute(
            f"INSERT INTO articles ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )


def _articles(db: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT * FROM articles ORDER BY id")]


def _run(source: Path, target: Path, *args: str) -> int:
    return _MODULE.main(
        ["--source-db", str(source), "--target-db", str(target), *args]
    )


class TestDryRunIsReadOnly:
    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        """默认（不带 --apply）**一个字节都不写**。

        鉴别力：把 `if args.apply:` 去掉，本用例立刻红（articles 会多出 1 行）。
        """
        source, target = _make_dbs(tmp_path)
        _seed_archive(source, url="https://example.com/a")

        assert _run(source, target) == 0

        assert _articles(target) == []

    def test_apply_writes(self, tmp_path: Path) -> None:
        source, target = _make_dbs(tmp_path)
        _seed_archive(source, url="https://example.com/a", title="标题甲")

        assert _run(source, target, "--apply") == 0

        rows = _articles(target)
        assert len(rows) == 1
        assert rows[0]["title"] == "标题甲"
        assert rows[0]["source_type"] == "read-archive"
        assert rows[0]["source_name"] == "知乎"
        assert rows[0]["content_text"] == "正文"

    def test_apply_is_idempotent(self, tmp_path: Path) -> None:
        source, target = _make_dbs(tmp_path)
        _seed_archive(source, url="https://example.com/a")

        _run(source, target, "--apply")
        _run(source, target, "--apply")

        assert len(_articles(target)) == 1


class TestNeverOverwrites:
    def test_existing_article_is_skipped(self, tmp_path: Path) -> None:
        """同一 URL 已在 articles 里 → 默认跳过，**不覆盖**（可能是洗过正文/摘过的行）。

        鉴别力：把 skip 改成 upsert，title 会从「已存在的文章」被改成「标题甲」。
        """
        source, target = _make_dbs(tmp_path)
        _seed_archive(source, url="https://example.com/a", title="标题甲")
        _seed_article(target, url="https://example.com/a", title="已存在的文章")

        _run(source, target, "--apply")

        rows = _articles(target)
        assert len(rows) == 1
        assert rows[0]["title"] == "已存在的文章"

    def test_fill_blank_only_fills_empty_columns(self, tmp_path: Path) -> None:
        source, target = _make_dbs(tmp_path)
        _seed_archive(source, url="https://example.com/a", title="标题甲", summary="来源摘要")
        _seed_article(target, url="https://example.com/a", title="已存在标题", summary="")

        _run(source, target, "--apply", "--fill-blank")

        row = _articles(target)[0]
        assert row["title"] == "已存在标题"  # 非空，不动
        assert row["summary"] == "来源摘要"  # 空，补上

    def test_mark_read_is_opt_in(self, tmp_path: Path) -> None:
        """迁移不替用户断言「已读」：默认保持 articles 的默认 status。"""
        source, target = _make_dbs(tmp_path)
        _seed_archive(source, url="https://example.com/a")

        _run(source, target, "--apply")
        assert _articles(target)[0]["status"] == "unread"

        source2, target2 = _make_dbs(tmp_path / "second")
        _seed_archive(source2, url="https://example.com/a")
        _run(source2, target2, "--apply", "--mark-read")
        assert _articles(target2)[0]["status"] == "finished"

class TestUrlPolicy:
    def test_non_http_scheme_is_migrated_and_reported(self, tmp_path: Path, capsys: Any) -> None:
        """`local://readlib/...` 是旧管线造的**稳定占位符**，要迁，但要提醒点不开。"""
        source, target = _make_dbs(tmp_path)
        _seed_archive(source, url="local://readlib/小红书-某篇笔记")

        assert _run(source, target, "--apply") == 0

        assert _articles(target)[0]["url"] == "local://readlib/小红书-某篇笔记"
        assert "不是 http(s) 链接" in capsys.readouterr().out

    def test_notes_without_a_real_link_is_not_migrated(self, tmp_path: Path) -> None:
        """`source_url = "original"` → 不可迁移（**绝不**编一个假 URL 填进去）。"""
        source, target = _make_dbs(tmp_path)
        _seed_note(source, url="original", title="用户负向行为建模 — 面试专题")

        assert _run(source, target, "--apply") == 0

        assert _articles(target) == []

    def test_notes_with_a_link_becomes_a_note_article(self, tmp_path: Path) -> None:
        source, target = _make_dbs(tmp_path)
        _seed_note(
            source,
            url="https://www.xiaohongshu.com/explore/abc123",
            title="Data Agent 的7层架构",
            content_md="正文",
            note_type="import",
        )

        _run(source, target, "--apply")

        row = _articles(target)[0]
        assert row["source_type"] == "note"
        assert row["source_name"] == "xiaohongshu"
        # note_type 不丢：追加进 tags
        assert "import" in json.loads(row["tags"])


class TestWithinBatchDedup:
    def test_same_url_in_both_tables_is_created_once(self, tmp_path: Path) -> None:
        """实测存在同一条内容同时躺在 read_archive 与 notes 里 → 只能插一次。

        鉴别力：去掉批内去重，第二条 INSERT 会撞 `url UNIQUE` 直接抛异常。
        """
        source, target = _make_dbs(tmp_path)
        url = "https://www.xiaohongshu.com/explore/abc123"
        _seed_archive(source, url=url, title="来自已读库")
        _seed_note(source, url=url, title="来自笔记")

        assert _run(source, target, "--apply") == 0

        rows = _articles(target)
        assert len(rows) == 1
        assert rows[0]["title"] == "来自已读库"  # 先到先得（read_archive 先规划）


class TestGuards:
    def test_missing_source_table_reports_and_exits_nonzero(self, tmp_path: Path) -> None:
        source, target = _make_dbs(tmp_path)
        with sqlite3.connect(source) as conn:
            conn.execute("DROP TABLE read_archive")

        assert _run(source, target, "--kind", "read-archive") == 2

    def test_missing_target_db_exits_nonzero(self, tmp_path: Path) -> None:
        source, _target = _make_dbs(tmp_path)
        assert _run(source, tmp_path / "nope.db") == 2

    def test_kind_filter_limits_the_scope(self, tmp_path: Path) -> None:
        source, target = _make_dbs(tmp_path)
        _seed_archive(source, url="https://example.com/a")
        _seed_note(source, url="https://example.com/b")

        _run(source, target, "--apply", "--kind", "notes")

        rows = _articles(target)
        assert len(rows) == 1
        assert rows[0]["source_type"] == "note"


class TestJsonSummary:
    def test_json_reports_counts_and_non_clickable(self, tmp_path: Path, capsys: Any) -> None:
        source, target = _make_dbs(tmp_path)
        _seed_archive(source, url="https://example.com/a")
        _seed_archive(source, url="local://readlib/占位符")
        _seed_note(source, url="original")

        assert _run(source, target, "--json") == 0

        payload = json.loads(capsys.readouterr().out)
        assert payload["mode"] == "dry-run"
        assert payload["counts"] == {"create": 2, "unmigratable": 1}
        assert payload["non_clickable_urls"] == 1
        assert payload["applied"] == {}


@pytest.mark.parametrize(
    ("sample", "expected"),
    [
        ("https://example.com/a", True),
        ("local://readlib/占位符", True),
        ("original", False),
        ("", False),
        ("just/a/path", False),
    ],
)
def test_url_migratable_predicate(sample: str, expected: bool) -> None:
    """自检判据本身：URI 与否，别把占位符和真 URL 搞混。"""
    assert _MODULE._is_migratable_url(sample) is expected

#!/usr/bin/env python3
"""导入求职知识库方向专题文档（02_方向知识库/*.md）到 knowledge.db 的 kb_documents 表，按章节切块。

用法
----
python scripts/import_kb_documents.py          # dry-run 预览
python scripts/import_kb_documents.py --apply  # 实际写入
python scripts/import_kb_documents.py --apply --force  # 清空重导

Schema (knowledge.db):
```sql
CREATE TABLE IF NOT EXISTS kb_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,          -- 专题主题（来自frontmatter.topic，缺则文件名默认）
    category TEXT NOT NULL,        -- 大分类（子目录路径，如"大模型推荐专题/01_方法体系"）
    chapter TEXT NOT NULL,        -- 章节标题（## 后的部分）
    content TEXT NOT NULL,        -- 章节正文
    tags TEXT DEFAULT '',        -- 逗号分隔tags（frontmatter）
    source_path TEXT NOT NULL,    -- 来源文件相对路径
    doc_type TEXT DEFAULT 'markdown',  -- 文档类型（frontmatter.type，缺markdown）
    updated TEXT DEFAULT '',     -- 更新时间（frontmatter.updated）
    created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS kb_documents_fts USING fts5(
    topic, category, chapter, content,
    content_rowid=id,
    tokenize='trigram'
);
```
"""
from __future__ import annotations

import re
import sys
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIRECTION_KB_ROOT = PROJECT_ROOT / "求职知识库" / "02_方向知识库"
INTERVIEW_DB = PROJECT_ROOT / "data" / "knowledge.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    category TEXT NOT NULL,
    chapter TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT DEFAULT '',
    source_path TEXT NOT NULL,
    doc_type TEXT DEFAULT 'markdown',
    updated TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_topic ON kb_documents(topic);
CREATE INDEX IF NOT EXISTS idx_kb_category ON kb_documents(category);
CREATE UNIQUE INDEX IF NOT EXISTS idx_kb_dedup ON kb_documents(topic, chapter);
CREATE VIRTUAL TABLE IF NOT EXISTS kb_documents_fts USING fts5(
    topic, category, chapter, content,
    content='kb_documents',
    content_rowid='id',
    tokenize='trigram'
);
"""

REBUILD_FTS = """
INSERT INTO kb_documents_fts(kb_documents_fts) VALUES('rebuild');
"""


def _extract_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """提取开头 --- 包裹的 YAML frontmatter → (meta_dict, rest_text)"""
    m = re.match(r"^\s*---\s*$(.*?)^\s*---\s*$", text, flags=re.MULTILINE | re.DOTALL)
    if not m:
        return {}, text
    fm_text = m.group(1)
    rest = text[m.end():]
    meta: dict[str, str] = {}
    for line in fm_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip().strip('[]"\'')
    return meta, rest


def _split_chapters(text: str) -> list[tuple[str, str]]:
    """按 ## 章节标题切分文本 → [(chapter_title, chapter_content), ...]"""
    chapters: list[tuple[str, str]] = []
    current_title = ""
    current_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current_title or current_lines:
                chapters.append((current_title, "\n".join(current_lines).strip()))
            current_title = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_title or current_lines:
        chapters.append((current_title, "\n".join(current_lines).strip()))
    # 如果开头没有 ##（比如开头大标题+正文直接），current_title 空，但有正文 → 记为 概述
    if not chapters and text.strip():
        chapters.append(("概述", text.strip()))
    elif chapters and not chapters[0][0] and chapters[0][1]:
        chapters[0] = ("概述", chapters[0][1])
    return chapters


def build_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(INTERVIEW_DB), timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def main() -> int:
    apply = "--apply" in sys.argv
    force = "--force" in sys.argv

    conn = build_conn()
    try:
        from datetime import datetime
        created_at = datetime.now().isoformat()

        if force:
            conn.execute("DELETE FROM kb_documents")
            conn.commit()
            if apply:
                print("已清空 kb_documents（--force）")

        # 扫描 02_方向知识库/**/*.md 所有文件
        all_md: list[Path] = list(DIRECTION_KB_ROOT.rglob("*.md"))
        print(f"找到 {len(all_md)} 个 Markdown 文件")

        inserted = 0
        skipped = 0
        for path in sorted(all_md):
            try:
                rel_path = path.relative_to(PROJECT_ROOT)
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                print(f"  [跳过] 编码错误: {rel_path}")
                skipped += 1
                continue

            meta, text_body = _extract_frontmatter(text)
            chapters = _split_chapters(text_body)
            if not chapters:
                print(f"  [跳过] 无章节: {rel_path}")
                skipped += 1
                continue

            # 提取元数据 → topic/tags/updated/type
            topic = meta.get("topic", path.stem)
            category = "/".join(path.relative_to(DIRECTION_KB_ROOT).parent.parts)
            tags = ",".join(
                [t.strip() for t in meta.get("tags", "").split(",") if t.strip()]
            )
            updated = meta.get("updated", "")
            doc_type = meta.get("type", "markdown")

            # 每个章节一条记录
            for chap_title, chap_content in chapters:
                if not chap_title.strip() and not chap_content.strip():
                    continue
                if apply:
                    conn.execute(
                        """INSERT OR IGNORE INTO kb_documents
                           (topic, category, chapter, content, tags, source_path, doc_type, updated, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (topic, category, chap_title, chap_content,
                         tags, str(rel_path), doc_type, updated, created_at),
                    )
                inserted += 1

        if apply:
            conn.commit()
            # rebuild FTS 索引
            conn.executescript(REBUILD_FTS)
            conn.commit()
            print(f"\n导入完成: {inserted} 章节 / {len(all_md)} 文件 → {INTERVIEW_DB} (跳过 {skipped})")
        else:
            print(f"\n[DRY-RUN] 将导入约 {inserted} 章节 / {len(all_md)} 文件。加 --apply 写入")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

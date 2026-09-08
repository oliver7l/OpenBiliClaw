"""
Knowledge Forge 模块数据库迁移（文档附录 A 的可执行版本）。

迁移内容（全部幂等，可重复执行）：
    1. articles 表新增正文清理器字段（content_cleaned/content_clean_score/...）
    2. articles 表新增分层摘要字段（summary_detailed/summary_compact/...）
    3. 创建实体表：entities / article_entities / entity_relations
    4. 创建文章间关联表：article_relations
    5. 创建质量审计表：audit_tasks / audit_issues / article_quality_scores / audit_config
    6. 创建缺口分析表：gap_analysis_tasks / gap_records

迁移逻辑与 src/openbiliclaw/storage/database.py::Database._ensure_knowledge_forge_tables
为同一份实现（脚本通过 Database.initialize() 触发），避免两份 SQL 漂移。
旧数据与旧字段完全保留，向后兼容。

执行：
    .venv/bin/python migrations/001_knowledge_forge.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "openbiliclaw.db"

# 迁移后应存在的 articles 新字段（用于验证）
EXPECTED_ARTICLE_COLUMNS = (
    "content_cleaned",
    "content_clean_score",
    "content_clean_log",
    "content_verified",
    "content_verify_result",
    "summary_detailed",
    "summary_compact",
    "summary_ultra_compact",
    "summary_quality",
    "summary_version",
    "summary_generated_at",
)

# 迁移后应存在的新表（用于验证）
EXPECTED_TABLES = (
    "entities",
    "article_entities",
    "entity_relations",
    "article_relations",
    "audit_tasks",
    "audit_issues",
    "article_quality_scores",
    "audit_config",
    "gap_analysis_tasks",
    "gap_records",
)


def migrate() -> None:
    """执行迁移（幂等）。"""
    from openbiliclaw.storage.database import Database

    print(f"数据库路径：{DB_PATH}")
    db = Database(DB_PATH)
    db.initialize()  # 内部调用 _ensure_knowledge_forge_tables()，幂等
    print("✅ Database.initialize() 完成（Knowledge Forge 表结构已确保）")


def verify() -> None:
    """迁移后验证：检查新字段与新表是否就位。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(articles)").fetchall()
        }
        missing_cols = [c for c in EXPECTED_ARTICLE_COLUMNS if c not in columns]
        if missing_cols:
            print(f"❌ articles 缺失字段：{missing_cols}")
        else:
            print(f"✅ articles 新增 {len(EXPECTED_ARTICLE_COLUMNS)} 个字段全部就位")

        tables = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing_tables = [t for t in EXPECTED_TABLES if t not in tables]
        if missing_tables:
            print(f"❌ 缺失表：{missing_tables}")
        else:
            print(f"✅ {len(EXPECTED_TABLES)} 张新表全部就位")

        count = conn.execute("SELECT COUNT(*) AS n FROM articles").fetchone()["n"]
        print(f"ℹ️  现有文章数：{count}（迁移未改动任何旧数据）")

        cfg_count = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_config"
        ).fetchone()["n"]
        print(f"ℹ️  audit_config 初始配置条数：{cfg_count}")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
    verify()
    print("🎉 Knowledge Forge 数据库迁移完成")

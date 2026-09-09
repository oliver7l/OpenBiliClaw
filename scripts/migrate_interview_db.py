#!/usr/bin/env python3
"""迁移：面试复盘拆分为独立子库 interview.db。

背景
----
interview_reviews（+ FTS5 全文索引）从主库 openbiliclaw.db 拆出到独立文件
interview.db，使面试复盘的转录长文本与 FTS 写入独立锁域，避免阻塞主库。

执行内容
--------
1. 确保 interview.db 存在并按与主库一致的 schema 建表
   （interview_reviews + FTS5 虚拟表 + 触发器）
2. 从主库把 interview_reviews 历史数据迁入 interview.db（保留 id 幂等）
3. 用 FTS rebuild 从主表重建 interview_reviews_fts 全文索引
4. 校验行数与主库一致
5. 主库旧表重命名为 _deprecated_interview_reviews（保留备份，可 DROP 清理）

幂等：interview.db 行数 >= 主库时跳过（基于 id 去重），可重复执行。
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

# 与 review_store.SCHEMA / FTS_SCHEMA / FTS_TRIGGERS 保持一致
SCHEMA = """
CREATE TABLE IF NOT EXISTS interview_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    position TEXT NOT NULL,
    interview_date TEXT NOT NULL,
    round TEXT NOT NULL DEFAULT 'first',
    result TEXT NOT NULL DEFAULT 'pending',
    duration_min INTEGER DEFAULT 0,
    transcript_text TEXT DEFAULT '',
    transcript_path TEXT DEFAULT '',
    audio_path TEXT DEFAULT '',
    ai_evaluation TEXT DEFAULT '',
    key_questions TEXT DEFAULT '',
    self_assessment TEXT DEFAULT '',
    emotional_review TEXT DEFAULT '',
    technical_review TEXT DEFAULT '',
    action_items TEXT DEFAULT '',
    emotion_level TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_review_company ON interview_reviews(company);
CREATE INDEX IF NOT EXISTS idx_review_date ON interview_reviews(interview_date);
CREATE INDEX IF NOT EXISTS idx_review_result ON interview_reviews(result);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS interview_reviews_fts USING fts5(
    company,
    position,
    key_questions,
    self_assessment,
    emotional_review,
    technical_review,
    action_items,
    ai_evaluation,
    notes,
    content='interview_reviews',
    content_rowid='id',
    tokenize='trigram'
);
"""

FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS review_fts_ai AFTER INSERT ON interview_reviews BEGIN
    INSERT INTO interview_reviews_fts(rowid, company, position, key_questions,
        self_assessment, emotional_review, technical_review, action_items,
        ai_evaluation, notes)
    VALUES (new.id, new.company, new.position, new.key_questions,
        new.self_assessment, new.emotional_review, new.technical_review,
        new.action_items, new.ai_evaluation, new.notes);
END;

CREATE TRIGGER IF NOT EXISTS review_fts_ad AFTER DELETE ON interview_reviews BEGIN
    INSERT INTO interview_reviews_fts(interview_reviews_fts, rowid, company, position,
        key_questions, self_assessment, emotional_review, technical_review,
        action_items, ai_evaluation, notes)
    VALUES ('delete', old.id, old.company, old.position, old.key_questions,
        old.self_assessment, old.emotional_review, old.technical_review,
        old.action_items, old.ai_evaluation, old.notes);
END;

CREATE TRIGGER IF NOT EXISTS review_fts_au AFTER UPDATE ON interview_reviews BEGIN
    INSERT INTO interview_reviews_fts(interview_reviews_fts, rowid, company, position,
        key_questions, self_assessment, emotional_review, technical_review,
        action_items, ai_evaluation, notes)
    VALUES ('delete', old.id, old.company, old.position, old.key_questions,
        old.self_assessment, old.emotional_review, old.technical_review,
        old.action_items, old.ai_evaluation, old.notes);
    INSERT INTO interview_reviews_fts(rowid, company, position, key_questions,
        self_assessment, emotional_review, technical_review, action_items,
        ai_evaluation, notes)
    VALUES (new.id, new.company, new.position, new.key_questions,
        new.self_assessment, new.emotional_review, new.technical_review,
        new.action_items, new.ai_evaluation, new.notes);
END;
"""


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def main() -> int:
    data_dir = Path(__file__).resolve().parent.parent / "data"
    main_db = data_dir / "openbiliclaw.db"
    interview_db = data_dir / "interview.db"
    backup_db = data_dir / "openbiliclaw.db.bak-pre-interview"

    if not main_db.exists():
        print(f"主库不存在: {main_db}")
        return 1

    # 1. 备份主库（一次性快照，迁移前必须）
    if not backup_db.exists():
        print(f"备份主库 → {backup_db.name} ...")
        shutil.copy2(main_db, backup_db)
        for suffix in ("-wal", "-shm"):
            side = Path(str(main_db) + suffix)
            if side.exists():
                shutil.copy2(side, Path(str(backup_db) + suffix))
        print("  备份完成。")
    else:
        print(f"已存在备份 {backup_db.name}，跳过备份。")

    src = sqlite3.connect(str(main_db), timeout=60.0)
    dst = sqlite3.connect(str(interview_db), timeout=60.0)
    try:
        # 2. 目标库建表（schema + FTS + 触发器）
        dst.executescript(SCHEMA)
        dst.executescript(FTS_SCHEMA)
        # 触发器放在 FTS_SCHEMA 之后、数据迁移之前建，迁移行数会用触发器顺带索引
        # （但外部内容表 FTS 需要显式 rebuild 才能从已有主表数据建索引）
        dst.executescript(FTS_TRIGGERS)
        dst.commit()
        print("  已确保 interview.db schema（含 FTS5 + 触发器）就绪。")

        # 3. 迁移数据（保留 id、幂等）
        src_count = _count(src, "interview_reviews")
        dst_count = _count(dst, "interview_reviews")
        if dst_count >= src_count:
            print(f"  interview.db 已 {dst_count} 行 >= 主库 {src_count} 行，跳过数据迁移（幂等）。")
        else:
            cols = [r[1] for r in dst.execute("PRAGMA table_info(interview_reviews)").fetchall()]
            sql = (
                f"INSERT OR IGNORE INTO interview_reviews ({', '.join(cols)}) "
                f"SELECT {', '.join(cols)} FROM src.interview_reviews"
            )
            dst.execute("ATTACH DATABASE ? AS src", (str(main_db),))
            dst.execute(sql)
            dst.commit()          # 先提交 INSERT，释放对 src 的读锁
            dst.execute("DETACH DATABASE src")
            dst.commit()
            migrated = _count(dst, "interview_reviews")
            print(f"  迁移完成：主库 {src_count} 行 → interview.db {migrated} 行")

        # 4. 重建 FTS 索引（外部内容表需显式 rebuild 才能索引已迁移的数据）
        dst.execute("INSERT INTO interview_reviews_fts(interview_reviews_fts) VALUES('rebuild')")
        dst.commit()
        fts_count = _count(dst, "interview_reviews_fts")
        print(f"  FTS 重建完成：interview_reviews_fts {fts_count} 行索引。")

        # 5. 校验行数一致
        s, d = _count(src, "interview_reviews"), _count(dst, "interview_reviews")
        match = s == d
        print(f"  校验：主库 {s} / interview.db {d} {'✓' if match else '✗'}")
        print("-" * 60)
        if not match:
            print("数据不一致，请勿使用新库。可重复运行本脚本幂等重迁。")
            return 1

        # 6. 主库旧表重命名为 _deprecated_（保留备份）
        existing = {
            r[0] for r in src.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "interview_reviews" in existing and "_deprecated_interview_reviews" not in existing:
            src.executescript("""
                DROP TRIGGER IF EXISTS review_fts_ai;
                DROP TRIGGER IF EXISTS review_fts_ad;
                DROP TRIGGER IF EXISTS review_fts_au;
                DROP TABLE IF EXISTS interview_reviews_fts;
                ALTER TABLE interview_reviews RENAME TO _deprecated_interview_reviews;
            """)
            src.commit()
            print("  主库旧表已重命名为 _deprecated_interview_reviews（含副表/触发器已清理）。")
        elif "_deprecated_interview_reviews" in existing:
            print("  主库旧表已为 _deprecated_interview_reviews，跳过。")
        else:
            print("  主库无 interview_reviews 表，跳过重命名。")

        print("迁移完成。面试复盘现已独立于 data/interview.db。")
        print("确认无误后可手动 DROP 主库 _deprecated_interview_reviews 彻底清理。")
        return 0
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    sys.exit(main())
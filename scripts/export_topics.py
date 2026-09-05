#!/usr/bin/env python3
"""把数据库中的专题内容导出为结构化文件目录。

用法:
    python scripts/export_topics.py                     # 导出全部 active 专题
    python scripts/export_topics.py --slug advertising  # 只导出指定专题
    python scripts/export_topics.py --output data/topics  # 指定输出目录
    python scripts/export_topics.py --no-content        # 只导出元数据，不导出正文
    python scripts/export_topics.py --no-fusion         # 不做跨平台融合分析
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))
sys.path.insert(0, str(_PROJECT_ROOT))

from openbiliclaw.storage.database import Database  # noqa: E402
from openbiliclaw.topics.exporter import (  # noqa: E402
    TopicItem,
    compute_content_hash,
    ensure_content_hash_column,
    export_topic_to_files,
)


def load_topic_items(conn: Any, topic_id: int) -> list[TopicItem]:
    """从数据库加载专题的所有内容。"""
    rows = conn.execute(
        """SELECT ti.content_key, ti.title, ti.url, ti.source_platform,
                  ti.source_name, ti.summary, ti.topic_label, ti.collected_at,
                  a.content_text
           FROM topic_items ti
           LEFT JOIN articles a ON a.url = ti.url
           WHERE ti.topic_id = ?
           ORDER BY ti.collected_at DESC""",
        (topic_id,),
    ).fetchall()
    items = []
    for row in rows:
        items.append(
            TopicItem(
                content_key=row[0] or "",
                title=row[1] or "",
                url=row[2] or "",
                source_platform=row[3] or "",
                source_name=row[4] or "",
                summary=row[5] or "",
                content_text=row[8] or "",
                topic_label=row[6] or "",
                collected_at=row[7] or "",
            )
        )
    return items


def backfill_content_hashes(conn: Any) -> int:
    """为已有 articles 回填 content_hash。返回更新的行数。"""
    rows = conn.execute(
        "SELECT id, content_text FROM articles WHERE content_hash = '' OR content_hash IS NULL"
    ).fetchall()
    updated = 0
    for row in rows:
        article_id, content_text = row
        if content_text:
            h = compute_content_hash(content_text)
            conn.execute("UPDATE articles SET content_hash = ? WHERE id = ?", (h, article_id))
            updated += 1
    conn.commit()
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="导出专题内容为结构化文件")
    parser.add_argument("--slug", help="只导出指定专题的 slug")
    parser.add_argument("--output", default="data/topics", help="输出目录（默认 data/topics）")
    parser.add_argument("--no-content", action="store_true", help="不导出正文内容")
    parser.add_argument("--no-fusion", action="store_true", help="不做跨平台融合分析")
    parser.add_argument("--backfill-hashes", action="store_true", help="为已有 articles 回填 content_hash")
    args = parser.parse_args()

    db = Database(db_path=str(_PROJECT_ROOT / "data" / "openbiliclaw.db"))
    db.initialize()
    conn = db.conn
    output_root = Path(args.output)

    # 确保 content_hash 列存在
    ensure_content_hash_column(conn)

    # 可选：回填已有内容的指纹
    if args.backfill_hashes:
        print("正在为已有 articles 回填 content_hash...")
        updated = backfill_content_hashes(conn)
        print(f"已回填 {updated} 条内容的指纹")

    # 加载专题列表
    if args.slug:
        topics = conn.execute(
            "SELECT * FROM topics WHERE slug = ? AND status = 'active'", (args.slug,)
        ).fetchall()
    else:
        topics = conn.execute(
            "SELECT * FROM topics WHERE status = 'active' ORDER BY id"
        ).fetchall()

    if not topics:
        print("没有找到 active 状态的专题")
        return

    print(f"找到 {len(topics)} 个专题，开始导出到 {output_root}/")
    print("=" * 60)

    # 获取 topics 表的列名
    topic_cols = [desc[1] for desc in conn.execute("PRAGMA table_info(topics)").fetchall()]

    total_exported = 0
    for topic_row in topics:
        # 把 row 转为 dict
        topic = dict(zip(topic_cols, topic_row, strict=False))

        # 解析 keywords JSON
        if isinstance(topic.get("keywords"), str):
            try:
                topic["keywords"] = json.loads(topic["keywords"])
            except Exception:
                topic["keywords"] = []

        slug = topic["slug"]
        name = topic["name"]
        items = load_topic_items(conn, topic["id"])

        print(f"\n📚 专题: {name} ({slug})")
        print(f"   内容数: {len(items)}")

        stats = export_topic_to_files(
            topic,
            items,
            output_root,
            include_content=not args.no_content,
            run_fusion=not args.no_fusion,
        )

        print(f"   导出正文: {stats['exported_items']} 条")
        print(f"   跳过空内容: {stats['skipped_empty']} 条")
        print(f"   平台分布: {stats['platforms']}")
        total_exported += stats["exported_items"]

    print("\n" + "=" * 60)
    print(f"✅ 导出完成！共导出 {total_exported} 条内容到 {output_root}/")
    print(f"   主索引: {output_root}/INDEX.md")
    print(f"   AI 读取指南: {output_root}/AGENTS.md")


if __name__ == "__main__":
    main()

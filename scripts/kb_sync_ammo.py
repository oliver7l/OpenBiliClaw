#!/usr/bin/env python3
"""求职弹药同步：03_岗位弹药库/*.md → data/interview.db.ammo_doc（幂等）。

架构定位（见 docs/plans/求职资料库三层架构重构.md 期 2 步骤 2）：

- ``03_岗位弹药库`` 是 **L3 应用层弹药源**（人读 md，唯一事实源）。
- ``data/interview.db.ammo_doc`` 是它的 **派生索引**（DB，供检索/前端消费）。
- 本脚本替换已归档的一次性脚本 ``_archive_oneoffs/kb_layers.py`` 中的 ammo 部分，
  成为可重入、幂等的同步器：

  * 扫描 ``03_岗位弹药库/**/*.md``
  * 以 ``rel_path``（相对 ``求职知识库/``）为唯一键 upsert
  * 同步重建 ``ammo_fts``（standalone fts5，需手动维护，rowid 对齐 ammo_doc.id）

用法
----
    python scripts/kb_sync_ammo.py            # 默认 dry-run 预览
    python scripts/kb_sync_ammo.py --apply    # 写入（upsert + 重建 FTS）
    python scripts/kb_sync_ammo.py --apply --prune  # 额外删除「磁盘已不存在」对应的行

幂等性：重复运行结果稳定（同输入 → 同 ammo_doc 集合）；不传 --prune 时绝不删除行。
"""
from __future__ import annotations

import re
import sys
import sqlite3
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KB_ROOT = PROJECT_ROOT / "求职知识库"          # rel_path 以此为基准
AMMO_ROOT = KB_ROOT / "03_岗位弹药库"
INTERVIEW_DB = PROJECT_ROOT / "data" / "interview.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ammo_doc(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT, kind TEXT, rel_path TEXT UNIQUE, title TEXT,
    content TEXT, char_count INTEGER, mtime REAL, updated_at TEXT
);
"""


def derive_company_kind(rel_parts: tuple[str, ...]) -> tuple[str, str]:
    """从相对路径推导 company / kind。

    ``rel_parts`` 形如 ``('03_岗位弹药库', '<面试准备目录>', ['<子目录>'], '<文件>')``。
    company 取面试准备目录名，去掉尾部 '-面试准备' 与日期后缀；
    kind 取子目录名（去数字前缀），无子目录则为 '根目录'。
    """
    interview_dir = rel_parts[1] if len(rel_parts) > 1 else ""
    company = interview_dir
    if company.endswith("-面试准备"):
        company = company[: -len("-面试准备")]
    company = re.sub(r"-\d{4}-\d{2}-\d{2}.*$", "", company)
    if len(rel_parts) >= 3:
        kind = re.sub(r"^\d+_", "", rel_parts[2])
    else:
        kind = "根目录"
    return (company or "通用"), kind


def main() -> int:
    apply = "--apply" in sys.argv
    prune = "--prune" in sys.argv

    conn = sqlite3.connect(str(INTERVIEW_DB), timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    conn.commit()

    files = sorted(AMMO_ROOT.rglob("*.md"))
    print(f"扫描到 {len(files)} 个 md（基准：{AMMO_ROOT.relative_to(PROJECT_ROOT)}）")

    seen: set[str] = set()
    upserted = 0
    for p in files:
        rel = str(p.relative_to(KB_ROOT))          # 03_岗位弹药库/...
        seen.add(rel)
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            print(f"  [跳过] 编码错误: {rel}")
            continue
        first = next((ln for ln in text.splitlines() if ln.strip()), "")
        title = first.lstrip("#").strip() or p.stem
        company, kind = derive_company_kind(p.relative_to(KB_ROOT).parts)
        if apply:
            conn.execute(
                """INSERT INTO ammo_doc(company,kind,rel_path,title,content,char_count,mtime,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(rel_path) DO UPDATE SET
                     company=excluded.company, kind=excluded.kind, title=excluded.title,
                     content=excluded.content, char_count=excluded.char_count,
                     mtime=excluded.mtime, updated_at=excluded.updated_at""",
                (company, kind, rel, title, text, len(text), p.stat().st_mtime,
                 datetime.now().isoformat()),
            )
        upserted += 1

    if apply and prune:
        for (rp,) in conn.execute("SELECT rel_path FROM ammo_doc"):
            if rp not in seen:
                conn.execute("DELETE FROM ammo_doc WHERE rel_path=?", (rp,))

    if apply:
        conn.commit()
        # 重建 standalone FTS；显式 rowid 对齐 ammo_doc.id（无 external-content / 无 trigger）
        conn.execute("DELETE FROM ammo_fts")
        conn.execute(
            "INSERT INTO ammo_fts(rowid, company, title, content) "
            "SELECT id, company, title, content FROM ammo_doc"
        )
        conn.commit()
        final = conn.execute("SELECT COUNT(*) FROM ammo_doc").fetchone()[0]
        fts = conn.execute("SELECT COUNT(*) FROM ammo_fts").fetchone()[0]
        print(f"同步完成：{upserted} 文件 upsert；ammo_doc 现 {final} 行；ammo_fts {fts} 行已重建")
    else:
        print("[DRY-RUN] 加 --apply 写入；加 --prune 删除磁盘已不存在文件对应的行")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

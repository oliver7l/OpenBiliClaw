"""数据库收尾：先备份主库，再移除主库旧表完成 db sharding 迁移。

阶段一（本脚本前半）：用 SQLite online backup API 对主库做一致性备份。
阶段二：校验各子库确有权威数据后，DROP 主库上 7 张旧表——
  这些表已迁移到对应子库（llm.db / events.db / pool.db / content.db），
  主库旧表只是双写验证期的冗余副本，权威数据在子库。
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

DATA = Path(__file__).resolve().parent.parent / "data"

# (主库表名, 权威子库文件, 子库表名)
REPLACEMENTS: list[tuple[str, str, str]] = [
    ("llm_usage", "llm.db", "llm_usage"),
    ("events", "events.db", "events"),
    ("view_history", "events.db", "view_history"),
    # 推荐流四表：裸名 SQL 将落到 pool.db（主库连接 ATTACH pool）
    ("content_cache", "pool.db", "content_cache"),
    ("recommendations", "pool.db", "recommendations"),
    ("user_feedback", "pool.db", "user_feedback"),
    ("xhs_observed_urls", "pool.db", "xhs_observed_urls"),
]


def main_db() -> Path:
    return DATA / "openbiliclaw.db"


def backup_main() -> Path:
    """用 online backup API 做一致性备份，避免 WAL 未合并的不一致。"""
    backups_dir = DATA / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backups_dir / f"openbiliclaw_pre_finalize_{stamp}.db"

    src = sqlite3.connect(f"file:{main_db()}?mode=ro", uri=True)
    dst = sqlite3.connect(dest)
    src.backup(dst)
    dst.commit()
    src.close()
    dst.close()

    # 校验备份可读且表存在
    check = sqlite3.connect(dest)
    n = check.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    check.close()
    print(f"[backup] 主库已备份 → {dest} (表数量 {n})")
    return dest


def row_count(db: Path, table: str) -> int:
    con = sqlite3.connect(db)
    try:
        return int(con.execute(f"SELECT COUNT(*) FROM \"{table}\"").fetchone()[0])
    except sqlite3.Error:
        return -1
    finally:
        con.close()


def verify_authority() -> bool:
    """校验每个被迁移表在权威子库中有数据，才允许 DROP 主库旧表。"""
    ok = True
    for main_tbl, sub_db, sub_tbl in REPLACEMENTS:
        sub_path = DATA / sub_db
        if not sub_path.exists():
            print(f"[verify] ✗ 子库缺失 {sub_db}（{main_tbl} 无法迁移）—— 跳过该表")
            ok = False
            continue
        sub_cnt = row_count(sub_path, sub_tbl)
        main_cnt = row_count(main_db(), main_tbl)
        if sub_cnt < 0:
            print(f"[verify] ✗ 子库 {sub_db}.{sub_tbl} 不存在或不可读")
            ok = False
            continue
        if main_cnt < 0:
            # 主库表已不存在：视为已迁移，跳过 DROP
            print(f"[verify] → 主库 {main_tbl} 已不存在（视为已迁移）")
            continue
        if sub_cnt == 0 and main_cnt > 0:
            print(f"[verify] ✗ 子库 {sub_db}.{sub_tbl} 为 0 行但主库 {main_tbl} 有 {main_cnt} 行——禁止 DROP")
            ok = False
        else:
            print(f"[verify] ✓ {main_tbl}: main={main_cnt} → {sub_db}.{sub_tbl}={sub_cnt}")
    return ok


def do_drop() -> None:
    con = sqlite3.connect(main_db())
    dropped = []
    for main_tbl, _sub_db, _sub_tbl in REPLACEMENTS:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (main_tbl,),
        ).fetchone()
        if not exists:
            continue
        con.execute(f"DROP TABLE \"{main_tbl}\"")
        dropped.append(main_tbl)
    con.commit()
    print(f"[drop] 已 DROP 主库表: {dropped}")
    con.close()


def cleanup_temp_backups() -> None:
    """清理已无意义的 pre-events / pre-pool 中间备份（权威已到子库）。"""
    for name in [
        "openbiliclaw.db.bak-pre-events",
        "openbiliclaw.db.bak-pre-events-shm",
        "openbiliclaw.db.bak-pre-events-wal",
        "openbiliclaw.db.bak-pre-pool",
        "openbiliclaw.db.bak-pre-pool-shm",
        "openbiliclaw.db.bak-pre-pool-wal",
    ]:
        p = DATA / name
        if p.exists():
            p.unlink()
            print(f"[cleanup] 删除中间备份 {name}")


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "drop"

    main_path = main_db()
    if not main_path.exists():
        print(f"[fatal] 主库不存在: {main_path}")
        sys.exit(1)

    if action in ("backup", "all"):
        backup_main()
        if action == "backup":
            return

    if action in ("drop", "all"):
        if not verify_authority():
            print("\n[abort] 校验未通过，未执行 DROP。")
            sys.exit(2)
        do_drop()

    if action == "all":
        con = sqlite3.connect(main_path)
        n = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        con.close()
        print(f"\n[final] 主库剩余表数量: {n}")


if __name__ == "__main__":
    main()
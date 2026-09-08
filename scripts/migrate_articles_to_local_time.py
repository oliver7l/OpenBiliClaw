#!/usr/bin/env python3
"""一次性迁移 v2: articles 表时间字段从 UTC 统一为北京时间(UTC+8)字符串。

规则:
  1. 'YYYY-MM-DD HH:MM:SS'(19字符) 字面量: 视为 UTC, SQL 内直接 +8h
  2. RFC822 带时区串(GMT/+0800 等): Python 解析后转北京时间字符串
  3. 其余(NULL/空/无法解析): 不动, 打印统计

安全:
  - 前置备份 data/backups/openbiliclaw_*.db(若今天已备份则复用)
  - 分批 commit(每 1 万行), 中断不丢已提交进度
  - _schema_meta 标记防二次 +8h
"""
import datetime
import email.utils
import glob
import os
import shutil
import sqlite3
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "data", "openbiliclaw.db")
BACKUP_DIR = os.path.join(BASE, "data", "backups")
CN_TZ = datetime.timezone(datetime.timedelta(hours=8))
FMT = "%Y-%m-%d %H:%M:%S"
MARK_KEY = "articles_time_localized_20260908"

UTC_LIT = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9]"


def ensure_backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    todays = sorted(glob.glob(os.path.join(BACKUP_DIR, "openbiliclaw_20260908_*.db")))
    if todays:
        print(f"[备份] 复用今日已有备份: {todays[-1]}")
        return
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(BACKUP_DIR, f"openbiliclaw_{stamp}.db")
    shutil.copy2(DB, backup)
    print(f"[备份] {backup} ({os.path.getsize(backup)/1024/1024:.0f} MB)")


def main():
    if not os.path.exists(DB):
        sys.exit(f"[错误] 找不到库: {DB}")

    conn = sqlite3.connect(DB, timeout=30)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE name='_schema_meta'")
    if cur.fetchone():
        cur.execute("SELECT 1 FROM _schema_meta WHERE key=?", (MARK_KEY,))
        if cur.fetchone():
            print("[跳过] 已迁移过, 防止二次 +8h。")
            conn.close()
            return
    conn.close()

    ensure_backup()

    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    # ---- 1. 19字符 UTC 字面量: SQL 内直接 +8h, 按主键区间分批 ----
    lo = cur.execute("SELECT MIN(id), MAX(id) FROM articles").fetchone()
    start, end = lo
    step = 10000
    total_upd = 0
    while start <= end:
        hi = start + step - 1
        n = 0
        for col in ("created_at", "updated_at", "published_at"):
            cur.execute(
                f"""UPDATE articles SET {col} = strftime('%Y-%m-%d %H:%M:%S', {col}, '+8 hours')
                    WHERE id BETWEEN ? AND ?
                      AND length({col}) = 19
                      AND {col} GLOB '{UTC_LIT}'
                      AND {col} >= '2020-01-01 00:00:00'""",
                (start, hi),
            )
            n += cur.rowcount
        conn.commit()
        total_upd += n
        print(f"  id {start}-{hi}: +8h 更新 {n} 行 (累计 {total_upd})", flush=True)
        start = hi + 1

    # ---- 2. RFC822 带时区串 ----
    rows = cur.execute(
        "SELECT id, published_at, updated_at, created_at FROM articles "
        "WHERE published_at LIKE '%GMT%' OR published_at LIKE '%UTC%' "
        "   OR published_at LIKE '%+0800%' OR published_at LIKE '%+0000%'"
    ).fetchall()
    pub_upd = upd_upd = cre_upd = 0
    batch = []
    for rid, pub, upd, cre in rows:
        for col, val in (("published_at", pub), ("updated_at", upd), ("created_at", cre)):
            s = (val or "").strip()
            if not s:
                continue
            try:
                dt = email.utils.parsedate_to_datetime(s)
                if dt is not None:
                    batch.append(
                        (col, dt.astimezone(CN_TZ).strftime(FMT), rid, val)
                    )
            except Exception:  # noqa: BLE001
                pass
    counts = {"published_at": 0, "updated_at": 0, "created_at": 0}
    for col, new, rid, old in batch:
        if new != old:
            cur.execute(f"UPDATE articles SET {col}=? WHERE id=? AND {col}=?", (new, rid, old))
            counts[col] += cur.rowcount
    conn.commit()
    print(f"[RFC822] published_at {counts['published_at']} / updated_at {counts['updated_at']} / created_at {counts['created_at']} 行已转北京时间")
    print(f"[合计] 字面量 +8h 共 {total_upd} 行, RFC822 转换 共 {sum(counts.values())} 行")

    # ---- 3. 写标记 ----
    cur.execute("CREATE TABLE IF NOT EXISTS _schema_meta (key TEXT PRIMARY KEY, value TEXT)")
    cur.execute(
        "INSERT OR REPLACE INTO _schema_meta (key, value) VALUES (?, ?)",
        (MARK_KEY, datetime.datetime.now(CN_TZ).strftime(FMT)),
    )
    conn.commit()
    conn.close()
    print("[完成] articles 表时间字段已统一为北京时间(UTC+8)。")


if __name__ == "__main__":
    main()
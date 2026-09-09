import sqlite3
import os

def conn(db):
    p = os.path.join("data", db)
    if not os.path.exists(p):
        return None
    return sqlite3.connect(p)


def q(c, sql, args=()):
    try:
        return c.execute(sql, args).fetchall()
    except Exception as e:  # noqa: BLE001
        return [("ERR", str(e))]


def last_ts(c, table, ts_col="timestamp"):
    r = q(c, f"SELECT MAX({ts_col}) FROM {table}")
    return r[0][0] if r else None


# 关键表：主库 vs 子库的行数 + 最近时间
checks = [
    ("llm_usage", "llm.db", "llm_usage"),
    ("events", "events.db", "events"),
    ("view_history", "events.db", "view_history"),
    ("articles", "content.db", "articles"),
    ("content_cache", "pool.db", "content_cache"),
    ("recommendations", "pool.db", "recommendations"),
    ("user_feedback", "pool.db", "user_feedback"),
    ("discovery_keywords", "discovery.db", "discovery_keywords"),
    ("discovery_candidates", "discovery.db", "discovery_candidates"),
    ("audit_issues", "knowledge_audit.db", "audit_issues"),
    ("gap_records", "knowledge_audit.db", "gap_records"),
    ("diary_entries", "diary.db", "diary_entries"),
]
main = conn("openbiliclaw.db")
print("== 主库(旧表) vs 子库: 行数 / 最近写入时间 ==")
for tbl, subdb, subtable in checks:
    sub = conn(subdb)
    mc = q(main, f"SELECT COUNT(*) FROM {tbl}")
    mt = last_ts(main, tbl)
    sc = q(sub, f"SELECT COUNT(*) FROM {subtable}") if sub else [("N/A",)]
    st = last_ts(sub, subtable) if sub else None
    print(f"{tbl:22s} main=({mc[0][0]}, last={mt})  {subdb}=({sc[0][0]}, last={st})")
    if sub:
        sub.close()

# 主库 vs pool.db 重复表
print("\n== 主库 vs pool.db 重复表对比 ==")
pool = conn("pool.db")
for t in ["content_cache", "recommendations", "user_feedback", "xhs_observed_urls"]:
    mc = q(main, f"SELECT COUNT(*) FROM {t}")
    pc = q(pool, f"SELECT COUNT(*) FROM {t}")
    print(f"{t:20s} main={mc[0][0]}  pool={pc[0][0]}")
pool.close()

# content.db 里到底有没有 推荐系？
print("\n== content.db 各表行数 ==")
content = conn("content.db")
for r in q(content, "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%fts%' ORDER BY name"):
    cnt = q(content, f"SELECT COUNT(*) FROM {r[0]}")
    print(f"  {r[0]:26s} {cnt[0][0]}")
content.close()
main.close()
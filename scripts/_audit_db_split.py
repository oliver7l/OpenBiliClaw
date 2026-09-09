import os
import sqlite3

DATA = "data"


def tables(db):
    p = os.path.join(DATA, db)
    if not os.path.exists(p):
        return []
    try:
        c = sqlite3.connect(p)
        r = [x[0] for x in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        c.close()
        return r
    except Exception as e:  # noqa: BLE001
        return ["ERR:" + str(e)]


main = tables("openbiliclaw.db")
main = [t for t in main if not t.startswith("ERR")]
print("[main] openbiliclaw.db 表数 =", len(main))

for sub in ["llm", "events", "content", "discovery", "knowledge_audit",
            "diary", "interview"]:
    t = [x for x in tables(f"{sub}.db") if not x.startswith("ERR")]
    print(f"[{sub}.db] 表数={len(t)} -> {t}")

should_move = {
    "P1_llm": ["llm_usage"],
    "P2_events": ["events", "view_history"],
    "P3_audit": ["audit_issues", "audit_tasks", "audit_config", "gap_records",
                 "gap_analysis_tasks"],
    "P4_content": ["articles", "article_quality_scores", "article_entities",
                   "article_relations", "article_snapshots", "article_tldrs",
                   "content_cache", "recommendations", "user_feedback",
                   "xhs_observed_urls", "read_archive", "reading_schedule",
                   "watch_later", "saved_items", "saved_memberships",
                   "saved_item_removals"],
    "P5_discovery": ["discovery_keywords", "discovery_candidates",
                     "discovery_keyword_yield", "discovery_planner_lock",
                     "v2ex_discovery_runs", "v2ex_discovery_state",
                     "youtube_discovery_runs", "reddit_discovery_runs",
                     "x_source_health", "x_creator_subscriptions",
                     "xhs_creator_subscriptions", "xhs_task_runtime_state"],
    "P6_diary": None,  # 动态：diary_ prefix
    "P7_health": None,  # 动态：health_ prefix
    "P8_knowledge": None,
}

print("\n==== 应迁出但仍留在主库的表 ====")
still_all = []
for ph, ts in should_move.items():
    if ts is None:
        continue
    still = [t for t in ts if t in main]
    still_all += still
    if still:
        print(f"  {ph}: {still}")

# 动态前缀检查
for prefix in ["diary_", "health_", "knowledge_", "entities", "entity_",
               "topics", "learning_paths", "insight_reports"]:
    hit = [t for t in main if t.startswith(prefix)]
    if hit:
        still_all += hit
        print(f"  P6/P7/P8 ({prefix}*): {hit}")

print("\n应迁出但仍留在主库的表总数 =", len(still_all))

# pool.db 状态
pool = [x for x in tables("pool.db") if not x.startswith("ERR")]
print("\n[pool.db] 表数=", len(pool), "->", pool)
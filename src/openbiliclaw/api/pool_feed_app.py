"""独立推荐流浏览服务（v0.3.193）。

与主 API（:8420）进程级隔离的极简推荐流服务（:8421）：

- 只读推荐流子库 ``data/pool.db``（``mode=ro``，与采集器/主库零锁交集）；
- 只提供 ``GET /api/pool/feed``：按 source 过滤 + 随机抽样，不经过
  count_pool_readiness / serve 引擎 / LLM 环节，毫秒级返回；
- 主 API 进程内的采集抓取、LLM 重排序、后台冷算等同步任务不再拖慢
  推荐流浏览——打开秒开、换一批秒换。

独立进程不依赖主 API 的任何注入，也不共享连接，符合"推荐流独立出来、
各模块不互相影响"的拆分目标。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

DB_PATH = "data/pool.db"

app = FastAPI(title="OpenBiliClaw Pool Feed API", version="0.3.193")

# 前端页面由主 API（:8420）服务，跨端口读取本服务，需放开 CORS。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _connect() -> sqlite3.Connection:
    """只读连接推荐流子库（WAL 下读不阻塞采集器写）。"""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _clean(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for k, v in d.items():
        if v is None:
            d[k] = 0.0 if k == "quality_score" else ""
    return d


@app.get("/api/pool/feed")
def pool_feed(
    source: str = Query(..., description="Feed source, e.g. xhs-feed."),
    limit: int = Query(default=40, ge=1, le=200, description="Max items to return."),
    shuffle: bool = Query(default=True, description="Randomize the result order."),
    status: str | None = Query(default=None, description="Filter by pool_status."),
) -> dict[str, Any]:
    where = ["source = ?"]
    params: list[Any] = [source]
    if status:
        where.append("pool_status = ?")
        params.append(status)
    where_sql = " AND ".join(where)

    conn = _connect()
    try:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM content_cache WHERE {where_sql}",
            params,
        ).fetchone()
        total = int(total_row["cnt"]) if total_row else 0

        order = "ORDER BY RANDOM()" if shuffle else "ORDER BY discovered_at DESC, bvid DESC"
        rows = conn.execute(
            "SELECT bvid, title, up_name, source_platform, content_type, "
            "cover_url, content_url, body_text, pool_status, quality_score, "
            "quality_reason, topic_group, pool_expression "
            f"FROM content_cache WHERE {where_sql} {order} LIMIT ?",
            params + [limit],
        ).fetchall()
    finally:
        conn.close()

    items = [_clean(r) for r in rows]
    return {"items": items, "total": total, "available": total, "raw": 0, "pending": 0}

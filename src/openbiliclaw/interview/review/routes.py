"""面试复盘记录（C · review）API 路由。

正规前缀 ``PREFIX = /api/interview/review``（期 2 URL 分区，注意由原 ``reviews``
单数化）；旧前缀 ``/api/interview/reviews`` 作为**双挂载别名**保留一版
（``include_in_schema=False``），由 ``mount_review_router`` 同时挂载两者。

Endpoints（下列为相对路径）：
- GET    /            — 列表（?company=&result=&limit=&offset=）
- GET    /{id}        — 详情
- POST   /            — 创建
- PATCH  /{id}        — 更新
- DELETE /{id}        — 删除
- GET    /search      — 全文检索（?q=）
- GET    /stats       — 统计
- GET    /companies   — 公司列表
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from openbiliclaw.interview._paths import PROJECT_ROOT
from openbiliclaw.interview.review.models import (
    InterviewReview,
    InterviewReviewCreate,
    InterviewReviewStats,
    InterviewReviewSummary,
    InterviewReviewUpdate,
)
from openbiliclaw.interview.review.service import InterviewReviewService

logger = logging.getLogger(__name__)

# 期 2 URL 分区：C 的正规前缀（原 reviews 单数化）；LEGACY_PREFIX 为别名前缀（双挂载）。
PREFIX = "/api/interview/review"
LEGACY_PREFIX = "/api/interview/reviews"


def _default_db_path() -> str:
    """默认数据库路径：data/interview.db（面试复盘子库，独立锁域）。"""
    project_root = PROJECT_ROOT
    try:
        from openbiliclaw.config import load_config

        settings = load_config()
        if settings.storage.interview_db_path:
            p = Path(settings.storage.interview_db_path)
            return str(p if p.is_absolute() else project_root / p)
    except Exception:  # noqa: BLE001 - 配置加载失败时回退默认路径
        pass
    return str(project_root / "data" / "interview.db")


def build_review_router(db_path: str | None = None) -> APIRouter:
    """创建面试复盘路由。"""
    # 注意：这里**不设 prefix**——由 mount_review_router 挂载时传入，
    # 以便同一份路由同时挂到新前缀与旧前缀（双挂载别名）。
    router = APIRouter(tags=["interview-review"])
    svc = InterviewReviewService(db_path or _default_db_path())

    @router.get("", response_model=list[InterviewReviewSummary])
    @router.get("/", response_model=list[InterviewReviewSummary], include_in_schema=False)
    def list_reviews(
        company: str | None = Query(None, description="按公司过滤"),
        result: str | None = Query(None, description="按结果过滤"),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> list[InterviewReviewSummary]:
        """列出面试复盘记录（摘要）。"""
        return svc.list_reviews(company=company, result=result, limit=limit, offset=offset)

    @router.get("/search", response_model=list[InterviewReviewSummary])
    def search_reviews(
        q: str = Query(..., min_length=1, description="检索关键词"),
        limit: int = Query(20, ge=1, le=100),
    ) -> list[InterviewReviewSummary]:
        """全文检索面试复盘记录。"""
        return svc.search(q, limit=limit)

    @router.get("/stats", response_model=InterviewReviewStats)
    def review_stats() -> InterviewReviewStats:
        """面试复盘统计。"""
        return svc.stats()

    @router.get("/companies", response_model=list[str])
    def review_companies() -> list[str]:
        """所有面试过的公司。"""
        return svc.get_companies()

    # ── 面试录音索引（必须定义在 /{review_id} 之前）────────────
    # interview_recordings 此前是「零引用死表」：库里有数据但全树无任何代码
    # 读写，导致转写卡在 transcribing 数天也无人发现。这里补上端点，
    # 并在列表里显式暴露 stuck_count 便于巡检。

    def _ensure_recordings_table(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS interview_recordings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company TEXT NOT NULL,
                round TEXT DEFAULT '',
                interview_date TEXT DEFAULT '',
                audio_path TEXT DEFAULT '',
                transcript_path TEXT DEFAULT '',
                duration_sec INTEGER DEFAULT 0,
                summary TEXT DEFAULT '',
                key_points TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
        """)

    @router.get("/recordings")
    def list_recordings(
        company: str | None = Query(None, description="按公司过滤"),
        status: str | None = Query(None, description="按状态过滤：pending/transcribing/completed"),
        limit: int = Query(100, ge=1, le=500),
    ) -> dict[str, Any]:
        """面试录音索引列表。

        ``stuck_count`` = status 不是 completed 的条数——这些是需要人工补转写
        的录音（历史上比亚迪 HR 面录音卡在 transcribing 五天无人发现，即因为
        这张表此前没有任何代码读取）。
        """
        conn = sqlite3.connect(_default_db_path())
        conn.row_factory = sqlite3.Row
        try:
            _ensure_recordings_table(conn)
            sql = "SELECT * FROM interview_recordings"
            conds: list[str] = []
            args: list[Any] = []
            if company:
                conds.append("company LIKE ?")
                args.append(f"%{company}%")
            if status:
                conds.append("status = ?")
                args.append(status)
            if conds:
                sql += " WHERE " + " AND ".join(conds)
            sql += " ORDER BY interview_date DESC, id DESC LIMIT ?"
            args.append(limit)
            rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()
        stuck = [r for r in rows if (r.get("status") or "") != "completed"]
        return {"total": len(rows), "stuck_count": len(stuck), "items": rows}

    @router.post("/recordings", status_code=201)
    def create_recording(payload: dict[str, Any]) -> dict[str, Any]:
        """登记一条面试录音（{company, round, interview_date, audio_path, ...}）。"""
        company = str(payload.get("company") or "").strip()
        if not company:
            raise HTTPException(status_code=422, detail="company 不能为空")
        conn = sqlite3.connect(_default_db_path())
        try:
            _ensure_recordings_table(conn)
            cur = conn.execute(
                "INSERT INTO interview_recordings"
                " (company, round, interview_date, audio_path, transcript_path,"
                "  duration_sec, summary, key_points, status, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    company,
                    str(payload.get("round") or "").strip(),
                    str(payload.get("interview_date") or "").strip(),
                    str(payload.get("audio_path") or "").strip(),
                    str(payload.get("transcript_path") or "").strip(),
                    int(payload.get("duration_sec") or 0),
                    str(payload.get("summary") or "").strip(),
                    str(payload.get("key_points") or "").strip(),
                    str(payload.get("status") or "pending").strip(),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            conn.commit()
            new_id = cur.lastrowid
        finally:
            conn.close()
        return {"ok": True, "id": new_id}

    @router.patch("/recordings/{recording_id}")
    def update_recording(recording_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """更新录音条目（常用于补 transcript_path 并把 status 置为 completed）。"""
        allowed = {
            "company", "round", "interview_date", "audio_path", "transcript_path",
            "duration_sec", "summary", "key_points", "status",
        }
        fields = {k: v for k, v in payload.items() if k in allowed}
        if not fields:
            raise HTTPException(status_code=422, detail=f"无有效字段，可选：{sorted(allowed)}")
        conn = sqlite3.connect(_default_db_path())
        try:
            _ensure_recordings_table(conn)
            exists = conn.execute(
                "SELECT id FROM interview_recordings WHERE id=?", (recording_id,)
            ).fetchone()
            if not exists:
                raise HTTPException(status_code=404, detail=f"录音 #{recording_id} 不存在")
            sets = ", ".join(f"{k}=?" for k in fields)
            conn.execute(
                f"UPDATE interview_recordings SET {sets} WHERE id=?",
                (*fields.values(), recording_id),
            )
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "id": recording_id, "updated": sorted(fields)}

    @router.get("/{review_id}", response_model=InterviewReview)
    def get_review(review_id: int) -> InterviewReview:
        """获取面试复盘详情。"""
        r = svc.get(review_id)
        if r is None:
            raise HTTPException(status_code=404, detail=f"面试复盘 #{review_id} 不存在")
        return r

    @router.post("", response_model=InterviewReview, status_code=201)
    @router.post("/", response_model=InterviewReview, status_code=201, include_in_schema=False)
    def create_review(payload: InterviewReviewCreate) -> InterviewReview:
        """创建面试复盘记录。"""
        return svc.create(payload)

    @router.patch("/{review_id}", response_model=InterviewReview)
    def update_review(
        review_id: int, payload: InterviewReviewUpdate
    ) -> InterviewReview:
        """更新面试复盘记录（部分更新）。"""
        r = svc.update(review_id, payload)
        if r is None:
            raise HTTPException(status_code=404, detail=f"面试复盘 #{review_id} 不存在")
        return r

    @router.delete("/{review_id}")
    def delete_review(review_id: int) -> dict[str, Any]:
        """删除面试复盘记录。"""
        if not svc.delete(review_id):
            raise HTTPException(status_code=404, detail=f"面试复盘 #{review_id} 不存在")
        return {"deleted": True, "id": review_id}

    return router


def mount_review_router(app: Any, db_path: str | None = None) -> APIRouter:
    """把 C（面试复盘）路由挂到新前缀 + 兼容旧前缀。

    双挂载别名：新前缀 ``/api/interview/review`` 进 OpenAPI；旧前缀
    ``/api/interview/reviews`` 以 ``include_in_schema=False`` 挂载（不做重定向，
    故 POST/PATCH/DELETE 全兼容）。卸载旧前缀时删掉第二行即可。
    """
    router = build_review_router(db_path=db_path)
    app.include_router(router, prefix=PREFIX)
    app.include_router(router, prefix=LEGACY_PREFIX, include_in_schema=False)
    return router


__all__ = ["build_review_router", "mount_review_router", "PREFIX", "LEGACY_PREFIX"]

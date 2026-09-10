"""面试题阅读追踪 API 路由。"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from openbiliclaw.interview.questions.models import MasteryLevel, QuestionCategory
from openbiliclaw.interview.questions.store import InterviewQuestionStore

router = APIRouter(prefix="/api/interview", tags=["interview"])

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "interview_questions.db"
INTERVIEW_DB_PATH = Path(__file__).resolve().parents[3] / "data" / "interview.db"
AMMO_DIR = Path(__file__).resolve().parents[3] / "求职知识库" / "03_岗位弹药库"


def _get_store() -> InterviewQuestionStore:
    return InterviewQuestionStore(DB_PATH)


class ReadRequest(BaseModel):
    mastery: str = "reading"
    notes: str = ""
    time_spent_min: int = 0


class PlanRequest(BaseModel):
    name: str = "秋招面试冲刺"
    daily_target: int = 5
    categories: str = ""
    min_difficulty: int = 1
    max_difficulty: int = 5


@router.get("/today")
def get_today() -> dict[str, Any]:
    """获取今日待读题目。"""
    store = _get_store()
    plan = store.get_active_plan()
    if not plan:
        plan = store.create_plan("秋招面试冲刺", daily_target=5)
    questions = store.get_today_queue(plan)
    result = []
    for q in questions:
        mastery = store.get_question_mastery(q.id)
        result.append({
            "id": q.id,
            "title": q.title,
            "answer": q.answer,
            "category": q.category.value,
            "difficulty": q.difficulty,
            "source": q.source,
            "tags": q.tags,
            "url": q.url,
            "notes": q.notes,
            "mastery": mastery.value,
        })
    return {
        "plan": {
            "id": plan.id,
            "name": plan.name,
            "daily_target": plan.daily_target,
            "start_date": plan.start_date.isoformat(),
        },
        "date": date.today().isoformat(),
        "questions": result,
    }


@router.get("/stats")
def get_stats() -> dict[str, Any]:
    """获取题库统计。"""
    store = _get_store()
    stats = store.stats()
    return {
        "total": stats.total,
        "by_category": stats.by_category,
        "by_difficulty": {str(k): v for k, v in stats.by_difficulty.items()},
        "by_mastery": stats.by_mastery,
        "not_started": stats.not_started,
        "reading": stats.reading,
        "understood": stats.understood,
        "mastered": stats.mastered,
        "need_review": stats.need_review,
        "mastery_rate": round((stats.mastered + stats.understood) / stats.total * 100, 1) if stats.total else 0,
    }


@router.get("/questions")
def list_questions(
    category: str | None = Query(None),
    difficulty_min: int | None = Query(None),
    difficulty_max: int | None = Query(None),
    source: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """列出题目。"""
    store = _get_store()
    cat = QuestionCategory(category) if category else None
    questions = store.list_questions(
        category=cat,
        difficulty_min=difficulty_min,
        difficulty_max=difficulty_max,
        source=source,
        limit=limit,
        offset=offset,
    )
    result = []
    for q in questions:
        mastery = store.get_question_mastery(q.id)
        result.append({
            "id": q.id,
            "title": q.title,
            "category": q.category.value,
            "difficulty": q.difficulty,
            "source": q.source,
            "tags": q.tags,
            "mastery": mastery.value,
        })
    return {"questions": result, "total": len(result)}


@router.get("/questions/{question_id}")
def get_question(question_id: int) -> dict[str, Any]:
    """获取题目详情。"""
    store = _get_store()
    q = store.get_question(question_id)
    if not q:
        raise HTTPException(status_code=404, detail="题目不存在")
    mastery = store.get_question_mastery(q.id)
    records = store.get_records(qid=q.id, limit=10)
    return {
        "id": q.id,
        "title": q.title,
        "answer": q.answer,
        "category": q.category.value,
        "difficulty": q.difficulty,
        "source": q.source,
        "tags": q.tags,
        "url": q.url,
        "notes": q.notes,
        "mastery": mastery.value,
        "records": [
            {
                "id": r.id,
                "read_date": r.read_date.isoformat(),
                "mastery": r.mastery.value,
                "review_count": r.review_count,
                "notes": r.notes,
                "time_spent_min": r.time_spent_min,
            }
            for r in records
        ],
    }


@router.post("/questions/{question_id}/read")
def mark_read(question_id: int, req: ReadRequest) -> dict[str, Any]:
    """标记已读。"""
    store = _get_store()
    try:
        mastery = MasteryLevel(req.mastery)
    except ValueError:
        mastery = MasteryLevel.READING
    record = store.mark_read(
        question_id,
        mastery=mastery,
        notes=req.notes,
        time_spent_min=req.time_spent_min,
    )
    q = store.get_question(question_id)
    return {
        "question_id": question_id,
        "title": q.title if q else "",
        "mastery": mastery.value,
        "review_count": record.review_count,
    }


@router.post("/questions/{question_id}/master")
def mark_master(question_id: int) -> dict[str, Any]:
    """标记掌握。"""
    store = _get_store()
    record = store.mark_read(question_id, mastery=MasteryLevel.MASTERED)
    q = store.get_question(question_id)
    return {
        "question_id": question_id,
        "title": q.title if q else "",
        "mastery": "mastered",
        "review_count": record.review_count,
    }


@router.post("/questions/{question_id}/review")
def mark_review(question_id: int) -> dict[str, Any]:
    """标记需要复习。"""
    store = _get_store()
    record = store.mark_read(question_id, mastery=MasteryLevel.NEED_REVIEW)
    q = store.get_question(question_id)
    return {
        "question_id": question_id,
        "title": q.title if q else "",
        "mastery": "need_review",
        "review_count": record.review_count,
    }


@router.get("/queue")
def get_queue() -> dict[str, Any]:
    """获取待看队列。"""
    store = _get_store()
    queue = store.get_queue(limit=100)
    result = []
    for q, priority, planned in queue:
        result.append({
            "id": q.id,
            "title": q.title,
            "category": q.category.value,
            "difficulty": q.difficulty,
            "source": q.source,
            "priority": priority.value,
            "planned_date": planned.isoformat() if planned else None,
        })
    return {"queue": result, "total": len(result)}


@router.get("/progress")
def get_progress(days: int = Query(7, ge=1, le=30)) -> dict[str, Any]:
    """获取阅读进度。"""
    store = _get_store()
    plan = store.get_active_plan()
    if not plan:
        return {"plan": None, "daily": [], "stats": None}
    daily = store.get_daily_progress(plan.id, days=days)
    stats = store.stats()
    return {
        "plan": {
            "id": plan.id,
            "name": plan.name,
            "daily_target": plan.daily_target,
            "start_date": plan.start_date.isoformat(),
        },
        "daily": [
            {
                "date": d.progress_date.isoformat(),
                "questions_read": d.questions_read,
                "questions_mastered": d.questions_mastered,
                "target": d.target,
            }
            for d in daily
        ],
        "stats": {
            "total": stats.total,
            "understood": stats.understood,
            "mastered": stats.mastered,
            "mastery_rate": round((stats.mastered + stats.understood) / stats.total * 100, 1) if stats.total else 0,
        },
    }


@router.post("/plan")
def create_plan(req: PlanRequest) -> dict[str, Any]:
    """创建阅读计划。"""
    store = _get_store()
    plan = store.create_plan(
        name=req.name,
        daily_target=req.daily_target,
        categories=req.categories,
        min_difficulty=req.min_difficulty,
        max_difficulty=req.max_difficulty,
    )
    return {
        "id": plan.id,
        "name": plan.name,
        "daily_target": plan.daily_target,
        "start_date": plan.start_date.isoformat(),
        "categories": plan.categories,
        "min_difficulty": plan.min_difficulty,
        "max_difficulty": plan.max_difficulty,
    }


# ── 面试安排（job 表）──────────────────────────────────────

def _get_interview_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(INTERVIEW_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


@router.get("/schedule")
def get_schedule() -> dict[str, Any]:
    """获取面试安排列表。"""
    conn = _get_interview_conn()
    try:
        rows = conn.execute(
            "SELECT company, role, interview_at, status, direction, prep_dir, resume_ver, note FROM job ORDER BY interview_at DESC"
        ).fetchall()
        jobs = []
        today = date.today().isoformat()
        for r in rows:
            interview_at = r["interview_at"] or ""
            # 判断是否为即将到来的面试（日期 >= 今天且状态为待面/进行中）
            is_upcoming = False
            if interview_at and r["status"] in ("待面", "进行中"):
                interview_date = interview_at.split()[0] if " " in interview_at else interview_at
                is_upcoming = interview_date >= today
            jobs.append({
                "company": r["company"],
                "role": r["role"],
                "interview_at": interview_at,
                "status": r["status"],
                "direction": r["direction"] or "",
                "prep_dir": r["prep_dir"] or "",
                "resume_ver": r["resume_ver"] or "",
                "note": r["note"] or "",
                "is_upcoming": is_upcoming,
            })
        upcoming = [j for j in jobs if j["is_upcoming"]]
        history = [j for j in jobs if not j["is_upcoming"]]
        return {
            "upcoming": upcoming,
            "history": history,
            "total": len(jobs),
            "upcoming_count": len(upcoming),
        }
    finally:
        conn.close()


@router.get("/reviews")
def get_reviews(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    """获取面试复盘记录。"""
    conn = _get_interview_conn()
    try:
        rows = conn.execute(
            """SELECT id, company, position, interview_date, round, result, duration_min,
                      emotion_level, tags, key_questions, self_assessment
               FROM interview_reviews ORDER BY interview_date DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        reviews = []
        for r in rows:
            reviews.append({
                "id": r["id"],
                "company": r["company"],
                "position": r["position"],
                "interview_date": r["interview_date"],
                "round": r["round"],
                "result": r["result"],
                "duration_min": r["duration_min"],
                "emotion_level": r["emotion_level"] or "",
                "tags": r["tags"] or "",
                "key_questions": r["key_questions"] or "",
                "self_assessment": r["self_assessment"] or "",
            })
        stats = conn.execute(
            "SELECT result, COUNT(*) as cnt FROM interview_reviews GROUP BY result"
        ).fetchall()
        return {
            "reviews": reviews,
            "stats": {s["result"]: s["cnt"] for s in stats},
            "total": len(reviews),
        }
    finally:
        conn.close()


# ── 岗位弹药库扫描 ─────────────────────────────────────────

def _scan_ammo_dir(company_dir: Path) -> dict[str, Any]:
    """扫描单个公司的弹药库目录。"""
    result = {
        "company": company_dir.name.replace("-面试准备", ""),
        "path": str(company_dir),
        "categories": {},
        "total_files": 0,
        "total_size": 0,
        "key_files": [],
    }
    category_map = {
        "01_岗位与公司信息": "岗位与公司信息",
        "02_面试备战资料": "面试备战资料",
        "03_速成包": "速成包",
    }
    for cat_dir_name, cat_label in category_map.items():
        cat_dir = company_dir / cat_dir_name
        if not cat_dir.exists():
            continue
        files = []
        for f in cat_dir.iterdir():
            if f.is_file() and not f.name.startswith("."):
                stat = f.stat()
                files.append({
                    "name": f.name,
                    "size": stat.st_size,
                    "size_kb": round(stat.st_size / 1024, 1),
                    "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d"),
                })
                result["total_files"] += 1
                result["total_size"] += stat.st_size
        files.sort(key=lambda x: x["mtime"], reverse=True)
        result["categories"][cat_label] = files
        # 收集关键文件（最新的3个）
        for f in files[:3]:
            result["key_files"].append({
                "category": cat_label,
                "name": f["name"],
                "mtime": f["mtime"],
                "size_kb": f["size_kb"],
            })
    # 通用模块索引
    index_file = company_dir / "04_通用模块索引.md"
    if index_file.exists():
        result["has_index"] = True
        result["index_mtime"] = datetime.fromtimestamp(index_file.stat().st_mtime).strftime("%Y-%m-%d")
    else:
        result["has_index"] = False
    result["total_size_mb"] = round(result["total_size"] / 1024 / 1024, 2)
    return result


@router.get("/ammo")
def get_ammo_library() -> dict[str, Any]:
    """获取岗位弹药库概览。"""
    if not AMMO_DIR.exists():
        return {"companies": [], "total_companies": 0}
    companies = []
    for d in AMMO_DIR.iterdir():
        if d.is_dir() and d.name.endswith("-面试准备"):
            companies.append(_scan_ammo_dir(d))
    companies.sort(key=lambda x: x["total_files"], reverse=True)
    return {
        "companies": companies,
        "total_companies": len(companies),
        "total_files": sum(c["total_files"] for c in companies),
        "total_size_mb": round(sum(c["total_size"] for c in companies) / 1024 / 1024, 2),
    }


@router.get("/ammo/{company}")
def get_company_ammo(company: str) -> dict[str, Any]:
    """获取单个公司的弹药库详情。"""
    company_dir = AMMO_DIR / f"{company}-面试准备"
    if not company_dir.exists():
        raise HTTPException(status_code=404, detail=f"未找到 {company} 的面试准备目录")
    return _scan_ammo_dir(company_dir)


def register_interview_routes(app: Any, ctx: Any) -> None:
    """注册面试题阅读追踪路由。"""
    app.include_router(router)

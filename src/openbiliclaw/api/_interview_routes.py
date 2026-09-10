"""面试题阅读追踪 API 路由。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from openbiliclaw.interview.questions.models import MasteryLevel, QuestionCategory
from openbiliclaw.interview.questions.store import InterviewQuestionStore

router = APIRouter(prefix="/api/interview", tags=["interview"])

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "interview_questions.db"


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


def register_interview_routes(app: Any, ctx: Any) -> None:
    """注册面试题阅读追踪路由。"""
    app.include_router(router)

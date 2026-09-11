"""面试题阅读追踪 API 路由。"""

from __future__ import annotations

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
            "SELECT company, role, interview_at, status, direction, prep_dir, resume_ver, note "
            "FROM job ORDER BY interview_at DESC"
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


@router.get("/ammo/reading")
def get_ammo_reading() -> dict[str, Any]:
    """获取全部弹药文件阅读状态（unread/reading/finished）。"""
    conn = sqlite3.connect(str(INTERVIEW_DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_ammo_reading_table(conn)
        rows = conn.execute(
            "SELECT company, category, name, status, updated_at FROM ammo_reading"
        ).fetchall()
        items = [
            {
                "company": r["company"],
                "category": r["category"],
                "name": r["name"],
                "status": r["status"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()
    return {"items": items}


@router.post("/ammo/reading")
def update_ammo_reading(payload: dict[str, Any]) -> dict[str, Any]:
    """更新单个弹药文件的阅读状态（upsert）。"""
    from datetime import datetime as _dt

    company = str(payload.get("company") or "").strip()
    category = str(payload.get("category") or "").strip()
    name = str(payload.get("name") or "").strip()
    status = str(payload.get("status") or "unread").strip()
    if not company or not category or not name:
        raise HTTPException(status_code=422, detail="company/category/name 均必填")
    if status not in {"unread", "reading", "finished"}:
        raise HTTPException(status_code=422, detail="status 仅支持 unread/reading/finished")
    now = _dt.now().isoformat(timespec="seconds")
    conn = sqlite3.connect(str(INTERVIEW_DB_PATH))
    try:
        _ensure_ammo_reading_table(conn)
        conn.execute(
            """INSERT INTO ammo_reading (company, category, name, status, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(company, category, name)
               DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at""",
            (company, category, name, status, now),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "company": company, "name": name, "status": status, "updated_at": now}


def _ensure_ammo_reading_table(conn: sqlite3.Connection) -> None:
    """确保弹药阅读状态表存在。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ammo_reading (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'unread',
            updated_at TEXT NOT NULL,
            UNIQUE(company, category, name)
        )
        """
    )
    conn.commit()


@router.get("/ammo/file")
def get_ammo_file(company: str, category: str, name: str) -> dict[str, Any]:
    """读取弹药库内单个文件内容（限岗位弹药库目录，供前端预览）。"""
    import urllib.parse

    company = urllib.parse.unquote(company)
    category = urllib.parse.unquote(category)
    name = urllib.parse.unquote(name)
    if not company or not category or not name:
        raise HTTPException(status_code=422, detail="company/category/name 均必填")

    company_dir = (
        AMMO_DIR / company
        if company.endswith("-面试准备")
        else AMMO_DIR / f"{company}-面试准备"
    )
    cat_map = {
        "岗位与公司信息": "01_岗位与公司信息",
        "面试备战资料": "02_面试备战资料",
        "速成包": "03_速成包",
    }
    rel_cat = cat_map.get(category, category)
    target = (company_dir / rel_cat / name).resolve()
    if not str(target).startswith(str(AMMO_DIR.resolve())):
        raise HTTPException(status_code=404, detail="路径越界，拒绝访问")
    if not target.is_file() or target.suffix.lower() not in {
        ".md", ".txt", ".html", ".json", ".csv",
    }:
        raise HTTPException(status_code=404, detail="文件不存在或不支持预览")
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(status_code=500, detail="读取失败") from exc
    limit = 50000
    return {
        "company": company,
        "category": rel_cat,
        "name": name,
        "content": content[:limit],
        "lines": content.count("\n") + 1,
        "truncated": len(content) > limit,
    }


@router.get("/ammo/{company}")
def get_company_ammo(company: str) -> dict[str, Any]:
    """获取单个公司的弹药库详情。"""
    company_dir = AMMO_DIR / f"{company}-面试准备"
    if not company_dir.exists():
        raise HTTPException(status_code=404, detail=f"未找到 {company} 的面试准备目录")
    return _scan_ammo_dir(company_dir)


# ── 公司岗位详情（提取关键信息）──────────────────────────────

def _read_file_safe(path: Path, max_lines: int = 100) -> str:
    """安全读取文件前N行。"""
    try:
        with open(path, encoding="utf-8") as f:
            lines = []
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                lines.append(line)
            return "".join(lines)
    except Exception:
        return ""


def _extract_company_profile(company_dir: Path, company_name: str) -> dict[str, Any]:
    """提取公司岗位的结构化信息。"""
    result = {
        "company": company_name,
        "position": "",
        "location": "",
        "direction": "",
        "interview_time": "",
        "status": "",
        "resume_version": "",
        "resume_file": "",
        "job_summary": "",
        "key_responsibilities": [],
        "match_highlights": [],
        "company_background": "",
        "interview_rounds": [],
        "notes": "",
    }

    # 1. 从 job 表获取基本信息
    try:
        conn = sqlite3.connect(str(INTERVIEW_DB_PATH))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM job WHERE company LIKE ?", (f"%{company_name}%",)
        ).fetchone()
        if row:
            result["position"] = row["role"] or ""
            result["interview_time"] = row["interview_at"] or ""
            result["status"] = row["status"] or ""
            result["direction"] = row["direction"] or ""
            result["resume_version"] = row["resume_ver"] or ""
            result["notes"] = row["note"] or ""
        conn.close()
    except Exception:
        pass

    # 2. 读取投递简历记录
    resume_file = company_dir / "01_岗位与公司信息" / "投递简历记录.md"
    if not resume_file.exists():
        resume_file = company_dir / "01_岗位与公司信息" / "投递简历_游戏数据分析版.md"
    if resume_file.exists():
        content = _read_file_safe(resume_file, 50)
        # 提取投递简历文件
        for line in content.split("\n"):
            if "投递简历文件" in line or "简历文件" in line:
                import re
                m = re.search(r"`([^`]+)`", line)
                if m:
                    result["resume_file"] = m.group(1)
                break

    # 3. 读取岗位JD拆解与公司背景
    jd_file = company_dir / "01_岗位与公司信息" / "岗位JD拆解与公司背景.md"
    if not jd_file.exists():
        jd_file = company_dir / "01_岗位与公司信息" / "公司背景与JD拆解.md"
    if not jd_file.exists():
        jd_file = company_dir / "01_岗位与公司信息" / "JD拆解_拼多多AI算法工程师_电商推荐.md"
    if jd_file.exists():
        content = _read_file_safe(jd_file, 120)
        lines = content.split("\n")
        # 提取地点
        for line in lines:
            if "地点" in line or "工作地点" in line or "base" in line.lower():
                import re
                m = re.search(r"[：:]\s*(.+)", line)
                if m and not result["location"]:
                    result["location"] = m.group(1).strip()[:50]
        # 提取职位描述核心一句话
        in_summary = False
        for line in lines:
            if "职位描述" in line or "岗位描述" in line:
                in_summary = True
                continue
            if in_summary and line.startswith(">"):
                result["job_summary"] = line.lstrip("> ").strip()
                break
            if in_summary and line.startswith("###"):
                break
        # 提取主要职责
        in_resp = False
        for line in lines:
            if "主要职责" in line or "岗位职责" in line:
                in_resp = True
                continue
            if in_resp:
                if line.startswith("###") or line.startswith("##"):
                    break
                if line.strip().startswith(("1.", "2.", "3.", "4.", "5.")):
                    import re
                    m = re.match(r"\d+\.\s*(.+)", line.strip())
                    if m:
                        resp = m.group(1).strip()
                        # 去掉加粗标记
                        resp = re.sub(r"\*\*(.+?)\*\*", r"\1", resp)
                        if len(resp) > 10:
                            result["key_responsibilities"].append(resp[:100])
                if len(result["key_responsibilities"]) >= 5:
                    break
        # 提取公司背景
        in_bg = False
        bg_lines = []
        for line in lines:
            if "公司背景" in line or "公司整体" in line or "公司介绍" in line:
                in_bg = True
                continue
            if in_bg:
                if line.startswith("###") or line.startswith("##"):
                    break
                if line.strip().startswith("- ") and len(line.strip()) > 5:
                    bg_lines.append(line.strip().lstrip("- "))
                if len(bg_lines) >= 4:
                    break
        result["company_background"] = "；".join(bg_lines[:3])

    # 4. 读取通用模块索引，提取匹配亮点
    index_file = company_dir / "04_通用模块索引.md"
    if index_file.exists():
        content = _read_file_safe(index_file, 80)
        # 提取数字口径
        in_numbers = False
        for line in content.split("\n"):
            if "数字口径" in line or "可讲的真实数字" in line:
                in_numbers = True
                continue
            if in_numbers and line.startswith("|") and "---" not in line:
                import re
                cells = [c.strip() for c in line.split("|") if c.strip()]
                if len(cells) >= 2 and cells[0] != "经历":
                    result["match_highlights"].append(f"{cells[0]}: {cells[1][:60]}")
                if len(result["match_highlights"]) >= 5:
                    break

    return result


@router.get("/company-profiles")
def get_company_profiles() -> dict[str, Any]:
    """获取所有公司的岗位详情（提取关键信息）。"""
    if not AMMO_DIR.exists():
        return {"companies": [], "total": 0}
    profiles = []
    for d in AMMO_DIR.iterdir():
        if d.is_dir() and d.name.endswith("-面试准备"):
            company_name = d.name.replace("-面试准备", "")
            # 简化公司名（去掉后缀）
            simple_name = company_name.replace("-广告岗", "").replace("-面试准备", "")
            profiles.append(_extract_company_profile(d, simple_name))
    # 按面试时间排序（有面试时间的排前面）
    profiles.sort(key=lambda x: x["interview_time"] or "", reverse=True)
    return {"companies": profiles, "total": len(profiles)}


@router.get("/company-profiles/{company}")
def get_company_profile(company: str) -> dict[str, Any]:
    """获取单个公司的岗位详情。"""
    company_dir = AMMO_DIR / f"{company}-面试准备"
    if not company_dir.exists():
        raise HTTPException(status_code=404, detail=f"未找到 {company}")
    return _extract_company_profile(company_dir, company)


# ── 反问话术 API ──────────────────────────────────────────────

class RebuttalRequest(BaseModel):
    category: str = "HR面"
    company: str = ""
    question: str
    purpose: str = ""
    priority: str = "中"
    tags: str = ""
    note: str = ""


def _get_rebuttal_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(INTERVIEW_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@router.get("/rebuttals")
def get_rebuttals(
    category: str = Query("", description="分类：HR面/技术面/业务面/通用"),
    company: str = Query("", description="公司，空表示通用"),
    priority: str = Query("", description="优先级：高/中/低"),
) -> dict[str, Any]:
    """获取反问话术列表。"""
    conn = _get_rebuttal_conn()
    cur = conn.cursor()
    query = "SELECT * FROM interview_rebuttals WHERE 1=1"
    params: list[Any] = []
    if category:
        query += " AND category = ?"
        params.append(category)
    if company:
        query += " AND (company = ? OR company = '')"
        params.append(company)
    if priority:
        query += " AND priority = ?"
        params.append(priority)
    query += " ORDER BY CASE priority WHEN '高' THEN 1 WHEN '中' THEN 2 ELSE 3 END, category, id"
    cur.execute(query, params)
    items = [dict(row) for row in cur.fetchall()]
    conn.close()
    return {"items": items, "total": len(items)}


@router.post("/rebuttals")
def create_rebuttal(req: RebuttalRequest) -> dict[str, Any]:
    """新增反问话术。"""
    conn = _get_rebuttal_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO interview_rebuttals (category, company, question, purpose, priority, tags, note)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (req.category, req.company, req.question, req.purpose, req.priority, req.tags, req.note),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return {"id": new_id, "status": "ok"}


@router.put("/rebuttals/{rebuttal_id}")
def update_rebuttal(rebuttal_id: int, req: RebuttalRequest) -> dict[str, Any]:
    """更新反问话术。"""
    conn = _get_rebuttal_conn()
    cur = conn.cursor()
    cur.execute(
        """UPDATE interview_rebuttals SET category=?, company=?, question=?, purpose=?, priority=?, tags=?, note=?
           WHERE id=?""",
        (req.category, req.company, req.question, req.purpose, req.priority, req.tags, req.note, rebuttal_id),
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "id": rebuttal_id}


@router.delete("/rebuttals/{rebuttal_id}")
def delete_rebuttal(rebuttal_id: int) -> dict[str, Any]:
    """删除反问话术。"""
    conn = _get_rebuttal_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM interview_rebuttals WHERE id=?", (rebuttal_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}


@router.post("/rebuttals/{rebuttal_id}/use")
def mark_rebuttal_used(rebuttal_id: int) -> dict[str, Any]:
    """标记反问话术已使用（used_count+1）。"""
    conn = _get_rebuttal_conn()
    cur = conn.cursor()
    cur.execute("UPDATE interview_rebuttals SET used_count = used_count + 1 WHERE id=?", (rebuttal_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}


def register_interview_routes(app: Any, ctx: Any) -> None:
    """注册面试题阅读追踪路由。"""
    app.include_router(router)

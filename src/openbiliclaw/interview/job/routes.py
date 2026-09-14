"""求职面试备战（A · job）API 路由。

正规前缀 ``PREFIX = /api/interview/job``（期 2 URL 分区）；旧前缀
``/api/interview`` 作为**双挂载别名**保留一版（``include_in_schema=False``），
由 ``mount_interview_router`` 同时挂载两者。

Endpoints（下列路径为相对路径，需加上前缀）：
- GET  /status        — 根目录配置与系统总览
- GET  /jobs          — 岗位列表（?keyword=）
- GET  /search        — 全文检索（?q=&limit=）
- GET  /numbers       — 真实数字表（?keyword=）
- GET  /projects      — 项目库（?keyword=）
- GET  /card/{company} — 某公司速记卡
- GET  /directions    — 方向知识库清单
- GET  /index         — 全库文件索引（?keyword=&layer=）
- GET  /logs          — 面试日志（最新在前）
- POST /logs          — 追加面试日志 {company, round, points}
- POST /scaffold      — 新岗位建档 {company, role}

数据目录由 ``[interview] root`` 配置（见 config.toml），引擎只读检索；
仅日志追加与新岗位建档两个 POST 会写入引擎数据目录。
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from openbiliclaw.interview._paths import PROJECT_ROOT
from openbiliclaw.interview.job.engine import InterviewEngine, resolve_root

logger = logging.getLogger(__name__)

# 期 2 URL 分区：A 的正规前缀；LEGACY_PREFIX 是兼容旧调用方的别名前缀（双挂载）。
PREFIX = "/api/interview/job"
LEGACY_PREFIX = "/api/interview"


class InterviewLogIn(BaseModel):
    company: str = Field(..., min_length=1, max_length=100)
    round: str = Field(..., min_length=1, max_length=50)
    points: str = Field(..., min_length=1, max_length=2000)


class InterviewScaffoldIn(BaseModel):
    company: str = Field(..., min_length=1, max_length=100)
    role: str = Field(..., min_length=1, max_length=100)


def build_interview_router(*, root: str | None = None) -> APIRouter:
    """创建 interview 路由。root 为空时引擎按默认路径解析。"""
    # 注意：这里**不设 prefix**——由 mount_interview_router 挂载时传入，
    # 以便同一份路由同时挂到新前缀与旧前缀（双挂载别名）。
    router = APIRouter(tags=["interview-job"])
    engine = InterviewEngine(resolve_root(root))

    def _ready() -> None:
        if not engine.is_configured:
            raise HTTPException(
                status_code=404,
                detail=f"求职知识库未配置或目录不存在: {engine.root}（请检查 [interview] root）",
            )

    @router.get("/status")
    def interview_status() -> dict[str, Any]:
        """系统总览：根目录、是否配置、岗位/项目/数字/日志统计。"""
        return engine.overview()

    @router.get("/jobs")
    def interview_jobs(keyword: str | None = None) -> dict[str, Any]:
        """岗位列表，支持公司/岗位/主打方向关键词过滤。"""
        _ready()
        rows = engine.jobs(keyword)
        return {"total": len(rows), "items": rows}

    @router.get("/search")
    def interview_search(q: str, limit: int = 40) -> dict[str, Any]:
        """全文检索（02/03 + 腾讯文档资料 + 解码文本）。"""
        _ready()
        if not q.strip():
            raise HTTPException(status_code=422, detail="检索关键词不能为空")
        limit = max(1, min(limit, 200))
        hits = engine.search(q, max_hits=limit)
        return {"keyword": q, "total": len(hits), "items": hits}

    @router.get("/numbers")
    def interview_numbers(keyword: str | None = None) -> dict[str, Any]:
        """真实数字表（口径权威源）。"""
        _ready()
        rows = engine.numbers(keyword)
        return {"total": len(rows), "items": rows}

    @router.get("/projects")
    def interview_projects(keyword: str | None = None) -> dict[str, Any]:
        """项目库。"""
        _ready()
        rows = engine.projects(keyword)
        return {"total": len(rows), "items": rows}

    @router.get("/card/{company}")
    def interview_card(company: str) -> dict[str, Any]:
        """某公司面试速记卡（岗位+数字+项目+题库入口）。"""
        _ready()
        try:
            return engine.speed_card(company)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/directions")
    def interview_directions() -> dict[str, Any]:
        """02_方向知识库 方向及文档清单。"""
        _ready()
        rows = engine.directions()
        return {"total": len(rows), "items": rows}

    @router.get("/index")
    def interview_index(keyword: str | None = None, layer: str | None = None) -> dict[str, Any]:
        """全库文件索引查询（knowledge.db / CSV 回退）。"""
        _ready()
        if layer is not None and layer not in ("01", "02", "03"):
            raise HTTPException(status_code=422, detail="layer 仅支持 01/02/03")
        rows = engine.index(keyword, layer)
        return {"total": len(rows), "items": rows}

    @router.post("/index/rebuild")
    def interview_index_rebuild() -> dict[str, Any]:
        """重建全库文件索引（覆盖 06 CSV + knowledge.db，不动原始文件）。"""
        _ready()
        try:
            return engine.rebuild_index()
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=500, detail=f"重建索引失败: {exc}") from exc

    @router.get("/doctor")
    def interview_doctor(fix: bool = False, full: bool = False) -> dict[str, Any]:
        """知识库健康检查（C1-C4 常规；C5 索引新鲜度仅 full；fix 可自动重建索引）。"""
        _ready()
        try:
            return engine.doctor(fix=fix, full=full)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=500, detail=f"健康检查失败: {exc}") from exc

    @router.get("/logs")
    def interview_logs() -> dict[str, Any]:
        """面试日志列表（最新在前）。"""
        _ready()
        rows = engine.logs()
        return {"total": len(rows), "items": rows}

    @router.post("/logs", status_code=201)
    def interview_log_add(payload: InterviewLogIn) -> dict[str, Any]:
        """追加一条面试日志（只追加，不修改历史行）。"""
        _ready()
        try:
            new_id = engine.add_log(payload.company, payload.round, payload.points)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=500, detail=f"写入面试日志失败: {exc}") from exc
        return {"id": new_id, "company": payload.company, "round": payload.round}

    @router.post("/scaffold", status_code=201)
    def interview_scaffold(payload: InterviewScaffoldIn) -> dict[str, Any]:
        """按统一规范新建岗位备战包目录（01/02/03 三件套）。"""
        _ready()
        try:
            result = engine.scaffold(payload.company, payload.role)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=500, detail=f"新建岗位目录失败: {exc}") from exc
        return result

    @router.get("/topics")
    def interview_topics(company: str | None = None, limit: int = 50) -> dict[str, Any]:
        """面试专题库（从 knowledge.db 的 kb_documents 表读取，doc_type=面试专题）。"""
        conn = sqlite3.connect(_knowledge_db())
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        if company:
            c.execute(
                "SELECT id, topic, category, chapter, tags, doc_type, updated, created_at FROM kb_documents WHERE doc_type='面试专题' AND (topic LIKE ? OR tags LIKE ?) ORDER BY updated DESC LIMIT ?",
                (f"%{company}%", f"%{company}%", limit),
            )
        else:
            c.execute(
                "SELECT id, topic, category, chapter, tags, doc_type, updated, created_at FROM kb_documents WHERE doc_type='面试专题' ORDER BY updated DESC LIMIT ?",
                (limit,),
            )
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return {"total": len(rows), "items": rows}

    @router.get("/topics/{topic_id}")
    def interview_topic_detail(topic_id: int) -> dict[str, Any]:
        """面试专题详情（含完整内容）。"""
        conn = sqlite3.connect(_knowledge_db())
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM kb_documents WHERE id=?", (topic_id,))
        row = c.fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail=f"专题 {topic_id} 不存在")
        return dict(row)

    def _scripts_db() -> str:
        db_path = PROJECT_ROOT / "data" / "interview.db"
        try:
            from openbiliclaw.config import load_config
            settings = load_config()
            if settings.storage.interview_db_path:
                p = Path(settings.storage.interview_db_path)
                return str(p if p.is_absolute() else PROJECT_ROOT / p)
        except Exception:
            pass
        return str(db_path)

    def _resume_db() -> str:
        """投递域库（data/resume.db：companies/job_postings/applications/resume_texts/job_ammo）。"""
        db_path = PROJECT_ROOT / "data" / "resume.db"
        try:
            from openbiliclaw.config import load_config
            settings = load_config()
            if getattr(settings.storage, "resume_db_path", None):
                p = Path(settings.storage.resume_db_path)
                return str(p if p.is_absolute() else PROJECT_ROOT / p)
        except Exception:
            pass
        return str(db_path)

    def _knowledge_db() -> str:
        """加工层知识库（data/knowledge.db：kb_documents 系已于 2026-09-14 迁入）。"""
        db_path = PROJECT_ROOT / "data" / "knowledge.db"
        try:
            from openbiliclaw.config import load_config
            settings = load_config()
            if getattr(settings.storage, "knowledge_db_path", None):
                p = Path(settings.storage.knowledge_db_path)
                return str(p if p.is_absolute() else PROJECT_ROOT / p)
        except Exception:
            pass
        return str(db_path)

    @router.get("/scripts")
    def interview_scripts(
        script_type: str | None = None,
        company: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """话术库列表，支持按类型/公司筛选。"""
        conn = sqlite3.connect(_scripts_db())
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        sql = "SELECT id, type, company, position, title, key_points, priority, tags, used_count, created_at, updated_at FROM interview_scripts WHERE 1=1"
        params: list[Any] = []
        if script_type:
            sql += " AND type=?"
            params.append(script_type)
        if company:
            sql += " AND (company=? OR company='通用')"
            params.append(company)
        sql += " ORDER BY priority='高' DESC, priority='中' DESC, updated_at DESC LIMIT ?"
        params.append(limit)
        c.execute(sql, params)
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return {"total": len(rows), "items": rows}

    @router.get("/scripts/types")
    def interview_script_types() -> dict[str, Any]:
        """话术类型统计。"""
        conn = sqlite3.connect(_scripts_db())
        c = conn.cursor()
        c.execute("SELECT type, COUNT(*) FROM interview_scripts GROUP BY type ORDER BY COUNT(*) DESC")
        rows = [{"type": r[0], "count": r[1]} for r in c.fetchall()]
        conn.close()
        return {"total": len(rows), "items": rows}

    @router.get("/scripts/companies")
    def interview_script_companies() -> dict[str, Any]:
        """有话术的公司列表。"""
        conn = sqlite3.connect(_scripts_db())
        c = conn.cursor()
        c.execute("SELECT company, COUNT(*) FROM interview_scripts WHERE company!='' GROUP BY company ORDER BY COUNT(*) DESC")
        rows = [{"company": r[0], "count": r[1]} for r in c.fetchall()]
        conn.close()
        return {"total": len(rows), "items": rows}

    @router.get("/scripts/{script_id}")
    def interview_script_detail(script_id: int) -> dict[str, Any]:
        """话术详情（含完整内容）。"""
        conn = sqlite3.connect(_scripts_db())
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM interview_scripts WHERE id=?", (script_id,))
        row = c.fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail=f"话术 {script_id} 不存在")
        return dict(row)

    # ── 岗位投递管理 ──────────────────────────────────────────

    @router.get("/positions")
    def interview_positions(
        company: str | None = None,
        city: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """岗位列表（支持按公司/城市/状态筛选，默认按匹配度降序）。"""
        conn = sqlite3.connect(_resume_db())
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        sql = "SELECT * FROM job_postings WHERE 1=1"
        params: list[Any] = []
        if company:
            sql += " AND company=?"
            params.append(company)
        if city:
            sql += " AND city LIKE ?"
            params.append(f"%{city}%")
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY match_score DESC, id DESC LIMIT ?"
        params.append(limit)
        c.execute(sql, params)
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return {"total": len(rows), "items": rows}

    @router.get("/positions/{position_id}")
    def interview_position_detail(position_id: int) -> dict[str, Any]:
        """岗位详情（含完整JD和要求）。"""
        conn = sqlite3.connect(_resume_db())
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM job_postings WHERE id=?", (position_id,))
        row = c.fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail=f"岗位 {position_id} 不存在")
        return dict(row)

    @router.post("/positions/{position_id}/status")
    def interview_position_update_status(
        position_id: int, status: str, resume_version: str | None = None, notes: str | None = None
    ) -> dict[str, Any]:
        """更新岗位投递状态（待投递/已投递/面试中/已offer/已拒绝/已归档）。"""
        valid_status = {"待投递", "已投递", "面试中", "已offer", "已拒绝", "已归档"}
        if status not in valid_status:
            raise HTTPException(status_code=400, detail=f"状态必须是 {valid_status} 之一")
        conn = sqlite3.connect(_resume_db())
        c = conn.cursor()
        c.execute("SELECT id FROM job_postings WHERE id=?", (position_id,))
        if not c.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail=f"岗位 {position_id} 不存在")
        updates = ["status=?", "updated_at=?"]
        params: list[Any] = [status, datetime.now().isoformat()]
        if resume_version:
            updates.append("resume_version=?")
            params.append(resume_version)
        if notes:
            updates.append("notes=?")
            params.append(notes)
        if status == "已投递":
            updates.append("applied_at=?")
            params.append(datetime.now().isoformat())
        params.append(position_id)
        c.execute(f"UPDATE job_postings SET {', '.join(updates)} WHERE id=?", params)
        conn.commit()
        conn.close()
        return {"ok": True, "id": position_id, "status": status}

    @router.post("/positions", status_code=201)
    def interview_position_create(data: dict[str, Any]) -> dict[str, Any]:
        """新增岗位（用于从招聘网站抓取后录入）。"""
        required = ["company", "title"]
        for f in required:
            if f not in data:
                raise HTTPException(status_code=400, detail=f"缺少必填字段 {f}")
        conn = sqlite3.connect(_resume_db())
        c = conn.cursor()
        now = datetime.now().isoformat()
        c.execute("""
            INSERT INTO job_postings
            (company, bg, title, city, years_required, education, job_url, job_id,
             description, requirements, match_score, match_points, status, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get("company"), data.get("bg"), data.get("title"), data.get("city"),
            data.get("years_required"), data.get("education"), data.get("job_url"), data.get("job_id"),
            data.get("description", ""), data.get("requirements", ""),
            data.get("match_score", 0), data.get("match_points", ""),
            data.get("status", "待投递"), data.get("tags", ""), now, now
        ))
        conn.commit()
        new_id = c.lastrowid
        conn.close()
        return {"ok": True, "id": new_id}

    # ========== 简历管理 ==========
    @router.get("/resumes")
    def interview_resumes(
        company: str = None,
        position_id: int = None,
        page: int = 1,
        page_size: int = 50,
    ):
        conn = sqlite3.connect(_resume_db())
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        where = []
        params = []
        if company:
            where.append("company = ?")
            params.append(company)
        if position_id:
            where.append("position_id = ?")
            params.append(position_id)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        c.execute(f"SELECT COUNT(*) FROM resume_texts{where_sql}", params)
        total = c.fetchone()[0]
        offset = (page - 1) * page_size
        c.execute(
            f"SELECT id, position_id, company, target_position, version_name, highlights, matched_keywords, file_path, created_at, updated_at FROM resume_texts{where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        )
        items = [dict(r) for r in c.fetchall()]
        conn.close()
        return {"total": total, "page": page, "page_size": page_size, "items": items}

    @router.get("/resumes/{resume_id}")
    def interview_resume_detail(resume_id: int):
        conn = sqlite3.connect(_resume_db())
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM resume_texts WHERE id = ?", (resume_id,))
        row = c.fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="简历不存在")
        return dict(row)

    @router.post("/resumes", status_code=201)
    def interview_create_resume(data: dict):
        conn = sqlite3.connect(_resume_db())
        c = conn.cursor()
        now = datetime.now().isoformat()
        c.execute("""
            INSERT INTO resume_texts (position_id, company, target_position, version_name, full_text, highlights, matched_keywords, file_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get("position_id"), data.get("company"), data.get("target_position"),
            data.get("version_name"), data.get("full_text", ""), data.get("highlights", ""),
            data.get("matched_keywords", ""), data.get("file_path", ""), now, now
        ))
        conn.commit()
        new_id = c.lastrowid
        conn.close()
        return {"ok": True, "id": new_id}

    # ========== 待办事项 ==========
    todo_priorities = ("高", "中", "低")

    def _ensure_todo_table(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS todo (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                detail TEXT DEFAULT '',
                company TEXT DEFAULT '',
                due_date TEXT DEFAULT '',
                priority TEXT DEFAULT '中',
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                done_at TEXT DEFAULT ''
            )
        """)

    @router.get("/todos")
    def interview_todos(include_done: bool = False, limit: int = 200) -> dict[str, Any]:
        """待办列表（pending 在前：逾期→有截止日→无截止日；done 沉底）。"""
        conn = sqlite3.connect(_scripts_db())
        conn.row_factory = sqlite3.Row
        _ensure_todo_table(conn)
        c = conn.cursor()
        sql = "SELECT * FROM todo"
        if not include_done:
            sql += " WHERE status='pending'"
        sql += " ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, due_date='', priority='高' DESC, priority='中' DESC, due_date ASC, id DESC LIMIT ?"
        c.execute(sql, (limit,))
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return {"total": len(rows), "items": rows}

    @router.post("/todos", status_code=201)
    def interview_todo_add(data: dict[str, Any]) -> dict[str, Any]:
        """新增待办 {title, detail?, company?, due_date?, priority?}。"""
        title = str(data.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=422, detail="title 不能为空")
        priority = data.get("priority") or "中"
        if priority not in todo_priorities:
            raise HTTPException(status_code=422, detail=f"priority 必须是 {todo_priorities} 之一")
        due_date = str(data.get("due_date") or "").strip()
        if due_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", due_date):
            raise HTTPException(status_code=422, detail="due_date 格式须为 YYYY-MM-DD")
        conn = sqlite3.connect(_scripts_db())
        _ensure_todo_table(conn)
        c = conn.cursor()
        c.execute(
            "INSERT INTO todo (title, detail, company, due_date, priority, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (
                title,
                str(data.get("detail") or "").strip(),
                str(data.get("company") or "").strip(),
                due_date,
                priority,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
        new_id = c.lastrowid
        conn.close()
        return {"ok": True, "id": new_id}

    @router.post("/todos/{todo_id}/status")
    def interview_todo_status(todo_id: int, status: str) -> dict[str, Any]:
        """更新待办状态（pending/done）。"""
        if status not in ("pending", "done"):
            raise HTTPException(status_code=422, detail="status 必须是 pending 或 done")
        conn = sqlite3.connect(_scripts_db())
        _ensure_todo_table(conn)
        c = conn.cursor()
        c.execute("SELECT id FROM todo WHERE id=?", (todo_id,))
        if not c.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail=f"待办 {todo_id} 不存在")
        done_at = datetime.now().isoformat(timespec="seconds") if status == "done" else ""
        c.execute("UPDATE todo SET status=?, done_at=? WHERE id=?", (status, done_at, todo_id))
        conn.commit()
        conn.close()
        return {"ok": True, "id": todo_id, "status": status}

    @router.delete("/todos/{todo_id}")
    def interview_todo_delete(todo_id: int) -> dict[str, Any]:
        """删除待办。"""
        conn = sqlite3.connect(_scripts_db())
        _ensure_todo_table(conn)
        c = conn.cursor()
        c.execute("SELECT id FROM todo WHERE id=?", (todo_id,))
        if not c.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail=f"待办 {todo_id} 不存在")
        c.execute("DELETE FROM todo WHERE id=?", (todo_id,))
        conn.commit()
        conn.close()
        return {"ok": True, "id": todo_id}

    return router


def mount_interview_router(app: Any, *, root: str | None = None) -> APIRouter:
    """把 A（岗位备战）路由挂到新前缀 + 兼容旧前缀。

    双挂载别名：同一份 router 挂两次——新前缀进 OpenAPI，旧前缀
    ``include_in_schema=False``（不进文档、不做重定向，故 POST/PUT/DELETE 全兼容）。
    下一个大版本摘除旧前缀时，删掉第二行 ``include_router`` 即可。
    """
    router = build_interview_router(root=root)
    app.include_router(router, prefix=PREFIX)
    app.include_router(router, prefix=LEGACY_PREFIX, include_in_schema=False)
    return router


__all__ = [
    "build_interview_router",
    "mount_interview_router",
    "PREFIX",
    "LEGACY_PREFIX",
    "InterviewLogIn",
    "InterviewScaffoldIn",
]

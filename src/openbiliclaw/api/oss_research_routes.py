"""开源项目研究 API 路由。

把「发给助手的开源项目 → 研究 → 入库 → 前端 tab 展示」做成一个可持续闭环。
数据落在独立 SQLite 库 ``12_开源项目研究/oss_research.db`` 的 ``oss_projects`` 表，
与既有各业务库（douban.db / travel.db / interview.db …）隔离，互不污染。

表字段说明（详见 docs/modules/oss_research.md）：
- name / owner / url          仓库名、GitHub  owner、仓库地址
- one_liner / purpose         一句话定位、它是什么
- tech_stack (JSON)           技术栈列表
- structure_notes             项目结构亮点（自由文本）
- key_features (JSON)         核心能力列表
- relevance_summary          与 OpenBiliClaw 的关联 / 可迁移点（摘要）
- reusable_techniques (JSON) 可迁移手法列表
- caveats                     不建议照搬的点
- report_path                对应的 markdown 研究报告路径
- tags (JSON)                 标签（用于前端筛选，如 chrome-extension / card-feed）
- created_at / updated_at    时间戳
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from openbiliclaw.config import _project_root

logger = logging.getLogger(__name__)

# src/openbiliclaw/api/oss_research_routes.py -> parents[3] == 项目根
# 开源研究模块资产 2026-09-18 收编到项目根 `12_开源项目研究/`
# （数据库 + 研究报告/克隆 + 回填脚本；路由代码本身必须留在可导入的 src 包内）
DEFAULT_DB_PATH = _project_root() / "12_开源项目研究" / "oss_research.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS oss_projects (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  name              TEXT NOT NULL,
  owner             TEXT NOT NULL DEFAULT '',
  url               TEXT NOT NULL DEFAULT '',
  one_liner         TEXT NOT NULL DEFAULT '',
  purpose           TEXT NOT NULL DEFAULT '',
  tech_stack        TEXT NOT NULL DEFAULT '[]',
  structure_notes   TEXT NOT NULL DEFAULT '',
  key_features      TEXT NOT NULL DEFAULT '[]',
  relevance_summary TEXT NOT NULL DEFAULT '',
  reusable_techniques TEXT NOT NULL DEFAULT '[]',
  caveats           TEXT NOT NULL DEFAULT '',
  report_path       TEXT NOT NULL DEFAULT '',
  tags              TEXT NOT NULL DEFAULT '[]',
  created_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
  updated_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""

# 允许写入的字段（create / update 共用）
_WRITABLE = (
    "name",
    "owner",
    "url",
    "one_liner",
    "purpose",
    "tech_stack",
    "structure_notes",
    "key_features",
    "relevance_summary",
    "reusable_techniques",
    "caveats",
    "report_path",
    "tags",
)

# 以 JSON 数组存储的字段，入库前需序列化、出库后需反序列化
_JSON_FIELDS = ("tech_stack", "key_features", "reusable_techniques", "tags")


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: Path) -> None:
    """确保库与表存在（幂等）。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _as_json(value: Any) -> str:
    """把列表/可序列化对象变成 JSON 文本；字符串直接原样存（前提是合法 JSON）。"""
    if value is None:
        return "[]"
    if isinstance(value, str):
        # 已经是 JSON 文本则原样；否则当普通字符串包成单元素数组
        try:
            json.loads(value)
            return value
        except (ValueError, TypeError):
            return json.dumps([value], ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key in _JSON_FIELDS:
        raw = d.get(key) or "[]"
        try:
            d[key] = json.loads(raw)
        except (ValueError, TypeError):
            d[key] = []
    return d


def insert_project(db_path: Path, data: dict[str, Any]) -> int:
    """插入一条开源项目记录，返回新行 id。供 API 与回填脚本复用。"""
    init_db(db_path)
    cols, vals = [], []
    for key in _WRITABLE:
        if key not in data:
            continue
        cols.append(key)
        v = data[key]
        vals.append(_as_json(v) if key in _JSON_FIELDS else (v or ""))
    if not cols:
        raise ValueError("nothing to insert: no writable fields provided")
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO oss_projects ({', '.join(cols)}) VALUES ({placeholders})"
    conn = _connect(db_path)
    try:
        cur = conn.execute(sql, vals)
        conn.commit()
        new_id = cur.lastrowid
        assert new_id is not None
        return new_id
    finally:
        conn.close()


def build_oss_research_router(db_path: str | None = None) -> APIRouter:
    """构建开源项目研究路由（FastAPI APIRouter）。

    db_path 为空时回退到项目根 ``12_开源项目研究/oss_research.db``。
    """
    resolved = Path(db_path) if db_path else DEFAULT_DB_PATH
    init_db(resolved)

    router = APIRouter(prefix="/api/oss-research", tags=["oss-research"])

    def _db() -> sqlite3.Connection:
        return _connect(resolved)

    @router.get("/projects")
    def list_projects(tag: str | None = None, search: str | None = None):
        """列出已研究项目，支持按标签 / 关键词筛选。"""
        conn = _db()
        try:
            sql = "SELECT * FROM oss_projects WHERE 1=1"
            params: list[Any] = []
            if tag:
                sql += " AND tags LIKE ?"
                params.append(f"%{tag}%")
            if search:
                like = f"%{search}%"
                sql += (
                    " AND (name LIKE ? OR one_liner LIKE ? OR purpose LIKE ?"
                    " OR relevance_summary LIKE ? OR owner LIKE ?)"
                )
                params += [like, like, like, like, like]
            sql += " ORDER BY created_at DESC, id DESC"
            rows = conn.execute(sql, params).fetchall()
            items = [_row_to_dict(r) for r in rows]
            return {"items": items, "count": len(items)}
        finally:
            conn.close()

    @router.get("/tags")
    def list_tags():
        """返回所有出现过的标签（去重排序），用于前端筛选条。"""
        conn = _db()
        try:
            rows = conn.execute("SELECT tags FROM oss_projects").fetchall()
            seen: set[str] = set()
            for (raw,) in rows:
                try:
                    for t in json.loads(raw or "[]") or []:
                        if t:
                            seen.add(t)
                except (ValueError, TypeError):
                    continue
            return {"tags": sorted(seen)}
        finally:
            conn.close()

    @router.get("/projects/{pid}")
    def get_project(pid: int):
        conn = _db()
        try:
            row = conn.execute("SELECT * FROM oss_projects WHERE id=?", (pid,)).fetchone()
            if not row:
                return JSONResponse({"error": "not found"}, status_code=404)
            return _row_to_dict(row)
        finally:
            conn.close()

    @router.post("/projects")
    async def create_project(request: Request):
        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid json body"}, status_code=400)
        if not (data.get("name") or "").strip():
            return JSONResponse({"error": "name is required"}, status_code=400)
        try:
            new_id = insert_project(resolved, data)
        except Exception as exc:  # noqa: BLE001
            logger.exception("insert oss project failed")
            return JSONResponse({"error": str(exc)}, status_code=500)
        conn = _db()
        try:
            row = conn.execute("SELECT * FROM oss_projects WHERE id=?", (new_id,)).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()

    @router.put("/projects/{pid}")
    async def update_project(pid: int, request: Request):
        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid json body"}, status_code=400)
        conn = _db()
        try:
            existing = conn.execute("SELECT id FROM oss_projects WHERE id=?", (pid,)).fetchone()
            if not existing:
                return JSONResponse({"error": "not found"}, status_code=404)
            sets, vals = [], []
            for key in _WRITABLE:
                if key not in data:
                    continue
                sets.append(f"{key}=?")
                v = data[key]
                vals.append(_as_json(v) if key in _JSON_FIELDS else (v or ""))
            if sets:
                sets.append("updated_at=datetime('now','localtime')")
                conn.execute(
                    f"UPDATE oss_projects SET {', '.join(sets)} WHERE id=?", vals + [pid]
                )
                conn.commit()
            row = conn.execute("SELECT * FROM oss_projects WHERE id=?", (pid,)).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()

    @router.delete("/projects/{pid}")
    def delete_project(pid: int):
        conn = _db()
        try:
            cur = conn.execute("DELETE FROM oss_projects WHERE id=?", (pid,))
            conn.commit()
            return {"deleted": cur.rowcount, "id": pid}
        finally:
            conn.close()

    # ── 研究报告静态服务（白名单目录，只读） ───────────────────
    # report_path 存的是仓库相对路径（如 12_开源项目研究/references/xxx.md），
    # 前端拼成站内链接。只允许白名单目录下的
    # .md 文件，resolve 后必须仍落在白名单目录内（防路径穿越）。
    _repo_root = DEFAULT_DB_PATH.parent.parent  # 项目根
    _allowed_dirs = (
        _repo_root / "12_开源项目研究" / "references",
        _repo_root / "docs",
    )
    _allowed_suffixes = {".md"}

    @router.get("/references/{file_path:path}")
    @router.get("/docs/{file_path:path}")
    def serve_report_file(file_path: str):
        if not file_path or Path(file_path).suffix.lower() not in _allowed_suffixes:
            return JSONResponse({"error": "not found"}, status_code=404)
        for base in _allowed_dirs:
            base_resolved = base.resolve()
            candidate = (base_resolved / file_path).resolve()
            try:
                candidate.relative_to(base_resolved)
            except ValueError:
                continue  # 路径穿越，拒绝
            if candidate.is_file():
                return FileResponse(candidate, media_type="text/markdown; charset=utf-8")
        return JSONResponse({"error": "not found"}, status_code=404)

    return router

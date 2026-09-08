"""InterviewEngine — 求职面试备战知识库引擎。

数据源是外部「三层求职知识库」（root 由 ``[interview] root`` / 环境变量
``OPENBILICLAW_INTERVIEW_ROOT`` / 默认路径三级解析），目录形态：

::

    <root>/
    ├── 00_总索引.md
    ├── _系统_知识库引擎/        # 系统层：数据表 + knowledge.db + 脚本 + 规范
    │   ├── 数据/01_岗位表.csv … 07_概念关系表.csv, knowledge.db
    │   └── scripts/kb.py …
    ├── 01_原始资料库/           # 底层：原始材料（只读）
    ├── 02_方向知识库/           # 中层：方法论（广告算法/推荐/数据科学/SQL/面试方法论）
    └── 03_岗位弹药库/           # 上层：每岗位一个备战包目录

本引擎复刻并规范化原 ``scripts/kb.py`` 的全部检索能力（去掉硬编码路径、
改为可配置 root、统一返回结构化数据），供 CLI / API 复用。
"""

from __future__ import annotations

import csv
import os
import re
import sqlite3
import time
from datetime import date
from pathlib import Path
from typing import Any

#: 默认求职知识库根目录：随项目走（<项目根>/求职知识库/），
#: 可被 [interview] root / OPENBILICLAW_INTERVIEW_ROOT 覆盖
DEFAULT_INTERVIEW_ROOT = str(Path(__file__).resolve().parents[3] / "求职知识库")
#: 环境变量覆盖项（优先级高于配置文件）
ENV_INTERVIEW_ROOT = "OPENBILICLAW_INTERVIEW_ROOT"

#: 全文检索覆盖的目录（相对 root）：三层 + 工作资料 + 系统层规范
SEARCH_REL_DIRS = (
    "02_方向知识库",
    "03_岗位弹药库",
    "01_原始资料库/腾讯文档资料",
    "01_原始资料库/解码文本",
    "01_原始资料库/工作资料_腾讯",
    "01_原始资料库/工作资料_微视",
    "_系统_知识库引擎/规范",
)
#: 只检索 2MB 以内文本，避免误读大文件
MAX_SEARCH_BYTES = 2 * 1024 * 1024
SEARCH_EXTS = (".md", ".txt")


def resolve_root(cfg_root: str | None = None) -> str:
    """按 配置 → 环境变量 → 默认路径 的顺序解析知识库根目录。"""
    env_root = os.environ.get(ENV_INTERVIEW_ROOT, "").strip()
    for cand in (cfg_root or "", env_root, DEFAULT_INTERVIEW_ROOT):
        if cand.strip():
            return cand.strip()
    return ""


class InterviewEngine:
    """求职面试备战知识库的统一访问入口。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()

    # ── 基础路径 ──────────────────────────────────────────────
    @property
    def is_configured(self) -> bool:
        """根目录是否已配置且包含系统引擎目录。"""
        return self.root.is_dir() and (self.root / "_系统_知识库引擎").is_dir()

    @property
    def engine_dir(self) -> Path:
        return self.root / "_系统_知识库引擎"

    @property
    def data_dir(self) -> Path:
        return self.engine_dir / "数据"

    def _require_configured(self) -> None:
        if not self.is_configured:
            raise ValueError(
                f"求职知识库未配置或目录不存在: {self.root}（请检查 [interview] root）"
            )

    # ── 数据表读取 ────────────────────────────────────────────
    def _read_csv(self, name: str) -> list[dict[str, str]]:
        """读取引擎数据表（UTF-8），文件不存在时返回空列表。"""
        path = self.data_dir / name
        if not path.exists():
            return []
        try:
            with path.open(encoding="utf-8", newline="") as f:
                return [dict(row) for row in csv.DictReader(f)]
        except (OSError, UnicodeDecodeError, csv.Error):
            return []

    # ── 岗位 ──────────────────────────────────────────────────
    def jobs(self, keyword: str | None = None) -> list[dict[str, str]]:
        """岗位列表（01_岗位表.csv），可按公司/岗位/主打方向过滤。"""
        rows = self._read_csv("01_岗位表.csv")
        if not keyword:
            return rows
        kw = keyword.strip()
        return [
            r
            for r in rows
            if kw in r.get("公司", "") or kw in r.get("岗位", "") or kw in r.get("主打方向", "")
        ]

    def job_count(self) -> int:
        return len(self._read_csv("01_岗位表.csv"))

    # ── 全文检索 ──────────────────────────────────────────────
    def search(self, keyword: str, max_hits: int = 40) -> list[dict[str, Any]]:
        """在三层 + 工作资料 + 系统层规范 中全文检索关键词。

        每个文件只返回第一个命中行（与 kb.py 一致），按目录顺序聚合。
        """
        self._require_configured()
        if not keyword.strip():
            return []
        pat = re.compile(re.escape(keyword.strip()), re.IGNORECASE)
        hits: list[dict[str, Any]] = []
        for rel_dir in SEARCH_REL_DIRS:
            base = self.root / rel_dir
            if not base.is_dir():
                continue
            for fp in sorted(base.rglob("*")):
                if not fp.is_file() or not fp.name.lower().endswith(SEARCH_EXTS):
                    continue
                try:
                    if fp.stat().st_size > MAX_SEARCH_BYTES:
                        continue
                    lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue
                for idx, line in enumerate(lines, start=1):
                    if pat.search(line):
                        snippet = line.strip()
                        if len(snippet) > 90:
                            snippet = snippet[:90] + "…"
                        hits.append(
                            {
                                "path": str(fp.relative_to(self.root)),
                                "line": idx,
                                "snippet": snippet,
                            }
                        )
                        break
                if len(hits) >= max_hits:
                    return hits
        return hits

    # ── 真实数字 ──────────────────────────────────────────────
    def numbers(self, keyword: str | None = None) -> list[dict[str, str]]:
        """真实数字表（03_真实数字表.csv）——口径权威源，禁止编造。"""
        rows = self._read_csv("03_真实数字表.csv")
        if not keyword:
            return rows
        kw = keyword.strip()
        return [
            r
            for r in rows
            if kw in r.get("数字", "")
            or kw in r.get("口径", "")
            or kw in r.get("公司/项目", "")
            or kw in r.get("来源", "")
        ]

    # ── 项目 ──────────────────────────────────────────────────
    def projects(self, keyword: str | None = None) -> list[dict[str, str]]:
        """项目库（02_项目表.csv），可按项目名/公司/可讲要点过滤。"""
        rows = self._read_csv("02_项目表.csv")
        if not keyword:
            return rows
        kw = keyword.strip()
        return [
            r
            for r in rows
            if kw in r.get("项目名", "") or kw in r.get("公司", "") or kw in r.get("可讲要点", "")
        ]

    # ── 面试题索引 ────────────────────────────────────────────
    def questions(self, company: str | None = None) -> list[dict[str, str]]:
        """面试题索引（05_面试题索引.csv），可按公司过滤（含跨岗位）。"""
        rows = self._read_csv("05_面试题索引.csv")
        if not company:
            return rows
        kw = company.strip()
        return [r for r in rows if kw in r.get("公司", "") or r.get("公司", "") == "跨岗位"]

    # ── 方向 ──────────────────────────────────────────────────
    def directions(self) -> list[dict[str, Any]]:
        """02_方向知识库 下全部方向及文档清单。"""
        base = self.root / "02_方向知识库"
        if not base.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for d in sorted(p.name for p in base.iterdir() if p.is_dir()):
            docs = [p.name for p in sorted((base / d).glob("*.md")) if p.is_file()]
            out.append({"direction": d, "docs": docs})
        return out

    # ── 全库索引（knowledge.db，缺失时回退 CSV） ───────────────
    INDEX_LAYERS = ("01_原始资料库", "02_方向知识库", "03_岗位弹药库")
    INDEX_FIELDS = [
        "路径",
        "层",
        "子层",
        "类型",
        "文件名",
        "扩展名",
        "大小KB",
        "修改日期",
    ]
    EXT_TYPE = {
        ".md": "文档",
        ".markdown": "文档",
        ".txt": "文本",
        ".doc": "Word",
        ".docx": "Word",
        ".pdf": "PDF",
        ".xls": "表格",
        ".xlsx": "表格",
        ".csv": "表格",
        ".ppt": "PPT",
        ".pptx": "PPT",
        ".png": "图片",
        ".jpg": "图片",
        ".jpeg": "图片",
        ".gif": "图片",
        ".webp": "图片",
        ".zip": "压缩包",
        ".rar": "压缩包",
        ".7z": "压缩包",
        ".html": "网页",
        ".htm": "网页",
        ".json": "数据",
        ".py": "脚本",
        ".sh": "脚本",
        ".mp4": "视频",
        ".mov": "视频",
        ".mp3": "音频",
        ".wav": "音频",
    }

    @staticmethod
    def _is_junk(fn: str) -> bool:
        return fn.startswith("._") or fn == ".DS_Store" or fn.startswith("~$")

    def rebuild_index(self) -> dict[str, Any]:
        """重建全库文件索引：扫描三层 → 06_全库文件索引.csv + knowledge.db。

        移植自知识库 ``_系统_知识库引擎/scripts/build_index.py``，
        root 可配置、返回结构化统计；覆盖重建，不动原始文件。
        """
        self._require_configured()
        rows: list[dict[str, Any]] = []
        for layer in self.INDEX_LAYERS:
            base = self.root / layer
            if not base.is_dir():
                continue
            for root_dir, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                parts = Path(root_dir).relative_to(self.root).parts
                sub = parts[1] if len(parts) > 1 else (parts[0] if len(parts) == 1 else "")
                for fn in sorted(files):
                    if self._is_junk(fn):
                        continue
                    fp = os.path.join(root_dir, fn)
                    rel = os.path.relpath(fp, self.root)
                    try:
                        size = os.path.getsize(fp)
                        mtime = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(fp)))
                    except OSError:
                        size, mtime = 0, ""
                    ext = os.path.splitext(fn)[1].lower()
                    rows.append(
                        {
                            "路径": rel,
                            "层": layer,
                            "子层": sub,
                            "类型": self.EXT_TYPE.get(ext, "其他"),
                            "文件名": fn,
                            "扩展名": ext,
                            "大小KB": round(size / 1024, 1),
                            "修改日期": mtime,
                        }
                    )

        # 写 CSV
        self.data_dir.mkdir(parents=True, exist_ok=True)
        csv_path = self.data_dir / "06_全库文件索引.csv"
        fields = self.INDEX_FIELDS
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)

        # 写 SQLite（覆盖重建）
        db_path = self.data_dir / "knowledge.db"
        if db_path.exists():
            db_path.unlink()
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"""CREATE TABLE file_index(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    {",".join(f'"{c}" TEXT' for c in fields)})"""
            )
            cur.execute('CREATE INDEX idx_layer ON file_index("层")')
            cur.execute('CREATE INDEX idx_sub ON file_index("子层")')
            cur.execute('CREATE INDEX idx_type ON file_index("类型")')
            cur.executemany(
                f"INSERT INTO file_index({','.join(fields)})\n"
                f"    VALUES({','.join(':' + f for f in fields)})",
                rows,
            )
            cur.execute(
                """CREATE VIEW layer_stats AS
                SELECT "层" AS 层, COUNT(*) AS 文件数,
                    SUM(CASE WHEN 类型='文档' OR 类型='文本' THEN 1 ELSE 0 END) AS 可检索文本
                FROM file_index GROUP BY 层"""
            )
            conn.commit()
        finally:
            conn.close()

        return {
            "total": len(rows),
            "per_layer": {
                layer: sum(1 for r in rows if r["层"] == layer) for layer in self.INDEX_LAYERS
            },
            "csv": str(csv_path),
            "db": str(db_path),
        }

    def doctor(self, *, fix: bool = False, full: bool = False) -> dict[str, Any]:
        """知识库健康检查（移植自 scripts/doctor.py）。

        C1 题索引引用 / C2 岗位目录 / C3 日志岗位对齐 / C4 数字表完整 /
        C5 索引新鲜度（仅 full）。``fix=True`` 且索引过期时自动重建索引。
        """
        self._require_configured()
        checks: list[dict[str, Any]] = []

        def _check(cid: str, name: str, issues: list[str]) -> None:
            checks.append({"id": cid, "name": name, "passed": not issues, "issues": issues})

        # C1 题索引引用
        issues: list[str] = []
        for i, r in enumerate(self._read_csv("05_面试题索引.csv"), start=2):
            loc = (r.get("答案位置") or "").strip()
            if not loc:
                issues.append(f"第{i}行 答案位置为空: {(r.get('题目') or '')[:30]}")
                continue
            target = Path(loc) if os.path.isabs(loc) else self.root / loc
            if not target.exists():
                issues.append(f"第{i}行 答案位置不存在: {loc}")
        _check("C1", "题索引引用", issues)

        # C2 岗位目录
        issues = []
        job_names: list[str] = []
        for i, r in enumerate(self._read_csv("01_岗位表.csv"), start=2):
            name = (r.get("公司") or "").strip()
            if name:
                job_names.append(name)
            d = (r.get("备战目录") or "").strip()
            if not d:
                issues.append(f"第{i}行 备战目录为空: {name}")
                continue
            target = Path(d) if os.path.isabs(d) else self.root / d
            if not target.is_dir():
                issues.append(f"第{i}行 备战目录不存在: {d}")
        _check("C2", "岗位目录", issues)

        # C3 日志岗位对齐
        issues = []
        for i, r in enumerate(self._read_csv("04_面试日志.csv"), start=2):
            c = (r.get("公司") or "").strip()
            if c and c not in job_names:
                issues.append(f"第{i}行 公司未登记在岗位表: {c}")
        _check("C3", "日志岗位对齐", issues)

        # C4 数字表完整
        issues = []
        for i, r in enumerate(self._read_csv("03_真实数字表.csv"), start=2):
            if not (r.get("数字") or "").strip():
                issues.append(f"第{i}行 数字为空")
            if not (r.get("口径") or "").strip():
                issues.append(f"第{i}行 口径为空: {(r.get('数字') or '')}")
            if not (r.get("来源") or "").strip():
                issues.append(f"第{i}行 来源为空: {(r.get('数字') or '')}")
        _check("C4", "数字表完整", issues)

        # C5 索引新鲜度（仅 full）
        fixed = False
        if full:
            issues = []
            db_count = 0
            db_path = self.data_dir / "knowledge.db"
            if db_path.exists():
                try:
                    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                    db_count = conn.execute("SELECT COUNT(*) FROM file_index").fetchone()[0]
                    conn.close()
                except sqlite3.Error:
                    db_count = -1
            actual = 0
            for layer in self.INDEX_LAYERS:
                base = self.root / layer
                if base.is_dir():
                    for _root_dir, _dirs, files in os.walk(base):
                        actual += sum(1 for fn in files if not self._is_junk(fn))
            if db_count != actual:
                issues.append(f"knowledge.db={db_count} 实际文件={actual}，需重建索引")
                if fix:
                    self.rebuild_index()
                    fixed = True
            _check("C5", "索引新鲜度", issues)

        passed = all(c["passed"] for c in checks)
        return {
            "passed": passed,
            "checks": checks,
            "fixed": fixed,
            "fix_available": full,
        }

    def index(
        self, keyword: str | None = None, layer: str | None = None, limit: int = 50
    ) -> list[dict[str, str]]:
        """全库文件索引查询。优先 knowledge.db，其次 06_全库文件索引.csv。"""
        if layer:
            layer = {
                "01": "01_原始资料库",
                "02": "02_方向知识库",
                "03": "03_岗位弹药库",
            }.get(layer, layer)
        db_path = self.data_dir / "knowledge.db"
        if db_path.exists():
            return self._index_from_db(keyword, layer, limit)
        rows = self._read_csv("06_全库文件索引.csv")
        if keyword:
            kw = keyword.strip()
            rows = [
                r
                for r in rows
                if kw in r.get("文件名", "") or kw in r.get("子层", "") or kw in r.get("路径", "")
            ]
        if layer:
            rows = [r for r in rows if r.get("层", "") == layer]
        return rows[:limit]

    def _index_from_db(
        self, keyword: str | None, layer: str | None, limit: int
    ) -> list[dict[str, str]]:
        db_path = self.data_dir / "knowledge.db"
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            return []
        try:
            cur = conn.cursor()
            sql = "SELECT 路径, 层, 子层, 类型 FROM file_index WHERE 1=1"
            params: list[str] = []
            if layer:
                sql += " AND 层=?"
                params.append(layer)
            if keyword:
                sql += " AND (文件名 LIKE ? OR 子层 LIKE ? OR 路径 LIKE ?)"
                kw = f"%{keyword.strip()}%"
                params += [kw, kw, kw]
            sql += " ORDER BY 层, 子层, 路径 LIMIT ?"
            params.append(str(limit))
            rows = cur.execute(sql, params).fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            conn.close()
        return [{"路径": r[0], "层": r[1], "子层": r[2], "类型": r[3]} for r in rows]

    # ── 面试日志 ──────────────────────────────────────────────
    def logs(self) -> list[dict[str, str]]:
        """面试日志（04_面试日志.csv），倒序返回（最新在前）。"""
        rows = self._read_csv("04_面试日志.csv")
        return list(reversed(rows))

    def add_log(self, company: str, rnd: str, points: str) -> int:
        """追加一条面试日志（只追加，不修改历史行），返回新日志序号。"""
        self._require_configured()
        rows = self._read_csv("04_面试日志.csv")
        new_id = len(rows) + 1
        row = {
            "日期": date.today().isoformat(),
            "公司": company.strip(),
            "轮次": rnd.strip(),
            "面试官角色": "",
            "被问要点": points.strip(),
            "复盘": "待复盘",
            "复盘文档": "",
        }
        path = self.data_dir / "04_面试日志.csv"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="") as f:
                csv.writer(f).writerow(list(row.keys()))
        with path.open("a", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(list(row.values()))
        return new_id

    # ── 速记卡 ────────────────────────────────────────────────
    def _job_prep(self, rel_dir: str | None) -> dict[str, Any]:
        """读取岗位目录下的备战弹药：03_速成包（速记卡/预测题库/速成问答）+ 02_备战资料清单。

        只读、不修改；速记卡文件全文纳入（截断），其余只列文件名与行数。
        """
        if not rel_dir:
            return {"dir": None, "quick_card": "", "quick_pack": [], "prep_docs": []}
        base = self.root / rel_dir
        if not base.is_dir():
            return {"dir": str(base), "quick_card": "", "quick_pack": [], "prep_docs": []}

        quick_card = ""
        quick_pack: list[dict[str, Any]] = []
        pack_dir = base / "03_速成包"
        if pack_dir.is_dir():
            for fp in sorted(pack_dir.glob("*.md")):
                try:
                    lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    lines = []
                if "速记卡" in fp.name:
                    quick_card = "\n".join(lines)[:4000]
                quick_pack.append({"name": fp.name, "lines": len(lines)})

        prep_docs: list[dict[str, Any]] = []
        prep_dir = base / "02_面试备战资料"
        if prep_dir.is_dir():
            for fp in sorted(prep_dir.glob("*.md")):
                try:
                    lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    lines = []
                prep_docs.append({"name": fp.name, "lines": len(lines)})

        return {
            "dir": str(base),
            "quick_card": quick_card,
            "quick_pack": quick_pack,
            "prep_docs": prep_docs,
        }

    def speed_card(self, company: str) -> dict[str, Any]:
        """一键组装某公司的面试速记卡。

        岗位 + 数字 + 项目 + 题库入口 + 岗位定制弹药（速成包/备战资料）。
        """
        jobs = self.jobs(company)
        if not jobs:
            raise KeyError(f"岗位表中未找到: {company}")
        job = jobs[0]

        nums = self.numbers(company)
        if not nums:
            nums = self._read_csv("03_真实数字表.csv")
        seen: set[tuple[str, str, str]] = set()
        numbers: list[dict[str, str]] = []
        for r in nums:
            key = (r.get("数字", ""), r.get("口径", ""), r.get("公司/项目", ""))
            if key not in seen:
                seen.add(key)
                numbers.append(r)

        projs = [r for r in self._read_csv("02_项目表.csv") if company in r.get("公司", "")]
        if not projs:
            projs = self._read_csv("02_项目表.csv")

        return {
            "job": job,
            "numbers": numbers[:15],
            "projects": projs,
            "questions": self.questions(company),
            "prep": self._job_prep(job.get("备战目录")),
        }

    # ── 总览 ──────────────────────────────────────────────────
    def overview(self) -> dict[str, Any]:
        """系统总览：根目录信息 + 岗位/项目/数字/日志/方向/规范统计。"""
        spec_dir = self.engine_dir / "规范"
        specs = [p.name for p in sorted(spec_dir.glob("*.md"))] if spec_dir.is_dir() else []
        return {
            "root": str(self.root),
            "configured": self.is_configured,
            "jobs": self.jobs(),
            "projects": self.projects(),
            "number_count": len(self._read_csv("03_真实数字表.csv")),
            "question_count": len(self._read_csv("05_面试题索引.csv")),
            "log_count": len(self._read_csv("04_面试日志.csv")),
            "directions": [d["direction"] for d in self.directions()],
            "specs": specs,
        }

    # ── 新岗位建档（scaffold） ────────────────────────────────
    def scaffold(self, company: str, role: str) -> dict[str, Any]:
        """按统一规范新建岗位备战包目录（01/02/03 三件套空目录）。

        目录已存在时不覆盖、不写入任何内容，仅返回现状。
        """
        self._require_configured()
        company = company.strip()
        role = role.strip()
        if not company or not role:
            raise ValueError("公司名与岗位名不能为空")
        target = self.root / "03_岗位弹药库" / f"{company}-{role}-面试准备"
        subs = [
            "01_岗位与公司信息",
            "02_面试备战资料",
            "03_速成包",
        ]
        created: list[str] = []
        existing: list[str] = []
        for sub in subs:
            p = target / sub
            if p.exists():
                existing.append(sub)
            else:
                p.mkdir(parents=True, exist_ok=True)
                created.append(sub)
        return {
            "company": company,
            "role": role,
            "path": str(target),
            "created": created,
            "existing": existing,
            "name": target.name,
        }

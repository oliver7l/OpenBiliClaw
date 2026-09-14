#!/usr/bin/env python3
"""扫描 简历库/ 与 求职知识库 内全部简历相关文件，重建 data/resume.db 索引。

用途：简历资产统一台账——任何投递前先查这里，避免改错散落副本。
运行：.venv/bin/python scripts/resume_library/build_resume_index.py
幂等：每次全量重建（drop & recreate）。
"""
import hashlib
import os
import re
import sqlite3
import sys
from datetime import datetime

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
KB = os.path.join(ROOT, "求职知识库")
DB = os.path.join(ROOT, "data", "resume.db")

SKIP_DIRS = {"awesome-material-master", ".workbuddy", "node_modules", ".venv", ".git"}
RESUME_PAT = re.compile(r"简历|resume|童力", re.IGNORECASE)
LIB = "简历库"  # 2026-09-14 上移至项目根

# (扫描根, rel_path 基准)：简历库文件 rel 相对项目根，库外文件 rel 相对 求职知识库
SCAN_ROOTS = [
    (os.path.join(ROOT, LIB), ROOT),
    (KB, KB),
]

CATEGORIES = [
    (f"{LIB}/00_事实源/", "事实源", "现役"),
    (f"{LIB}/10_母版/", "母版", "现役"),
    (f"{LIB}/20_公司定制版/", "公司定制版", "现役"),
    (f"{LIB}/30_原始简历档案/腾讯文档版/", "投递留存", "档案"),
    (f"{LIB}/30_原始简历档案/", "原始档案", "档案"),
    (f"{LIB}/35_解码文本/", "解码文本", "档案"),
    (f"{LIB}/90_污染备份_20260913/", "污染备份", "弃用勿投"),
]

# 童力-公司-岗位描述-地点/日期 的文件名尽力解析
NAME_PAT = re.compile(
    r"^童力(?:简历)?-(?P<company>[^-]+?)-(?P<rest>.+?)\.(?:md|docx|doc|pdf|txt)$"
)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def categorize(rel):
    for prefix, cat, status in CATEGORIES:
        if rel.startswith(prefix):
            return cat, status
    return "岗位材料副本", "副本"


def parse_name(fn):
    m = NAME_PAT.match(fn)
    company, position, vdate = "", "", ""
    if m:
        company = m.group("company")
        rest = m.group("rest")
        dm = re.search(r"(20\d{6})", rest)
        if dm:
            vdate = f"{dm.group(1)[:4]}-{dm.group(1)[4:6]}-{dm.group(1)[6:]}"
        position = re.sub(r"-(?:北京|上海|深圳|广州|杭州|新加坡|北京上海|深圳北京|深圳新加坡|广州北京)$", "", rest)
        position = re.sub(r"-20\d{6}$", "", position)
    return company, position, vdate


def main():
    rows = []
    for scan_root, rel_base in SCAN_ROOTS:
        for root, dirs, files in os.walk(scan_root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fn in files:
                if not RESUME_PAT.search(fn):
                    continue
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, rel_base)
                try:
                    st = os.stat(full)
                    digest = sha256_of(full)
                except OSError as e:
                    print(f"[SKIP] {rel}: {e}", file=sys.stderr)
                    continue
                cat, status = categorize(rel)
                company = position = vdate = ""
                if cat in ("母版", "公司定制版", "投递留存", "原始档案", "污染备份"):
                    company, position, vdate = parse_name(fn)
                if cat == "公司定制版":
                    company = rel.split("/")[2]  # 简历库/20_公司定制版/<公司>/文件
                note = ""
                if cat == "岗位材料副本":
                    note = "岗位文件夹自包含副本，勿单独修改；正文以简历库为准"
                rows.append((rel, cat, company, position, vdate, fn.rsplit(".", 1)[-1],
                             st.st_size, digest, status, note))

    # 去重标注：副本 sha 与库内文件相同时记录
    in_lib = {}
    for r in rows:
        if r[1] != "岗位材料副本":
            in_lib.setdefault(r[7], r[0])
    marked = []
    for r in rows:
        rel, cat, comp, pos, vd, ext, size, digest, status, note = r
        if cat == "岗位材料副本" and digest in in_lib:
            note = (note + "；" if note else "") + f"与 {in_lib[digest]} 内容相同（sha256 一致）"
        marked.append((rel, cat, comp, pos, vd, ext, size, digest, status, note))

    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("DROP TABLE IF EXISTS resume_files")
    con.execute("""
        CREATE TABLE resume_files (
            id INTEGER PRIMARY KEY,
            rel_path TEXT UNIQUE,
            category TEXT,
            company TEXT,
            position TEXT,
            version_date TEXT,
            ext TEXT,
            size INTEGER,
            sha256 TEXT,
            status TEXT,
            note TEXT,
            indexed_at TEXT
        )""")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    con.executemany(
        "INSERT INTO resume_files (rel_path,category,company,position,version_date,"
        "ext,size,sha256,status,note,indexed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [r + (now,) for r in marked])
    con.execute("CREATE INDEX IF NOT EXISTS idx_sha ON resume_files(sha256)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_cat ON resume_files(category)")
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM resume_files").fetchone()[0]
    dupes = con.execute(
        "SELECT COUNT(*) FROM (SELECT sha256 FROM resume_files GROUP BY sha256 HAVING COUNT(*)>1)"
    ).fetchone()[0]
    by_cat = con.execute(
        "SELECT category, COUNT(*) FROM resume_files GROUP BY category ORDER BY 1").fetchall()
    con.close()
    print(f"indexed: {total} files, dup groups: {dupes}")
    for c, k in by_cat:
        print(f"  {c}: {k}")


if __name__ == "__main__":
    main()

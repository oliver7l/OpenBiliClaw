"""Data Agent · SQL 护栏（执行前校验 + 兜底）。

对应面试弹药 §1 第 4 点：生成 SQL 白名单校验（只读、行数限制）。
面试答法原话：「金融场景合规也要求只读+行数限制——我练手项目里就是这么做的」。
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

ROW_CAP = 500          # 单次返回行数上限
CELL_CAP = 4000        # 单个单元格字符截断
TIMEOUT_S = 10         # 执行超时

# 禁止出现的词（写操作/危险语句一律拒）
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|reindex|ftruncate)\b",
    re.I,
)
# 允许的库文件白名单（防跨库 ATTACH 已被禁；这里限制 run 只对白名单库开连接）
ALLOWED_DBS = {"content.db", "diary.db", "interview.db", "resume.db", "pool.db", "knowledge.db"}

# 多库前缀：SQL 里写 content.db.articles 形式
_DB_DOT = re.compile(r"\b(content|diary|interview|resume|pool|knowledge)\.db\.", re.I)


class GuardError(Exception):
    """校验不通过。"""


def validate_sql(sql: str, allowed_tables: set[str] | None = None) -> str:
    """校验并规范化 SQL。返回清洗后的 SQL；不通过抛 GuardError。"""
    s = sql.strip().rstrip(";").strip()

    # 1) 单语句：不允许内部再带分号
    if ";" in s:
        raise GuardError("拒绝：多条语句（只允许单条查询）")

    # 2) 只允许 SELECT / WITH 开头
    if not re.match(r"^(select|with)\b", s, re.I):
        raise GuardError("拒绝：只允许 SELECT/WITH 查询")

    # 3) 危险词
    m = _FORBIDDEN.search(s)
    if m:
        raise GuardError(f"拒绝：包含写操作/危险关键词 {m.group(1).upper()}")

    # 4) 统一 db.table 前缀 → 纯表名（跨库在 Python 层分别执行不了，所以 catalog 已做库内消歧）
    s = _DB_DOT.sub("", s)

    # 5) 表白名单（若提供）
    if allowed_tables is not None:
        used = {t.lower() for t in re.findall(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)", s, re.I)}
        unknown = used - allowed_tables
        if unknown:
            raise GuardError(f"拒绝：访问了目录之外的表 {sorted(unknown)}")

    # 6) 强制 LIMIT（没有就补；超过 ROW_CAP 的会被截断）
    if not re.search(r"\blimit\s+\d+", s, re.I):
        s += f" LIMIT {ROW_CAP}"
    else:
        s = re.sub(r"\blimit\s+(\d+)", lambda m: f"LIMIT {min(int(m.group(1)), ROW_CAP)}", s, flags=re.I)

    return s


def run_sql(sql: str, dbs: list[str] | None = None) -> dict:
    """在只读连接上执行。返回 {columns, rows, truncated, elapsed_ms, dbs_used}。

    跨库表用 `db名.表名` 引用（如 content.db.articles），本函数自动拆库执行。
    """
    dbs = dbs or sorted(ALLOWED_DBS)

    # 判定用到的库：db. 前缀；无前缀时在所有库中找该表
    tables_used = [t.lower() for t in re.findall(r"\b(?:from|join)\s+([a-z_.][a-z0-9_.]*)", sql, re.I)]
    prefixed = [t for t in tables_used if "." in t]
    if prefixed:
        dbs_used = sorted({t.split(".")[0] + ".db" for t in prefixed})
    elif tables_used:
        dbs_used = _locate_tables(tables_used)
    else:
        dbs_used = ["content.db"]

    for d in dbs_used:
        if d not in ALLOWED_DBS:
            raise GuardError(f"拒绝：库 {d} 不在白名单")

    # SQLite 单连接跨库用 ATTACH——但我们禁 ATTACH。方案：同目录下用 URI 附加（RO）
    main = dbs_used[0]
    con = sqlite3.connect(f"file:{DATA_DIR / main}?mode=ro", uri=True, timeout=TIMEOUT_S)
    con.execute("PRAGMA query_only = 1")
    for d in dbs_used[1:]:
        alias = d.replace(".db", "")
        con.execute("ATTACH DATABASE ? AS ?", (f"file:{DATA_DIR / d}?mode=ro", alias))
        # 把无前缀表别名挂到 schema 上（SQLite 默认先找 main；附带库需显式 alias.table）
        # 简化：要求跨库 SQL 都写 db.table 形式，attach 只为引用 alias 表
    con.row_factory = sqlite3.Row
    start = time.time()
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(ROW_CAP + 1)
    except sqlite3.Error as e:
        raise GuardError(f"SQL 执行错误：{e}") from e
    elapsed = round((time.time() - start) * 1000)
    truncated = len(rows) > ROW_CAP
    rows = rows[:ROW_CAP]
    out_rows = []
    for r in rows:
        out_rows.append([
            (str(v)[:CELL_CAP] if isinstance(v, str) else v) for v in tuple(r)
        ])
    con.close()
    return {"columns": cols, "rows": out_rows, "truncated": truncated,
            "elapsed_ms": elapsed, "dbs_used": dbs_used}


def _locate_tables(tables: list[str]) -> list[str]:
    """无前缀表 → 在哪些库里存在。"""
    found: set[str] = set()
    for db in ALLOWED_DBS:
        path = DATA_DIR / db
        if not path.exists():
            continue
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        names = {r[0].lower() for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        if any(t in names for t in tables):
            found.add(db)
    return sorted(found) or ["content.db"]


def format_result(res: dict, max_rows: int = 20) -> str:
    cols, rows = res["columns"], res["rows"]
    if not cols:
        return "（无结果）"
    head = " | ".join(cols)
    sep = "-+-".join("-" * max(3, len(c)) for c in cols)
    body = "\n".join(" | ".join(("NULL" if v is None else str(v)) for v in r) for r in rows[:max_rows])
    tail = f"\n…（共 {len(rows)} 行{'，已截断' if res['truncated'] else ''}，{res['elapsed_ms']}ms，库:{','.join(res['dbs_used'])}）"
    return f"{head}\n{sep}\n{body}{tail}"


if __name__ == "__main__":
    # 自测
    ok_sql = "SELECT platform, COUNT(*) n, SUM(ok)*100.0/COUNT(*) rate FROM content.db.fetch_log GROUP BY platform ORDER BY n DESC"
    print(validate_sql(ok_sql)[:80], "…")
    bad = ["DELETE FROM articles", "SELECT 1; DROP TABLE articles", "PRAGMA table_info(articles)", "ATTACH DATABASE 'x' AS x"]
    for b in bad:
        try:
            validate_sql(b)
            print("❌ 漏拦:", b)
        except GuardError as e:
            print("✓", e)

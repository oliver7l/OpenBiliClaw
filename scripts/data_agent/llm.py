"""Data Agent · LLM 客户端（Ollama 本地模型）。

全链路本地可跑：NL→SQL 用 qwen2.5:7b，离线、免费、数据不出机。
"""
from __future__ import annotations

import json
import re
import urllib.request

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen2.5:7b"


def chat(prompt: str, system: str = "", temperature: float = 0.0, timeout: int = 120) -> str:
    payload = {
        "model": MODEL,
        "stream": False,
        "options": {"temperature": temperature},
        "messages": ([{"role": "system", "content": system}] if system else [])
        + [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return data.get("message", {}).get("content", "").strip()


SQL_SYSTEM = """你是 SQLite SQL 生成器。根据用户问题和给定的表结构，输出一条只读查询 SQL。
规则：
1. 只输出 SQL 本体，不要解释、不要 markdown 代码块。
2. 只允许 SELECT/WITH，单条语句。
3. 跨库表用「库名.表名」引用（如 content.db.articles），本会话可用库：content.db, diary.db, interview.db, resume.db, pool.db, knowledge.db。
4. 文本判空必须用 col IS NULL OR col = ''，不要直接 WHERE text_col。
5. 时间列多为 TEXT ISO 格式；本地日期用 date(x, 'localtime')。
6. 控制结果规模：聚合优先，明细最多 LIMIT 50。
7. 如果问题含糊（缺时间范围/缺维度），输出一行 JSON：{"clarify": "你要问清楚的问题"}；有把握时输出 SQL。"""


def nl_to_sql(question: str, schema_ctx: str, few_shot: str = "", temperature: float = 0.0) -> dict:
    """返回 {"sql": ...} 或 {"clarify": ...}。"""
    prompt = f"{schema_ctx}\n\n{few_shot}\n\n用户问题：{question}\n\n输出（SQL 或 clarify JSON）："
    raw = chat(prompt, system=SQL_SYSTEM, temperature=temperature)
    sql_or_clarify = _parse_output(raw)
    if "sql" in sql_or_clarify:
        sql_or_clarify["sql"] = _strip_db_prefix_alias(sql_or_clarify["sql"])
    return sql_or_clarify


def _parse_output(raw: str) -> dict:
    # 提取 SQL（容错：剥 markdown / 取首个 select...到结尾）
    m = re.search(r"```(?:sql)?\s*(.+?)```", raw, re.S)
    text = (m.group(1) if m else raw).strip()
    cm = re.search(r'\{\s*"clarify"', text, re.I)
    if cm or text.lower().startswith('{"clarify"'):
        try:
            return json.loads(text[text.index("{"): text.rindex("}") + 1])
        except Exception:
            pass
    sm = re.search(r"(?is)\b(with|select)\b.*", text)
    if sm:
        return {"sql": sm.group(0).strip().rstrip(";")}
    return {"clarify": f"模型输出无法解析：{text[:120]}"}


REPAIR_SYSTEM = """你是 SQL 修正器。给定：用户问题、被拒绝/执行失败的 SQL、报错信息、表结构目录。
任务：输出一条修正后的只读查询 SQL，解决报错所指的问题。
规则：
1. 只输出 SQL 本体，不要解释、不要 markdown。
2. 只允许 SELECT/WITH，单条语句。
3. 表名必须来自目录中的真实表（报错常因幻觉表名/列名，优先对照目录修正）。
4. 文本判空用 col IS NULL OR col = ''；时间列多为 TEXT ISO 格式。
5. 聚合优先，明细最多 LIMIT 50。
6. 若原 SQL 逻辑本身合理只是语法/表名错，做最小改动。"""


def repair_sql(question: str, bad_sql: str, error: str, schema_ctx: str) -> dict:
    """W2 语义修正器：拿报错上下文重生成。返回 {"sql": ...} 或 {"clarify": ...}。"""
    prompt = (
        f"{schema_ctx}\n\n用户问题：{question}\n\n"
        f"被拒绝/失败的 SQL：\n{bad_sql}\n\n报错信息：\n{error}\n\n输出修正后的 SQL："
    )
    raw = chat(prompt, system=REPAIR_SYSTEM, temperature=0.0)
    out = _parse_output(raw)
    if "sql" in out:
        out["sql"] = _strip_db_prefix_alias(out["sql"])
    return out


def _strip_db_prefix_alias(sql: str) -> str:
    """剥掉 `库.db.` 前缀后，为跨库查询补上 attach 别名前缀（diary.db.diary_entries → diary.diary_entries）。"""
    def _sub(m: re.Match) -> str:
        return f"{m.group(1)}.{m.group(2)}"
    return re.sub(r"\b(content|diary|interview|resume|pool|knowledge)\.db\.([a-z_][a-z0-9_]*)", _sub, sql, flags=re.I)


def nl_to_sql_vote(question: str, schema_ctx: str, few_shot: str = "", k: int = 3) -> dict:
    """W3 多采样投票：k 个采样（温度 0.7），以「规范化 SQL 指纹」多数派胜出。

    返回 {"sql": 多数派SQL, "votes": k, "agree": n} 或单样本结果；
    全部失败/无法解析时返回 {"clarify": ...}。
    """
    from collections import Counter

    def _fingerprint(sql: str) -> str:
        # 指纹：去空白/引号差异/尾分号、小写 —— 聚合同义写法归一
        s = re.sub(r'"([a-z_][a-z0-9_]*)"', r"\1", sql, flags=re.I)
        s = re.sub(r"'([^']*)'", r"'\1'", s)
        return re.sub(r"\s+", " ", s).strip().rstrip(";").lower()

    samples: list[str] = []
    for i in range(k):
        out = nl_to_sql(question, schema_ctx, few_shot=few_shot,
                        temperature=0.0 if i == 0 else 0.7)
        if "sql" in out:
            samples.append(out["sql"])
    if not samples:
        return {"clarify": f"{k} 次采样均未产出可解析 SQL"}
    cnt = Counter(_fingerprint(s) for s in samples)
    fp, agree = cnt.most_common(1)[0]
    for s in samples:
        if _fingerprint(s) == fp:
            return {"sql": s, "votes": k, "agree": agree}
    return {"sql": samples[0], "votes": k, "agree": agree}  # 理论不可达，兜底


if __name__ == "__main__":
    print(chat("用一句话回答：1+1=?"))

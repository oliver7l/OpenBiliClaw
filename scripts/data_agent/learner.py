"""Data Agent · W1 自学习错误回流。

失败样本落盘（data_agent_errors.jsonl），修正成功后沉淀为「经验对」
（data_agent_learned.json），few-shot 检索时与黄金集一起召回。

设计要点：
- 错误日志只追加（jsonl），方便事后分析失败模式分布；
- 经验对是幂等 dict（question -> fixed_sql），同一问题反复修正只保留最新；
- 经验对上限 200 条，防止无限膨胀稀释黄金集权重。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

_DIR = Path(__file__).resolve().parent
ERRORS_PATH = _DIR / "data_agent_errors.jsonl"
LEARNED_PATH = _DIR / "data_agent_learned.json"
MAX_LEARNED = 200


def log_error(question: str, sql: str, error: str, stage: str) -> None:
    """每次执行失败追加一条失败样本。stage: generate|validate|execute|repair|vote"""
    try:
        rec = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "question": question[:200],
            "sql": (sql or "")[:500],
            "error": str(error)[:300],
            "stage": stage,
        }
        with ERRORS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 学习层永不阻塞主链路


def record_fix(question: str, fixed_sql: str) -> bool:
    """修正成功 → 沉淀经验对（幂等，按 question 覆盖）。"""
    try:
        learned: dict[str, str] = {}
        if LEARNED_PATH.exists():
            learned = json.loads(LEARNED_PATH.read_text(encoding="utf-8"))
        learned[question.strip()] = fixed_sql.strip()
        # 超限则按插入序淘汰最早（dict 保序）
        while len(learned) > MAX_LEARNED:
            learned.pop(next(iter(learned)))
        LEARNED_PATH.write_text(
            json.dumps(learned, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        return True
    except Exception:
        return False


def load_learned() -> dict[str, str]:
    """经验对：question -> fixed_sql。few-shot 与黄金集同池召回。"""
    if not LEARNED_PATH.exists():
        return {}
    try:
        return json.loads(LEARNED_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def stats() -> dict:
    n_err = 0
    by_stage: dict[str, int] = {}
    if ERRORS_PATH.exists():
        for line in ERRORS_PATH.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                n_err += 1
                by_stage[r.get("stage", "?")] = by_stage.get(r.get("stage", "?"), 0) + 1
            except Exception:
                continue
    return {"errors_total": n_err, "by_stage": by_stage,
            "learned_pairs": len(load_learned())}

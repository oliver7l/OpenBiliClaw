#!/usr/bin/env python3
"""Data Agent · 问数入口（NL→SQL→护栏→执行→表格）。

用法：
    python3 agent.py ask "最近7天采集成功率怎么样"          # 全自动（口径模板优先，长尾走 LLM）
    python3 agent.py ask "为什么昨天采集掉了" --days 2      # 参数透传
    python3 agent.py metric 采集成功率 --days 14            # 直走口径层
    python3 agent.py sql "SELECT ..."                       # 直跑 SQL（仍过护栏）
    python3 agent.py eval                                   # 跑黄金评测集

对应微信支付-数据科学 JD：NL2SQL、自动化业务洞察、异常排查（归因入口）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import catalog as catalog_mod
import guard
import llm
import metrics as metrics_mod

# few-shot：从黄金集抽 3 条当示例（对应「模板+生成混合+检索增强」）
GOLDEN_PATH = Path(__file__).resolve().parent / "data_agent_golden.json"


def _few_shot(question: str = "", k: int = 3) -> str:
    """few-shot 检索：按问题相似度从黄金集召回 top-k 当示例（检索增强）。"""
    if not GOLDEN_PATH.exists():
        return ""
    try:
        items = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return ""
    shots = [it for it in items if it.get("sql")]
    if not shots:
        return ""
    if question:
        def score(it: dict) -> int:
            q2, hits = it["question"], 0
            for i in range(len(question) - 1):
                if question[i : i + 2] in q2:
                    hits += 1
            return hits

        shots = sorted(shots, key=lambda it: -score(it))[:k]
    else:
        shots = shots[:k]
    parts = ["参考示例（问题→SQL）："]
    for it in shots:
        parts.append(f"Q: {it['question']}\nSQL: {it['sql']}")
    return "\n\n".join(parts)


def _allowed_tables(catalog: dict) -> set[str]:
    """目录里所有表名（小写）→ 护栏表白名单。"""
    return {t.lower() for d in catalog["dbs"].values() for t in d["tables"]}


def ask(question: str, days: int | None = None, use_llm: bool = True) -> int:
    t0 = time.time()
    print(f"❓ {question}\n")

    # ── 路径 1：口径层命中（高频指标，模板直达，准确率≈100%）──
    m = metrics_mod.match_metric(question)
    if m:
        d = days or _extract_days(question) or 7
        sql = metrics_mod.fill_metric(m, d)
        print(f"🎯 命中口径模板【{m.name}】（{m.desc}，N={d}天）")
        res = guard.run_sql(guard.validate_sql(sql))
        print(guard.format_result(res))
        _note_interpret(m.name, res)
        return 0

    # ── 路径 2：LLM 生成（长尾探索查询）──
    if not use_llm:
        print("未命中口径模板（--no-llm 模式不启用生成）。可用指标：")
        for mm in metrics_mod.METRICS:
            print(f"  - {mm.name}: {mm.desc}")
        return 1

    print("🧠 未命中口径模板，走 LLM 生成（qwen2.5:7b，本地）…")
    # 护栏前置：破坏性意图不进 LLM（纵深防御第一层）
    if re.search(r"删除|删掉|清空|改一下|修改|写入|插入|更新.*表|drop|delete|truncate", question, re.I):
        print("🛡️ 护栏拦截：检测到写操作意图。本 Agent 对你的数据库只有只读权限，请换个问法（例如查询类问题）。")
        return 3
    cat = catalog_mod.load_catalog()
    ctx = catalog_mod.render_catalog_context(cat)
    out = llm.nl_to_sql(question, ctx, few_shot=_few_shot(question))
    if "clarify" in out:
        print(f"🤔 置信不足，反问澄清：{out['clarify']}")
        return 2
    sql = out["sql"]
    print(f"📝 生成 SQL：\n   {sql}\n")
    try:
        checked = guard.validate_sql(sql)
        res = guard.run_sql(checked)
    except guard.GuardError as e:
        print(f"🛡️ 护栏拦截：{e}")
        return 3
    print(guard.format_result(res))
    print(f"\n（路径：LLM生成 → 护栏通过 → 执行 {round((time.time()-t0)*1000)}ms）")
    return 0


def _extract_days(q: str) -> int | None:
    m = re.search(r"(\d+)\s*天", q)
    if m:
        return int(m.group(1))
    if "昨天" in q:
        return 1
    if "本周" in q or "这周" in q:
        return 7
    if "本月" in q:
        return 30
    return None


def _note_interpret(name: str, res: dict) -> None:
    """模板路径的一句话自动解读（自动化业务洞察的最小形态）。"""
    if not res["rows"]:
        print("\n💡 解读：窗口期内无数据。")
        return
    if name == "采集成功率":
        worst = min(res["rows"], key=lambda r: r[-1] if isinstance(r[-1], (int, float)) else 999)
        if isinstance(worst[-1], (int, float)) and worst[-1] < 80:
            print(f"\n💡 解读：{worst[0]} 成功率仅 {worst[-1]}%，建议先查该平台失败原因分布。")
        else:
            print("\n💡 解读：各平台成功率均 ≥80%，采集线整体健康。")
    elif name == "投递漏斗":
        print("\n💡 解读：面试推进看 stage 分布；『已终止』占比过高时需复盘投递质量。")


def cmd_metric(name: str, days: int) -> int:
    for m in metrics_mod.METRICS:
        if m.name == name or name in m.aliases:
            res = guard.run_sql(guard.validate_sql(metrics_mod.fill_metric(m, days)))
            print(f"【{m.name}】{m.desc}\n")
            print(guard.format_result(res))
            return 0
    print(f"未找到指标 {name}；可用：{', '.join(m.name for m in metrics_mod.METRICS)}")
    return 1


def cmd_sql(sql: str) -> int:
    try:
        checked = guard.validate_sql(sql)
        res = guard.run_sql(checked)
    except guard.GuardError as e:
        print(f"🛡️ 护栏拦截：{e}")
        return 3
    print(guard.format_result(res))
    return 0


def cmd_eval(verbose: bool = False) -> int:
    """评测体系：黄金问答集，执行结果一致性打分（对应 JD「评测体系搭建」）。"""
    items = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    total = passed = 0
    print(f"{'问题':<30} 路径      结果")
    print("-" * 70)
    for it in items:
        q = it["question"]
        m = metrics_mod.match_metric(q)
        if m and "sql" in it:
            got_sql = metrics_mod.fill_metric(m, it.get("days", 7))
            path = "模板"
        elif "sql" not in it:
            # 应拒答题：走完整 ask 链路（含护栏前置），确认被拦/被澄清
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = ask(q, use_llm=True)
            total += 1
            ok = rc in (2, 3)  # 2=澄清 3=护栏拦截
            passed += ok
            print(f"{q:<30} 拒答      {'PASS(正确拒答)' if ok else 'FAIL(未被拦截)'}")
            if verbose and not ok:
                print(buf.getvalue()[:300])
            continue
        else:
            cat = catalog_mod.load_catalog()
            out = llm.nl_to_sql(q, catalog_mod.render_catalog_context(cat), few_shot=_few_shot(q))
            if "clarify" in out:
                total += 1
                ok = bool(it.get("expect_clarify"))
                passed += ok
                print(f"{q:<30} 生成      {'PASS(正确拒答)' if ok else 'FAIL(应给SQL)'}")
                continue
            got_sql = out["sql"]
            path = "生成"
        expected_sql = it["sql"]
        total += 1
        try:
            cat = catalog_mod.load_catalog()
            r1 = guard.run_sql(guard.validate_sql(expected_sql, allowed_tables=_allowed_tables(cat)))
            r2 = guard.run_sql(guard.validate_sql(got_sql, allowed_tables=_allowed_tables(cat)))
            ok = _rows_equal(r1["rows"], r2["rows"])
        except guard.GuardError as e:
            ok = False
            if verbose:
                print(f"   护栏错误: {e}")
        passed += ok
        print(f"{q:<30} {path:<9} {'PASS' if ok else 'FAIL'}")
        if not ok and verbose:
            print(f"   期望SQL: {expected_sql[:100]}")
            print(f"   生成SQL: {got_sql[:100]}")
    print("-" * 70)
    print(f"执行准确率: {passed}/{total} = {round(passed*100/max(total,1),1)}%")
    return 0 if passed == total else 1


def _rows_equal(a: list, b: list) -> bool:
    """行数一致 + 数值容差比较（忽略顺序和浮点尾差）。"""
    if len(a) != len(b):
        return False
    ka = sorted([tuple(str(x) if not isinstance(x, float) else round(x, 2) for x in r) for r in a])
    kb = sorted([tuple(str(x) if not isinstance(x, float) else round(x, 2) for x in r) for r in b])
    return ka == kb


def main() -> int:
    ap = argparse.ArgumentParser(description="OpenBiliClaw Data Agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_ask = sub.add_parser("ask")
    p_ask.add_argument("question")
    p_ask.add_argument("--days", type=int)
    p_ask.add_argument("--no-llm", action="store_true")
    p_m = sub.add_parser("metric")
    p_m.add_argument("name")
    p_m.add_argument("--days", type=int, default=7)
    p_s = sub.add_parser("sql")
    p_s.add_argument("sql")
    p_e = sub.add_parser("eval")
    p_e.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if args.cmd == "ask":
        return ask(args.question, days=args.days, use_llm=not args.no_llm)
    if args.cmd == "metric":
        return cmd_metric(args.name, args.days)
    if args.cmd == "sql":
        return cmd_sql(args.sql)
    if args.cmd == "eval":
        return cmd_eval(args.verbose)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

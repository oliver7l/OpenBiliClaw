"""Report rendering for offline evaluation runs.

Produces a machine-readable JSON blob and a human-readable Markdown report
with mean ± std for every method, plus an explicit disclosure of the
evaluation assumptions.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def _fmt(value: float, digits: int = 3) -> str:
    if value != value:  # NaN
        return "n/a"
    return f"{value:.{digits}f}"


def _fmt_pair(agg: dict[str, Any], key: str) -> str:
    if key not in agg:
        return "n/a"
    mean = agg[key]["mean"]
    std = agg[key]["std"]
    return f"{_fmt(mean)} ± {_fmt(std)}"


def render_markdown(
    eval_result: dict[str, Any],
    *,
    meta: dict[str, Any] | None = None,
    assumptions: str | None = None,
) -> str:
    k = eval_result.get("k", 10)
    methods = eval_result.get("methods", {})
    lines: list[str] = []
    lines.append("# 离线评估报告（Offline Eval）")
    lines.append("")
    if meta:
        lines.append("## 运行信息")
        for key, value in meta.items():
            lines.append(f"- **{key}**：{value}")
        lines.append("")
    lines.append(f"## 指标对比（K={k}，mean ± std）")
    lines.append("")
    all_keys: list[str] = []
    for m in methods.values():
        all_keys.extend(m.get("metrics", {}).keys())
    all_keys = sorted(set(all_keys))
    header = "| 指标 | " + " | ".join(methods.keys()) + " |"
    sep = "|" + "---|" * (len(methods) + 1)
    lines.append(header)
    lines.append(sep)
    for key in all_keys:
        row = f"| {key} | "
        row += " | ".join(
            _fmt_pair(methods[method]["metrics"], key) for method in methods
        )
        row += " |"
        lines.append(row)
    lines.append("")
    lines.append("## 口径说明")
    lines.append("")
    lines.append(
        "- **范式**：隐式反馈偏好预测评估（无曝光-点击数据，故不使用 CTR）。"
    )
    lines.append(
        "- **正样本**：events 中 favorite/like/article_finished/view 行为对应的候选池内容"
        "（强信号优先）。"
    )
    lines.append(
        "- **负样本**：候选池中用户未消费的内容"
        "（`未消费 ≠ 不喜欢` 的选择偏差已在设计中披露）。"
    )
    lines.append(
        "- **评估单元**：每个单元 = 用户画像快照 + 采样候选集（正+负），"
        "由推荐引擎真实排序逻辑打分。"
    )
    if assumptions:
        lines.append(f"- **附加说明**：{assumptions}")
    lines.append("")
    lines.append(f"*生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    return "\n".join(lines)


def render_json(eval_result: dict[str, Any], meta: dict[str, Any] | None = None) -> str:
    payload = {"meta": meta or {}, "result": eval_result}
    return json.dumps(payload, ensure_ascii=False, indent=2)

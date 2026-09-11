"""豆瓣深度画像报告：基于统计画像让 LLM 生成用户的观影/读书心路报告。

统计画像由 analytics.py 提供（确定性、不调 LLM）；本模块把统计摘要 + 近期代表
清单填进 prompt，调用 LLM 生成第一人称画像报告，并缓存到
``data/douban/profile_report.json``（已被 .gitignore）。非 force 时读缓存。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openbiliclaw.douban.analytics import DoubanAnalytics

if TYPE_CHECKING:
    from openbiliclaw.douban.store import DoubanStore

logger = logging.getLogger(__name__)

DEFAULT_CACHE = Path("data/douban/profile_report.json")


def build_profile_prompt(stats: dict) -> str:
    """从统计画像构造"我的观影/读书画像"prompt。"""
    era = stats.get("era_span", {})
    status_ratio = stats.get("status_ratio", {})
    total = stats.get("total", 0)
    recent = stats.get("recent_names", [])

    cat_parts = []
    dist = stats.get("type_distribution", {}).get("categories", {})
    for key, cat in dist.items():
        subs = ", ".join(f"{s['label']} {s['count']}" for s in cat.get("statuses", {}).values())
        cat_parts.append(f"{cat.get('label', key)}：{subs}")
    cat_str = "\n".join(cat_parts)

    recent_str = "\n".join(
        f"- {it.get('name', '')}（{it.get('category', '')} / {it.get('date', '无日期')}）"
        for it in recent[:40]
    )

    return f"""你是了解用户文化偏好的观察者，正回看 TA 的豆瓣书影音清单，生成画像报告。

请根据以下统计与近期清单，概括用户的观影 / 读书 / 听乐偏好、风格倾向与成长脉络。

## 总体
- 书影音总条目：{total} 条
- 实际消费（看过/读过/听过）：{status_ratio.get('collected', 0)} 条
- 占比：{status_ratio.get('collected_pct', 0)}%
- 最早年份：{era.get('earliest_year', '?')}，最近年份：{era.get('latest_year', '?')}
- 跨度：{era.get('span_years', 0)} 年

## 分类分布
{cat_str or '（暂无）'}

## 近期代表清单（近 {len(recent)} 条）
{recent_str or '（暂无）'}

请按以下结构生成报告（中文，第一人称"我"，真诚有洞察，不鸡汤不说教）：

1. **总体画像**：一句话概括"我是一个怎样的书影音消费者"
2. **类型偏好**：影视 / 书 / 音乐各自的口味倾向（结合清单里的类型、书名、音乐风格）
3. **成长与变化**：从清单的时间线看出什么（早年 vs 近年口味 / 兴趣有没有演变）
4. **三个洞察**：从数据里读出 3 个我不一定察觉到的特征
5. **一句给未来的话**

要求：
- 只基于提供的数据做概括，不编造具体情节
- 说洞察，不说教；可以温和地点出"你似乎更爱……"
- 总字数 600 字以内
"""


async def generate_insight_report(
    llm_service: Any,
    store: DoubanStore,
    *,
    force: bool = False,
    cache_path: str | Path = DEFAULT_CACHE,
) -> dict:
    """生成（或读缓存）深度画像报告。

    返回 {"ok": True, "report": str, "generated_at": str, "cached": bool}
    或 {"ok": False, "reason": "..."}（LLM 未配置 / 失败）。
    """
    import time

    cache = Path(cache_path)
    if not force and cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("ok") and data.get("report"):
                return {
                    "ok": True,
                    "report": data["report"],
                    "generated_at": data.get("generated_at", ""),
                    "cached": True,
                }
        except Exception as exc:  # noqa: BLE001
            logger.warning("豆瓣画像报告缓存读取失败，重新生成: %s", exc)

    if llm_service is None:
        return {"ok": False, "reason": "LLM 未配置，无法生成深度画像报告"}

    stats = DoubanAnalytics(store).full_report()
    prompt = build_profile_prompt(stats)
    try:
        resp = await llm_service.complete_structured_task(
            system_instruction=prompt,
            user_input="请生成我的豆瓣书影音画像报告。",
            temperature=0.6,
            max_tokens=4096,
            caller="douban.insight",
            reasoning_effort="none",
            inject_core_memory=False,
        )
        report = getattr(resp, "content", "") or ""
        if not report:
            return {"ok": False, "reason": "LLM 返回为空"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("豆瓣画像报告生成失败: %s", exc)
        return {"ok": False, "reason": f"LLM 生成失败：{exc}"}

    generated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps(
                {"ok": True, "report": report, "generated_at": generated_at},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("豆瓣画像报告缓存写入失败: %s", exc)  # 缓存失败不影响返回

    return {"ok": True, "report": report, "generated_at": generated_at, "cached": False}


def load_cached_report(cache_path: str | Path = DEFAULT_CACHE) -> dict:
    """读取已有缓存报告；没有则返回 {"ok": False, "reason": "尚未生成"}。"""
    cache = Path(cache_path)
    if not cache.exists():
        return {"ok": False, "reason": "尚未生成画像报告"}
    try:
        return json.loads(cache.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("豆瓣画像报告缓存读取失败: %s", exc)
        return {"ok": False, "reason": "缓存读取失败"}

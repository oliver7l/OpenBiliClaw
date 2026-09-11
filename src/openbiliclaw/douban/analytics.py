"""豆瓣书影音画像：纯数据统计聚合（不调 LLM）。

读取 douban_items 全量清单，做年维度/类型/分布聚合，输出确定性 JSON 供前端
画像视图渲染。字段兜底：空 date / 缺 pub 等不报错。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.douban.store import DoubanStore

CATEGORY_LABELS = {
    "movie": "影视",
    "book": "书",
    "music": "音乐",
}
STATUS_LABELS = {
    "collect": "看过/读过/听过",
    "wish": "想看/想读/想听",
    "do": "在看/在读/在听",
}


def _year_from_date(date: str) -> str | None:
    """从 'YYYY-MM-DD' 或 'YYYY' 提取年份；失败返回 None。"""
    if not date:
        return None
    m = re.match(r"\s*(\d{4})", date)
    return m.group(1) if m else None


class DoubanAnalytics:
    """对 douban_items 做统计画像。"""

    def __init__(self, store: DoubanStore) -> None:
        self._store = store
        self._items = store.list_items(limit=100000)

    # ── 核心聚合 ────────────────────────────────────────────────
    def yearly_trend(self) -> dict:
        """按年份 × 分类聚合（只统计 collect，即实际看过/读过/听过）。"""
        by_year: dict[str, Counter] = defaultdict(Counter)
        for it in self._items:
            if it.get("status") != "collect":
                continue
            y = _year_from_date(it.get("date", ""))
            if not y:
                continue
            cat = it.get("category", "")
            by_year[y][cat] += 1
        return {
            "years": [y for y in sorted(by_year.keys())],
            "series": {c: [by_year[y][c] for y in sorted(by_year.keys())] for c in CATEGORY_LABELS},
        }

    def type_distribution(self) -> dict:
        """分类 × 状态分布（与 stats 一致但独立实现，供画像展示）。"""
        dist: dict = {"categories": {}, "total": 0}
        for it in self._items:
            cat = it.get("category", "")
            st = it.get("status", "")
            dist["categories"].setdefault(cat, {})
            dist["categories"][cat].setdefault(st, 0)
            dist["categories"][cat][st] += 1
            dist["total"] += 1
        # 加标签
        for cat, statuses in dist["categories"].items():
            dist["categories"][cat] = {
                "label": CATEGORY_LABELS.get(cat, cat),
                "statuses": {
                    s: {"label": STATUS_LABELS.get(s, s), "count": c} for s, c in statuses.items()
                },
            }
        return dist

    def era_span(self) -> dict:
        """品味跨度：最早/最近有实际消费记录的年份（collect）。"""
        years = []
        for it in self._items:
            if it.get("status") != "collect":
                continue
            y = _year_from_date(it.get("date", ""))
            if y:
                years.append(int(y))
        if not years:
            return {"earliest_year": None, "latest_year": None, "span_years": 0}
        earliest, latest = min(years), max(years)
        return {
            "earliest_year": earliest,
            "latest_year": latest,
            "span_years": latest - earliest + 1,
        }

    def status_ratio(self) -> dict:
        """collect(实际消费) 占比。"""
        total = len(self._items)
        collected = sum(1 for it in self._items if it.get("status") == "collect")
        pct = round(collected / total * 100, 1) if total else 0
        return {"total": total, "collected": collected, "collected_pct": pct}

    def recent_names(self, limit: int = 40) -> list[dict]:
        """最近消费的代表条目（collect，按 date/id 倒序），供 LLM 报告用。

        空 date 的条目排到末尾（真实消费记录的 date 一般都有）。
        """
        collected = [it for it in self._items if it.get("status") == "collect"]
        collected.sort(
            key=lambda x: (not bool(x.get("date")), x.get("date", ""), -int(x.get("id", 0)))
        )
        return collected[:limit]

    def full_report(self) -> dict:
        """一次性返回完整统计画像供前端与 LLM prompt 使用。"""
        return {
            "total": len(self._items),
            "status_ratio": self.status_ratio(),
            "type_distribution": self.type_distribution(),
            "yearly_trend": self.yearly_trend(),
            "era_span": self.era_span(),
            "recent_names": self.recent_names(),
        }

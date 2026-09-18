"""Data Agent · 口径层（语义层）。

对应面试弹药 §1 第 1 点：「口径层优先于模型层」——高频指标走模板
（准确率近 100%），长尾探索查询才走 LLM 生成。
面试答法原话：「很多 text-to-SQL 失败不是模型不行，是口径没定义清楚」。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Metric:
    name: str                    # 指标名
    desc: str                    # 口径定义（人话）
    sql: str                     # SQL 模板，支持 {days} 参数
    aliases: list[str] = field(default_factory=list)  # 命中关键词
    params: list[str] = field(default_factory=lambda: ["days"])  # 可问用户澄清的参数


METRICS: list[Metric] = [
    Metric(
        name="采集成功率",
        desc="近 N 天 fetch_log 各平台抓取成功率（%）= 成功次数×100/总次数",
        sql="""SELECT platform, COUNT(*) AS 总次数, SUM(ok) AS 成功次数,
                      ROUND(SUM(ok)*100.0/COUNT(*), 1) AS 成功率
               FROM content.db.fetch_log
               WHERE ts >= datetime('now', '-{days} days')
               GROUP BY platform ORDER BY 总次数 DESC""",
        aliases=["采集成功率", "抓取成功率", "补抓成功率", "成功率"],
    ),
    Metric(
        name="采集失败原因",
        desc="近 N 天各平台失败原因分布（error_kind 计数）",
        sql="""SELECT platform, error_kind, COUNT(*) AS 次数
               FROM content.db.fetch_log
               WHERE ok = 0 AND ts >= datetime('now', '-{days} days')
               GROUP BY platform, error_kind ORDER BY 次数 DESC""",
        aliases=["失败原因", "为什么失败", "失败分布", "报错"],
    ),
    Metric(
        name="通道耗时",
        desc="近 N 天各通道（channel）平均耗时与成功率",
        sql="""SELECT channel, COUNT(*) AS 次数,
                      ROUND(AVG(latency_ms)) AS 平均毫秒,
                      ROUND(SUM(ok)*100.0/COUNT(*), 1) AS 成功率
               FROM content.db.fetch_log
               WHERE ts >= datetime('now', '-{days} days')
               GROUP BY channel ORDER BY 次数 DESC""",
        aliases=["通道耗时", "通道表现", "通道成功率"],
    ),
    Metric(
        name="收藏库增长",
        desc="近 N 天阅读收藏库每天新增条数（按入库日分组）",
        sql="""SELECT date(created_at, 'localtime') AS 入库日, COUNT(*) AS 新增
               FROM content.db.articles
               WHERE created_at >= datetime('now', '-{days} days')
               GROUP BY 入库日 ORDER BY 入库日""",
        aliases=["收藏增长", "收藏库增长", "每天收藏", "新增多少"],
    ),
    Metric(
        name="正文补抓积压",
        desc="收藏库中正文为空的文章数（按平台）——补抓线待办",
        sql="""SELECT source_type AS 平台, COUNT(*) AS 缺正文
               FROM content.db.articles
               WHERE content_text IS NULL OR content_text = ''
               GROUP BY source_type ORDER BY 缺正文 DESC""",
        aliases=["补抓积压", "缺正文", "正文积压", "没抓到正文"],
    ),
    Metric(
        name="投递漏斗",
        desc="求职投递按阶段计数（投递/一面/二面/HR面/终止）",
        sql="""SELECT COALESCE(NULLIF(stage, ''), status) AS 阶段, COUNT(*) AS 数量
               FROM resume.db.applications
               GROUP BY 阶段 ORDER BY 数量 DESC""",
        aliases=["投递漏斗", "投递进展", "投了多少", "面试到哪一步"],
    ),
    Metric(
        name="日记频率",
        desc="近 N 天每天日记碎片条数（按归属日）",
        sql="""SELECT fragment_date AS 日期, COUNT(*) AS 条数
               FROM diary.db.diary_fragments
               WHERE fragment_date >= date('now', 'localtime', '-{days} days')
               GROUP BY fragment_date ORDER BY 日期""",
        aliases=["日记频率", "日记多少", "记了几条", "碎片统计"],
    ),
    Metric(
        name="题库分布",
        desc="刷题题库按方向（direction）的题量分布",
        sql="""SELECT COALESCE(NULLIF(direction, ''), '未分类') AS 方向, COUNT(*) AS 题数
               FROM interview.db.question
               GROUP BY 方向 ORDER BY 题数 DESC""",
        aliases=["题库分布", "刷题分布", "题量", "题目分布"],
    ),
]


def match_metric(question: str) -> Metric | None:
    """关键词命中 → 走模板路径（确定性强、不依赖 LLM）。"""
    q = question.strip()
    for m in METRICS:
        if any(a in q for a in m.aliases):
            return m
    return None


def fill_metric(m: Metric, days: int = 7) -> str:
    days = max(1, min(int(days), 365))
    return m.sql.replace("{days}", str(days)).strip()


if __name__ == "__main__":
    print(f"口径词典：{len(METRICS)} 个指标")
    for m in METRICS:
        print(f"  - {m.name}: {m.desc}")
    # 自测命中
    assert match_metric("最近7天采集成功率怎么样").name == "采集成功率"
    assert match_metric("投递漏斗给我看看").name == "投递漏斗"
    assert match_metric("今天天气如何") is None
    print("命中自测 PASS")

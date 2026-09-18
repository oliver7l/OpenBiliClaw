#!/usr/bin/env python3
"""正文补抓全家桶体检 —— 一条命令汇总所有补正文任务状态。

用法:
    python3 backfill_status.py            # 打印人话报告（可直接丢给微信推送）
    python3 backfill_status.py --quiet    # 只在异常时输出（供自动化静默判断）

数据源:
    - content.db: articles 缺口 / fetch_log 近 7 天成功率 / 小红书最后成功日
    - openbiliclaw.db: getnote_body_task 状态分布 / 今日补抓量
"""
import json
import os
import sqlite3
import sys

BASE = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
CONTENT_DB = os.path.join(BASE, "data/content.db")
OB_DB = os.path.join(BASE, "data/openbiliclaw.db")

ALERTS = []  # 需要人工关注的点


def q(db, sql, args=()):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def main():
    lines = ["📖 正文补抓体检 " + __import__("time").strftime("%m-%d %H:%M")]

    # 1) 各源缺口
    gaps = q(CONTENT_DB, """
        SELECT source_type, COUNT(*),
               SUM(CASE WHEN body_fetch_attempts>=3 THEN 1 ELSE 0 END)
        FROM articles WHERE content_text IS NULL OR content_text=''
        GROUP BY 1 ORDER BY 2 DESC""")
    total_gap = sum(r[1] for r in gaps)
    lines.append(f"总缺口 {total_gap} 条：")
    name_map = {"xiaohongshu": "小红书", "youtube": "YouTube", "bilibili": "B站",
                "douyin": "抖音", "zhihu": "知乎", "v2ex": "V2EX"}
    for st, n, exhausted in gaps:
        note = f"（重试耗尽 {exhausted}）" if exhausted else ""
        flag = "🔴" if st == "xiaohongshu" and n > 1000 else "　"
        lines.append(f"{flag} {name_map.get(st, st)}: {n}{note}")

    # 2) 近 7 天抓取成功率
    recent = q(CONTENT_DB, """
        SELECT date(ts), SUM(ok), SUM(1-ok) FROM fetch_log
        WHERE ts >= datetime('now','-7 day') GROUP BY 1 ORDER BY 1 DESC LIMIT 7""")
    if recent:
        yesterday = recent[0]
        ok, fail = yesterday[1] or 0, yesterday[2] or 0
        lines.append(f"最近抓取（昨天）: 成功 {ok} / 失败 {fail}")
        if ok == 0 and fail > 0:
            ALERTS.append(f"统一补抓通道昨天全失败（{fail} 次）")
        # 多天零成功 = 通道挂了
        all_zero = all((r[1] or 0) == 0 for r in recent if r[2])
        if all_zero and len(recent) >= 3:
            ALERTS.append(f"统一补抓已连续 {len(recent)} 天零成功，通道疑似失效")

    # 3) 小红书最后成功日（token 断供探测器）
    row = q(CONTENT_DB, """
        SELECT MAX(date(ts)) FROM fetch_log
        WHERE platform='xiaohongshu' AND ok=1""")[0][0]
    if row:
        import datetime
        days = (datetime.date.today() - datetime.date.fromisoformat(row)).days
        if days >= 3:
            ALERTS.append(f"小红书正文已 {days} 天无成功（最后 {row}），token 链路待修")

    # 4) 得到大脑通道
    tasks = dict(q(OB_DB, "SELECT status, COUNT(*) FROM getnote_body_task GROUP BY 1"))
    today_fetched = q(OB_DB, """
        SELECT COUNT(*) FROM getnote_body_task
        WHERE status='fetched' AND date(updated_at)=date('now','localtime')""")[0][0]
    parts = " / ".join(f"{k} {v}" for k, v in sorted(tasks.items()))
    lines.append(f"得到大脑任务: {parts}")
    recent3 = q(OB_DB, """
        SELECT COUNT(*) FROM getnote_body_task
        WHERE status='fetched' AND updated_at >= datetime('now','-3 day')""")[0][0]
    lines.append(f"　近 3 天已补 {recent3} 条（低频 ≈48/天）")
    if recent3 == 0 and tasks.get("submitted", 0) == 0:
        ALERTS.append("得到大脑补抓近 3 天 0 产出，检查自动化 7e599a17 是否在跑")

    # 5) 抖音设计缺口提示（首次出现即可，不刷屏）
    douyin = next((r[1] for r in gaps if r[0] == "douyin"), 0)
    if douyin:
        lines.append(f"ℹ️ 抖音 {douyin} 条无补抓通道（设计缺口，未修）")

    lines.append("")
    if ALERTS:
        lines.append("⚠️ 待处理：" + "；".join(ALERTS))
    else:
        lines.append("✅ 各通道运转正常")
    report = "\n".join(lines)
    if "--quiet" in sys.argv:
        if ALERTS:
            print(report)  # 仅异常时输出，供自动化判断是否推送
        sys.exit(1 if ALERTS else 0)
    print(report)


if __name__ == "__main__":
    main()

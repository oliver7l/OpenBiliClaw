"""CLI 入口：python -m fetchhub / scripts/content_library/fetch_hub.py"""
from __future__ import annotations

import argparse
import sys

from .core import AllChannelsFailed, fetch, health
from .draft import write_draft


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="fetch_hub",
        description="统一数据获取 Hub：URL → 规格化文档（多通道自动降级，AgentLimb 兜底）",
    )
    p.add_argument("url", nargs="?", help="目标 URL（v2ex/zhihu/xhs/bilibili/任意页面）")
    p.add_argument("--json", action="store_true", help="输出完整 UnifiedDoc JSON")
    p.add_argument("--archive-draft", action="store_true", help="把结果写入 阅读收藏库/.drafts/ 五段式草稿（解读留人写）")
    p.add_argument("--health", action="store_true", help="查看近 N 天通道健康度")
    p.add_argument("--days", type=int, default=7, help="健康度统计窗口天数（默认 7）")
    p.add_argument("--channel", help="强制指定通道名（跳过降级链）")
    p.add_argument("--no-fallback", action="store_true", help="只试链上第一条通道，不降级")
    a = p.parse_args(argv)

    if a.health:
        rows = health(a.days)
        print(f"近 {a.days} 天通道健康度：")
        if not rows:
            print("  （暂无记录）")
        for r in rows:
            print(f"  {r['platform']:<9} {r['channel']:<16} 成功 {r['ok']}/{r['total']} ({r['rate']:.0%})  平均 {r['avg_ms']}ms")
        return 0

    if not a.url:
        p.error("需要 URL（或使用 --health）")

    try:
        doc, attempts = fetch(a.url, forced_channel=a.channel, no_fallback=a.no_fallback)
    except AllChannelsFailed as e:
        print(f"✗ 全部通道失败：{e.url}（platform={e.platform}）", file=sys.stderr)
        for at in e.attempts:
            print(f"    ✗ {at['channel']}: {at['error_kind']} — {at.get('detail', '')[:140]}", file=sys.stderr)
        challenge = [at for at in e.attempts if at["error_kind"] == "challenge"]
        if challenge:
            print("  提示：存在 challenge 类失败——请在 AgentLimb 浏览器中手动打开目标页过一次挑战后重试。", file=sys.stderr)
        return 1

    if a.json:
        print(doc.to_json())
    else:
        print(f"✓ {doc.title}")
        print(
            f"  via={doc.fetched_via} | platform={doc.platform} | author={doc.author} | "
            f"published={doc.published_at} | replies={len(doc.replies)} | confidence={doc.confidence}"
        )
        for at in attempts[:-1]:
            if not at["ok"]:
                print(f"  （{at['channel']} 失败已降级：{at['error_kind']}）")

    if a.archive_draft:
        path = write_draft(doc)
        print(f"草稿已写入：{path}")
    return 0

"""CLI 命令组：openbiliclaw refill（阅读库正文统一回补）。

M1 交付 ``status``（队列观测）；M2 交付 ``run``（手动补一轮验证）与
``schedule``（PM2 cron 入口，走完整配额 + 防风控 jitter）及 ``channel --list`` /
``reset``。调度基于 :class:`RefillScheduler` + direct / search_click 通道。
本模块顶层不 import ``openbiliclaw.cli``，渲染 helper 经 ``openbiliclaw.cli._render`` 取用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from rich.table import Table

from openbiliclaw.cli._render import _print_page_title, _print_status_panel
from openbiliclaw.config import Config, load_config
from openbiliclaw.refill import RefillQueue, RefillScheduler
from openbiliclaw.refill.channels.bridge import AgentLimbBridge
from openbiliclaw.refill.channels.base import route_channels
from openbiliclaw.refill.writer import build_content_writer
from openbiliclaw.runtime.init_flow import console

refill_app = typer.Typer(help="阅读库正文统一回补：队列 / 观测 / 调度之子命令")


def register(app: typer.Typer) -> None:
    app.add_typer(refill_app, name="refill")


def _resolve_paths(config: Config) -> tuple[Path, Path]:
    """返回 (refill.db 绝对路径, content.db 绝对路径)。"""
    data = Path(config.data_path)
    content_db = data / "content.db"
    refill_cfg = Path(config.refill.db)
    refill_db = refill_cfg if refill_cfg.is_absolute() else data / refill_cfg
    return refill_db, content_db


def _open_queue(config: Config, *, fill: bool = False) -> RefillQueue:
    refill_db, content_db = _resolve_paths(config)
    queue = RefillQueue(refill_db, content_db)
    queue.ensure_schema()
    if fill:
        queue.fill_gaps()
    return queue


def _build_scheduler(config: Config, *, jitter_max_min: int | None = None) -> RefillScheduler:
    """按配置组 Scheduler：真实 AgentLimb 桥接 + direct/search_click 通道 +
    基于 Database 的正文写回。桥接探测在 run_cycle 内做，关断时整轮跳过。
    """
    refill_db, content_db = _resolve_paths(config)
    queue = _open_queue(config)
    queue.ensure_schema()
    bridge = AgentLimbBridge()
    write = build_content_writer(content_db)
    jitter = config.refill.jitter_max_min if jitter_max_min is None else jitter_max_min
    return RefillScheduler(
        queue,
        min_body_len=config.refill.min_body_len,
        quota=config.refill.quota,
        write_content=write,
        bridge=bridge,
        jitter_max_min=jitter,
    )


@refill_app.command("run")
def refill_run(
    source: str | None = typer.Option(None, "--source", "-s", help="限定平台（可用逗号分隔多个）。缺省按配置配额全部平台。"),
    n: int | None = typer.Option(None, "--n", "-n", help="本平台本轮条数上限（覆盖配置配额）。"),
) -> None:
    """手动补一轮（验证 / 临时加仓）。不随机憩志，便于观察。

    需要 AgentLimb 桥接（真实登录态 Chrome）在线，否则本轮整轮跳过完成不消耗重试。
    """
    config = load_config()
    _print_page_title("阅读库回补 · 手动运行", "pick → route → 抓 → 写正文 → 收口")
    if not config.refill.enabled:
        _print_status_panel("warning", "refill 已禁用", "[refill].enabled = false，仅读队列，不会调度。")

    sources = tuple(s.strip() for s in (source or "").split(",") if s.strip()) or None
    quota_override = {s: _per_cycle_quota(n) for s in sources} if (sources and n is not None) else None
    scheduler = _build_scheduler(config, jitter_max_min=0)
    try:
        summary = scheduler.run_cycle(sources=sources, quota_override=quota_override)
    finally:
        scheduler.queue.close()
    _render_cycle_summary(summary)


@refill_app.command("schedule")
def refill_schedule(
    no_jitter: bool = typer.Option(False, "--no-jitter", help="关闭首段随机憩志（调试 cron 用）。"),
) -> None:
    """按完整配置与配额跑一轮（PM2 cron ``openbiliclaw refill schedule`` 入口）。

    依照 ``[refill].quota`` 每平台每轮补配额条数；默认随机憩志防风控。
    """
    config = load_config()
    if not config.refill.enabled:
        _print_status_panel("warning", "refill 已禁用", "[refill].enabled = false，本轮不调度。")
        return
    scheduler = _build_scheduler(config, jitter_max_min=0 if no_jitter else None)
    try:
        summary = scheduler.run_cycle()
    finally:
        scheduler.queue.close()
    _render_cycle_summary(summary)


@refill_app.command("channel")
def refill_channel() -> None:
    """列出已注册抓取通道及平台→通道路由。"""
    _print_page_title("回补通道", "已注册 Channel 与路由优先级")
    table = Table(header_style="bold", box=None, pad_edge=False)
    table.add_column("平台")
    table.add_column("通道路由（优先 → 兜底）")
    table.add_row("xiaohongshu（无 token+有标题）", " → ".join(route_channels("xiaohongshu", "https://x.com/explore/abc", "某标题")))
    table.add_row("xiaohongshu（带 token / 标题空）", " → ".join(route_channels("xiaohongshu", "https://x.com/explore/abc?xsec_token=k", "")))
    table.add_row("youtube", " → ".join(route_channels("youtube", "https://youtu.be/dQw4w9WgXcQ", "某标题")))
    table.add_row("douyin", " → ".join(route_channels("douyin", "https://v.douyin.com/abc/", "某标题")))
    table.add_row("bilibili", " → ".join(route_channels("bilibili", "https://bilibili.com/video/BV1xx411c7mD", "某标题")))
    table.add_row("zhihu", " → ".join(route_channels("zhihu", "https://zhihu.com/answer/123", "某标题")))
    table.add_row("其余平台", " → ".join(route_channels("v2ex", "https://v2ex.com/t/1", "某标题")))
    console.print(table)
    _print_status_panel(
        "info",
        "通道工厂",
        "direct（登录态 Chrome）· search_click（小红书搜索点击）· "
        "ytdlp（YouTube 字幕/简介）· bili_cli（B站字幕/AI）· "
        "zhihu_api（知乎直连）· getnote（得到大脑服务端兜底）。",
    )


@refill_app.command("reset")
def refill_reset(
    source: str | None = typer.Option(None, "--source", "-s", help="限定平台；缺省重置所有平台。"),
) -> None:
    """把 ``dropped``（重试已满）队项重置回 ``pending`` 并清零 attempts。

    更换通道 / 调整配额 / 节点复活后，用它重启某平台回补。
    """
    config = load_config()
    queue = _open_queue(config)
    try:
        count = queue.reset_by_source(source)
    finally:
        queue.close()
    _print_status_panel("success", "已重置", f"{source or '所有平台'} dropped→pending，共 {count} 条回到待补。")


@refill_app.command("status")
def refill_status(
    fill: bool = typer.Option(False, "--fill", "-f", help="先执行一次全量缺口灌入，再展示队列统计。"),
    fresh: bool = typer.Option(False, "--fresh", "--recount", help="同时展示 articles 表实时缺口口径。"),
) -> None:
    """一键查看阅读库正文缺口的中央队列统计（各平台 待补/已补/已满/已完成）。

    不传 ``--fill`` 时仅读现有队列（首次运行队列为空，需先 ``--fill`` 灌入一次）。
    """
    config = load_config()
    _print_page_title("阅读库回补队列", "refill_queue 各平台口径")

    if not config.refill.enabled:
        _print_status_panel("warning", "refill 已禁用", "config.toml [refill].enabled = false，命令仅读现有队列。")

    queue = _open_queue(config, fill=fill)
    try:
        rows = queue.status()

        table = Table(title="队列状态（按平台）", header_style="bold", box=None, pad_edge=False)
        table.add_column("平台")
        table.add_column("待补 pending", justify="right")
        table.add_column("已完成 done", justify="right")
        table.add_column("跳过 skipped", justify="right")
        table.add_column("已满 dropped", justify="right")
        table.add_column("入队合计", justify="right")

        total_pending = total_done = 0
        for row in rows:
            total_pending += row.pending
            total_done += row.done
            table.add_row(
                row.source_type,
                str(row.pending),
                str(row.done),
                str(row.skipped),
                str(row.dropped),
                str(row.total),
            )
        if rows:
            table.add_row(
                "合计", str(total_pending), str(total_done), "", "", str(sum(r.total for r in rows)),
                style="bold",
            )
        console.print(table)

        if not rows:
            _print_status_panel(
                "info",
                "队列为空",
                "首次使用请加 `--fill` 全量灌入：openbiliclaw refill status --fill。",
            )
            return

        if fresh:
            missing = queue.count_missing()
            console.print("")
            fresh_table = Table(title="articles 表实时缺口（补抓真实欠账）", header_style="bold",
                                box=None, pad_edge=False)
            fresh_table.add_column("平台")
            fresh_table.add_column("实时缺正文数", justify="right")
            fresh_table.add_column("队列已入队", justify="right")
            for source_type, n in missing.items():
                queued = next((r.total for r in rows if r.source_type == source_type), 0)
                fresh_table.add_row(source_type, str(n), str(queued))
            console.print(fresh_table)

        _print_status_panel(
            "success",
            "口径说明",
            "队列合计数 = 已扫描入队的缺口；articles 实时缺口 = 此刻仍然缺正文。",
        )
    finally:
        queue.close()


def _per_cycle_quota(n: int) -> Any:
    from openbiliclaw.config import RefillQuota

    return RefillQuota(per_cycle=max(0, int(n or 0)), interval_min=0)


def _render_cycle_summary(summary: dict[str, dict[str, int]]) -> None:
    """渲染一轮调度结果表。"""
    table = Table(title="本轮调度结果", header_style="bold", box=None, pad_edge=False)
    table.add_column("平台")
    table.add_column("pick", justify="right")
    table.add_column("成功 done", justify="right")
    table.add_column("失败", justify="right")
    table.add_column("跳过", justify="right")
    table.add_column("已满", justify="right")
    table.add_column("桥接断", justify="right")
    for source_type, c in summary.items():
        table.add_row(
            source_type,
            str(c.get("picked", 0)),
            str(c.get("done", 0)),
            str(c.get("failed", 0)),
            str(c.get("skipped", 0)),
            str(c.get("dropped", 0)),
            str(c.get("bridge_off", 0)),
        )
    console.print(table)
    console.print("[dim]再次 `refill status` 查看队列收口后的各平台口径。[/dim]")


def _build_refill_command_group() -> typer.Typer:
    return refill_app
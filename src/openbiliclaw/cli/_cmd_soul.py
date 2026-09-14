"""CLI 灵魂画像 / 推荐 / 对话命令组。

集合 ``rebuild-profile`` / ``profile-consolidate`` / ``import-youtube`` /
``recommend`` / ``feedback`` / ``profile`` / ``chat`` / ``delight`` / ``probe``。
从上帝文件 ``cli/__init__.py`` 抽出（P4 第七刀，2026-09-14）。

本模块顶层**不得** import ``openbiliclaw.cli``（避免循环依赖）；
9 个平铺命令经 ``register(app)`` 挂回主 app（命令名与形状不变）。

patch 语义保留：被测试 patch 到 ``cli`` 命名空间的共享符号
（``_build_soul_engine`` / ``_build_recommendation_engine`` /
``_build_memory_manager`` / ``_build_dialogue`` / ``_build_registry`` /
``_build_usage_recorder`` / ``_require_runtime_config`` /
``_get_runtime_database`` / ``_prepare_init_runtime`` / ``_run_with_progress`` /
``_print_init_cost_summary`` / ``_notify_running_server_init_completed``）
一律在**函数体内**经 ``from openbiliclaw import cli as _cli`` 动态取属性——
顶层 from-import 会绑定旧值并破坏 ``monkeypatch.setattr(cli_module, ...)``。
"""

from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from pathlib import Path
from typing import Any

import click
import typer
from rich.panel import Panel

from openbiliclaw.cli._render import (
    _print_key_value_table,
    _print_page_title,
    _print_recommendation_card,
    _print_status_panel,
)
from openbiliclaw.runtime.init_flow import (
    _print_section_title,
    console,
)
from openbiliclaw.soul.preference_analyzer import DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE


def register(app: typer.Typer) -> None:
    """把本模块的 9 个平铺命令挂回主 app（顺序与旧定义一致）。"""
    app.command("rebuild-profile")(rebuild_profile)
    app.command("profile-consolidate")(profile_consolidate)
    app.command("import-youtube")(import_youtube)
    app.command()(recommend)
    app.command()(feedback)
    app.command()(profile)
    app.command()(chat)
    app.command()(delight)
    app.command()(probe)


def rebuild_profile(
    limit: int = typer.Option(
        5000,
        "--limit",
        help="从数据库加载的最大事件数（默认 5000）。",
    ),
    source: str = typer.Option(
        "",
        "--source",
        help="只用指定来源：bilibili / xiaohongshu / douyin / youtube，留空=全部。",
    ),
    no_analyze: bool = typer.Option(
        False,
        "--no-analyze",
        help="跳过 analyze_events，直接重跑 build_initial_profile。",
    ),
) -> None:
    """从数据库重新生成灵魂画像（调试用）。

    从已存储的行为事件重跑完整的偏好分析 + 画像生成流程，
    无需重新从任何平台拉取数据。适合：

    \\b
      - 调整了 LLM prompt 后验证效果
      - 新接入平台后补充旧数据重跑
      - init 中途中断后只补跑画像阶段
    """
    import json as _json

    from openbiliclaw import cli as _cli

    _cli._prepare_init_runtime()
    memory = _cli._build_memory_manager()
    soul_engine = _cli._build_soul_engine()

    _print_page_title("重新生成灵魂画像", "rebuild-profile")

    init_start_usage_id: int | None = None
    with suppress(Exception):
        init_start_usage_id = _cli._get_runtime_database().max_llm_usage_id()

    # ── 1. 从 DB 加载事件 ────────────────────────────────────────────
    console.print(f"  [dim]从数据库加载最多 {limit} 条事件...[/dim]")
    raw_rows = memory.query_events(limit=limit)

    # metadata 在 DB 中以 JSON 文本存储；context 是纯文本（v0.3.23+）。
    events: list[dict[str, Any]] = []
    for row in raw_rows:
        ev = dict(row)
        meta_raw = ev.get("metadata")
        if isinstance(meta_raw, str) and meta_raw:
            try:
                parsed = _json.loads(meta_raw)
                ev["metadata"] = parsed if isinstance(parsed, dict) else {}
            except _json.JSONDecodeError:
                ev["metadata"] = {}
        events.append(ev)

    # 来源过滤
    source = source.strip().lower()
    if source:
        events = [e for e in events if str((e.get("metadata") or {}).get("source_platform", "")).lower() == source]

    if not events:
        console.print(
            "[yellow]  没有找到事件。"
            + (f"来源 '{source}' 不存在，或" if source else "")
            + "请先运行 [cyan]openbiliclaw init[/cyan] 拉取数据。[/yellow]"
        )
        raise typer.Exit(code=1)

    # 按来源平台打印分布
    from collections import Counter

    platform_counts: Counter[str] = Counter()
    for ev in events:
        platform_counts[str((ev.get("metadata") or {}).get("source_platform", "unknown"))] += 1
    console.print(f"  已加载 [green]{len(events)}[/green] 条事件：")
    for platform, count in sorted(platform_counts.items(), key=lambda x: -x[1]):
        console.print(f"    {platform}: [green]{count}[/green] 条")

    # ── 2. 偏好分析 ──────────────────────────────────────────────────
    if not no_analyze:
        _print_section_title("1/2 分析偏好")
        console.print(f"  总信号量: [green]{len(events)}[/green] 条")
        asyncio.run(
            _cli._run_with_progress(
                soul_engine.analyze_events(
                    events,
                    event_chunk_size=DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE,
                ),
                label="分析偏好（分片并发）",
                eta_seconds=180,
            )
        )
    else:
        console.print("  [dim]跳过 analyze_events（--no-analyze）。[/dim]")

    # ── 3. 画像生成 ──────────────────────────────────────────────────
    section_label = "2/2 生成画像" if not no_analyze else "1/1 生成画像"
    _print_section_title(section_label)
    asyncio.run(
        _cli._run_with_progress(
            soul_engine.build_initial_profile(events),
            label="生成灵魂画像（单次 LLM 综合分析）",
            eta_seconds=70,
        )
    )

    _print_status_panel("success", "完成", "灵魂画像已重新生成")

    if init_start_usage_id is not None:
        _cli._print_init_cost_summary(init_start_usage_id)

    _cli._notify_running_server_init_completed()


def profile_consolidate(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="真正写入合并结果。默认 dry-run：只打印建议，不改任何数据。",
    ),
    revert: str = typer.Option(
        "",
        "--revert",
        help="按 run_id 回滚一次已应用的整理（备份在 data/memory/consolidation_runs/）。",
    ),
    migrate_categories: bool = typer.Option(
        False,
        "--migrate-categories",
        help="一次性把存量一级分类迁移到固定词表（默认 dry-run，配 --apply 写入）。",
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help="把 likes 整理边界从默认 top-512 开到全量标签库（嫌疑簇 32/批送审）。",
    ),
) -> None:
    """用 LLM 整理合并画像里重复的喜欢 / 讨厌主题。

    兴趣标签和避雷主题会不断积累措辞变体（「智能体开发」vs
    「智能体开发与实现」），把进入 prompt 的兴趣名额挤占掉。
    本命令按「规则合并 → embedding 聚类 → LLM 裁决 → 校验执行」
    的流水线做同义合并（likes 看权重 top-512 + 全量避雷主题，
    LLM 裁决每批 32 簇分批执行）。

    \b
      - 默认 dry-run，先看建议再决定
      - --apply 写入,自动备份到 data/memory/consolidation_runs/
      - --migrate-categories 一次性分类词表迁移（同样 dry-run/--apply/--revert）
      - --full 一次性全量清理 likes 长尾标签（与 --migrate-categories 互斥）
      - 审计记录追加到 data/memory/soul_changelog.md
    """
    import asyncio as _asyncio

    from openbiliclaw import cli as _cli
    from openbiliclaw.config import load_config
    from openbiliclaw.llm._compat_registry import build_embedding_service
    from openbiliclaw.llm.service import LLMService, module_overrides_from_config
    from openbiliclaw.soul.consolidator import ProfileConsolidator

    _print_page_title("画像整理", "profile-consolidate")

    cfg = load_config()
    memory = _cli._build_memory_manager()
    llm_service = None
    registry = None
    try:
        registry = _cli._build_registry()
        llm_service = LLMService(
            registry=registry,
            memory=memory,
            usage_recorder=_cli._build_usage_recorder(),
            module_overrides=module_overrides_from_config(cfg),
            concurrency=cfg.llm.concurrency,
        )
    except Exception as exc:
        console.print(f"[yellow]  LLM 不可用（{exc}）— 只做规则合并与聚类预览。[/yellow]")
    embedding_service = None
    if registry is not None:
        try:
            embedding_service = build_embedding_service(cfg, registry)
        except Exception:
            embedding_service = None
    if embedding_service is None:
        console.print("[dim]  embedding 服务不可用，退回子串聚类。[/dim]")

    if full and migrate_categories:
        console.print("[bold red]  --full 与 --migrate-categories 不能同时使用。[/bold red]")
        console.print("[dim]  推荐顺序：先 --migrate-categories --apply，再 --full --apply。[/dim]")
        raise typer.Exit(code=1)

    if full:
        raw_interests = memory.get_layer("preference").data.get("interests", [])
        interest_count = len([item for item in raw_interests if isinstance(item, dict)])
        likes_boundary = max(interest_count, 128)
        console.print(f"  [cyan]--full：likes 边界开到全量（{likes_boundary} 条）。[/cyan]")
        consolidator = ProfileConsolidator(
            memory=memory,
            llm_service=llm_service,
            embedding_service=embedding_service,
            likes_boundary=likes_boundary,
            like_target_upper=cfg.scheduler.profile_consolidation_like_target_upper,
            like_target_soft=cfg.scheduler.profile_consolidation_like_target_soft,
            archive_enabled=cfg.scheduler.profile_consolidation_archive_enabled,
        )
    else:
        consolidator = ProfileConsolidator(
            memory=memory,
            llm_service=llm_service,
            embedding_service=embedding_service,
            like_target_upper=cfg.scheduler.profile_consolidation_like_target_upper,
            like_target_soft=cfg.scheduler.profile_consolidation_like_target_soft,
            archive_enabled=cfg.scheduler.profile_consolidation_archive_enabled,
        )

    if revert.strip():
        ok = consolidator.revert(revert.strip())
        if ok:
            console.print(f"  [green]已回滚 run {revert.strip()}，画像与覆盖层均已恢复。[/green]")
            console.print("  [dim]被回滚的合并已记入 no-merge 记忆，下轮整理不会重做。[/dim]")
        else:
            console.print(f"[bold red]  回滚失败：找不到 run 记录 {revert.strip()}。[/bold red]")
            raise typer.Exit(code=1)
        return

    if migrate_categories:
        from openbiliclaw.soul.category_migration import CategoryMigrator

        migrator = CategoryMigrator(memory=memory, llm_service=llm_service)
        migration_report = _asyncio.run(migrator.run(dry_run=not apply))
        for err in migration_report.errors:
            console.print(f"[yellow]  ⚠ {err}[/yellow]")
        console.print(
            f"  现存分类: {len(migration_report.histogram)} 个，标签 {sum(migration_report.histogram.values())} 条"
        )
        for old, new in sorted(
            migration_report.mapping.items(),
            key=lambda item: -migration_report.histogram.get(item[0], 0),
        ):
            console.print(f"  {old}({migration_report.histogram.get(old, 0)}) → [bold]{new}[/bold]")
        if migration_report.mapping:
            suffix = "  [yellow]⚠ 超过 10%[/yellow]" if migration_report.other_ratio > 0.10 else ""
            console.print(f"\n  「其他」占比: {migration_report.other_ratio:.1%}{suffix}")
        if not apply and migration_report.mapping:
            console.print("\n  [dim]满意的话用 --apply 真正写入。[/dim]")
        if migration_report.applied:
            console.print(
                f"\n  [dim]已备份，run_id={migration_report.run_id}（--revert {migration_report.run_id} 可回滚）[/dim]"
            )
        # 只有「LLM 服务不可用」是降级只读预览（打印 histogram 即成功，code=0）；
        # LLM 调用异常 / 映射校验失败必须非零退出，脚本化调用才能区分失败与预览。
        degraded = migration_report.errors == ["llm: service unavailable"]
        if migration_report.errors and not migration_report.mapping and not degraded:
            raise typer.Exit(code=1)
        return

    mode_label = "[bold]apply[/bold]" if apply else "dry-run（加 --apply 才会写入）"
    console.print(f"  模式: {mode_label}")
    report = _asyncio.run(consolidator.run(dry_run=not apply))

    if report.errors:
        for err in report.errors:
            console.print(f"[yellow]  ⚠ {err}[/yellow]")
    if report.likes_before > report.likes_target_upper:
        console.print(f"  [cyan]likes 动态聚类阈值:[/cyan] cosine ≥ {report.like_similarity_threshold:.2f}")
    console.print(f"  嫌疑簇送审: {report.clusters_sent} 个")
    for rule_merge in report.rule_merges:
        console.print(f"  [cyan][规则][/cyan] {rule_merge}")
    for merge in report.merges:
        raw_members = merge.get("members", [])
        member_items = raw_members if isinstance(raw_members, list) else []
        members = " / ".join(str(m) for m in member_items)
        scope = "兴趣" if merge.get("scope") == "likes" else "避雷"
        console.print(f"  [green][{scope}][/green] {members} → [bold]{merge.get('canonical')}[/bold]")
    for rejected in report.rejected_clusters:
        console.print(f"  [dim][放弃簇] {rejected}[/dim]")
    console.print(
        f"\n  兴趣: {report.likes_before} → {report.likes_after}"
        f"    避雷: {report.dislikes_before} → {report.dislikes_after}"
    )
    if report.archived_interests:
        console.print(
            f"  [cyan]归档低权重兴趣:[/cyan] {len(report.archived_interests)} 个"
            f"（目标 ≤ {report.likes_target_upper}，整理水位 {report.likes_target_soft}）"
        )
    if report.inventory_reason:
        console.print(f"  [yellow]库存说明:[/yellow] {report.inventory_reason}")
    if not apply and (report.merges or report.rule_merges):
        console.print("\n  [dim]满意的话用 --apply 真正写入。[/dim]")
    if apply and (report.merges or report.rule_merges or report.archived_interests):
        console.print(f"\n  [dim]已备份，run_id={report.run_id}[/dim]")


def import_youtube(
    path: str = typer.Argument(
        ...,
        help="Google Takeout 导出路径：.zip 文件或解压后的目录。",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="只解析打印统计，不写入数据库 / 不更新画像。",
    ),
) -> None:
    """从 Google Takeout 导入 YouTube 观看历史、订阅和点赞数据。

    使用步骤：

    \b
    1. 访问 https://takeout.google.com
    2. 仅选择 "YouTube and YouTube Music"
    3. 格式选 JSON（默认 HTML 也支持，但 JSON 更精确）
    4. 下载后将 .zip 路径传给本命令，或先解压再传目录。
    """
    from openbiliclaw import cli as _cli
    from openbiliclaw.youtube.takeout import parse_takeout

    _print_page_title("导入 YouTube Takeout", "冷启动画像补充")

    takeout_path = Path(path)
    if not takeout_path.exists():
        console.print(f"[red]路径不存在: {takeout_path}[/red]")
        raise typer.Exit(code=1)

    console.print(f"  解析 [cyan]{takeout_path}[/cyan] …")
    result = parse_takeout(takeout_path)

    for warning in result.warnings:
        console.print(f"  [yellow]⚠ {warning}[/yellow]")

    stats = result.stats
    console.print(
        f"\n  解析完成：\n"
        f"    观看历史  [green]{stats.watch_history}[/green] 条\n"
        f"    订阅频道  [green]{stats.subscriptions}[/green] 个\n"
        f"    点赞视频  [green]{stats.liked_videos}[/green] 个\n"
        f"    合计      [green]{stats.total}[/green] 条事件"
    )

    if stats.total == 0:
        console.print("[yellow]未找到任何 YouTube 信号，请检查 Takeout 目录结构。[/yellow]")
        raise typer.Exit(code=0)

    if dry_run:
        console.print("\n[dim]--dry-run 模式，不写入数据库，结束。[/dim]")
        raise typer.Exit(code=0)

    _cli._require_runtime_config()
    memory = _cli._build_memory_manager()
    soul_engine = _cli._build_soul_engine()

    _print_section_title("1/2 写入记忆层")
    console.print(f"  将 {stats.total} 条事件传播到记忆层 …")

    async def _propagate() -> None:
        for event in result.events:
            await memory.propagate_event(event)

    asyncio.run(_propagate())
    console.print("  [green]✓ 记忆层写入完成[/green]")

    _print_section_title("2/2 更新偏好画像")
    console.print(f"  分析 {stats.total} 条 YouTube 信号（分片 {DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE} 条）…")
    asyncio.run(
        _cli._run_with_progress(
            soul_engine.analyze_events(
                result.events,
                event_chunk_size=DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE,
            ),
            label="分析偏好（YouTube 信号）",
            eta_seconds=90,
        )
    )
    console.print("  [green]✓ 偏好画像已更新[/green]")

    console.print(
        "\n[bold green]✓ YouTube Takeout 导入完成。[/bold green]\n"
        "  运行 [cyan]openbiliclaw profile[/cyan] 查看更新后的用户画像。"
    )


def recommend() -> None:
    """查看推荐内容."""
    from openbiliclaw import cli as _cli
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError

    _cli._require_runtime_config()
    soul_engine = _cli._build_soul_engine()
    recommendation_engine = _cli._build_recommendation_engine()

    try:
        profile_data = asyncio.run(soul_engine.get_profile())
    except SoulProfileNotInitializedError as exc:
        console.print("[bold yellow]尚未初始化用户画像[/bold yellow]")
        console.print("请先执行 `openbiliclaw init` 拉取历史并生成初始画像。")
        raise typer.Exit(code=1) from exc

    recommendations = asyncio.run(
        recommendation_engine.generate_recommendations(
            discovered=None,
            profile=profile_data,
            limit=5,
        )
    )

    _print_page_title("本轮推荐", "朋友式推荐列表")
    if not recommendations:
        _print_status_panel(
            "info",
            "暂无可推荐内容",
            "请先执行 `openbiliclaw discover`。",
        )
        return

    presented_ids: list[int] = []
    for index, item in enumerate(recommendations, start=1):
        _print_recommendation_card(item, index)
        presented_ids.append(item.recommendation_id)

    recommendation_engine.mark_presented(presented_ids)


def feedback(
    recommendation_id: int,
    signal: str,
    note: str = typer.Option("", "--note", help="补充反馈备注"),
) -> None:
    """对一条推荐记录提交反馈."""
    from openbiliclaw import cli as _cli

    _cli._require_runtime_config()
    normalized_signal = signal.strip().lower()
    if normalized_signal not in {"like", "dislike", "comment", "dismiss"}:
        _print_status_panel("error", "反馈类型无效", "仅支持: like, dislike, comment, dismiss")
        raise typer.Exit(code=1)
    if normalized_signal == "comment" and not note.strip():
        _print_status_panel("error", "comment 需要备注", "请通过 `--note` 补充一句你的想法。")
        raise typer.Exit(code=1)

    recommendation_engine = _cli._build_recommendation_engine()
    memory = _cli._build_memory_manager()
    recommendation = recommendation_engine.get_recommendation(recommendation_id)
    if recommendation is None:
        _print_status_panel("error", "推荐不存在", f"recommendation_id={recommendation_id}")
        raise typer.Exit(code=1)
    soul_engine = _cli._build_soul_engine()

    asyncio.run(
        recommendation_engine.record_feedback(
            recommendation_id,
            feedback_type=normalized_signal,
            note=note.strip(),
        )
    )
    asyncio.run(
        memory.propagate_event(
            {
                "event_type": "feedback",
                "title": str(recommendation.get("title", "")),
                "metadata": {
                    "recommendation_id": recommendation_id,
                    "bvid": recommendation.get("bvid", ""),
                    "feedback_type": normalized_signal,
                    "feedback_note": note.strip(),
                },
            }
        )
    )
    record_immediate_feedback_cognition = getattr(
        soul_engine,
        "record_immediate_feedback_cognition",
        None,
    )
    if callable(record_immediate_feedback_cognition):
        with suppress(Exception):
            record_immediate_feedback_cognition(
                feedback_type=normalized_signal,
                title=str(recommendation.get("title", "")),
                note=note.strip(),
            )
    with suppress(Exception):
        asyncio.run(soul_engine.process_feedback_batch_if_needed())

    _print_status_panel("success", "反馈已记录", f"推荐ID {recommendation_id} 已更新。")
    rows = [
        ("推荐ID", str(recommendation_id)),
        ("反馈", normalized_signal),
    ]
    if note:
        rows.append(("备注", note.strip()))
    _print_key_value_table("反馈详情", rows)


def profile() -> None:
    """查看用户画像."""
    from openbiliclaw import cli as _cli
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError

    engine = _cli._build_soul_engine()
    try:
        profile_data = asyncio.run(engine.get_profile())
    except SoulProfileNotInitializedError as exc:
        console.print("[bold yellow]尚未初始化用户画像[/bold yellow]")
        console.print("请先执行 `openbiliclaw init` 拉取历史并生成初始画像。")
        raise typer.Exit(code=1) from exc

    _print_page_title("用户画像概览", "当前稳定画像")

    # -- 人格描述 ------------------------------------------------------------
    # Split by Chinese sentence terminators so Rich wraps at sentence boundaries
    # instead of mid-word CJK cell breaks. Each sentence starts on its own line.
    portrait_raw = profile_data.personality_portrait or "（暂无）"
    sentences = [s.strip() for s in re.split(r"(?<=[。！？])", portrait_raw) if s.strip()]
    portrait_body = "\n".join(sentences) if sentences else portrait_raw
    console.print(
        Panel(
            portrait_body,
            title="[bold cyan]人格描述[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
    )

    # -- 核心层 Core ---------------------------------------------------------
    core = profile_data.core
    _print_section_title("核心层 Core")
    core_traits = "、".join(core.core_traits) if core.core_traits else "（暂无）"
    deep_needs = "、".join(core.deep_needs) if core.deep_needs else "（暂无）"
    console.print(f"  [bold]人格特质[/bold]：{core_traits}")
    console.print(f"  [bold]深层需求[/bold]：{deep_needs}")
    mbti = core.mbti
    if mbti.type:
        dim_parts = [f"{key}={dim.pole}({dim.strength:.2f})" for key, dim in mbti.dimensions.items()]
        dims_text = "  ".join(dim_parts) if dim_parts else ""
        console.print(
            f"  [bold]MBTI[/bold]：{mbti.type}  置信度 {mbti.confidence:.0%}"
            + (f"  [dim]{dims_text}[/dim]" if dims_text else "")
        )

    # -- 价值层 Values -------------------------------------------------------
    values_layer = profile_data.values_layer
    _print_section_title("价值层 Values")
    values_text = "、".join(values_layer.values) if values_layer.values else "（暂无）"
    drivers_text = "、".join(values_layer.motivational_drivers) if values_layer.motivational_drivers else "（暂无）"
    console.print(f"  [bold]价值观[/bold]：{values_text}")
    console.print(f"  [bold]动机驱动[/bold]：{drivers_text}")

    # -- 角色层 Role ---------------------------------------------------------
    role = profile_data.role
    _print_section_title("角色层 Role")
    console.print(f"  [bold]生活阶段[/bold]：{role.life_stage or '（暂无）'}")
    console.print(f"  [bold]当前阶段[/bold]：{role.current_phase or '（暂无）'}")

    # -- 兴趣层 Interest -----------------------------------------------------
    interest = profile_data.interest
    _print_section_title("兴趣层 Interest")
    if interest.likes:
        sorted_likes = sorted(interest.likes, key=lambda d: d.weight, reverse=True)
        for dom in sorted_likes[:10]:
            spec_names = [s.name for s in dom.specifics[:5]]
            spec_text = "、".join(spec_names)
            suffix = f"  [dim]{spec_text}[/dim]" if spec_text else ""
            console.print(f"  ▸ [bold]{dom.domain}[/bold] [dim]({dom.weight:.2f})[/dim]{suffix}")
    else:
        console.print("  （暂无兴趣领域）")
    if interest.dislikes:
        dislike_text = "、".join(d.domain for d in interest.dislikes[:8])
        console.print(f"  [dim]讨厌领域：{dislike_text}[/dim]")
    if interest.favorite_up_users:
        up_total = len(interest.favorite_up_users)
        preview = "、".join(interest.favorite_up_users[:6])
        suffix = f"（共{up_total}位）" if up_total > 6 else ""
        console.print(f"  [bold]常看UP主[/bold]：{preview}{suffix}")

    # -- 表层 Surface --------------------------------------------------------
    surface = profile_data.surface
    _print_section_title("表层 Surface")
    if surface.cognitive_style:
        for idx, item in enumerate(surface.cognitive_style, start=1):
            console.print(f"  {idx}. {item}")
    else:
        console.print("  认知风格：（暂无）")
    console.print(
        f"  [bold]深度偏好[/bold]：{surface.style.depth_preference:.2f}"
        f"   [bold]探索开放度[/bold]：{surface.exploration_openness:.2f}"
    )


def chat() -> None:
    """与 Agent 对话（苏格拉底式深度交流）."""
    from openbiliclaw import cli as _cli
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError

    _cli._require_runtime_config()
    soul_engine = _cli._build_soul_engine()
    try:
        asyncio.run(soul_engine.get_profile())
    except SoulProfileNotInitializedError as exc:
        _print_status_panel(
            "warning",
            "尚未初始化用户画像",
            "请先执行 `openbiliclaw init` 拉取历史并生成初始画像。",
        )
        raise typer.Exit(code=1) from exc

    dialogue = _cli._build_dialogue(soul_engine)
    _print_page_title("苏格拉底式对话", "输入 exit / quit / 空行结束")

    try:
        while True:
            try:
                user_message = typer.prompt("你", prompt_suffix="： ").strip()
            except (click.Abort, EOFError, KeyboardInterrupt):
                console.print("阿花：对话结束。")
                return

            if user_message.lower() in {"", "exit", "quit"}:
                console.print("阿花：对话结束。")
                return

            reply = asyncio.run(dialogue.respond(user_message))
            console.print(f"阿花：{reply}")
    except KeyboardInterrupt:
        console.print("阿花：对话结束。")


def delight() -> None:
    """手动触发一次惊喜推荐检查."""
    from openbiliclaw import cli as _cli
    from openbiliclaw.recommendation.delight import DEFAULT_DELIGHT_THRESHOLD
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError

    _cli._require_runtime_config()
    soul_engine = _cli._build_soul_engine()
    try:
        profile = asyncio.run(soul_engine.get_profile())
    except SoulProfileNotInitializedError as exc:
        _print_status_panel(
            "warning",
            "尚未初始化用户画像",
            "请先执行 `openbiliclaw init` 拉取历史并生成初始画像。",
        )
        raise typer.Exit(code=1) from exc

    database = _cli._get_runtime_database()
    recommendation_engine = _cli._build_recommendation_engine()

    # Score un-scored items first
    asyncio.run(
        recommendation_engine.precompute_delight_scores(
            profile=profile,
            limit=30,
        )
    )

    candidate = database.get_delight_candidate(min_delight_score=DEFAULT_DELIGHT_THRESHOLD)

    _print_page_title("惊喜推荐", "从池中寻找你可能意外喜欢的内容")
    if candidate is None:
        _print_status_panel(
            "info",
            "暂时没有惊喜候选",
            "池中还没有文案已就绪的高分惊喜内容，多刷一阵会有的。",
        )
        return

    bvid = str(candidate.get("bvid", ""))
    title = str(candidate.get("title", ""))
    score = float(candidate.get("delight_score", 0.0))
    hook = str(candidate.get("delight_hook", ""))
    reason = str(candidate.get("delight_reason", ""))
    platform = str(candidate.get("source_platform", "") or "bilibili")
    url = str(candidate.get("content_url", ""))

    hook_label = f"【{hook}】" if hook else ""
    _print_key_value_table(
        f"{hook_label}阿B 觉得这条你会意外喜欢",
        [
            ("标题", title),
            ("惊喜分", f"{score:.2f}"),
            ("理由", reason or "—"),
            ("来源", platform),
            ("链接", url or f"https://www.bilibili.com/video/{bvid}"),
        ],
    )

    # Mark as notified so it won't be pushed again
    database.mark_delight_notified(bvid)
    console.print(f"  [dim]已标记 {bvid} 为已通知，不会重复推送。[/dim]")


def probe() -> None:
    """手动触发一次兴趣探针，确认或拒绝猜测方向."""
    from openbiliclaw import cli as _cli
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError

    _cli._require_runtime_config()
    soul_engine = _cli._build_soul_engine()
    try:
        asyncio.run(soul_engine.get_profile())
    except SoulProfileNotInitializedError as exc:
        _print_status_panel(
            "warning",
            "尚未初始化用户画像",
            "请先执行 `openbiliclaw init` 拉取历史并生成初始画像。",
        )
        raise typer.Exit(code=1) from exc

    speculator = getattr(soul_engine, "_speculator", None)
    if speculator is None:
        _print_status_panel("info", "猜测引擎未就绪", "Speculator 未初始化。")
        raise typer.Exit(code=1)

    specs = speculator.get_active_speculations()
    _print_page_title("兴趣探针", "确认或拒绝阿B 正在试探的方向")

    if not specs:
        _print_status_panel("info", "暂时没有活跃的猜测", "过一阵阿B 会生成新的猜测方向。")
        return

    for i, spec in enumerate(specs, 1):
        specifics = [
            str(getattr(s, "name", "")).strip()
            for s in getattr(spec, "specifics", [])
            if str(getattr(s, "name", "")).strip()
        ][:3]
        hint = f"（{', '.join(specifics)}）" if specifics else ""
        progress = f"{spec.confirmation_count}/{spec.confirmation_threshold}"

        console.print(f"\n  [bold]{i}. {spec.domain}[/bold] {hint}")
        console.print(f"     理由：{spec.reason or '—'}")
        console.print(f"     确认进度：{progress}  置信度：{spec.confidence:.0%}")

    console.print()
    try:
        choice = typer.prompt(
            "输入序号确认（是），序号+n 拒绝（如 1n），或 q 退出",
            prompt_suffix="： ",
        ).strip()
    except (click.Abort, EOFError, KeyboardInterrupt):
        return

    if choice.lower() in {"q", "quit", "exit", ""}:
        return

    reject = choice.endswith("n") or choice.endswith("N")
    index_str = choice.rstrip("nN").strip()
    try:
        index = int(index_str) - 1
    except ValueError:
        console.print("[red]无效输入[/red]")
        raise typer.Exit(code=1) from None

    if index < 0 or index >= len(specs):
        console.print("[red]序号超出范围[/red]")
        raise typer.Exit(code=1)

    target = specs[index]
    domain = target.domain

    if reject:
        ok = speculator.user_reject_speculation(domain)
        if ok:
            console.print(f"  好，「{domain}」先不看了，30 天内不再猜测这个方向。")
        else:
            console.print(f"  [yellow]未找到活跃的「{domain}」猜测。[/yellow]")
    else:
        ok = speculator.user_confirm_speculation(domain)
        if ok:
            # Trigger promotion
            memory = getattr(soul_engine, "_memory", None)
            load_runtime_state = getattr(memory, "load_discovery_runtime_state", None)

            def _load_feedback_history() -> object:
                if not callable(load_runtime_state):
                    return []
                runtime_state = load_runtime_state()
                if not isinstance(runtime_state, dict):
                    return []
                return runtime_state.get("probe_feedback_history", [])

            profile = asyncio.run(soul_engine.get_profile())
            asyncio.run(
                speculator.force_tick(
                    profile,
                    feedback_history=_load_feedback_history(),
                    feedback_history_loader=_load_feedback_history,
                )
            )
            console.print(f"  好，「{domain}」记住了，已转入正式兴趣。")
        else:
            console.print(f"  [yellow]未找到活跃的「{domain}」猜测。[/yellow]")

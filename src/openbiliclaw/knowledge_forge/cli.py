"""Knowledge Forge CLI 命令（文档 §6）。

通过 ``openbiliclaw knowledge-forge <subcommand>`` 调用。
"""

from __future__ import annotations

import json
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

console = Console()

kf_app = typer.Typer(
    name="knowledge-forge",
    help="Knowledge Forge：正文清理/分层摘要/实体/知识Wiki/缺口分析/质量审计",
    no_args_is_help=True,
)


def _render_stats(stats: dict[str, Any], title: str) -> None:
    table = Table(title=title)
    table.add_column("指标", style="cyan")
    table.add_column("数值", style="green")
    for k, v in stats.items():
        if isinstance(v, (dict, list)):
            continue
        table.add_row(str(k), str(v))
    console.print(table)
    for k, v in stats.items():
        if isinstance(v, dict):
            console.print(f"[bold]{k}[/bold]")
            sub = Table()
            sub.add_column("键", style="cyan")
            sub.add_column("值", style="green")
            for sk, sv in list(v.items())[:20]:
                sub.add_row(str(sk), str(sv))
            console.print(sub)


@kf_app.command("summary")
def summary_cmd(
    article_id: int | None = typer.Option(None, "--article-id", "-a", help="单篇文章 ID"),
    limit: int = typer.Option(100, "--limit", help="批量生成上限"),
    source: str | None = typer.Option(None, "--source", help="按来源筛选（zhihu/xhs/...）"),
    force: bool = typer.Option(False, "--force", help="覆盖已有摘要重新生成"),
    dry_run: bool = typer.Option(False, "--dry-run", help="试运行（最多处理 5 篇）"),
) -> None:
    """分层摘要：单篇或批量生成三层摘要。"""
    from .summary_engine import SummaryEngine, summarize_article

    if article_id is not None:
        result = summarize_article(article_id, force=force)
        if result.error:
            console.print(f"[red]失败：{result.error}[/red]")
            raise typer.Exit(1)
        console.print(
            f"文章 {article_id} 摘要生成完成：\n"
            f"  detailed: {len(result.detailed)} 字\n"
            f"  compact: {len(result.compact)} 字\n"
            f"  ultra_compact: {len(result.ultra_compact)} 字\n"
            f"  质量分: {result.quality if result.quality is not None else 'N/A'}"
        )
        return

    import asyncio

    stats = asyncio.run(
        SummaryEngine().generate_batch(
            limit=limit, source_type=source, force=force, dry_run=dry_run
        )
    )
    _render_stats(stats, "分层摘要批量生成")


@kf_app.command("entities")
def entities_cmd(
    article_id: int | None = typer.Option(None, "--article-id", "-a", help="单篇文章 ID"),
    limit: int = typer.Option(100, "--limit", help="批量提取上限"),
    source: str | None = typer.Option(None, "--source", help="按来源筛选"),
    dry_run: bool = typer.Option(False, "--dry-run", help="试运行"),
) -> None:
    """实体/概念提取：作者、主题、概念。"""
    from .entity_extractor import EntityExtractor, extract_article

    if article_id is not None:
        result = extract_article(article_id)
        if result.get("error"):
            console.print(f"[red]失败：{result['error']}[/red]")
            raise typer.Exit(1)
        console.print(
            f"文章 {article_id} 提取完成：\n"
            f"  作者: {result.get('author')}\n"
            f"  主题: {result.get('topics')}\n"
            f"  概念: {result.get('concepts')}"
        )
        return

    import asyncio

    stats = asyncio.run(
        EntityExtractor().extract_batch(limit=limit, source_type=source, dry_run=dry_run)
    )
    _render_stats(stats, "实体/概念批量提取")


@kf_app.command("audit")
def audit_cmd(
    output: str | None = typer.Option(None, "--output", help="报告输出文件（Markdown）"),
) -> None:
    """全量质量审计（只读扫描，不修改文章数据）。"""
    from .quality_auditor import QualityAuditor

    auditor = QualityAuditor()
    console.print("[bold]正在执行全量质量审计（只读）…[/bold]")
    stats = auditor.run_full_audit()
    _render_stats(stats, "质量审计结果")
    report = auditor.generate_report(task_id=stats.get("task_id"))
    if output:
        from pathlib import Path

        Path(output).write_text(report, encoding="utf-8")
        console.print(f"[green]报告已写入 {output}[/green]")
    else:
        console.print(report)


@kf_app.command("wiki")
def wiki_cmd(
    limit: int = typer.Option(100, "--limit", help="本次检测文章数上限"),
    dry_run: bool = typer.Option(False, "--dry-run", help="试运行"),
) -> None:
    """知识 Wiki：检测文章间语义关联。"""
    from .wiki_builder import detect_relations

    console.print("[bold]正在检测文章间语义关联…[/bold]")
    stats = detect_relations(limit=limit)
    _render_stats(stats, "知识 Wiki 关联检测")


@kf_app.command("gap-analysis")
def gap_cmd(
    output: str | None = typer.Option(None, "--output", help="报告输出文件（Markdown）"),
) -> None:
    """知识缺口分析。"""
    from .gap_analyst import run_gap_analysis

    console.print("[bold]正在执行知识缺口分析…[/bold]")
    result = run_gap_analysis()
    console.print(
        "发现缺口："
        f"{result['gaps_found']}"
        f"（高 {result['high']} / 中 {result['medium']} / 低 {result['low']}）"
    )
    if output:
        from pathlib import Path

        Path(output).write_text(result["report"], encoding="utf-8")
        console.print(f"[green]报告已写入 {output}[/green]")
    else:
        console.print(result["report"])


@kf_app.command("contradiction")
def contradiction_cmd(
    limit: int = typer.Option(0, "--limit", help="最多检测的文章对数（0=全部候选）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="试运行，不落库"),
    output: str | None = typer.Option(None, "--output", help="矛盾报告输出文件（Markdown）"),
) -> None:
    """观点矛盾检测：LLM 判定同主题文章的观点冲突。"""
    from .contradiction_detector import ContradictionDetector, run_contradiction_detection

    console.print("[bold]正在检测观点矛盾…[/bold]")
    if dry_run:
        result = run_contradiction_detection(limit=limit, dry_run=True)
    else:
        result = run_contradiction_detection(limit=limit)
    console.print(
        f"扫描 {result['pairs_scanned']} 对候选，"
        f"判定 {result['checked']} 对，发现矛盾 {result['contradictions']} 个"
    )
    if result["llm_errors"]:
        console.print(f"[yellow]LLM 失败 {result['llm_errors']} 对（已跳过）[/yellow]")
    if output:
        from pathlib import Path

        report = ContradictionDetector().generate_report()
        Path(output).write_text(report, encoding="utf-8")
        console.print(f"[green]矛盾报告已写入 {output}[/green]")
    else:
        console.print(ContradictionDetector().generate_report())


@kf_app.command("dead-link")
def dead_link_cmd(
    limit: int = typer.Option(0, "--limit", help="检查上限（0=全部待检）"),
    batch_size: int | None = typer.Option(None, "--batch-size", help="单批大小（默认用配置）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="试运行，不落库不请求"),
) -> None:
    """死链检测：分批异步检查文章 URL 可用性。"""
    from .dead_link_checker import run_dead_link_check

    console.print("[bold]正在执行死链检测…[/bold]")
    stats = run_dead_link_check(limit=limit, batch_size=batch_size, dry_run=dry_run)
    console.print(
        f"检查 {stats['checked']} 篇：有效 {stats['alive']} / 死链 {stats['dead']} / "
        f"待确认 {stats['pending']} / 重定向 {stats['redirect']} / 异常 {stats['error']}"
        f"（跳过近期已检 {stats['skipped_recent']}）"
    )
    if dry_run:
        console.print("[yellow]试运行：未发请求未落库[/yellow]")


@kf_app.command("low-quality")
def low_quality_cmd(
    limit: int = typer.Option(0, "--limit", help="抽样上限（0=按配置比例）"),
    sample_ratio: float | None = typer.Option(None, "--sample-ratio", help="抽样比例（默认 0.05）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="试运行，不落库不调 LLM"),
) -> None:
    """低质量内容检测：LLM 抽样判定广告/垃圾/无意义内容。"""
    from .low_quality_detector import run_low_quality_detection

    console.print("[bold]正在执行低质量内容抽样检测…[/bold]")
    stats = run_low_quality_detection(limit=limit, sample_ratio=sample_ratio, dry_run=dry_run)
    console.print(
        f"抽样 {stats['sampled']} 篇：低质量 {stats['low_quality']} / 正常 {stats['normal']}"
        f" / 失败 {stats['failed']}"
    )
    if dry_run:
        console.print("[yellow]试运行：未调 LLM 未落库[/yellow]")


@kf_app.command("auto-fix")
def auto_fix_cmd(
    issue_types: str | None = typer.Option(
        None, "--issue-types", help="逗号分隔的问题类型（如 content_contamination,missing_tags）"
    ),
    limit: int = typer.Option(0, "--limit", help="修复上限（0=全部可修复）"),
    enable: bool = typer.Option(False, "--enable", help="强制开启自动修复（默认需 config 开启）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="试运行，不落库"),
) -> None:
    """自动修复：处理审计问题（污染重清理/补哈希/补标签/补摘要/标记重复）。"""
    from .auto_fixer import run_auto_fix

    console.print("[bold]正在执行自动修复…[/bold]")
    types = [t.strip() for t in (issue_types or "").split(",") if t.strip()] or None
    stats = run_auto_fix(issue_types=types, limit=limit, enable=enable, dry_run=dry_run)
    if stats.get("disabled"):
        console.print(f"[yellow]未启用：{stats.get('reason')}[/yellow]")
        return
    console.print(
        f"扫描 {stats['scanned']} 条问题：修复 {stats['fixed']} / 待人工 {stats['skipped_manual']}"
        f" / 失败 {stats['failed']}"
    )
    if dry_run:
        console.print("[yellow]试运行：未落库[/yellow]")


@kf_app.command("backfill")
def backfill_cmd(
    limit: int = typer.Option(10, "--limit", help="本批处理篇数"),
    batch_size: int = typer.Option(10, "--batch-size", help="并发批次大小"),
    priority: str = typer.Option("high", "--priority", help="high=高价值优先 / all=全部补齐"),
    offset: int = typer.Option(0, "--offset", help="断点续跑：从文章 id > offset 开始"),
    dry_run: bool = typer.Option(False, "--dry-run", help="试运行，不落库不调 LLM"),
) -> None:
    """历史文章批量处理：分批补全 清理/摘要/标签/实体/质量分。"""
    from .batch_processor import run_backfill

    console.print("[bold]正在批量补全历史文章…[/bold]")
    stats = run_backfill(
        limit=limit, batch_size=batch_size, priority=priority, offset=offset, dry_run=dry_run
    )
    console.print(
        f"处理 {stats['processed']} 篇（失败 {stats['failed']}）："
        f"清理 {stats['cleaned']} / 摘要 {stats['summarized']} / "
        f"标签 {stats['tagged']} / 实体 {stats['entities']} / 质量分 {stats['scored']}"
    )
    console.print(f"断点：下次从 id > {stats['last_id']} 继续")
    if dry_run:
        console.print("[yellow]试运行：未落库未调 LLM[/yellow]")


@kf_app.command("gap-fill")
def gap_fill_cmd(
    limit_gaps: int = typer.Option(5, "--limit-gaps", help="本批处理的缺口数"),
    fill_per_gap: int = typer.Option(3, "--fill-per-gap", help="每个缺口最多填补篇数"),
    search_per_topic: int = typer.Option(10, "--search-per-topic", help="每主题搜索候选数"),
    dry_run: bool = typer.Option(False, "--dry-run", help="试运行，不落库不搜索"),
) -> None:
    """自动补充闭环：缺口 → B站搜索 → 提取入库（含清理）→ 标记缺口。"""
    from .gap_filler import run_gap_fill

    console.print("[bold]正在执行自动补充闭环…[/bold]")
    stats = run_gap_fill(
        limit_gaps=limit_gaps,
        fill_per_gap=fill_per_gap,
        search_per_topic=search_per_topic,
        dry_run=dry_run,
    )
    console.print(
        f"扫描缺口 {stats['gaps_scanned']} 个：填补 {stats['gaps_filled']} 个"
        f" / 入库 {stats['articles_filled']} 篇 / 已存在 {stats['already_exists']}"
        f" / 提取失败 {stats['extract_failed']}"
    )
    for f in stats.get("filled", []):
        console.print(f"  ✓ 缺口 #{f['gap_id']}「{f['topic']}」已补齐")
    if dry_run:
        console.print("[yellow]试运行：未搜索未落库[/yellow]")


@kf_app.command("entity-describe")
def entity_describe_cmd(
    limit: int = typer.Option(10, "--limit", help="本批处理实体数"),
    entity_type: str = typer.Option("", "--type", help="限定实体类型 author/topic/concept"),
    refresh_days: int = typer.Option(30, "--refresh-days", help="超过 N 天未更新才重新生成"),
    dry_run: bool = typer.Option(False, "--dry-run", help="试运行，不落库"),
) -> None:
    """实体描述自动更新：LLM 基于关联文章生成/更新实体简介（设计 3.2）。"""
    from .entity_description_updater import run_entity_describe

    console.print("[bold]正在更新实体描述…[/bold]")
    stats = run_entity_describe(
        limit=limit, entity_type=entity_type, refresh_days=refresh_days, dry_run=dry_run
    )
    console.print(
        f"扫描 {stats['scanned']} 个实体：更新 {stats['updated']} 个 / 失败 {stats['failed']} 个"
    )
    for u in stats.get("updates", []):
        console.print(f"  ✓ #{u['id']}「{u['name']}」({u['type']}) 描述已更新")
    if dry_run:
        console.print("[yellow]试运行：未落库[/yellow]")


@kf_app.command("entity-relations")
def entity_relations_cmd(
    min_co_occur: int = typer.Option(1, "--min-co-occur", help="最小共现文章数"),
    dry_run: bool = typer.Option(False, "--dry-run", help="试运行，不落库"),
) -> None:
    """实体间关联持久化：从文章-实体关联计算共现并写入 entity_relations。"""
    from .entity_relation_builder import run_entity_relations

    console.print("[bold]正在计算实体共现关系…[/bold]")
    stats = run_entity_relations(min_co_occur=min_co_occur, dry_run=dry_run)
    console.print(
        f"共现对 {stats['pairs_computed']} 个：写入 {stats['pairs_written']}"
        f" / 低于阈值跳过 {stats['skipped_below_min']}"
    )
    if dry_run:
        console.print("[yellow]试运行：未落库[/yellow]")


@kf_app.command("run-scheduled")
def run_scheduled_cmd(
    include_dead_link: bool = typer.Option(
        False, "--include-dead-link", help="同时执行死链检查（默认关闭避免风控）"
    ),
    include_gap_fill: bool = typer.Option(
        False, "--include-gap-fill", help="同时执行自动补充闭环（默认关闭，B站搜索有冷却/风控）"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="试运行，不落库"),
) -> None:
    """定时组合任务：质量审计 → 缺口分析 → 实体共现刷新 →（可选）死链/自动补充。"""
    from .schedule import run_scheduled

    console.print("[bold]正在执行定时组合任务…[/bold]")
    stats = run_scheduled(
        include_dead_link=include_dead_link,
        include_gap_fill=include_gap_fill,
        dry_run=dry_run,
    )
    console.print(stats["report"])


@kf_app.command("schedule-show")
def schedule_show_cmd() -> None:
    """输出接入系统 crontab 的推荐命令。"""
    from .schedule import build_schedule_guide

    console.print(build_schedule_guide())


@kf_app.command("clean")
def clean_cmd(
    article_id: int | None = typer.Option(None, "--article-id", "-a", help="单篇文章 ID"),
    limit: int = typer.Option(100, "--limit", help="批量清理上限"),
    dry_run: bool = typer.Option(False, "--dry-run", help="试运行"),
) -> None:
    """正文清理：清理并回写 content_cleaned。"""
    from .content_cleaner import ContentCleaner

    cleaner = ContentCleaner()
    if article_id is not None:
        import sqlite3

        from .summary_engine import _default_db_path

        conn = sqlite3.connect(_default_db_path())
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        if row is None:
            console.print("[red]文章不存在[/red]")
            raise typer.Exit(1)
        result = cleaner.clean_article_row(dict(row))
        if result.cleaned_text:
            conn.execute(
                "UPDATE articles SET content_cleaned=?, content_clean_score=?,"
                " content_clean_log=?, content_verified=?, content_verify_result=?"
                " WHERE id=?",
                (
                    result.cleaned_text,
                    result.clean_score,
                    result.to_log_json(),
                    1 if result.verified else 0,
                    json.dumps(result.verify_issues, ensure_ascii=False),
                    article_id,
                ),
            )
            conn.commit()
        conn.close()
        status = "通过" if result.verified else f"未通过：{result.verify_issues}"
        console.print(
            f"文章 {article_id} 清理完成：{result.original_length} → "
            f"{len(result.cleaned_text)} 字，评分 {result.clean_score}，验证{status}"
        )
        return
    console.print("批量清理请用：openbiliclaw knowledge-forge clean --article-id <id>")


def register(app: typer.Typer) -> None:
    """注册到主 CLI。"""
    app.add_typer(kf_app, name="knowledge-forge")

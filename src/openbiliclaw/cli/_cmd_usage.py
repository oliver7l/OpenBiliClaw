"""CLI 花费与日志清理命令组（``openbiliclaw cost`` / ``logs-prune``）。

从上帝文件 ``cli/__init__.py`` 抽出（P4 第三刀，2026-09-13）。
本模块顶层**不得** import ``openbiliclaw.cli``（避免循环依赖）；
命令经 ``register(app)`` 挂回主 app（与 knowledge_forge 等外部命令组
同款模式）。

patch 语义保留：cost 引用的 ``_ensure_runtime_database_healthy`` /
``_get_runtime_database`` 等共享符号**在函数体内延迟**经
``from openbiliclaw import cli as _cli`` 动态取属性 —— 顶层 from-import
会绑定旧值并破坏 ``monkeypatch.setattr(cli_module, ...)``（项目已知的
from-import 绑定坑）。
"""

from __future__ import annotations

import typer
from rich.table import Table


def register(app: typer.Typer) -> None:
    app.command()(cost)
    app.command("logs-prune")(logs_prune)


def cost(
    days: int = typer.Option(7, "--days", min=1, max=90, help="统计窗口(天)"),
    by: str = typer.Option(
        "all",
        "--by",
        help="单维度展开: all (默认 / 三表全显) / day / provider / caller",
    ),
) -> None:
    """显示本机 LLM 调用花费(按天 + 按 provider/model + 按 caller 模块)。

    数据来源:每次成功的 LLM 调用都会写一条到 ``llm_usage`` 表(v0.3.26+)。
    费用按 ``llm.pricing`` 里的官方单价估算,允许 ±20% 误差。本地 Ollama
    调用单价 0,只统计调用次数。

    ``--by caller`` 显示按模块(discovery / recommendation / soul / api 等)
    拆分的占比,这是排查"钱花在哪一层"最有用的视图。
    """
    from openbiliclaw import cli as _cli

    _cli._print_page_title("LLM 调用花费", f"最近 {days} 天")
    _cli._ensure_runtime_database_healthy()
    db = _cli._get_runtime_database()
    console = _cli.console

    daily = db.query_llm_usage_by_day(days=days)
    by_provider = db.query_llm_usage_by_provider(days=days)
    by_caller = db.query_llm_usage_by_caller(days=days)
    total = db.query_llm_usage_total(days=days)

    if total["calls"] == 0:
        _cli._print_status_panel(
            "info",
            "暂无数据",
            "这台机器最近没记录到 LLM 调用。\n如果你刚升级到 v0.3.26+,旧数据不会回填——继续运行一段时间后再来查。",
        )
        return

    show_all = by == "all"

    if show_all or by == "day":
        daily_table = Table(show_header=True, header_style="bold cyan", title="按天 (cost by day)")
        daily_table.add_column("日期", no_wrap=True)
        daily_table.add_column("调用数", justify="right")
        daily_table.add_column("input tokens", justify="right")
        daily_table.add_column("output tokens", justify="right")
        daily_table.add_column("¥ 估算", justify="right", style="bold yellow")
        for row in daily:
            daily_table.add_row(
                str(row["day"]),
                f"{row['calls']:,}",
                f"{row['prompt_tokens']:,}",
                f"{row['completion_tokens']:,}",
                f"¥{row['cost_cny']:.4f}",
            )
        console.print(daily_table)
        console.print()

    total_cost = total["cost_cny"] or 1e-9

    if show_all or by == "provider":
        provider_table = Table(
            show_header=True,
            header_style="bold magenta",
            title="按 Provider/Model (cost by provider)",
        )
        provider_table.add_column("Provider", no_wrap=True)
        provider_table.add_column("Model")
        provider_table.add_column("调用数", justify="right")
        provider_table.add_column("input", justify="right")
        provider_table.add_column("output", justify="right")
        provider_table.add_column("¥ 占比", justify="right", style="bold yellow")
        for row in by_provider:
            share = row["cost_cny"] / total_cost * 100
            provider_table.add_row(
                row["provider"] or "?",
                row["model"] or "(default)",
                f"{row['calls']:,}",
                f"{row['prompt_tokens']:,}",
                f"{row['completion_tokens']:,}",
                f"¥{row['cost_cny']:.4f} ({share:.0f}%)",
            )
        console.print(provider_table)
        console.print()

    if show_all or by == "caller":
        caller_table = Table(
            show_header=True,
            header_style="bold green",
            title="按模块 (cost by caller — 钱花在哪一层 / cache 命中率)",
        )
        caller_table.add_column("Caller (模块.动作)", no_wrap=True)
        caller_table.add_column("调用数", justify="right")
        caller_table.add_column("input", justify="right")
        caller_table.add_column("output", justify="right")
        # v0.3.28+: cache hit rate per caller. Low hit rate (red) on a
        # high-cost caller is the smoking gun for prompt-prefix
        # instability — that's where to focus prompt-builder audits.
        caller_table.add_column("cache 命中", justify="right")
        caller_table.add_column("¥ 占比", justify="right", style="bold yellow")
        for row in by_caller:
            share = row["cost_cny"] / total_cost * 100
            prompt_tok = int(row["prompt_tokens"])
            cached_tok = int(row.get("cached_input_tokens", 0) or 0)
            if prompt_tok > 0 and cached_tok > 0:
                hit_pct = cached_tok / prompt_tok * 100
                if hit_pct < 30:
                    cache_cell = f"[red]{hit_pct:.0f}%[/red]"
                elif hit_pct < 60:
                    cache_cell = f"[yellow]{hit_pct:.0f}%[/yellow]"
                else:
                    cache_cell = f"[green]{hit_pct:.0f}%[/green]"
                cache_cell += f" ({cached_tok:,}/{prompt_tok:,})"
            else:
                cache_cell = "[dim]—[/dim]"
            caller_table.add_row(
                row["caller"] or "[dim](untagged)[/dim]",
                f"{row['calls']:,}",
                f"{row['prompt_tokens']:,}",
                f"{row['completion_tokens']:,}",
                cache_cell,
                f"¥{row['cost_cny']:.4f} ({share:.0f}%)",
            )
        console.print(caller_table)
        console.print()

    avg_per_day = total["cost_cny"] / max(1, len(daily))
    total_prompt = int(total["prompt_tokens"])
    total_cached = int(total.get("cached_input_tokens", 0) or 0)
    cache_summary = ""
    if total_prompt > 0 and total_cached > 0:
        overall_hit = total_cached / total_prompt * 100
        cache_summary = (
            f"\ncache 命中: [bold green]{overall_hit:.1f}%[/bold green] "
            f"({total_cached:,}/{total_prompt:,} input tokens served from cache)"
        )
    elif total_prompt > 0:
        cache_summary = "\ncache 命中: [dim]0%(还没命中或 provider 不上报 cache 字段)[/dim]"
    _cli._print_status_panel(
        "info",
        f"近 {days} 天合计",
        f"总调用 [bold]{total['calls']:,}[/bold] 次, "
        f"总 token [bold]{total['total_tokens']:,}[/bold] "
        f"(input {total['prompt_tokens']:,} + output {total['completion_tokens']:,}), "
        f"估算消耗 [bold yellow]¥{total['cost_cny']:.4f}[/bold yellow]"
        f"{cache_summary}\n"
        f"按记录到的天数平均 ≈ ¥{avg_per_day:.4f}/天 ≈ "
        f"¥{avg_per_day * 30:.2f}/月\n"
        "[dim]（费率为公开渠道估算,与 provider 实际账单可能差 ±20%。"
        "tail daemon 日志可以看每次调用的实时 [llm-cost] INFO 行,"
        "cache 命中率 < 30% 的 caller 在 by-caller 表里会标红。）[/dim]",
    )


def logs_prune(
    truncate_mb: int = typer.Option(
        200,
        "--truncate-mb",
        min=0,
        help="单个 unmanaged 日志文件超过此 MB 数则截断为 0 字节(0 = 关闭)",
    ),
    max_age_days: int = typer.Option(
        30,
        "--max-age-days",
        min=0,
        help="超过此天数的 unmanaged 日志文件直接删除(0 = 关闭)",
    ),
    aggregate_budget_mb: int = typer.Option(
        500,
        "--aggregate-budget-mb",
        min=0,
        help="logs/ 目录(含 unmanaged + managed)总磁盘预算 MB,超出时按 mtime 从旧到新删 unmanaged",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="实际执行删除/截断;默认是 dry-run 模式只列出会改什么",
    ),
) -> None:
    """手动 prune logs/ 目录的日志文件(默认 dry-run)。

    daemon 启动时已经会按 config 自动跑这套清理(v0.3.30+),这个命令是
    手动触发用的 —— 比如 daemon 没在运行 / 想查看会删什么 / 临时换一组
    更激进或更保守的阈值。
    """
    import time as _time
    from pathlib import Path

    from openbiliclaw import cli as _cli
    from openbiliclaw.config import load_config
    from openbiliclaw.logging_setup import _is_managed_log

    console = _cli.console
    config = load_config()
    log_dir = config.logging.directory_path
    managed = config.logging.filename

    _cli._print_page_title("LLM 日志清理 (logs prune)", str(log_dir))
    if not log_dir.exists():
        _cli._print_status_panel("warning", "日志目录不存在", f"{log_dir} 还没创建。")
        return

    truncate_bytes = truncate_mb * 1024 * 1024
    age_cutoff = _time.time() - max_age_days * 86400 if max_age_days > 0 else 0.0
    budget_bytes = aggregate_budget_mb * 1024 * 1024

    actions: list[tuple[str, str, int]] = []  # (action, path, size)
    total = 0
    for path in sorted(log_dir.iterdir()):
        if not path.is_file():
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        total += st.st_size
        is_managed = _is_managed_log(path, managed)
        tag = "managed" if is_managed else "unmanaged"
        if is_managed:
            actions.append(("keep", f"{path.name}  [{tag}]", st.st_size))
            continue
        if truncate_mb > 0 and st.st_size >= truncate_bytes:
            actions.append(
                (
                    "truncate",
                    f"{path.name}  [{tag}, > {truncate_mb} MB]",
                    st.st_size,
                )
            )
            continue
        if max_age_days > 0 and st.st_mtime < age_cutoff:
            age_days = (_time.time() - st.st_mtime) / 86400
            actions.append(
                (
                    "delete (age)",
                    f"{path.name}  [{tag}, {age_days:.0f} days old]",
                    st.st_size,
                )
            )
            continue
        actions.append(("keep", f"{path.name}  [{tag}]", st.st_size))

    # Aggregate-budget pass: simulate evicting oldest unmanaged 'keep' rows
    if aggregate_budget_mb > 0 and total > budget_bytes:
        # Re-sort the not-yet-doomed unmanaged ones by mtime
        unmanaged_keep: list[tuple[Path, float, int, int]] = []
        for i, (action, label, size) in enumerate(actions):
            if action != "keep" or "[managed]" in label:
                continue
            name = label.split("  ")[0]
            try:
                st = (log_dir / name).stat()
            except OSError:
                continue
            unmanaged_keep.append((log_dir / name, st.st_mtime, size, i))
        unmanaged_keep.sort(key=lambda x: x[1])
        running = total
        for path, _mt, size, idx in unmanaged_keep:
            if running <= budget_bytes:
                break
            actions[idx] = (
                "delete (budget)",
                f"{path.name}  [unmanaged, oldest, evict to fit {aggregate_budget_mb} MB]",
                size,
            )
            running -= size

    table = Table(
        show_header=True,
        header_style="bold cyan",
        title=f"Plan ({'APPLY' if apply else 'DRY-RUN'})",
    )
    table.add_column("Action", no_wrap=True)
    table.add_column("File", overflow="fold")
    table.add_column("Size", justify="right")
    for action, label, size in actions:
        size_h = f"{size / (1024 * 1024):.1f} MB"
        style = "green" if action == "keep" else "yellow" if action == "truncate" else "red"
        table.add_row(f"[{style}]{action}[/{style}]", label, size_h)
    console.print(table)

    will_change = [a for a in actions if a[0] != "keep"]
    freed = sum(s for action, _, s in actions if action.startswith("delete")) + sum(
        s - 1
        for action, _, s in actions
        if action == "truncate"  # leaves ~1 byte stub
    )
    console.print(
        f"\n会释放约 [bold]{freed / (1024 * 1024):.1f} MB[/bold] 磁盘 / 影响 [bold]{len(will_change)}[/bold] 个文件"
    )

    if not apply:
        console.print("\n[yellow]这是 dry-run。加上 --apply 才会真的改文件。[/yellow]")
        return

    # Apply
    import time as _time2

    actually_freed = 0
    for action, label, size in actions:
        name = label.split("  ")[0]
        path = log_dir / name
        if action == "truncate":
            try:
                with path.open("w", encoding="utf-8") as f:
                    f.write(
                        f"# truncated by `openbiliclaw logs-prune` "
                        f"{_time2.strftime('%Y-%m-%d %H:%M:%S')} — was "
                        f"{size / (1024 * 1024):.0f} MB\n"
                    )
                actually_freed += size
            except OSError as exc:
                console.print(f"[red]✗ truncate {path}: {exc}[/red]")
        elif action.startswith("delete"):
            try:
                path.unlink()
                actually_freed += size
            except OSError as exc:
                console.print(f"[red]✗ unlink {path}: {exc}[/red]")
    freed_mb = actually_freed / (1024 * 1024)
    console.print(f"\n[bold green]✓ Applied — actually freed {freed_mb:.1f} MB[/bold green]")

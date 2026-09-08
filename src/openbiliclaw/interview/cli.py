"""``openbiliclaw interview`` 命令组——求职面试备战入口。

对应外部 `kb.py` 的检索能力，全部命令走 ``InterviewEngine``。
子命令与 kb.py 中文命令的对应关系见 docs/modules/interview.md。
"""

from __future__ import annotations

import os

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from openbiliclaw.interview.engine import InterviewEngine, resolve_root

interview_app = typer.Typer(help="求职面试备战命令（查/速记/岗位/数字/项目/日志/建档）")
console = Console()

_LAYER_HINT = {"01": "原始资料库", "02": "方向知识库", "03": "岗位弹药库"}


def _engine(root_override: str | None = None) -> InterviewEngine | None:
    """创建引擎；未配置时打印提示并返回 None。"""
    cfg_root: str | None = None
    try:
        from openbiliclaw.config import load_config

        cfg = load_config()
        cfg_root = str(getattr(getattr(cfg, "interview", None), "root", "") or "")
    except Exception:  # noqa: BLE001
        cfg_root = None

    root = root_override or cfg_root or ""
    engine = InterviewEngine(resolve_root(root))
    if not engine.is_configured:
        console.print(
            f"[red]求职知识库未配置或目录不存在: {engine.root}[/red]\n"
            "请设置 [bold]config.toml 的 [interview] root[/bold]，"
            "或环境变量 OPENBILICLAW_INTERVIEW_ROOT。"
        )
        return None
    return engine


def _render_jobs_table(jobs: list[dict[str, str]]) -> None:
    if not jobs:
        console.print("[yellow]未找到匹配岗位[/yellow]")
        return
    table = Table(title="岗位总览", show_lines=False)
    for col in ("公司", "岗位", "面试时间", "状态", "主打方向"):
        table.add_column(col, overflow="fold")
    for j in jobs:
        table.add_row(
            j.get("公司", ""),
            j.get("岗位", ""),
            j.get("面试时间", ""),
            j.get("状态", ""),
            j.get("主打方向", ""),
        )
    console.print(table)


@interview_app.command("search")
def interview_search(
    keyword: str = typer.Argument(..., help="检索关键词"),
    limit: int = typer.Option(40, "--limit", "-l", help="最多返回命中文件数"),
) -> None:
    """全文检索（02方向库/03岗位库/腾讯文档资料/解码文本）。"""
    engine = _engine()
    if engine is None:
        return
    hits = engine.search(keyword, max_hits=limit)
    console.print(f"[bold]全文检索『{keyword}』命中 {len(hits)} 个文件:[/bold]")
    for h in hits:
        console.print(f"  [cyan]{h['path']}[/cyan]:{h['line']}  {h['snippet']}")


@interview_app.command("job")
def interview_job(
    company: str = typer.Argument(..., help="公司关键词"),
) -> None:
    """查看某公司/岗位登记信息（01_岗位表.csv）。"""
    engine = _engine()
    if engine is None:
        return
    _render_jobs_table(engine.jobs(company))


@interview_app.command("numbers")
def interview_numbers(
    keyword: str | None = typer.Argument(None, help="过滤关键词（默认全部）"),
) -> None:
    """真实数字表（口径权威源，严禁编造）。"""
    engine = _engine()
    if engine is None:
        return
    rows = engine.numbers(keyword)
    if not rows:
        console.print(f"[yellow]真实数字表中未找到: {keyword or '(空)'}[/yellow]")
        return
    table = Table(title=f"真实数字表（{len(rows)} 条）")
    for col in ("数字", "口径", "公司/项目", "来源"):
        table.add_column(col, overflow="fold")
    for r in rows:
        table.add_row(
            r.get("数字", ""),
            r.get("口径", ""),
            r.get("公司/项目", ""),
            r.get("来源", ""),
        )
    console.print(table)


@interview_app.command("projects")
def interview_projects(
    keyword: str | None = typer.Argument(None, help="过滤关键词（默认全部）"),
) -> None:
    """项目库（02_项目表.csv）。"""
    engine = _engine()
    if engine is None:
        return
    rows = engine.projects(keyword)
    if not rows:
        console.print("[yellow]未找到匹配项目[/yellow]")
        return
    table = Table(title=f"项目库（{len(rows)} 个）")
    for col in ("项目名", "公司", "核心数字"):
        table.add_column(col, overflow="fold")
    for r in rows:
        table.add_row(r.get("项目名", ""), r.get("公司", ""), r.get("核心数字", ""))
    console.print(table)


@interview_app.command("card")
def interview_card(
    company: str = typer.Argument(..., help="公司名（须与岗位表一致）"),
) -> None:
    """一键生成某公司面试速记卡（数字+项目+题库入口）。"""
    engine = _engine()
    if engine is None:
        return
    try:
        card = engine.speed_card(company)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    job = card["job"]
    console.print(
        Panel.fit(
            f"速记卡 · [bold]{job.get('公司', '')}[/bold] · {job.get('岗位', '')}\n"
            f"面试 {job.get('面试时间', '')} · {job.get('状态', '')}\n"
            f"主打方向: {job.get('主打方向', '')}  备战目录: {job.get('备战目录', '')}"
            + (f"\n备注: {job.get('备注', '')}" if job.get("备注") else ""),
            border_style="cyan",
        )
    )
    console.print("[bold]【核心数字】[/bold]")
    for r in card["numbers"]:
        console.print(f"  {r.get('数字', '')} {r.get('口径', '')} ({r.get('公司/项目', '')})")
    console.print("[bold]【可讲项目】[/bold]")
    for r in card["projects"]:
        console.print(f"  · {r.get('项目名', '')} [{r.get('公司', '')}]: {r.get('核心数字', '')}")
    console.print("[bold]【题库入口】[/bold]")
    for r in card["questions"]:
        console.print(f"  · {r.get('题目', '')} [{r.get('方向', '')}] -> {r.get('答案位置', '')}")
    prep = card.get("prep") or {}
    if prep.get("dir"):
        console.print(f"[bold]【岗位定制弹药】[/bold] 备战目录: {prep['dir']}")
        for p in prep.get("quick_pack", []):
            console.print(f"  速成包: {p['name']} ({p['lines']} 行)")
        for d in prep.get("prep_docs", []):
            console.print(f"  备战资料: {d['name']} ({d['lines']} 行)")
        qc = prep.get("quick_card", "")
        if qc:
            console.print(Panel.fit(qc[:1500], title="面试前速记卡", border_style="magenta"))


@interview_app.command("direction")
def interview_direction(
    name: str = typer.Argument(..., help="方向名（可模糊匹配，如 推荐/数据科学/广告）"),
) -> None:
    """列出某方向的全部方法论文档（02_方向知识库）。"""
    engine = _engine()
    if engine is None:
        return
    matched: dict[str, list[str]] = {}
    for d in engine.directions():
        if name in d["direction"] or d["direction"] in name:
            matched[d["direction"]] = d["docs"]
    if not matched:
        console.print(f"[yellow]02_方向知识库下未找到方向: {name}[/yellow]")
        return
    for direction, docs in matched.items():
        console.print(f"[bold]方向 [{direction}][/bold] 的文档:")
        for doc in docs:
            console.print(f"  · {doc}")


@interview_app.command("index")
def interview_index(
    keyword: str | None = typer.Argument(None, help="关键词过滤"),
    layer: str | None = typer.Option(None, "--layer", "-L", help="按层过滤: 01/02/03"),
) -> None:
    """全库文件索引查询（knowledge.db / 06_全库文件索引.csv）。"""
    engine = _engine()
    if engine is None:
        return
    rows = engine.index(keyword, layer)
    if not rows:
        console.print(f"[yellow]全库索引中未找到: {keyword or '(空)'}[/yellow]")
        return
    console.print(f"[bold]全库索引命中 {len(rows)} 个文件:[/bold]")
    for r in rows:
        layer_name = _LAYER_HINT.get((r.get("层", "") or "")[:2], r.get("层", ""))
        console.print(
            f"  [{r.get('层', '')[:2]}] {r.get('类型', ''):<4} "
            f"{layer_name:<8} {r.get('子层', '')}  {r.get('路径', '')}"
        )


@interview_app.command("logs")
def interview_logs() -> None:
    """面试日志列表（04_面试日志.csv，最新在前）。"""
    engine = _engine()
    if engine is None:
        return
    rows = engine.logs()
    if not rows:
        console.print("[yellow]暂无面试日志[/yellow]")
        return
    table = Table(title=f"面试日志（{len(rows)} 条）")
    for col in ("日期", "公司", "轮次", "被问要点", "复盘"):
        table.add_column(col, overflow="fold")
    for r in rows:
        table.add_row(
            r.get("日期", ""),
            r.get("公司", ""),
            r.get("轮次", ""),
            r.get("被问要点", ""),
            r.get("复盘", ""),
        )
    console.print(table)


@interview_app.command("log")
def interview_log(
    company: str = typer.Argument(..., help="公司名"),
    rnd: str = typer.Argument(..., help="轮次（如 一面/二面/HR面）"),
    points: str = typer.Argument(..., help="被问要点（可多词）"),
) -> None:
    """追加一条面试日志（只追加，不修改历史）。"""
    engine = _engine()
    if engine is None:
        return
    new_id = engine.add_log(company, rnd, points)
    console.print(f"[green]已追加面试日志 #{new_id}: {company} / {rnd} / {points}[/green]")


@interview_app.command("scaffold")
def interview_scaffold(
    company: str = typer.Argument(..., help="公司名"),
    role: str = typer.Argument(..., help="岗位名"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认直接建档"),
) -> None:
    """按统一规范新建岗位备战包目录（01/02/03 三件套空目录）。"""
    engine = _engine()
    if engine is None:
        return
    target = engine.root / "03_岗位弹药库" / f"{company}-{role}-面试准备"
    if target.exists():
        console.print(f"[yellow]目录已存在，不重复创建: {target}[/yellow]")
        return
    if not yes:
        confirm = typer.confirm(f"将在 03_岗位弹药库 下创建 {target.name} 的三件套目录？")
        if not confirm:
            console.print("[yellow]已取消[/yellow]")
            raise typer.Exit(code=0)
    result = engine.scaffold(company, role)
    console.print(
        f"[green]新岗位建档完成: {result['path']}[/green]\n"
        f"新建: {', '.join(result['created']) or '(无)'}\n"
        f"已存在: {', '.join(result['existing']) or '(无)'}"
    )
    console.print("下一步：归档 JD/公司材料 → 产出 JD 拆解/面试攻略/预测题库。")


@interview_app.command("status")
def interview_status() -> None:
    """系统总览（岗位/项目/数字/方向/日志统计）。"""
    engine = _engine()
    if engine is None:
        return
    overview = engine.overview()
    console.print(
        Panel.fit(
            f"求职知识库 · [bold]{overview['root']}[/bold]\n"
            f"岗位 {len(overview['jobs'])} 个 · 项目 {len(overview['projects'])} 个 · "
            f"真实数字 {overview['number_count']} 条 · 面试题 {overview['question_count']} 条 · "
            f"日志 {overview['log_count']} 条\n"
            f"方向: {', '.join(overview['directions']) or '(无)'}\n"
            f"规范: {', '.join(overview.get('specs') or []) or '(无)'}",
            title="interview status",
            border_style="green",
        )
    )
    _render_jobs_table(overview["jobs"])


@interview_app.command("root")
def interview_root() -> None:
    """显示当前求职知识库根目录（含解析来源）。"""
    from openbiliclaw.config import load_config

    cfg = load_config()
    cfg_root = str(getattr(getattr(cfg, "interview", None), "root", "") or "")
    root = resolve_root(cfg_root)
    console.print(f"[bold]interview root:[/bold] {root}")
    if cfg_root:
        console.print(f"来源: config.toml [interview] root = {cfg_root}")
    elif os.environ.get("OPENBILICLAW_INTERVIEW_ROOT"):
        console.print("来源: 环境变量 OPENBILICLAW_INTERVIEW_ROOT")
    else:
        console.print("来源: 默认路径（DEFAULT_INTERVIEW_ROOT）")


def register(app: typer.Typer) -> None:
    """注册 interview 命令组到主 CLI。"""
    app.add_typer(interview_app, name="interview")


__all__ = ["interview_app", "register", "_engine"]

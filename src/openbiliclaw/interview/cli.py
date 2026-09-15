"""``openbiliclaw interview`` 命令组——求职面试备战入口。

对应外部 `kb.py` 的检索能力，全部命令走 ``InterviewEngine``。
子命令与 kb.py 中文命令的对应关系见 docs/modules/interview.md。
"""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from openbiliclaw.interview._paths import PROJECT_ROOT
from openbiliclaw.interview.job.engine import InterviewEngine, resolve_root
from openbiliclaw.interview.review.service import InterviewReviewService

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
    rebuild: bool = typer.Option(False, "--rebuild", help="重建全库文件索引（覆盖）"),
) -> None:
    """全库文件索引查询 / 重建（--rebuild）。"""
    engine = _engine()
    if engine is None:
        return
    if rebuild:
        result = engine.rebuild_index()
        console.print(
            Panel.fit(
                f"全库索引重建完成: 共 {result['total']} 个文件\n"
                + "\n".join(f"  {layer}: {n} 个" for layer, n in result["per_layer"].items()),
                title="interview index --rebuild",
                border_style="green",
            )
        )
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


@interview_app.command("doctor")
def interview_doctor(
    fix: bool = typer.Option(False, "--fix", help="索引过期时自动重建索引"),
    full: bool = typer.Option(False, "--full", help="含全库文件数核对（较慢）"),
) -> None:
    """知识库健康检查（C1 题索引 / C2 岗位目录 / C3 日志对齐 / C4 数字表 / C5 索引新鲜度）。"""
    engine = _engine()
    if engine is None:
        return
    result = engine.doctor(fix=fix, full=full)
    for c in result["checks"]:
        status = "[green]PASS[/green]" if c["passed"] else "[red]FAIL[/red]"
        console.print(f"  {status} {c['id']} {c['name']}")
        for issue in c["issues"]:
            console.print(f"      - {issue}")
    if result["passed"]:
        console.print("[bold green]全部检查通过 ✅[/bold green]")
    else:
        n_issues = sum(len(c["issues"]) for c in result["checks"])
        console.print(f"[bold red]发现 {n_issues} 个问题[/bold red]")
        if result["fixed"]:
            console.print("[green]已自动重建索引，请重跑 doctor 复核。[/green]")
        elif full:
            console.print("[yellow]提示: 索引类问题可加 --fix 自动修复。[/yellow]")


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


# ── 面试复盘记录 ──────────────────────────────────────────


def _review_service() -> InterviewReviewService | None:
    """创建复盘服务（使用 data/interview.db，面试复盘子库）。"""
    project_root = PROJECT_ROOT
    db_path = project_root / "data" / "interview.db"
    try:
        from openbiliclaw.config import load_config

        settings = load_config()
        if settings.storage.interview_db_path:
            p = Path(settings.storage.interview_db_path)
            db_path = p if p.is_absolute() else project_root / p
    except Exception:  # noqa: BLE001 - 配置加载失败时回退默认路径
        pass
    return InterviewReviewService(str(db_path))


@interview_app.command("review-list")
def review_list(
    company: str = typer.Option(None, "--company", "-c", help="按公司过滤"),
    result: str = typer.Option(None, "--result", "-r", help="按结果过滤"),
    limit: int = typer.Option(20, "--limit", "-l", help="最多返回条数"),
) -> None:
    """列出面试复盘记录。"""
    svc = _review_service()
    if svc is None:
        return
    reviews = svc.list_reviews(company=company, result=result, limit=limit)
    if not reviews:
        console.print("[yellow]暂无面试复盘记录[/yellow]")
        return
    table = Table(title=f"面试复盘记录（{len(reviews)} 条）")
    for col in ("ID", "公司", "岗位", "日期", "轮次", "结果", "时长", "情绪", "标签"):
        table.add_column(col, overflow="fold")
    for r in reviews:
        table.add_row(
            str(r.id),
            r.company,
            r.position,
            str(r.interview_date),
            r.round,
            r.result,
            f"{r.duration_min}min",
            r.emotion_level or "-",
            r.tags,
        )
    console.print(table)


@interview_app.command("review-show")
def review_show(
    review_id: int = typer.Argument(..., help="复盘记录 ID"),
) -> None:
    """查看面试复盘详情。"""
    svc = _review_service()
    if svc is None:
        return
    r = svc.get(review_id)
    if r is None:
        console.print(f"[red]复盘记录 #{review_id} 不存在[/red]")
        return
    console.print(
        Panel.fit(
            f"#{r.id} · [bold]{r.company}[/bold] · {r.position}\n"
            f"日期: {r.interview_date} · 轮次: {r.round} · 结果: {r.result}\n"
            f"时长: {r.duration_min}min · 情绪: {r.emotion_level or '-'}\n"
            f"标签: {r.tags or '-'}",
            border_style="cyan",
        )
    )
    if r.key_questions:
        console.print(f"[bold]【被问要点】[/bold]\n{r.key_questions}")
    if r.self_assessment:
        console.print(f"[bold]【自我评估】[/bold]\n{r.self_assessment}")
    if r.emotional_review:
        console.print(f"[bold]【情绪复盘】[/bold]\n{r.emotional_review}")
    if r.technical_review:
        console.print(f"[bold]【技术复盘】[/bold]\n{r.technical_review}")
    if r.action_items:
        console.print(f"[bold]【行动计划】[/bold]\n{r.action_items}")
    if r.ai_evaluation:
        console.print(f"[bold]【AI评价】[/bold]\n{r.ai_evaluation[:500]}…")
    if r.transcript_text:
        console.print(f"[bold]【转录文本】[/bold] ({len(r.transcript_text)} 字)")
    if r.transcript_path:
        console.print(f"转录文件: {r.transcript_path}")
    if r.audio_path:
        console.print(f"音频文件: {r.audio_path}")
    if r.notes:
        console.print(f"[bold]【备注】[/bold]\n{r.notes}")


@interview_app.command("review-search")
def review_search(
    query: str = typer.Argument(..., help="检索关键词"),
    limit: int = typer.Option(20, "--limit", "-l", help="最多返回条数"),
) -> None:
    """全文检索面试复盘记录。"""
    svc = _review_service()
    if svc is None:
        return
    results = svc.search(query, limit=limit)
    if not results:
        console.print(f"[yellow]未找到匹配 '{query}' 的复盘记录[/yellow]")
        return
    console.print(f"[bold]检索 '{query}' 命中 {len(results)} 条:[/bold]")
    for r in results:
        console.print(
            f"  #{r.id} [cyan]{r.company}[/cyan] · {r.position} · "
            f"{r.interview_date} · {r.result}"
        )


@interview_app.command("review-stats")
def review_stats() -> None:
    """面试复盘统计。"""
    svc = _review_service()
    if svc is None:
        return
    s = svc.stats()
    console.print(
        Panel.fit(
            f"总复盘记录: {s.total} 条\n"
            f"最近30天: {s.recent_count} 条\n"
            f"平均时长: {s.avg_duration} min\n"
            f"按结果: {s.by_result or '(无)'}\n"
            f"按公司: {s.by_company or '(无)'}\n"
            f"按情绪: {s.by_emotion or '(无)'}",
            title="面试复盘统计",
            border_style="green",
        )
    )


@interview_app.command("review-add")
def review_add(
    company: str = typer.Argument(..., help="公司名"),
    position: str = typer.Argument(..., help="岗位名"),
    date_str: str = typer.Argument(..., help="面试日期 YYYY-MM-DD"),
    round_val: str = typer.Option("first", "--round", "-r", help="轮次"),
    result: str = typer.Option("pending", "--result", help="结果"),
    duration: int = typer.Option(0, "--duration", "-d", help="时长（分钟）"),
    questions: str = typer.Option("", "--questions", "-q", help="被问要点"),
    emotional: str = typer.Option("", "--emotional", help="情绪复盘"),
    technical: str = typer.Option("", "--technical", help="技术复盘"),
    action: str = typer.Option("", "--action", help="行动计划"),
    tags: str = typer.Option("", "--tags", help="标签（逗号分隔）"),
) -> None:
    """添加一条面试复盘记录。"""
    from datetime import date as date_cls

    from openbiliclaw.interview.review.models import (
        InterviewResult,
        InterviewReviewCreate,
        InterviewRound,
    )

    svc = _review_service()
    if svc is None:
        return
    data = InterviewReviewCreate(
        company=company,
        position=position,
        interview_date=date_cls.fromisoformat(date_str),
        round=InterviewRound(round_val),
        result=InterviewResult(result),
        duration_min=duration,
        key_questions=questions,
        emotional_review=emotional,
        technical_review=technical,
        action_items=action,
        tags=tags,
    )
    r = svc.create(data)
    console.print(f"[green]已创建面试复盘 #{r.id}: {r.company} / {r.position}[/green]")


def register(app: typer.Typer) -> None:
    """注册 interview 命令组到主 CLI。"""
    app.add_typer(interview_app, name="interview")


__all__ = ["interview_app", "register", "_engine"]

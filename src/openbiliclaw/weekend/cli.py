"""``openbiliclaw weekend`` 命令组——周末怎么玩入口。

全部命令走 ``WeekendEngine`` / ``WeekendStore``，与 API 共用同一存储。
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .engine import WeekendEngine
from .models import PlanMode, PlanStatus
from .store import WeekendStore

weekend_app = typer.Typer(help="周末怎么玩：生成计划 / 查活动 / 确认 / 打卡复盘")
console = Console()

_SEED_DEFAULT = "data/weekend_seed_activities.json"


def _store() -> WeekendStore:
    # 2026-09-15：此前 CLI 完全忽略 config 的 weekend.db_path / seed_path，
    # 与 API 可能读写不同的库。现在 db 走配置（未配置时回退到锚定默认值）。
    from openbiliclaw.config import load_config

    cfg = getattr(load_config(), "weekend", None)
    return WeekendStore(getattr(cfg, "db_path", None) or None)


@weekend_app.command("generate")
def generate_cmd(
    mode: str = typer.Option(PlanMode.AUTO.value, "--mode", "-m", help="auto/outdoor/indoor"),
    week_of: str | None = typer.Option(None, "--week", "-w", help="周六日期 YYYY-MM-DD"),
) -> None:
    """生成本周末计划（自动按情绪决定出门/宅家）。"""
    engine = WeekendEngine(_store())
    plan = engine.generate(mode=mode, week_of=week_of)
    console.print(
        Panel(
            f"[bold]本周末（{plan.week_of}）计划[/bold]\n"
            f"模式：{plan.mode}　状态：{plan.status}\n"
            f"[dim]{plan.mood_basis}[/dim]",
            title="🎉 周末怎么玩",
        )
    )
    for i, o in enumerate(plan.options):
        console.print(f"\n[bold cyan]方案{i + 1} · {o.title}[/bold cyan]（{o.mode}/{o.energy}）")
        console.print(f"[green]为什么适合你：[/green]{o.why}")
        for a in o.actions:
            console.print(f"  • {a}")


@weekend_app.command("plans")
def plans_cmd(limit: int = typer.Option(20, "--limit", "-n")) -> None:
    """列出历史周末计划。"""
    rows = _store().list_plans(limit=limit)
    if not rows:
        console.print("[yellow]还没有任何计划[/yellow]")
        return
    t = Table(title="周末计划", show_lines=False)
    for col in ("周", "模式", "状态", "方案数", "来源"):
        t.add_column(col)
    for p in rows:
        t.add_row(p.week_of, p.mode, p.status, str(len(p.options)), p.source)
    console.print(t)


@weekend_app.command("spots")
def spots_cmd(
    district: str | None = typer.Option(None, "--district", "-d"),
    suitable_for: str | None = typer.Option(None, "--for", "-f"),
) -> None:
    """列出本地活动种子（默认排除已过期）。"""
    spots = _store().list_spots(district=district, suitable_for=suitable_for)
    if not spots:
        console.print("[yellow]没有匹配的活动[/yellow]")
        return
    t = Table(title="本地活动", show_lines=False)
    for col in ("活动", "区域", "时间", "免费", "适合"):
        t.add_column(col)
    for s in spots:
        t.add_row(s.title, s.district, s.time_text, "免费" if s.free else "收费", "/".join(s.suitable_for))
    console.print(t)


@weekend_app.command("decide")
def decide_cmd(
    week: str = typer.Option(..., "--week", "-w", help="周六日期 YYYY-MM-DD"),
    status: str = typer.Option(PlanStatus.CONFIRMED.value, "--status", "-s", help="confirmed/skipped"),
) -> None:
    """确认或跳过某周计划。"""
    plan = _store().decide_plan(week, status)
    if plan is None:
        console.print(f"[red]未找到 {week} 的计划[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]已{('确认' if status == 'confirmed' else '跳过')} {week} 的计划[/green]")


@weekend_app.command("checkin")
def checkin_cmd(
    plan_id: str = typer.Option(..., "--plan", "-p"),
    option: int = typer.Option(..., "--option", "-o", help="方案序号，从 0 开始"),
    rating: int = typer.Option(..., "--rating", "-r", help="1~5"),
    note: str = typer.Option("", "--note", "-t"),
) -> None:
    """事后打卡复盘。"""
    from .models import CheckIn

    store = _store()
    if store.get_plan_by_id(plan_id) is None:
        console.print("[red]计划不存在[/red]")
        raise typer.Exit(code=1)
    store.add_checkin(CheckIn(plan_id=plan_id, option_index=option, rating=rating, note=note))
    console.print(f"[green]已打卡：方案{option + 1} 评分 {rating}/5[/green]")


@weekend_app.command("seed")
def seed_cmd(
    path: str = typer.Option(_SEED_DEFAULT, "--path", "-p", help="种子 JSON 路径"),
    clear: bool = typer.Option(False, "--clear", help="先清空再导入"),
) -> None:
    """把本地活动种子 JSON 导入 weekend.db。"""
    from openbiliclaw.config import load_config

    from .store import resolve_weekend_path

    if path == _SEED_DEFAULT:
        # 未显式传 --path 时优先用配置里的 seed_path（同样锚定项目根）
        cfg = getattr(load_config(), "weekend", None)
        path = getattr(cfg, "seed_path", None) or path
    p = resolve_weekend_path(path)
    if not p.exists():
        console.print(f"[red]种子文件不存在：{path}[/red]")
        raise typer.Exit(code=1)
    data = json.loads(p.read_text(encoding="utf-8"))
    from .models import WeekendSpot

    spots = [WeekendSpot(**a) for a in data["activities"]]
    n = _store().import_spots(spots, clear=clear)
    console.print(f"[green]已导入 {n} 个活动（来源：{data.get('source', '?')}）[/green]")


def register(app: typer.Typer) -> None:
    """注册 weekend 命令组到主 CLI。"""
    app.add_typer(weekend_app, name="weekend")


__all__ = ["weekend_app", "register"]

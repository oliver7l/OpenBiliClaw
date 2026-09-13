"""CLI 渲染共享 helper（标题面板 / 状态面板 / 键值表）。

从上帝文件 ``cli/__init__.py`` 抽出（P4 第四刀，2026-09-13）。
各命令组子模块（``_cmd_notes`` / ``_cmd_usage`` / ``_cmd_autostart``）
与主 ``__init__`` 均从本模块取用，避免渲染逻辑复制。
本模块顶层**不得** import ``openbiliclaw.cli``（避免循环 import）。
"""

from __future__ import annotations

from typing import Any

from rich.panel import Panel
from rich.table import Table

from openbiliclaw.runtime.init_flow import console


def _print_page_title(title: str, subtitle: str = "") -> None:
    """Render a consistent page title."""
    body = title if not subtitle else f"{title}\n[dim]{subtitle}[/dim]"
    console.print(Panel.fit(body, border_style="cyan"))


def _print_status_panel(kind: str, title: str, body: str) -> None:
    """Render a status panel with consistent visual semantics."""
    styles = {
        "success": "green",
        "warning": "yellow",
        "error": "red",
        "info": "cyan",
        "stub": "blue",
    }
    console.print(Panel(body, title=title, border_style=styles.get(kind, "cyan")))


def _print_key_value_table(title: str, rows: list[tuple[str, str]]) -> None:
    """Render a key-value table for status-like commands."""
    table = Table(title=title, show_header=False, box=None, pad_edge=False)
    table.add_column("key", style="bold cyan", no_wrap=True)
    table.add_column("value")
    for key, value in rows:
        table.add_row(key, value)
    console.print(table)


def _print_discovered_content_preview(item: Any, index: int) -> None:
    """Render one discovered content preview row."""
    _print_key_value_table(
        f"发现 {index}",
        [
            ("标题", item.title or "（暂无）"),
            ("UP 主", item.up_name or "（未知）"),
            ("来源策略", item.source_strategy or "（未知）"),
            ("相关性分数", f"{float(item.relevance_score or 0.0):.2f}"),
        ],
    )

"""CLI 笔记命令组（``openbiliclaw note *``）。

从上帝文件 ``cli/__init__.py`` 抽出（P4 第二刀，2026-09-13）。
本模块顶层**不得** import ``openbiliclaw.cli``（避免循环 import）；
所需运行时上下文（数据库路径 / 配置）自行经 ``openbiliclaw.config``
解析，与 ``cli._runtime_database_path()`` 同源。

同刀修复：旧实现读取 ``_APP_CONTEXT["data_dir"] / ["config"]``，但该
dict 从未有这两键的写入点（只有 ``log_level`` 与三个可选命令组的
import 错误信息）→ 全部 note 命令此前**静默 no-op**（``note_video``
恒报「无法获取笔记服务」）。现改经 ``load_config().data_path`` 取库，
与 db-repair / start / serve-api 等命令一致。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.panel import Panel

from openbiliclaw.runtime.init_flow import console

note_app = typer.Typer(help="笔记管理命令")


def _note_database_path() -> Path:
    from openbiliclaw.config import load_config

    return load_config().data_path / "openbiliclaw.db"


@note_app.command("list")
def note_list(
    limit: int = typer.Option(50, "--limit", "-l", help="返回数量"),
    offset: int = typer.Option(0, "--offset", "-o", help="偏移量"),
    note_type: str | None = typer.Option(None, "--type", "-t", help="笔记类型筛选"),
    platform: str | None = typer.Option(None, "--platform", "-p", help="来源平台筛选"),
    tag: str | None = typer.Option(None, "--tag", help="标签筛选"),
    search: str | None = typer.Option(None, "--search", "-s", help="全文搜索关键词"),
) -> None:
    """列出笔记。"""
    from openbiliclaw.notes import NoteListParams

    svc = _get_note_service()
    if svc is None:
        return
    params = NoteListParams(
        limit=limit,
        offset=offset,
        note_type=note_type,
        source_platform=platform,
        tag=tag,
        search=search,
    )
    items = svc.list_notes(params)
    total = svc.count_notes(params)
    console.print(f"[bold]笔记总数: {total}[/bold]")
    for n in items:
        tags_str = ", ".join(n.tags) if n.tags else ""
        console.print(f"  [{n.id}] {n.title}  ({n.note_type}, {n.source_platform})  {tags_str}")


@note_app.command("get")
def note_get(note_id: int = typer.Argument(..., help="笔记 ID")) -> None:
    """查看笔记详情。"""
    svc = _get_note_service()
    if svc is None:
        return
    note = svc.get_note(note_id)
    if note is None:
        console.print(f"[red]笔记 {note_id} 不存在[/red]")
        raise typer.Exit(code=1)
    console.print(f"[bold]#{note.id}[/bold] {note.title}")
    console.print(f"类型: {note.note_type}  平台: {note.source_platform}")
    if note.source_url:
        console.print(f"来源: {note.source_url}")
    if note.author:
        console.print(f"作者: {note.author}")
    if note.tags:
        console.print(f"标签: {', '.join(note.tags)}")
    console.print(Panel(note.content_md or "(无内容)", border_style="cyan"))


@note_app.command("create")
def note_create(
    title: str = typer.Argument(..., help="笔记标题"),
    content: str = typer.Argument("", help="笔记正文 Markdown"),
    note_type: str = typer.Option("manual", "--type", "-t", help="笔记类型"),
    platform: str = typer.Option("", "--platform", "-p", help="来源平台"),
    url: str = typer.Option("", "--url", "-u", help="来源 URL"),
    ref: str = typer.Option("", "--ref", help="来源 ID"),
    author: str = typer.Option("", "--author", "-a", help="来源作者"),
    tags: str = typer.Option("", "--tags", help="标签，逗号分隔"),
) -> None:
    """创建笔记。"""
    from openbiliclaw.notes import NoteCreate

    svc = _get_note_service()
    if svc is None:
        return
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    data = NoteCreate(
        title=title,
        content_md=content,
        note_type=note_type,
        source_platform=platform,
        source_url=url,
        source_ref=ref,
        author=author,
        tags=tag_list,
    )
    note = svc.create_note(data)
    console.print(f"[green]笔记已创建: #{note.id}[/green]")


@note_app.command("delete")
def note_delete(note_id: int = typer.Argument(..., help="笔记 ID")) -> None:
    """删除笔记。"""
    svc = _get_note_service()
    if svc is None:
        return
    if svc.delete_note(note_id):
        console.print(f"[green]笔记 {note_id} 已删除[/green]")
    else:
        console.print(f"[red]笔记 {note_id} 不存在[/red]")
        raise typer.Exit(code=1)


@note_app.command("search")
def note_search(
    query: str = typer.Argument(..., help="搜索关键词"),
    limit: int = typer.Option(50, "--limit", "-l", help="返回数量"),
) -> None:
    """全文搜索笔记。"""
    from openbiliclaw.notes import NoteListParams

    svc = _get_note_service()
    if svc is None:
        return
    params = NoteListParams(limit=limit, search=query)
    items = svc.list_notes(params)
    console.print(f"[bold]搜索结果: {len(items)} 条[/bold]")
    for n in items:
        console.print(f"  [{n.id}] {n.title}")


@note_app.command("stats")
def note_stats() -> None:
    """查看笔记统计信息。"""
    svc = _get_note_service()
    if svc is None:
        return
    stats = svc.get_stats()
    console.print("[bold]笔记统计[/bold]")
    console.print(f"总数: {stats.total}")
    console.print(f"按类型: {stats.by_type}")
    console.print(f"按平台: {stats.by_platform}")
    if stats.top_tags:
        console.print("热门标签:")
        for t in stats.top_tags[:10]:
            console.print(f"  {t['name']}: {t['count']}")


@note_app.command("import-read-archive")
def note_import_read_archive(
    notes_dir: str = typer.Argument(..., help="已读库目录路径"),
) -> None:
    """从已读库目录导入笔记。"""
    svc = _get_note_service()
    if svc is None:
        return
    result = svc.import_from_read_archive(notes_dir)
    console.print(
        f"[green]导入完成: {result['imported']} 导入, {result['skipped']} 跳过, {result['errors']} 错误[/green]"
    )


@note_app.command("video")
def note_video(
    bvid: str = typer.Argument(..., help="视频 BV 号"),
    content_type: str = typer.Option("article", "--type", "-t", help="笔记类型：article/study/news/general"),
    no_subtitle: bool = typer.Option(False, "--no-subtitle", help="跳过字幕，直接走音频转录"),
    no_rectify: bool = typer.Option(False, "--no-rectify", help="跳过 ASR 校对"),
    no_save: bool = typer.Option(False, "--no-save", help="不保存到笔记库，仅输出结果"),
    whisper_model: str = typer.Option("base", "--whisper-model", help="faster-whisper 模型大小"),
) -> None:
    """将 B 站视频转为结构化笔记。

    字幕优先策略：先尝试获取 CC 字幕，失败则下载音频用 faster-whisper 本地转录。
    """
    from openbiliclaw.config import load_config
    from openbiliclaw.notes import NoteService

    svc = _get_note_service()
    if svc is None:
        console.print("[red]无法获取笔记服务[/red]")
        return

    # 获取 cookie 配置
    cookie = ""
    config = load_config()
    if hasattr(config, "bilibili"):
        cookie = getattr(config.bilibili, "cookie", "") or ""

    # 如果配置了 bilibili cookie，创建 client
    bilibili_client = None
    if cookie:
        from openbiliclaw.bilibili.api import BilibiliAPIClient

        bilibili_client = BilibiliAPIClient(cookie=cookie)

    # 注入 llm_service（如果可用）
    llm_service = None
    try:
        from obc_llm.service import LLMService, module_overrides_from_config

        overrides = module_overrides_from_config(config)
        from openbiliclaw.llm._compat_registry import build_llm_registry as _build_registry

        llm_service = LLMService(
            registry=_build_registry(config),
            memory=None,
            module_overrides=overrides,
        )
    except Exception:
        pass

    # 重新创建带依赖的 NoteService
    from openbiliclaw.storage.database import Database

    db = Database(_note_database_path())
    db.initialize()
    svc = NoteService(
        database=db,
        llm_service=llm_service,
        bilibili_client=bilibili_client,
    )

    console.print(f"[bold]开始转换视频: {bvid}[/bold]")
    console.print(f"  内容类型: {content_type}")
    console.print(f"  字幕优先: {'否' if no_subtitle else '是'}")
    console.print(f"  ASR 校对: {'否' if no_rectify else '是'}")

    result = asyncio.run(
        svc.video_to_note(
            bvid,
            save_note=not no_save,
            prefer_subtitle=not no_subtitle,
            enable_asr_rectify=not no_rectify,
            content_type=content_type,
            whisper_model=whisper_model,
            cookie=cookie,
        )
    )

    if result.success:
        console.print("[green]✓ 转换成功[/green]")
        console.print(f"  文本来源: {result.source}")
        if result.note:
            console.print(f"  笔记 ID: {result.note.id}")
            console.print(f"  标题: {result.note.title}")
        for stage, info in result.stages.items():
            status = "✓" if info.get("success", True) else "✗"
            console.print(f"  {status} {stage}: {info}")
    else:
        console.print(f"[red]✗ 转换失败: {result.error}[/red]")
        for stage, info in result.stages.items():
            status = "✓" if info.get("success", True) else "✗"
            console.print(f"  {status} {stage}: {info}")


@note_app.command("tasks")
def note_tasks(
    limit: int = typer.Option(20, "--limit", "-l", help="返回数量"),
    status: str | None = typer.Option(None, "--status", "-s", help="按状态筛选"),
) -> None:
    """列出笔记生成任务。"""
    svc = _get_note_service()
    if svc is None:
        return
    tasks = svc.list_tasks(limit=limit, status=status)
    console.print(f"[bold]任务列表 ({len(tasks)}):[/bold]")
    for t in tasks:
        console.print(f"  {t.task_id} | {t.source_platform}:{t.source_ref} | {t.status}")


def _get_note_service():
    """获取 NoteService 实例。"""
    from openbiliclaw.notes import NoteService
    from openbiliclaw.storage.database import Database

    db = Database(_note_database_path())
    db.initialize()
    return NoteService(database=db)

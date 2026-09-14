"""CLI 内容抓取与发现命令组（fetch-* / search-* / discover-*）。

从上帝文件 ``cli/__init__.py`` 抽出（P4 第五刀，2026-09-13）。
本模块顶层**不得** import ``openbiliclaw.cli``（避免循环依赖）；
13 个平铺命令经 ``register(app)`` 挂回主 app（命令名与形状不变）。

patch 语义保留：被测试 patch 的共享符号（``_enqueue_*`` 系列入口、
``_prepare_init_runtime`` / ``_build_soul_engine`` / ``_build_memory_manager``
/ ``_get_runtime_database`` / ``_require_runtime_config`` / 本模块的
``_run_zhihu_discovery``）一律在**函数体内**经
``from openbiliclaw import cli as _cli`` 动态取属性——顶层 from-import
会绑定旧值并破坏 ``monkeypatch.setattr(cli_module, ...)``。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from contextlib import suppress
from typing import Any, cast

import typer

from openbiliclaw.cli._render import (  # noqa: F401
    _print_discovered_content_preview,
    _print_key_value_table,
    _print_page_title,
    _print_status_panel,
)
from openbiliclaw.runtime.init_flow import (  # noqa: F401
    _DEFAULT_DY_BOOTSTRAP_WAIT_SECONDS,
    _DEFAULT_XHS_BOOTSTRAP_WAIT_SECONDS,
    _DEFAULT_YT_BOOTSTRAP_WAIT_SECONDS,
    _DEFAULT_ZHIHU_BOOTSTRAP_WAIT_SECONDS,
    _print_section_title,
    console,
)

_DISCOVER_STRATEGIES_OPTION = typer.Option(
    None,
    "--strategy",
    "-S",
    help=(
        "Bilibili 策略过滤，可多次传或逗号分隔："
        "search / trending / explore / related_chain。"
        "仅在 --source=bilibili 时生效。"
    ),
)
_ZHIHU_DISCOVER_KEYWORDS_ARGUMENT = typer.Argument(
    ...,
    help="知乎搜索关键词，可传多个；单个参数里也可以用逗号分隔。",
)
_ZHIHU_CREATOR_URLS_ARGUMENT = typer.Argument(
    ...,
    help="知乎作者主页 URL 或 people slug，可传多个。",
)
_ZHIHU_RELATED_URLS_ARGUMENT = typer.Argument(
    ...,
    help="知乎问题 / 回答 / 文章 URL，可传多个。",
)
_DOUYIN_DISCOVERY_KEYWORDS_OPTION = typer.Option(
    None,
    "--keyword",
    "-k",
    help="指定搜索关键词；可多次传或逗号分隔。不传时从 Soul 画像兴趣生成。",
)
_DOUYIN_DISCOVERY_CREATOR_SEC_UIDS_OPTION = typer.Option(
    None,
    "--creator-sec-uid",
    help=("兼容旧参数；当前公开 discovery 来源不再包含 creator。"),
)
_DOUYIN_DISCOVERY_SOURCES_OPTION = typer.Option(
    None,
    "--source",
    "-s",
    help="抖音 discovery 子来源：search、hot、feed，可多次传或逗号分隔。",
)
_DOUYIN_SEARCH_KEYWORDS_OPTION = typer.Option(
    ...,
    "--keyword",
    "-k",
    help="抖音搜索关键词，可重复传或用逗号分隔。",
)


def register(app: typer.Typer) -> None:
    app.command("fetch-douyin")(fetch_douyin)
    app.command("search-douyin")(search_douyin)
    app.command("fetch-xhs")(fetch_xhs)
    app.command("fetch-youtube")(fetch_youtube)
    app.command("fetch-zhihu")(fetch_zhihu)
    app.command("discover-zhihu")(discover_zhihu)
    app.command("discover-zhihu-hot")(discover_zhihu_hot)
    app.command("discover-zhihu-feed")(discover_zhihu_feed)
    app.command("discover-zhihu-creator")(discover_zhihu_creator)
    app.command("discover-zhihu-related")(discover_zhihu_related)
    app.command("fetch-x")(fetch_x)
    app.command("discover-douyin")(discover_douyin)
    app.command()(discover)


def _run_single_source_bootstrap(
    *,
    source_label: str,
    enqueue: Callable[[], str | None],
    collect: Callable[[str | None], tuple[list[dict[str, Any]], dict[str, int], str]],
    wait_seconds: float,
    summary_renderer: Callable[[dict[str, int], str, int], None],
) -> None:
    """Shared core for ``fetch-douyin`` / ``fetch-xhs`` standalone commands.

    从上帝文件 ``cli/__init__.py`` 迁入本模块（P4 第七刀，2026-09-14）；
    仍经 ``cli._run_single_source_bootstrap`` re-export 供既有调用点使用。

    Pure pull pipeline — enqueue → kick → wait for completion →
    render scope_counts. Does NOT touch B站 auth, does NOT propagate
    events to memory. The daemon's
    ``/api/sources/{xhs,dy}/task-result`` handler ALREADY propagates
    incoming events to memory when it receives partials, so a CLI-side
    propagate would double-write. Init still runs the soul pipeline
    (preference / awareness / soul) on top — this command is the
    isolated 'just verify the extension can pull data' rung beneath
    that, useful for testing one platform at a time.
    """
    _print_page_title(f"{source_label} 数据拉取", "扩展任务 → 后端入库")
    console.print(f"[dim]入队 {source_label} bootstrap 任务,等扩展执行(最多 {wait_seconds:.0f}s)...[/dim]")

    task_id = enqueue()
    if not task_id:
        console.print(f"[bold red]无法入队 {source_label} 任务[/bold red] — 看上面的提示(数据库 / 预算 / 任务表问题)。")
        raise typer.Exit(code=1)

    events, scope_counts, status_label = collect(task_id)
    summary_renderer(scope_counts, status_label, len(events))


def fetch_douyin(
    wait_seconds: float = typer.Option(
        _DEFAULT_DY_BOOTSTRAP_WAIT_SECONDS,
        "--wait-seconds",
        "-w",
        help="等扩展回结果的最大秒数(默认 180s,4 个 scope 串行 + 滚动 + 兜底)。",
    ),
) -> None:
    """单独触发抖音 bootstrap 拉取(纯执行,不跑 init 的画像 / 发现层).

    流程:CLI 入队 → /api/sources/dy/kick(WS push 立即唤醒扩展)→ 扩展 dispatcher
    跑完 4 个 scope → POST 回 /api/sources/dy/task-result → daemon propagate
    事件到 memory(daemon 端自己干,CLI 不再 propagate 一次)。

    适合什么时候用:
      - 单独测试抖音的扩展能不能拉数据(不污染 init 的画像 / 发现池逻辑)
      - 已经 init 过画像后,补一次抖音拉取
      - 调扩展或诊断风控时反复跑

    前提:
      1. ``openbiliclaw start`` daemon 在跑(kick 才有人接)
      2. 浏览器扩展已装、service-worker 在线
      3. 浏览器登录了 https://www.douyin.com
    """

    from openbiliclaw import cli as _cli  # noqa: E402

    def _render(scope_counts: dict[str, int], status_label: str, event_count: int) -> None:
        if status_label == "ok":
            console.print(
                "  抖音 "
                f"发布 [green]{scope_counts.get('dy_post', 0)}[/green] 条"
                f" / 收藏 [green]{scope_counts.get('dy_collect', 0)}[/green] 个"
                f" / 点赞 [green]{scope_counts.get('dy_like', 0)}[/green] 个"
                f" / 关注 [green]{scope_counts.get('dy_follow', 0)}[/green] 人"
            )
            console.print(f"  共 [green]{event_count}[/green] 条事件已由 daemon 写入 memory。")
        elif status_label == "empty":
            console.print(
                "  [yellow]抖音任务跑通但 0 条 videos —— 未登录抖音(常见,"
                "抖音对未登录返回 200+空 body),或风控触发。[/yellow]"
            )
        elif status_label == "timeout":
            console.print(
                "  [dim]抖音任务超时:扩展未连接 / 任务还在跑。"
                "可加 --wait-seconds 240 重试,或确认 daemon + 扩展都在跑。[/dim]"
            )
        elif status_label == "failed":
            console.print("  [yellow]抖音任务失败 —— 检查扩展日志。[/yellow]")

    _cli._run_single_source_bootstrap(
        source_label="抖音",
        enqueue=_cli._enqueue_dy_bootstrap_task,
        collect=lambda tid: _cli._collect_dy_bootstrap_events(tid, max_wait_seconds=wait_seconds),
        wait_seconds=wait_seconds,
        summary_renderer=_render,
    )


def search_douyin(
    keywords: list[str] = _DOUYIN_SEARCH_KEYWORDS_OPTION,
    wait_seconds: float = typer.Option(
        180.0,
        "--wait-seconds",
        "-w",
        help="等扩展回搜索结果的最大秒数(默认 180s)。",
    ),
    max_items_per_keyword: int = typer.Option(
        20,
        "--max-items-per-keyword",
        min=1,
        help="每个关键词最多抓取多少条视频候选。",
    ),
) -> None:
    """通过浏览器插件执行抖音搜索 discovery smoke."""

    from obc_discovery.douyin import split_csv_values

    from openbiliclaw import cli as _cli  # noqa: E402

    selected_keywords = split_csv_values(keywords)
    _print_page_title("抖音搜索发现", "浏览器插件任务 → dy_tasks 结果")
    console.print(f"[dim]入队抖音搜索任务,等扩展执行(最多 {wait_seconds:.0f}s)...[/dim]")
    task_id = _cli._enqueue_dy_search_task(
        selected_keywords,
        max_items_per_keyword=max_items_per_keyword,
    )
    if not task_id:
        raise typer.Exit(code=1)

    videos, counts, status_label = _cli._collect_dy_search_results(
        task_id,
        max_wait_seconds=wait_seconds,
    )
    if status_label == "ok":
        console.print(f"  抖音搜索 [green]{counts.get('dy_search', len(videos))}[/green] 条候选")
        for index, video in enumerate(videos[:5], start=1):
            title = str(video.get("title", "") or "（无标题）")
            author = str(video.get("author", "") or "")
            url = str(video.get("url", "") or "")
            suffix = f" [dim]{author}[/dim]" if author else ""
            console.print(f"  {index}. {title}{suffix}")
            if url:
                console.print(f"     [dim]{url}[/dim]")
        return
    if status_label == "empty":
        console.print(
            "  [yellow]抖音搜索任务跑通但 0 条候选 —— 搜索页可能仍被风控软空，或页面 DOM / 接口字段漂移。[/yellow]"
        )
        return
    if status_label == "timeout":
        console.print("  [dim]抖音搜索任务超时:扩展未连接 / 任务还在跑。可加 --wait-seconds 240 重试。[/dim]")
        return
    if status_label == "failed":
        console.print("  [yellow]抖音搜索任务失败 —— 检查扩展日志。[/yellow]")


def fetch_xhs(
    wait_seconds: float = typer.Option(
        _DEFAULT_XHS_BOOTSTRAP_WAIT_SECONDS,
        "--wait-seconds",
        "-w",
        help="等扩展回结果的最大秒数(默认 180s)。",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="忽略近期小红书 bootstrap 任务，强制重新拉取收藏 / 点赞。",
    ),
) -> None:
    """单独测试小红书 bootstrap(独立于 ``init``).

    用于在不重新跑完整 init 的情况下逐项验证小红书端到端链路。
    需要 daemon + 扩展 + 浏览器登录 https://www.xiaohongshu.com。
    """

    from openbiliclaw import cli as _cli  # noqa: E402

    def _render(scope_counts: dict[str, int], status_label: str, event_count: int) -> None:
        if status_label == "ok":
            console.print(
                "  小红书 "
                f"收藏 [green]{scope_counts.get('saved', 0)}[/green] 个"
                f" / 点赞 [green]{scope_counts.get('liked', 0)}[/green] 个"
                f" / 浏览记录 [green]{scope_counts.get('xhs_history', 0)}[/green] 个"
            )
            console.print(f"  共生成 [green]{event_count}[/green] 条事件。")
        elif status_label == "empty":
            console.print(
                "  [yellow]小红书任务跑通但 0 条 notes —— 可能未登录 /个人主页没有公开收藏 / 页面 state 漂移。[/yellow]"
            )
        elif status_label == "timeout":
            console.print("  [dim]小红书任务超时:扩展未连接 / 任务还在跑。可加 --wait-seconds 240 重试。[/dim]")
        elif status_label == "failed":
            console.print("  [yellow]小红书任务失败 —— 检查扩展日志。[/yellow]")

    _cli._run_single_source_bootstrap(
        source_label="小红书",
        enqueue=(lambda: _cli._enqueue_xhs_bootstrap_task(force=True)) if force else _cli._enqueue_xhs_bootstrap_task,
        collect=lambda tid: _cli._collect_xhs_bootstrap_events(tid, max_wait_seconds=wait_seconds),
        wait_seconds=wait_seconds,
        summary_renderer=_render,
    )


def fetch_youtube(
    wait_seconds: float = typer.Option(
        _DEFAULT_YT_BOOTSTRAP_WAIT_SECONDS,
        "--wait-seconds",
        "-w",
        help="等扩展回结果的最大秒数(默认 240s，YouTube 滚动比较慢)。",
    ),
) -> None:
    """单独测试 YouTube bootstrap（独立于 ``init``）。

    用于在不重新跑完整 init 的情况下验证 YouTube 端到端链路。
    需要 daemon + 扩展 + 浏览器登录 https://www.youtube.com。

    \b
    采集范围：
      yt_history      — /feed/history        观看历史 (弱信号)
      yt_subscriptions — /feed/channels       订阅频道 (强信号)
      yt_likes        — /playlist?list=LL    点赞视频 (强信号)
    """

    from openbiliclaw import cli as _cli  # noqa: E402

    def _render(scope_counts: dict[str, int], status_label: str, event_count: int) -> None:
        if status_label == "ok":
            console.print(
                "  YouTube "
                f"观看历史 [green]{scope_counts.get('yt_history', 0)}[/green] 条"
                f" / 订阅 [green]{scope_counts.get('yt_subscriptions', 0)}[/green] 个"
                f" / 点赞 [green]{scope_counts.get('yt_likes', 0)}[/green] 个"
            )
            console.print(f"  共生成 [green]{event_count}[/green] 条事件。")
        elif status_label == "empty":
            console.print(
                "  [yellow]YouTube 任务跑通但 0 条数据 —— 可能未登录 YouTube / 页面还未渲染完 / 选择器失效。[/yellow]"
            )
        elif status_label == "timeout":
            console.print("  [dim]YouTube 任务超时：扩展未连接 / 任务还在跑。可加 --wait-seconds 360 重试。[/dim]")
        elif status_label == "failed":
            console.print("  [yellow]YouTube 任务失败 —— 检查扩展日志。[/yellow]")

    _cli._run_single_source_bootstrap(
        source_label="YouTube",
        enqueue=_cli._enqueue_yt_bootstrap_task,
        collect=lambda tid: _cli._collect_yt_bootstrap_events(tid, max_wait_seconds=wait_seconds),
        wait_seconds=wait_seconds,
        summary_renderer=_render,
    )


def fetch_zhihu(
    profile_slug: str = typer.Option(
        "",
        "--profile-slug",
        help=(
            "知乎个人主页 slug，例如 https://www.zhihu.com/people/<slug>。不提供时扩展会尝试从当前知乎登录态自动识别。"
        ),
    ),
    wait_seconds: float = typer.Option(
        _DEFAULT_ZHIHU_BOOTSTRAP_WAIT_SECONDS,
        "--wait-seconds",
        "-w",
        help="等扩展回结果的最大秒数(默认 180s)。",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="忽略近期知乎 bootstrap 任务，强制重新拉取事件。",
    ),
    write_memory: bool = typer.Option(
        False,
        "--write-memory",
        help="将本次抓到的知乎事件写入 memory；默认只做抓取 smoke。",
    ),
    rebuild_profile: bool = typer.Option(
        False,
        "--rebuild-profile",
        help="写入 memory 后用本次知乎事件重建画像（会触发真实 LLM 调用）。",
    ),
) -> None:
    """单独测试知乎事件拉取(默认独立于 ``init``，不生成画像)。

    需要 daemon + 扩展 + 浏览器登录 https://www.zhihu.com。扩展会在知乎
    页面内用当前登录态拉取最近浏览、收藏夹内容和个人动态中的点赞 / 收藏。
    传 ``--profile-slug`` 可手动指定用户主页；不传时扩展会尝试自动识别。
    默认只读取任务结果并打印统计；传 ``--write-memory`` 才写入 memory，
    传 ``--rebuild-profile`` 会继续触发画像生成。
    """

    from openbiliclaw import cli as _cli  # noqa: E402

    write_memory = write_memory or rebuild_profile

    def _render(scope_counts: dict[str, int], status_label: str, event_count: int) -> None:
        if status_label == "ok":
            activity_favorites = scope_counts.get("zhihu_activity_favorite", 0)
            total_favorites = scope_counts.get("zhihu_collection", 0) + activity_favorites
            console.print(
                "  知乎 "
                f"浏览 [green]{scope_counts.get('zhihu_read_history', 0)}[/green] 条"
                f" / 收藏 [green]{total_favorites}[/green] 条"
                f" / 点赞 [green]{scope_counts.get('zhihu_activity_like', 0)}[/green] 条"
            )
            if rebuild_profile:
                suffix = "将写入 memory 并重建画像。"
            elif write_memory:
                suffix = "将写入 memory。"
            else:
                suffix = "未触发画像生成。"
            console.print(f"  共抓取并转换 [green]{event_count}[/green] 条事件；{suffix}")
        elif status_label == "empty":
            console.print(
                "  [yellow]知乎任务跑通但 0 条数据 —— "
                "可能未登录知乎 / 浏览历史关闭 / 收藏夹为空 / 接口字段漂移。[/yellow]"
            )
        elif status_label == "timeout":
            console.print("  [dim]知乎任务超时:扩展未连接 / 任务还在跑。可加 --wait-seconds 240 重试。[/dim]")
        elif status_label == "failed":
            console.print("  [yellow]知乎任务失败 —— 检查扩展日志。[/yellow]")
        elif status_label == "login_required":
            console.print(
                "  [yellow]知乎任务已到达浏览器，但当前知乎页面未登录。"
                "请先在当前浏览器登录知乎，再用 --force 重试。[/yellow]"
            )

    def _enqueue() -> str | None:
        # A write/rebuild run must not silently reuse a previous smoke task that
        # was already collected without persistence.

        from openbiliclaw import cli as _cli  # noqa: E402

        dedupe_disabled = force or write_memory
        previous = os.environ.get("OPENBILICLAW_ZHIHU_BOOTSTRAP_DEDUPE_HOURS")
        if dedupe_disabled:
            os.environ["OPENBILICLAW_ZHIHU_BOOTSTRAP_DEDUPE_HOURS"] = "0"
        try:
            return _cli._enqueue_zhihu_bootstrap_task(
                profile_slug=profile_slug,
                profile_update=False,
            )
        finally:
            if dedupe_disabled:
                if previous is None:
                    os.environ.pop("OPENBILICLAW_ZHIHU_BOOTSTRAP_DEDUPE_HOURS", None)
                else:
                    os.environ["OPENBILICLAW_ZHIHU_BOOTSTRAP_DEDUPE_HOURS"] = previous

    _print_page_title("知乎 数据拉取", "扩展任务 → 后端入库")
    console.print(f"[dim]入队 知乎 bootstrap 任务,等扩展执行(最多 {wait_seconds:.0f}s)...[/dim]")

    task_id = _enqueue()
    if not task_id:
        console.print("[bold red]无法入队 知乎 任务[/bold red] — 看上面的提示(数据库 / 预算 / 任务表问题)。")
        raise typer.Exit(code=1)

    events, scope_counts, status_label = _cli._collect_zhihu_bootstrap_events(
        task_id,
        max_wait_seconds=wait_seconds,
    )
    _render(scope_counts, status_label, len(events))
    if status_label != "ok":
        return

    if write_memory:
        written, skipped = _cli._write_events_to_memory(events, source="zhihu")
        console.print(
            f"  [green]已写入 memory: {written} 条知乎事件[/green]{f'，跳过重复 {skipped} 条。' if skipped else '。'}"
        )

    if rebuild_profile:
        _cli._prepare_init_runtime()
        soul_engine = _cli._build_soul_engine()
        _print_section_title("1/2 分析知乎偏好")
        asyncio.run(
            _cli._run_with_progress(
                soul_engine.analyze_events(events, event_chunk_size=200),
                label="分析知乎偏好",
                eta_seconds=180,
            )
        )
        _print_section_title("2/2 生成画像")
        asyncio.run(
            _cli._run_with_progress(
                soul_engine.build_initial_profile(_cli._zhihu_events_to_history_items(events)),
                label="生成灵魂画像",
                eta_seconds=70,
            )
        )
        _print_status_panel("success", "完成", "知乎事件已写入并完成画像重建")


def discover_zhihu(
    keywords: list[str] = _ZHIHU_DISCOVER_KEYWORDS_ARGUMENT,
    limit: int = typer.Option(
        20,
        "--limit",
        "-n",
        min=1,
        help="每个关键词最多抓取的搜索结果数。",
    ),
    wait_seconds: float = typer.Option(
        180.0,
        "--wait-seconds",
        "-w",
        help="等扩展回结果的最大秒数。",
    ),
    no_enqueue: bool = typer.Option(
        False,
        "--no-enqueue",
        help="只预览插件搜索结果，不写入 discovery_candidates。",
    ),
) -> None:
    """通过浏览器插件触发一次知乎搜索 discovery。"""

    from obc_discovery.douyin import split_csv_values

    from openbiliclaw import cli as _cli  # noqa: E402

    selected_keywords = split_csv_values(keywords)
    _print_page_title("知乎内容发现", "插件搜索 → discovery_candidates")
    console.print(f"[dim]入队知乎 search 任务,等扩展执行(最多 {wait_seconds:.0f}s)...[/dim]")
    task_id = _cli._enqueue_zhihu_search_task(
        tuple(selected_keywords),
        max_items_per_keyword=limit,
    )
    if not task_id:
        raise typer.Exit(code=1)

    items, scope_counts, status_label = _cli._collect_zhihu_search_results(
        task_id,
        max_wait_seconds=wait_seconds,
    )
    if status_label == "login_required":
        console.print("  [yellow]知乎任务已到达浏览器，但当前知乎页面未登录。请先在当前浏览器登录知乎后重试。[/yellow]")
        raise typer.Exit(code=1)
    if status_label == "timeout":
        console.print("  [yellow]知乎搜索任务超时:扩展未连接 / 任务还在跑。可加 --wait-seconds 240 重试。[/yellow]")
        raise typer.Exit(code=1)
    if status_label == "failed":
        console.print("  [yellow]知乎搜索任务失败 —— 检查扩展日志。[/yellow]")
        raise typer.Exit(code=1)
    if status_label == "empty" or not items:
        _print_status_panel(
            "info",
            "没有发现到知乎内容",
            "可能是搜索接口返回空、知乎未登录，或关键词没有结果。",
        )
        return

    enqueued = 0
    contents: list[Any] = []
    if no_enqueue:
        from openbiliclaw.sources.zhihu_tasks import zhihu_discovery_items_to_contents

        contents = zhihu_discovery_items_to_contents(items)
    else:
        enqueued, contents = _cli._enqueue_zhihu_discovery_candidates(items)

    _print_key_value_table(
        "发现摘要",
        [
            ("搜索结果", str(scope_counts.get("zhihu_search", len(items)))),
            ("转换候选", str(len(contents))),
            ("入池候选", "跳过（--no-enqueue）" if no_enqueue else str(enqueued)),
            ("来源", "zhihu"),
            ("策略", "zhihu-search"),
        ],
    )
    for index, item in enumerate(contents[:5], start=1):
        _print_discovered_content_preview(item, index)


def _run_zhihu_discovery_smoke(
    *,
    title: str,
    task_type: str,
    strategy: str,
    scope_key: str,
    payload: dict[str, object],
    daily_budget_key: str,
    wait_seconds: float,
    no_enqueue: bool,
) -> None:

    from openbiliclaw import cli as _cli  # noqa: E402

    _print_page_title(title, f"插件 {strategy} → discovery_candidates")
    console.print(f"[dim]入队知乎 {task_type} 任务,等扩展执行(最多 {wait_seconds:.0f}s)...[/dim]")
    task_id = _cli._enqueue_zhihu_discovery_task(
        task_type,
        payload,
        daily_budget_key=daily_budget_key,
    )
    if not task_id:
        raise typer.Exit(code=1)

    items, scope_counts, status_label = _cli._collect_zhihu_discovery_results(
        task_id,
        max_wait_seconds=wait_seconds,
    )
    if status_label == "login_required":
        console.print("  [yellow]知乎任务已到达浏览器，但当前知乎页面未登录。请先在当前浏览器登录知乎后重试。[/yellow]")
        raise typer.Exit(code=1)
    if status_label == "timeout":
        console.print(
            "  [yellow]知乎 discovery 任务超时:扩展未连接 / 任务还在跑。可加 --wait-seconds 240 重试。[/yellow]"
        )
        raise typer.Exit(code=1)
    if status_label == "failed":
        console.print("  [yellow]知乎 discovery 任务失败 —— 检查扩展日志。[/yellow]")
        raise typer.Exit(code=1)
    if status_label == "empty" or not items:
        _print_status_panel("info", "没有发现到知乎内容", f"{strategy} 返回为空。")
        return

    enqueued = 0
    contents: list[Any] = []
    if no_enqueue:
        from openbiliclaw.sources.zhihu_tasks import zhihu_discovery_items_to_contents

        contents = zhihu_discovery_items_to_contents(items)
    else:
        enqueued, contents = _cli._enqueue_zhihu_discovery_candidates(items)

    _print_key_value_table(
        "发现摘要",
        [
            ("抓取结果", str(scope_counts.get(scope_key, len(items)))),
            ("转换候选", str(len(contents))),
            ("入池候选", "跳过（--no-enqueue）" if no_enqueue else str(enqueued)),
            ("来源", "zhihu"),
            ("策略", strategy),
        ],
    )
    for index, item in enumerate(contents[:5], start=1):
        _print_discovered_content_preview(item, index)


def discover_zhihu_hot(
    limit: int = typer.Option(20, "--limit", "-n", min=1, help="最多抓取的热榜条数。"),
    wait_seconds: float = typer.Option(180.0, "--wait-seconds", "-w", help="等扩展回结果的最大秒数。"),
    no_enqueue: bool = typer.Option(False, "--no-enqueue", help="只预览插件结果，不写入 discovery_candidates。"),
) -> None:
    """通过浏览器插件触发一次知乎热榜 discovery。"""
    _run_zhihu_discovery_smoke(
        title="知乎热榜发现",
        task_type="hot",
        strategy="zhihu-hot",
        scope_key="zhihu_hot",
        payload={"max_items": max(1, int(limit))},
        daily_budget_key="daily_hot_budget",
        wait_seconds=wait_seconds,
        no_enqueue=no_enqueue,
    )


def discover_zhihu_feed(
    limit: int = typer.Option(20, "--limit", "-n", min=1, help="最多抓取的首页推荐条数。"),
    wait_seconds: float = typer.Option(180.0, "--wait-seconds", "-w", help="等扩展回结果的最大秒数。"),
    no_enqueue: bool = typer.Option(False, "--no-enqueue", help="只预览插件结果，不写入 discovery_candidates。"),
) -> None:
    """通过浏览器插件触发一次知乎首页推荐 discovery。"""
    _run_zhihu_discovery_smoke(
        title="知乎首页发现",
        task_type="feed",
        strategy="zhihu-feed",
        scope_key="zhihu_feed",
        payload={"max_items": max(1, int(limit))},
        daily_budget_key="daily_feed_budget",
        wait_seconds=wait_seconds,
        no_enqueue=no_enqueue,
    )


def discover_zhihu_creator(
    creator_urls: list[str] = _ZHIHU_CREATOR_URLS_ARGUMENT,
    limit: int = typer.Option(20, "--limit", "-n", min=1, help="每个作者最多抓取的内容数。"),
    wait_seconds: float = typer.Option(180.0, "--wait-seconds", "-w", help="等扩展回结果的最大秒数。"),
    no_enqueue: bool = typer.Option(False, "--no-enqueue", help="只预览插件结果，不写入 discovery_candidates。"),
) -> None:
    """通过浏览器插件触发一次知乎作者 discovery。"""
    from obc_discovery.douyin import split_csv_values

    selected = split_csv_values(creator_urls)
    _run_zhihu_discovery_smoke(
        title="知乎作者发现",
        task_type="creator",
        strategy="zhihu-creator",
        scope_key="zhihu_creator",
        payload={"creator_urls": selected, "max_items_per_creator": max(1, int(limit))},
        daily_budget_key="daily_creator_budget",
        wait_seconds=wait_seconds,
        no_enqueue=no_enqueue,
    )


def discover_zhihu_related(
    related_urls: list[str] = _ZHIHU_RELATED_URLS_ARGUMENT,
    limit: int = typer.Option(20, "--limit", "-n", min=1, help="每个种子最多扩展的相关内容数。"),
    wait_seconds: float = typer.Option(180.0, "--wait-seconds", "-w", help="等扩展回结果的最大秒数。"),
    no_enqueue: bool = typer.Option(False, "--no-enqueue", help="只预览插件结果，不写入 discovery_candidates。"),
) -> None:
    """通过浏览器插件触发一次知乎相关内容 discovery。"""
    from obc_discovery.douyin import split_csv_values

    selected = split_csv_values(related_urls)
    _run_zhihu_discovery_smoke(
        title="知乎相关发现",
        task_type="related",
        strategy="zhihu-related",
        scope_key="zhihu_related",
        payload={"related_urls": selected, "max_items_per_seed": max(1, int(limit))},
        daily_budget_key="daily_related_budget",
        wait_seconds=wait_seconds,
        no_enqueue=no_enqueue,
    )


def fetch_x(
    limit: int = typer.Option(
        50,
        "--limit",
        "-n",
        help="每类(点赞 / 收藏)最多拉取条数(默认 50,init 回填用 200)。",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="只拉取并打印,不写入 memory / 不更新画像。",
    ),
) -> None:
    """单独触发 X(Twitter)点赞 / 收藏拉取(独立于 ``init``)。

    与 fetch-xhs / fetch-douyin / fetch-youtube 对应,但 X 是服务端 cookie
    重放(无扩展 bootstrap 任务):本命令直接用已同步的 x.com cookie 拉取你
    自己的点赞 + 收藏,转成统一事件写入 memory —— 用于在不重跑完整 ``init``
    的情况下验证 X 历史偏好回填链路。不需要 daemon。

    \b
    采集范围:
      like      — 你的点赞 timeline   (强信号 → event_type="like")
      favorite  — 你的收藏 / 书签      (强信号 → event_type="favorite")

    前提:
      1. 浏览器扩展已把 x.com cookie 同步到后端(登录 x.com 即自动同步),
         或设置环境变量 ``OPENBILICLAW_X_COOKIE``。cookie 缺失时静默跳过。
    """

    from openbiliclaw import cli as _cli  # noqa: E402

    _cli._require_runtime_config()
    _print_page_title("拉取 X 点赞 / 收藏", "服务端 cookie 重放,独立于 init")

    likes_data, bookmarks_data = asyncio.run(_cli._fetch_x_init_data(likes_limit=limit, bookmarks_limit=limit))
    like_events = [ev for tw in likes_data if (ev := _cli._x_tweet_to_event(tw, event_type="like")) is not None]
    bookmark_events = [
        ev for tw in bookmarks_data if (ev := _cli._x_tweet_to_event(tw, event_type="favorite")) is not None
    ]
    events = like_events + bookmark_events

    console.print(
        f"  X 点赞 [green]{len(like_events)}[/green] 条"
        f" / 收藏 [green]{len(bookmark_events)}[/green] 条"
        f" → 共 [green]{len(events)}[/green] 条事件。"
    )
    for ev in events[:5]:
        console.print(f"    [dim]- {ev.get('event_type')}: {(ev.get('title') or '')[:50]}[/dim]")

    if not events:
        console.print("  [yellow]没有可写入的事件 —— 未登录 X / cookie 未同步 / 账号无点赞收藏。[/yellow]")
        raise typer.Exit(code=0)

    if dry_run:
        console.print("  [dim]--dry-run:未写入 memory。[/dim]")
        return

    memory = _cli._build_memory_manager()

    async def _persist() -> None:
        for ev in events:
            await memory.propagate_event(ev)

    asyncio.run(_persist())
    console.print(
        f"  [green]已写入 memory:{len(events)} 条事件。[/green] 跑 `openbiliclaw rebuild-profile` 让画像吃进新信号。"
    )


def _run_xhs_discovery(*, force: bool) -> None:
    """Trigger one Soul-driven xhs keyword production cycle."""

    from obc_llm.service import LLMService, module_overrides_from_config
    from obc_soul.engine import SoulProfileNotInitializedError

    from openbiliclaw import cli as _cli  # noqa: E402
    from openbiliclaw.config import load_config
    from openbiliclaw.runtime.xhs_producer import XhsTaskProducer
    from openbiliclaw.sources.xhs_tasks import XhsTaskQueue

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

    config = load_config()
    memory = _cli._build_memory_manager()
    database = _cli._get_runtime_database()
    registry = _cli._build_registry()
    llm_service = LLMService(
        registry=registry,
        memory=memory,
        usage_recorder=_cli._build_usage_recorder(),
        module_overrides=module_overrides_from_config(config),
        concurrency=config.llm.concurrency,
    )

    xhs_cfg = getattr(config.sources, "xiaohongshu", None)
    producer = XhsTaskProducer(
        task_queue=XhsTaskQueue(database),
        soul_engine=soul_engine,
        llm_service=llm_service,
        enabled=True,
        daily_budget=int(getattr(xhs_cfg, "daily_search_budget", 0)),
        min_interval_hours=0 if force else 4,
    )
    result = asyncio.run(producer.produce_if_due())

    reason = str(result.get("reason", ""))
    enqueued = int(cast("int", result.get("enqueued", 0)))
    attempted = int(cast("int", result.get("attempted", 0)))

    _print_page_title("小红书关键词生产", "已将关键词写入 xhs_tasks，由浏览器扩展在后台抓取")
    if reason == "ok":
        _print_key_value_table(
            "生产摘要",
            [
                ("入队关键词数", str(enqueued)),
                ("尝试关键词数", str(attempted)),
                ("今日预算", str(int(getattr(xhs_cfg, "daily_search_budget", 0)))),
                ("节流开关", "已跳过（--force）" if force else "4 小时节流"),
            ],
        )
        return

    messages = {
        "disabled": (
            "info",
            "xhs producer 已禁用",
            "config.scheduler.enabled = false 时无法触发。",
        ),
        "throttled": (
            "info",
            "距离上次关键词生产不足 4 小时",
            "可使用 `--force` 忽略节流重新触发。",
        ),
        "no_profile": (
            "warning",
            "尚未初始化 Soul 画像",
            "请先执行 `openbiliclaw init` 生成初始画像。",
        ),
        "no_keywords": (
            "info",
            "本次未产出关键词",
            "Soul 画像兴趣列表可能为空，或 LLM 返回了空结果。",
        ),
    }
    kind, title, body = messages.get(reason, ("info", "未知状态", reason or "无详细信息"))
    _print_status_panel(kind, title, body)


def _comma_separated_env_values(name: str) -> tuple[str, ...]:
    from obc_discovery.douyin import split_csv_values

    return split_csv_values([os.environ.get(name, "")])


def _normalize_douyin_discovery_sources(sources: tuple[str, ...]) -> tuple[str, ...]:
    allowed = {"search", "hot", "feed"}
    normalized: list[str] = []
    seen: set[str] = set()
    for source in sources:
        for part in str(source).split(","):
            value = part.strip().lower()
            if not value or value in seen:
                continue
            if value not in allowed:
                raise typer.BadParameter(f"未知的抖音 discovery 来源 `{value}`，当前支持：search、hot、feed。")
            seen.add(value)
            normalized.append(value)
    return tuple(normalized) or ("search", "hot", "feed")


def _recent_douyin_creator_sec_uids(*, limit: int = 20) -> tuple[str, ...]:

    from openbiliclaw import cli as _cli  # noqa: E402

    try:
        database = _cli._get_runtime_database()
    except Exception:
        return ()
    if not hasattr(database, "conn"):
        return ()
    try:
        from openbiliclaw.sources.dy_tasks import recent_dy_creator_sec_uids

        return recent_dy_creator_sec_uids(database, limit=limit)
    except Exception:
        return ()


def _run_douyin_discovery(
    *,
    limit: int,
    keywords: tuple[str, ...] = (),
    creator_sec_uids: tuple[str, ...] = (),
    sources: tuple[str, ...] = ("search", "hot", "feed"),
    cache: bool = True,
    evaluate: bool = True,
) -> None:
    """Run one direct-cookie Douyin discovery cycle."""

    from obc_discovery.douyin import (
        DouyinDiscoveryOptions,
        DouyinDiscoveryResult,
        DouyinDiscoveryService,
    )
    from obc_soul.engine import SoulProfileNotInitializedError

    import openbiliclaw.config as config_module
    from openbiliclaw import cli as _cli  # noqa: E402
    from openbiliclaw.sources.douyin_auth import resolve_douyin_cookie
    from openbiliclaw.sources.douyin_direct import DouyinDirectAuthError, DouyinDirectClient
    from openbiliclaw.sources.douyin_plugin_search import DouyinPluginSearchClient

    _cli._require_runtime_config()
    config = config_module.load_config()
    dy_cfg = getattr(config.sources, "douyin", None)
    if dy_cfg is None or not bool(getattr(dy_cfg, "enabled", False)):
        _print_status_panel(
            "warning",
            "抖音 direct discovery 未启用",
            (
                "请在 config.toml 中设置 [sources.douyin].enabled = true；Cookie 可由"
                " OPENBILICLAW_DOUYIN_COOKIE 覆盖，或由浏览器扩展同步到本机。"
            ),
        )
        raise typer.Exit(code=1)

    mode = str(getattr(dy_cfg, "mode", "direct")).strip().lower()
    if mode != "direct":
        _print_status_panel(
            "warning",
            "抖音 discovery 模式暂不支持",
            f"当前 mode={mode!r}；本版本仅支持 direct。",
        )
        raise typer.Exit(code=1)

    cookie_env = str(getattr(dy_cfg, "cookie_env", "OPENBILICLAW_DOUYIN_COOKIE"))
    cookie = resolve_douyin_cookie(data_dir=config.data_path, cookie_env=cookie_env)
    if not cookie:
        _print_status_panel(
            "warning",
            "缺少抖音 Cookie",
            (f"请设置环境变量 {cookie_env}，或保持浏览器扩展在线，让它同步 douyin.com Cookie 到本机。"),
        )
        raise typer.Exit(code=1)

    soul_engine = _cli._build_soul_engine()
    try:
        profile_data = asyncio.run(soul_engine.get_profile())
    except SoulProfileNotInitializedError as exc:
        _print_status_panel(
            "warning",
            "尚未初始化用户画像",
            "请先执行 `openbiliclaw init` 拉取历史并生成初始画像。",
        )
        raise typer.Exit(code=1) from exc

    normalized_sources = _normalize_douyin_discovery_sources(sources)
    resolved_creator_sec_uids = creator_sec_uids or _comma_separated_env_values("OPENBILICLAW_DOUYIN_CREATOR_SEC_UIDS")
    if not resolved_creator_sec_uids and "creator" in normalized_sources:
        resolved_creator_sec_uids = _recent_douyin_creator_sec_uids(limit=max(1, min(limit * 2, 20)))

    async def _discover() -> DouyinDiscoveryResult:

        from openbiliclaw import cli as _cli  # noqa: E402

        async with DouyinDirectClient(cookie=cookie) as direct_client:
            client: Any = direct_client
            if any(source in normalized_sources for source in ("search", "hot", "feed")):
                try:
                    database = _cli._get_runtime_database()
                except Exception:
                    database = None
                if database is not None and hasattr(database, "conn"):
                    search_wait_seconds = float(os.environ.get("OPENBILICLAW_DY_DISCOVERY_SEARCH_WAIT_SECONDS", "180"))
                    client = DouyinPluginSearchClient(
                        database=database,
                        direct_client=direct_client,
                        wait_seconds=search_wait_seconds,
                        daily_search_budget=int(getattr(dy_cfg, "daily_search_budget", 0)),
                        daily_hot_budget=int(getattr(dy_cfg, "daily_hot_budget", 0)),
                        daily_feed_budget=int(getattr(dy_cfg, "daily_feed_budget", 0)),
                    )
            discovery_engine = _cli._build_discovery_engine() if cache else None
            service = DouyinDiscoveryService(
                client=client,
                discovery_engine=discovery_engine,
            )
            return await service.discover(
                profile_data,
                DouyinDiscoveryOptions(
                    limit=limit,
                    sources=normalized_sources,
                    keywords=keywords,
                    creator_sec_uids=resolved_creator_sec_uids,
                    cache=cache,
                    evaluate=evaluate,
                    per_source_limit=max(1, min(limit, 30)),
                ),
            )

    try:
        result = asyncio.run(_discover())
    except DouyinDirectAuthError as exc:
        _print_status_panel("warning", "抖音 Cookie 无效", str(exc))
        raise typer.Exit(code=1) from exc

    discovered = result.items
    source_counts = ", ".join(f"{source}:{count}" for source, count in sorted(result.source_counts.items()))
    _print_page_title("抖音内容发现", f"plugin/direct {' / '.join(normalized_sources)}")
    if not discovered:
        _print_status_panel(
            "info",
            "没有发现到新抖音内容",
            "可能是 Cookie 失效、签名被拒绝，或本轮关键词没有结果。",
        )
        return

    strategies = sorted({str(getattr(item, "source_strategy", "") or "") for item in discovered})
    _print_key_value_table(
        "发现摘要",
        [
            ("发现条数", str(len(discovered))),
            ("缓存状态", "已写入 content_cache" if result.cached else "未写入 content_cache"),
            ("来源", "douyin"),
            ("来源分布", source_counts or "（无）"),
            ("策略", ", ".join(s for s in strategies if s) or "douyin_direct"),
        ],
    )
    for index, item in enumerate(discovered[:5], start=1):
        _print_discovered_content_preview(item, index)


def _build_discovery_candidate_pipeline(
    *,
    config: Any,
    database: Any,
    discovery_engine: Any,
) -> Any:
    """Build the shared raw-candidate evaluator for manual producer runs."""
    from obc_discovery.candidate_pipeline import DiscoveryCandidatePipeline

    discovery_cfg = getattr(config, "discovery", None)
    admission_min_score = float(getattr(discovery_cfg, "admission_min_score", 0.60) or 0.60)
    set_admission_min_score = getattr(database, "set_admission_min_score", None)
    if callable(set_admission_min_score):
        with suppress(Exception):
            set_admission_min_score(admission_min_score)
    return DiscoveryCandidatePipeline(
        database=database,
        discovery_engine=discovery_engine,
        pool_target_count=int(getattr(config.scheduler, "pool_target_count", 300)),
        admission_min_score=admission_min_score,
    )


def _run_zhihu_discovery(*, limit: int) -> None:
    """Run one formal Zhihu discovery cycle through the runtime producer."""

    from obc_soul.engine import SoulProfileNotInitializedError

    from openbiliclaw import cli as _cli  # noqa: E402
    from openbiliclaw.config import load_config
    from openbiliclaw.runtime.keyword_fetch import KeywordFetchCoordinator
    from openbiliclaw.runtime.zhihu_producer import build_zhihu_discovery_producer

    _cli._require_runtime_config()
    config = load_config()
    zh_cfg = getattr(getattr(config, "sources", None), "zhihu", None)
    if zh_cfg is None or not bool(getattr(zh_cfg, "enabled", False)):
        _print_status_panel(
            "warning",
            "知乎 discovery 未启用",
            "请在配置页或 config.toml 中启用 [sources.zhihu].enabled。",
        )
        raise typer.Exit(code=1)

    database = _cli._get_runtime_database()
    if not hasattr(database, "conn"):
        _print_status_panel("warning", "知乎任务表不可用", "当前数据库不支持 zhihu_tasks。")
        raise typer.Exit(code=1)

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

    discovery_engine = _cli._build_discovery_engine()
    candidate_pipeline = _build_discovery_candidate_pipeline(
        config=config,
        database=database,
        discovery_engine=discovery_engine,
    )
    keyword_fetch = KeywordFetchCoordinator(
        database=database,
        discovery_config=config.discovery,
    )
    producer = build_zhihu_discovery_producer(
        config=config,
        database=database,
        soul_engine=soul_engine,
        candidate_pipeline=candidate_pipeline,
        keyword_fetch=keyword_fetch,
    )
    if producer is None:
        _print_status_panel(
            "warning",
            "知乎 discovery producer 未启动",
            "请确认知乎来源和 scheduler 均已启用。",
        )
        raise typer.Exit(code=1)

    result = asyncio.run(producer.produce_if_due(limit=limit))
    reason = str(result.get("reason", ""))
    discovered_raw = result.get("discovered", 0)
    enqueued_raw = result.get("enqueued", 0)
    discovered = int(cast("int | float | str | bool", discovered_raw) if discovered_raw else 0)
    enqueued = int(cast("int | float | str | bool", enqueued_raw) if enqueued_raw else 0)
    source_counts_raw = result.get("source_counts", {})
    source_counts = source_counts_raw if isinstance(source_counts_raw, dict) else {}
    source_counts_text = ", ".join(f"{source}:{count}" for source, count in sorted(source_counts.items()))
    source_modes = ", ".join(str(mode) for mode in getattr(zh_cfg, "source_modes", ()) or ())

    _print_page_title("知乎内容发现", f"正式 discover · {source_modes or 'search'}")
    if reason == "ok":
        _print_key_value_table(
            "发现摘要",
            [
                ("发现条数", str(discovered)),
                ("入池候选", str(enqueued)),
                ("来源", "zhihu"),
                ("来源分布", source_counts_text or "（无）"),
                ("分支", source_modes or "search"),
            ],
        )
        for index, item in enumerate(candidate_pipeline.last_admitted_items[:5], start=1):
            _print_discovered_content_preview(item, index)
        return

    messages = {
        "disabled": ("info", "知乎 discovery 已禁用", "请启用知乎来源后重试。"),
        "throttled": (
            "info",
            "距离上次知乎 discovery 不足最小调度间隔",
            "可在配置页调整知乎最小调度间隔分钟数。",
        ),
        "pool_full": ("info", "候选池已满", "当前无需继续补充知乎候选。"),
        "no_profile": ("warning", "尚未初始化 Soul 画像", "请先执行 `openbiliclaw init`。"),
        "no_keywords": ("info", "没有可用搜索词", "画像兴趣或统一关键词池为空。"),
        "no_creator_seeds": (
            "info",
            "没有作者分支 seed",
            "先跑 search/hot/feed 或手动 `discover-zhihu-creator` 积累作者 URL。",
        ),
        "no_related_seeds": (
            "info",
            "没有相关分支 seed",
            "先跑 search/hot/feed 或手动 `discover-zhihu-related` 积累内容 URL。",
        ),
        "budget_exhausted": (
            "info",
            "知乎 discovery 今日预算已用完",
            "可在配置页调整对应分支预算。",
        ),
        "empty": ("info", "知乎 discovery 返回为空", "插件任务完成但没有可转换的候选。"),
    }
    kind, title, body = messages.get(
        reason,
        ("info", "知乎 discovery 未产出内容", reason or "无详细信息"),
    )
    _print_status_panel(kind, title, body)


def discover_douyin(
    keywords: list[str] | None = _DOUYIN_DISCOVERY_KEYWORDS_OPTION,
    creator_sec_uids: list[str] | None = _DOUYIN_DISCOVERY_CREATOR_SEC_UIDS_OPTION,
    sources: list[str] | None = _DOUYIN_DISCOVERY_SOURCES_OPTION,
    limit: int = typer.Option(30, "--limit", "-n", min=1, help="发现结果条数上限。"),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="只跑策略并预览结果，不写入 content_cache。",
    ),
    no_evaluate: bool = typer.Option(
        False,
        "--no-evaluate",
        help="跳过 LLM 相关性评估，便于调试源接口原始召回。",
    ),
) -> None:
    """单独调试抖音 direct-cookie 内容 discovery."""
    from obc_discovery.douyin import split_csv_values

    from openbiliclaw import cli as _cli

    selected_sources = _normalize_douyin_discovery_sources(split_csv_values(sources) or ("search", "hot", "feed"))
    _cli._run_douyin_discovery(
        limit=limit,
        keywords=split_csv_values(keywords),
        creator_sec_uids=split_csv_values(creator_sec_uids),
        sources=selected_sources,
        cache=not no_cache,
        evaluate=not no_evaluate,
    )


def discover(
    source: str = typer.Option(
        "bilibili",
        "--source",
        "-s",
        help="触发发现的内容源：bilibili、xiaohongshu、douyin 或 zhihu。",
        case_sensitive=False,
    ),
    strategies: list[str] | None = _DISCOVER_STRATEGIES_OPTION,
    limit: int = typer.Option(30, "--limit", "-n", min=1, help="发现结果条数上限。"),
    force: bool = typer.Option(
        False,
        "--force",
        help="xiaohongshu：忽略 4 小时节流强制生产一次关键词。",
    ),
) -> None:
    """手动触发内容发现（按来源选择渠道）."""

    from obc_soul.engine import SoulProfileNotInitializedError

    from openbiliclaw import cli as _cli  # noqa: E402

    source_normalized = source.strip().lower()
    if source_normalized == "xiaohongshu":
        if strategies:
            _print_status_panel(
                "info",
                "--strategy 仅对 Bilibili 生效",
                "xiaohongshu 渠道走关键词生产流程，已忽略策略过滤。",
            )
        _cli._run_xhs_discovery(force=force)
        return

    if source_normalized == "douyin":
        if strategies:
            _print_status_panel(
                "info",
                "--strategy 仅对 Bilibili 生效",
                "douyin 渠道走 direct-cookie discovery，已忽略策略过滤。",
            )
        _cli._run_douyin_discovery(limit=limit)
        return

    if source_normalized == "zhihu":
        if strategies:
            _print_status_panel(
                "info",
                "--strategy 仅对 Bilibili 生效",
                "zhihu 渠道走配置页 source_modes 选择的插件 discovery 分支，已忽略策略过滤。",
            )
        _cli._run_zhihu_discovery(limit=limit)
        return

    if source_normalized != "bilibili":
        raise typer.BadParameter(f"未知的内容源 `{source}`，当前支持：bilibili、xiaohongshu、douyin、zhihu。")

    active_strategies = _cli._normalize_strategy_names(strategies)

    _cli._require_runtime_config()
    soul_engine = _cli._build_soul_engine()
    try:
        profile_data = asyncio.run(soul_engine.get_profile())
    except SoulProfileNotInitializedError as exc:
        _print_status_panel(
            "warning",
            "尚未初始化用户画像",
            "请先执行 `openbiliclaw init` 拉取历史并生成初始画像。",
        )
        raise typer.Exit(code=1) from exc

    discovery_engine = _cli._build_discovery_engine()
    discovered = asyncio.run(
        discovery_engine.discover(
            profile_data,
            strategies=active_strategies or None,
            limit=limit,
        )
    )

    subtitle = "发现结果预览"
    if active_strategies:
        subtitle += f"（策略：{', '.join(active_strategies)}）"
    _print_page_title("本次内容发现", subtitle)
    if not discovered:
        _print_status_panel("info", "没有发现到新内容", "当前没有发现到新的可缓存内容。")
        return

    _print_key_value_table(
        "发现摘要",
        [
            ("发现条数", str(len(discovered))),
            ("缓存状态", "已写入 content_cache"),
            ("来源", "bilibili"),
            ("策略", ", ".join(active_strategies) if active_strategies else "全部"),
        ],
    )
    for index, item in enumerate(discovered[:5], start=1):
        _print_discovered_content_preview(item, index)

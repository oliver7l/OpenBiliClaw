"""CLI 初始化引导命令组（init 及其问询 / 落盘 helper）。

从上帝文件 ``cli/__init__.py`` 抽出（P4 第六刀，2026-09-13）。

本模块顶层**不得** import ``openbiliclaw.cli``（避免循环依赖）；
``init`` 命令经 ``register(app)`` 挂回主 app。

patch 语义保留：被 ``tests/cli`` patch 的符号一律在**函数体内**经
``from openbiliclaw import cli as _cli`` 动态取属性——顶层 from-import
会绑定旧值并破坏 ``monkeypatch.setattr(cli_module, ...)``。
分两类：
- 本文件定义、但测试 patch 到 cli 命名空间的：``_ask_network_binding`` /
  ``_maybe_setup_password_in_init`` / ``_notify_running_server_init_completed``
  / ``_persist_api_host_choice``（cli 侧 re-export 后动态取即命中补丁）；
- 外部定义、测试 patch 到 cli 命名空间的：``_is_interactive_terminal`` /
  ``_prepare_init_runtime`` / ``_get_runtime_database`` /
  ``_build_bilibili_client`` / ``_build_memory_manager`` /
  ``_build_soul_engine`` / ``_run_init_discovery_backfill_async``。

签名默认值引用的 ``_INIT_*`` 常量与 ``console`` / ``run_guided_init`` /
``GuidedInitError`` 属模块加载期求值，直接 ``from
openbiliclaw.runtime.init_flow import``。
"""

from __future__ import annotations

import asyncio
import os

import typer
from rich.table import Table

from openbiliclaw.cli._render import (
    _print_key_value_table,
    _print_page_title,
    _print_status_panel,
)
from openbiliclaw.runtime.init_flow import (  # noqa: F401
    _INIT_BILIBILI_FAVORITE_LIMIT,
    _INIT_BILIBILI_FOLLOW_LIMIT,
    _INIT_BILIBILI_HISTORY_LIMIT,
    _INIT_POOL_TARGET_COUNT,
    GuidedInitError,
    console,
    run_guided_init,
)


def register(app: typer.Typer) -> None:
    """把 init 命令挂回主 app（命令名与形状不变）。"""
    app.command()(init)


def _ask_xhs_inclusion() -> bool:
    """Decide whether to enqueue the xhs bootstrap task on this init.

    Resolution order (first match wins):
      1. ``OPENBILICLAW_NO_XHS=1`` env var → False, silent
      2. Non-interactive terminal (CI / piped stdin) → False, silent.
      3. Interactive terminal → ask the user with default N, then
         (if Y) walk them through a prep checklist.

    Returns True iff the caller should proceed with xhs bootstrap.
    """

    from openbiliclaw import cli as _cli  # noqa: E402

    if os.environ.get("OPENBILICLAW_NO_XHS", "").strip() == "1":
        console.print("[dim]  跳过小红书数据接入(OPENBILICLAW_NO_XHS=1)。[/dim]")
        return False
    if not _cli._is_interactive_terminal():
        return False

    console.print()
    console.print("[bold]🌸 小红书数据接入(可选)[/bold]")
    console.print(
        "把你的小红书[bold cyan]收藏 / 点赞[/bold cyan]混进画像,"
        "系统能读懂你跨平台的口味——\n"
        "你刷小红书喜欢的领域(咖啡 / 摄影 / 穿搭…)也会反映到 B 站推荐里。"
    )
    console.print()
    console.print("启用需要:")
    console.print("  1. 装好 OpenBiliClaw 浏览器扩展")
    console.print(
        "     [link=https://github.com/whiteguo233/OpenBiliClaw/releases]"
        "https://github.com/whiteguo233/OpenBiliClaw/releases[/link]"
    )
    console.print("  2. 浏览器登录 [link=https://www.xiaohongshu.com]https://www.xiaohongshu.com[/link]")
    console.print()
    console.print(
        "[dim]说 N 也没关系,init 只用 B 站数据建画像;以后想加随时再跑一次 init,"
        "或设 OPENBILICLAW_NO_XHS=1 永久跳过。[/dim]"
    )
    console.print()

    if not typer.confirm("加入小红书数据?", default=False):
        console.print("[dim]  已选择跳过,本次 init 不会请求扩展。[/dim]")
        return False

    # User said yes — walk them through the prep checklist before
    # we hit the extension. The bootstrap task has a 30-60s timeout
    # built-in, so if they say "ready" but actually aren't, the
    # collect step degrades gracefully (status="empty"/"timeout") and
    # init still completes on B站 data alone.
    console.print()
    console.print("[bold]准备小红书接入[/bold]")
    console.print("请确认以下三件事都做了:")
    console.print("  [cyan]☐[/cyan] 装好了 OpenBiliClaw 浏览器扩展")
    console.print(
        "  [cyan]☐[/cyan] 浏览器目前是打开的且是当前 [bold]活跃窗口[/bold]"
        "(扩展需要前台 tab 才能触发小红书的瀑布流懒加载)"
    )
    console.print("  [cyan]☐[/cyan] 已经登录了 https://www.xiaohongshu.com")
    console.print()
    console.print(
        "[bold yellow]⚠[/bold yellow]  接下来扩展会[bold]在你的浏览器里自动打开"
        "一个新 tab[/bold]并切到那个 tab(会抢一次焦点),进到你的小红书 profile 页"
        "向下滚动加载收藏/点赞。整个过程 10-30 秒。"
    )
    console.print(
        "[dim]   — 期间不要关那个 tab、不要切走太久(可能影响滚动加载)。完成后扩展会自动关闭它,焦点还回来。[/dim]"
    )
    console.print(
        "[dim]   — 想跳过焦点抢占的话:Ctrl-C 退出,改用 "
        "`OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS=0 openbiliclaw init` "
        "拿浅层数据(只读初始 state,无前台 tab,但只能拿到 ~10-20 条)。[/dim]"
    )
    console.print()
    if not typer.confirm("准备好了吗,可以开始吗?", default=True):
        console.print(
            "[dim]  已暂缓小红书接入,本次 init 只用 B 站数据。装好扩展+登录小红书后随时再跑一次 init 就能补上。[/dim]"
        )
        return False
    return True


def _ask_dy_inclusion() -> bool:
    """Decide whether to enqueue the Douyin bootstrap task on this init.

    Resolution order (first match wins):
      1. ``OPENBILICLAW_NO_DOUYIN=1`` env var → False, silent
      2. Non-interactive terminal (CI / piped stdin) → **False**, silent.
         Conservative default because Douyin hits more-aggressive risk-control
         if the user isn't actually logged in, and the soft anti-bot returns
         HTTP 200 + empty body (design-doc Risk #7) which we can only
         detect after the bootstrap runs. Better to require explicit
         opt-in for Douyin than auto-fire it on every CI run.
      3. Interactive terminal → ask the user with default N, then
         (if Y) walk them through a prep checklist.
    """

    from openbiliclaw import cli as _cli  # noqa: E402

    if os.environ.get("OPENBILICLAW_NO_DOUYIN", "").strip() == "1":
        console.print("[dim]  跳过抖音数据接入(OPENBILICLAW_NO_DOUYIN=1)。[/dim]")
        return False
    if not _cli._is_interactive_terminal():
        return False

    console.print()
    console.print("[bold]🎵 抖音数据接入(可选)[/bold]")
    console.print(
        "把你的抖音[bold cyan]发布 / 收藏 / 点赞 / 关注[/bold cyan]混进画像,"
        "系统能读懂你跨平台的口味——\n"
        "你刷抖音常停留的领域(美食 / 历史 / 知识区…)也会反映到 B 站推荐里。"
    )
    console.print()
    console.print("启用需要:")
    console.print("  1. 装好 OpenBiliClaw 浏览器扩展")
    console.print(
        "     [link=https://github.com/whiteguo233/OpenBiliClaw/releases]"
        "https://github.com/whiteguo233/OpenBiliClaw/releases[/link]"
    )
    console.print("  2. 浏览器登录 [link=https://www.douyin.com]https://www.douyin.com[/link]")
    console.print()
    console.print(
        "[dim]说 N 也没关系,init 会用 B 站(+小红书,如启用)数据建画像;"
        "以后想加随时再跑一次 init,或设 OPENBILICLAW_NO_DOUYIN=1 永久跳过。[/dim]"
    )
    console.print()

    if not typer.confirm("加入抖音数据?", default=False):
        console.print("[dim]  已选择跳过,本次 init 不会请求抖音数据。[/dim]")
        return False

    console.print()
    console.print("[bold]准备抖音接入[/bold]")
    console.print("请确认以下三件事都做了:")
    console.print("  [cyan]☐[/cyan] 装好了 OpenBiliClaw 浏览器扩展")
    console.print(
        "  [cyan]☐[/cyan] 浏览器目前是打开的且是当前 [bold]活跃窗口[/bold]"
        "(扩展需要前台 tab 才能让抖音的虚拟列表分页加载)"
    )
    console.print("  [cyan]☐[/cyan] 已经登录了 https://www.douyin.com")
    console.print()
    console.print(
        "[bold yellow]⚠[/bold yellow]  接下来扩展会[bold]在你的浏览器里自动打开"
        "一个新 tab[/bold]并切到那个 tab(会抢一次焦点),依次访问 4 个 profile sub-tab"
        "(发布 / 收藏 / 点赞 / 关注)向下滚动加载。整个过程 30-90 秒。"
    )
    console.print(
        "[dim]   — 期间不要关那个 tab、不要切走太久(可能影响虚拟列表分页)。完成后扩展会自动关闭它,焦点还回来。[/dim]"
    )
    console.print(
        "[dim]   — 想跳过焦点抢占的话:Ctrl-C 退出,改用 "
        "`OPENBILICLAW_DY_BOOTSTRAP_SCROLL_ROUNDS=0 openbiliclaw init` "
        "拿浅层数据。[/dim]"
    )
    console.print()
    if not typer.confirm("准备好了吗,可以开始吗?", default=True):
        console.print(
            "[dim]  已暂缓抖音接入,本次 init 不会拉抖音数据。装好扩展+登录抖音后随时再跑一次 init 就能补上。[/dim]"
        )
        return False
    return True


def _ask_yt_inclusion() -> bool:
    """Decide whether to enqueue the YouTube bootstrap task on this init.

    Resolution order (first match wins):
      1. ``OPENBILICLAW_NO_YOUTUBE=1`` env var → False, silent
      2. Non-interactive terminal (CI / piped stdin) → **False**, silent.
         Conservative default — YouTube requires browser login and focus.
      3. Interactive terminal → ask the user with default N, then
         (if Y) walk them through a prep checklist.
    """

    from openbiliclaw import cli as _cli  # noqa: E402

    if os.environ.get("OPENBILICLAW_NO_YOUTUBE", "").strip() == "1":
        console.print("[dim]  跳过 YouTube 数据接入(OPENBILICLAW_NO_YOUTUBE=1)。[/dim]")
        return False
    if not _cli._is_interactive_terminal():
        return False

    console.print()
    console.print("[bold]▶ YouTube 数据接入(可选)[/bold]")
    console.print(
        "把你的 YouTube[bold cyan]观看历史 / 订阅 / 点赞[/bold cyan]混进画像,"
        "系统能读懂你跨平台的兴趣——\n"
        "你在 YouTube 常看的领域(科技 / 历史 / 音乐…)也会反映到 B 站推荐里。"
    )
    console.print()
    console.print("启用需要:")
    console.print("  1. 装好 OpenBiliClaw 浏览器扩展")
    console.print(
        "     [link=https://github.com/whiteguo233/OpenBiliClaw/releases]"
        "https://github.com/whiteguo233/OpenBiliClaw/releases[/link]"
    )
    console.print("  2. 浏览器登录 [link=https://www.youtube.com]https://www.youtube.com[/link]")
    console.print()
    console.print(
        "[dim]说 N 也没关系,init 会用 B 站(+其他已启用平台)数据建画像;"
        "以后想加随时再跑一次 init,或设 OPENBILICLAW_NO_YOUTUBE=1 永久跳过。[/dim]"
    )
    console.print()

    if not typer.confirm("加入 YouTube 数据?", default=False):
        console.print("[dim]  已选择跳过,本次 init 不会请求 YouTube 数据。[/dim]")
        return False

    console.print()
    console.print("[bold]准备 YouTube 接入[/bold]")
    console.print("请确认以下三件事都做了:")
    console.print("  [cyan]☐[/cyan] 装好了 OpenBiliClaw 浏览器扩展")
    console.print(
        "  [cyan]☐[/cyan] 浏览器目前是打开的且是当前 [bold]活跃窗口[/bold]"
        "(扩展需要前台 tab 才能滚动加载 YouTube 历史/订阅/点赞列表)"
    )
    console.print("  [cyan]☐[/cyan] 已经登录了 https://www.youtube.com")
    console.print()
    console.print(
        "[bold yellow]⚠[/bold yellow]  接下来扩展会[bold]在你的浏览器里自动打开"
        "一个新 tab[/bold]并切到那个 tab(会抢一次焦点),依次访问 3 个页面"
        "(观看历史 / 订阅频道 / 点赞列表)向下滚动加载。整个过程 30-90 秒。"
    )
    console.print(
        "[dim]   — 期间不要关那个 tab、不要切走太久(可能影响滚动加载)。完成后扩展会自动关闭它,焦点还回来。[/dim]"
    )
    console.print(
        "[dim]   — 想跳过焦点抢占的话:Ctrl-C 退出,改用 "
        "`OPENBILICLAW_YT_BOOTSTRAP_SCROLL_ROUNDS=0 openbiliclaw init` "
        "拿浅层数据。[/dim]"
    )
    console.print()
    if not typer.confirm("准备好了吗,可以开始吗?", default=True):
        console.print(
            "[dim]  已暂缓 YouTube 接入,本次 init 不会拉 YouTube 数据。装好扩展+登录"
            "YouTube 后随时再跑一次 init 就能补上。[/dim]"
        )
        return False
    return True


def _ask_x_inclusion() -> bool:
    """Decide whether to enable the X (Twitter) discovery source on this init.

    Unlike xhs/douyin/youtube, X has no extension bootstrap task — discovery is
    server-side cookie replay. So this only flips ``[sources.twitter].enabled``;
    the actual fetch runs later via the backend producer once x.com cookies are
    synced. Resolution order (first match wins):
      1. ``OPENBILICLAW_NO_X=1`` env var → False, silent.
      2. Non-interactive terminal (CI / piped stdin) → **False**, silent.
      3. Interactive terminal → ask the user with default N (opt-in).
    """

    from openbiliclaw import cli as _cli  # noqa: E402

    if os.environ.get("OPENBILICLAW_NO_X", "").strip() == "1":
        console.print("[dim]  跳过 X 数据接入(OPENBILICLAW_NO_X=1)。[/dim]")
        return False
    if not _cli._is_interactive_terminal():
        return False

    console.print()
    console.print("[bold]𝕏 X (Twitter) 数据接入(可选)[/bold]")
    console.print(
        "把 X 内容混进发现池,系统会按你的画像在 X 上"
        "[bold cyan]搜索 / 拉 For-You / 追订阅作者[/bold cyan],"
        "推荐里会多出 X 的文字卡片。"
    )
    console.print()
    console.print("启用需要:")
    console.print("  1. 装好 OpenBiliClaw 浏览器扩展")
    console.print(
        "     [link=https://github.com/whiteguo233/OpenBiliClaw/releases]"
        "https://github.com/whiteguo233/OpenBiliClaw/releases[/link]"
    )
    console.print("  2. 浏览器登录 [link=https://x.com]https://x.com[/link](扩展会自动把 cookie 同步给后端)")
    console.print()
    console.print(
        "[dim]说 N 也没关系,init 会用 B 站(+其他已启用平台)数据建画像;"
        "以后想加随时再跑一次 init,或在设置页开启 X 来源,或设 OPENBILICLAW_NO_X=1 永久跳过。[/dim]"
    )
    console.print()

    if not typer.confirm("加入 X 数据?", default=False):
        console.print("[dim]  已选择跳过,本次 init 不会启用 X 来源。[/dim]")
        return False
    return True


def _ask_zhihu_inclusion() -> bool:
    """Decide whether to enqueue the Zhihu bootstrap task on this init."""

    from openbiliclaw import cli as _cli  # noqa: E402

    if os.environ.get("OPENBILICLAW_NO_ZHIHU", "").strip() == "1":
        console.print("[dim]  跳过知乎数据接入(OPENBILICLAW_NO_ZHIHU=1)。[/dim]")
        return False
    if not _cli._is_interactive_terminal():
        return False

    console.print()
    console.print("[bold]知乎数据接入(可选)[/bold]")
    console.print(
        "把你的知乎[bold cyan]浏览 / 收藏 / 点赞[/bold cyan]混进画像，知识类回答、文章和关注领域会参与首次偏好分析。"
    )
    console.print()
    console.print("启用需要:")
    console.print("  1. 装好 OpenBiliClaw 浏览器扩展")
    console.print("  2. 浏览器登录 [link=https://www.zhihu.com]https://www.zhihu.com[/link]")
    console.print()
    console.print(
        "[dim]知乎通过浏览器插件使用当前登录态抓取；说 N 也没关系，以后可在设置页开启知乎来源，或重新运行 init。[/dim]"
    )
    console.print()

    if not typer.confirm("加入知乎数据?", default=False):
        console.print("[dim]  已选择跳过，本次 init 不会请求知乎数据。[/dim]")
        return False
    return True


def _ask_network_binding() -> bool:
    """Ask whether the backend should listen on all interfaces (0.0.0.0).

    Returns True if the user confirms all-interface binding, False for
    localhost-only.  Non-interactive terminals default to True (the new
    default keeps mobile web accessible).
    """

    from openbiliclaw import cli as _cli  # noqa: E402

    if not _cli._is_interactive_terminal():
        return True

    console.print()
    console.print("[bold]📱 移动端访问[/bold]")
    console.print("OpenBiliClaw 自带移动端 Web（[bold cyan]/m/[/bold cyan]），同一局域网的手机扫码即可打开。")
    console.print()
    console.print(
        "为此，后端需要监听 [bold]0.0.0.0[/bold]（所有网卡），"
        "这样手机才能连上来。\n"
        "如果你只在本机使用、不需要手机端，选 N 会改为仅监听 127.0.0.1。"
    )
    console.print()
    console.print("[dim]后续可在 config.toml 的 [api].host 随时切换。[/dim]")
    console.print()
    return typer.confirm("允许局域网设备访问（推荐）?", default=True)


def _persist_api_host_choice(*, allow_lan: bool) -> None:
    """Persist the user's network binding choice to config.toml."""
    try:
        from openbiliclaw.config import load_config, save_config

        cfg = load_config()
        target_host = "0.0.0.0" if allow_lan else "127.0.0.1"
        if cfg.api.host != target_host:
            cfg.api.host = target_host
            save_config(cfg)
    except Exception:
        return


def _maybe_setup_password_in_init(*, allow_lan: bool) -> None:
    """Offer to set a LAN access password during init (only when LAN is enabled)."""

    from openbiliclaw import cli as _cli  # noqa: E402

    if not allow_lan or not _cli._is_interactive_terminal():
        return
    console.print()
    console.print("[bold]🔒 访问密码（可选）[/bold]")
    console.print(
        "为局域网/远程设备访问设置登录密码？[bold]本机访问始终免登录[/bold]，只有手机和其他电脑需要输入密码。"
    )
    console.print("[dim]后续可用 `openbiliclaw set-password` 设置或修改。[/dim]")
    console.print()
    if not typer.confirm("为局域网访问设置登录密码?", default=False):
        return
    password = str(typer.prompt("设置访问密码", hide_input=True, confirmation_prompt=True) or "").strip()
    if not password:
        console.print("[dim]密码为空，已跳过。[/dim]")
        return
    try:
        import secrets as _secrets

        from openbiliclaw.auth_core import hash_password
        from openbiliclaw.config import load_config, save_config

        cfg = load_config()
        cfg.api.auth.password_hash = hash_password(password)
        cfg.api.auth.enabled = True
        if not cfg.api.auth.session_secret.strip():
            cfg.api.auth.session_secret = _secrets.token_urlsafe(32)
        save_config(cfg)
        console.print("[green]已设置访问密码，局域网访问将需要登录。[/green]")
    except Exception:
        console.print("[yellow]密码设置失败，可稍后用 `openbiliclaw set-password` 重试。[/yellow]")


def _persist_init_source_enabled_flags(
    *,
    include_bili: bool = True,
    include_xhs: bool,
    include_dy: bool,
    include_yt: bool,
    include_x: bool = False,
    include_zhihu: bool = False,
) -> None:
    """Persist init source choices so background discovery obeys them."""
    try:
        from openbiliclaw.config import load_config, save_config

        cfg = load_config()
        changed = False
        bilibili_cfg = getattr(cfg.sources, "bilibili", None)
        if bilibili_cfg is not None and bool(getattr(bilibili_cfg, "enabled", True)) != include_bili:
            bilibili_cfg.enabled = include_bili
            changed = True
        if bool(getattr(cfg.sources.xiaohongshu, "enabled", False)) != include_xhs:
            cfg.sources.xiaohongshu.enabled = include_xhs
            changed = True
        if bool(getattr(cfg.sources.douyin, "enabled", False)) != include_dy:
            cfg.sources.douyin.enabled = include_dy
            changed = True
        if bool(getattr(cfg.sources.youtube, "enabled", False)) != include_yt:
            cfg.sources.youtube.enabled = include_yt
            changed = True
        twitter_cfg = getattr(cfg.sources, "twitter", None)
        if twitter_cfg is not None and bool(getattr(twitter_cfg, "enabled", False)) != include_x:
            twitter_cfg.enabled = include_x
            changed = True
        zhihu_cfg = getattr(cfg.sources, "zhihu", None)
        if zhihu_cfg is not None and bool(getattr(zhihu_cfg, "enabled", False)) != include_zhihu:
            zhihu_cfg.enabled = include_zhihu
            changed = True
        if changed:
            save_config(cfg)
    except Exception:
        # Persisting init choices is best-effort; init should continue.
        return


def _normalize_init_bilibili_limit(value: int | None, *, default: int) -> int:
    """Normalize user-facing init signal limits.

    Callers own the meaning of 0: history treats it as "fetch all",
    while favorite/follow keep the existing "skip this signal" meaning.
    """
    if value is None:
        return default
    return max(0, int(value))


def _ask_init_bilibili_limits(
    *,
    history_limit: int | None,
    favorite_limit: int | None,
    follow_limit: int | None,
) -> tuple[int, int, int]:
    """Ask interactive users to confirm Bilibili init signal caps."""

    from openbiliclaw import cli as _cli  # noqa: E402

    history = _normalize_init_bilibili_limit(
        history_limit,
        default=_INIT_BILIBILI_HISTORY_LIMIT,
    )
    favorite = _normalize_init_bilibili_limit(
        favorite_limit,
        default=_INIT_BILIBILI_FAVORITE_LIMIT,
    )
    follow = _normalize_init_bilibili_limit(
        follow_limit,
        default=_INIT_BILIBILI_FOLLOW_LIMIT,
    )
    if not _cli._is_interactive_terminal():
        return history, favorite, follow
    if history_limit is not None and favorite_limit is not None and follow_limit is not None:
        return history, favorite, follow

    console.print(
        "\n[bold]B 站初始化信号上限[/bold]\n"
        "[dim]回车使用默认值；历史输入 0 表示拉全部，收藏 / 关注输入 0 表示跳过。[/dim]"
    )
    if history_limit is None:
        raw = typer.prompt(
            "B 站历史最多导入多少条",
            default=str(_INIT_BILIBILI_HISTORY_LIMIT),
        )
        try:
            history = max(0, int(str(raw).strip()))
        except ValueError:
            history = _INIT_BILIBILI_HISTORY_LIMIT
    if favorite_limit is None:
        raw = typer.prompt(
            "B 站收藏最多导入多少条",
            default=str(_INIT_BILIBILI_FAVORITE_LIMIT),
        )
        try:
            favorite = max(0, int(str(raw).strip()))
        except ValueError:
            favorite = _INIT_BILIBILI_FAVORITE_LIMIT
    if follow_limit is None:
        raw = typer.prompt(
            "B 站关注 UP 最多导入多少人",
            default=str(_INIT_BILIBILI_FOLLOW_LIMIT),
        )
        try:
            follow = max(0, int(str(raw).strip()))
        except ValueError:
            follow = _INIT_BILIBILI_FOLLOW_LIMIT
    return history, favorite, follow


def init(
    no_bilibili: bool = typer.Option(
        False,
        "--no-bilibili",
        help="跳过 B 站数据接入(默认包含；init 至少需要保留一个数据来源)。",
    ),
    no_xhs: bool = typer.Option(
        False,
        "--no-xhs",
        help="跳过小红书数据接入(默认会问)。",
    ),
    skip_xhs_prompt: bool = typer.Option(
        False,
        "--yes-xhs",
        help="跳过小红书的 y/n 提问,直接启用(适合脚本化场景)。",
    ),
    no_douyin: bool = typer.Option(
        False,
        "--no-douyin",
        help="跳过抖音数据接入(默认非交互模式下就是跳过)。",
    ),
    skip_dy_prompt: bool = typer.Option(
        False,
        "--yes-douyin",
        help="跳过抖音的 y/n 提问,直接启用(适合脚本化场景)。",
    ),
    no_youtube: bool = typer.Option(
        False,
        "--no-youtube",
        help="跳过 YouTube 数据接入(默认非交互模式下就是跳过)。",
    ),
    skip_yt_prompt: bool = typer.Option(
        False,
        "--yes-youtube",
        help="跳过 YouTube 的 y/n 提问,直接启用(适合脚本化场景)。",
    ),
    no_x: bool = typer.Option(
        False,
        "--no-x",
        help="跳过 X (Twitter) 数据接入(默认非交互模式下就是跳过)。",
    ),
    skip_x_prompt: bool = typer.Option(
        False,
        "--yes-x",
        help="跳过 X 的 y/n 提问,直接启用 X 来源(适合脚本化场景)。",
    ),
    no_zhihu: bool = typer.Option(
        False,
        "--no-zhihu",
        help="跳过知乎数据接入(默认非交互模式下就是跳过)。",
    ),
    skip_zhihu_prompt: bool = typer.Option(
        False,
        "--yes-zhihu",
        help="跳过知乎的 y/n 提问,直接启用知乎来源(适合脚本化场景)。",
    ),
    bilibili_history_limit: int | None = typer.Option(
        None,
        "--bilibili-history-limit",
        min=0,
        help="B 站历史初始化信号上限；默认 500，0 表示拉全部历史。",
    ),
    bilibili_favorite_limit: int | None = typer.Option(
        None,
        "--bilibili-favorite-limit",
        min=0,
        help="B 站收藏初始化信号上限；默认 500，0 表示跳过收藏。",
    ),
    bilibili_follow_limit: int | None = typer.Option(
        None,
        "--bilibili-follow-limit",
        min=0,
        help="B 站关注 UP 初始化信号上限；默认 100，0 表示跳过关注。",
    ),
) -> None:
    """首次运行：拉取历史、生成画像并补足首轮发现池."""

    from openbiliclaw import cli as _cli  # noqa: E402

    _cli._prepare_init_runtime()

    # Snapshot the highest llm_usage row id seen at start so the
    # post-init cost summary can scope to "this init only" rather
    # than the user's lifetime ledger. Wrapped in try/except —
    # billing is best-effort and must not block init startup.
    init_start_usage_id: int | None = None
    try:
        init_start_usage_id = _cli._get_runtime_database().max_llm_usage_id()
    except Exception:
        init_start_usage_id = None

    # B站 is optional like every other source (v0.3.118+): --no-bilibili or
    # OPENBILICLAW_NO_BILIBILI=1 skips it, as long as ≥1 source remains.
    include_bili = not (no_bilibili or os.environ.get("OPENBILICLAW_NO_BILIBILI", "").strip() == "1")

    client = _cli._build_bilibili_client() if include_bili else None
    memory = _cli._build_memory_manager()
    soul_engine = _cli._build_soul_engine()

    _print_page_title("初始化 OpenBiliClaw", "首次运行引导")
    stage1_label = (
        "拉 B 站历史 / 收藏 / 关注（≈ 20–60s，看你的列表大小）" if include_bili else "拉取所选平台数据（B 站已跳过）"
    )
    console.print(
        "[bold yellow]⏱  这一步首次运行预计需要 2–5 分钟，"
        "请保持网络畅通别中断。[/bold yellow]\n"
        "  四个阶段会依次跑：\n"
        f"    1/4  {stage1_label}\n"
        "    2/4  分析偏好（LLM 调用，≈ 30–90s）\n"
        "    3/4  生成灵魂画像（LLM 调用，≈ 30–60s）\n"
        "    4/4  发现首轮内容池（多策略并发 + LLM 评估，≈ 1–3 分钟）\n"
        "[dim]全程会打印进度，不要以为卡住了——LLM 单次响应可能就要 10–30s。[/dim]\n"
    )
    if not include_bili:
        console.print(
            "[dim]  跳过 B 站数据接入"
            f"({'命令行 --no-bilibili' if no_bilibili else 'OPENBILICLAW_NO_BILIBILI=1'})。[/dim]"
        )

    # v0.3.89+: ask user whether the backend should be reachable from
    # the local network (0.0.0.0) so mobile /m/ works out of the box.
    allow_lan = _cli._ask_network_binding()
    _cli._persist_api_host_choice(allow_lan=allow_lan)
    _cli._maybe_setup_password_in_init(allow_lan=allow_lan)

    if include_bili:
        (
            resolved_bilibili_history_limit,
            resolved_bilibili_favorite_limit,
            resolved_bilibili_follow_limit,
        ) = _ask_init_bilibili_limits(
            history_limit=bilibili_history_limit,
            favorite_limit=bilibili_favorite_limit,
            follow_limit=bilibili_follow_limit,
        )
    else:
        resolved_bilibili_history_limit = 0
        resolved_bilibili_favorite_limit = 0
        resolved_bilibili_follow_limit = 0

    # v0.3.27+: ask the user whether to include xhs data, with a prep
    # checklist when they opt in. Defaults stay off unless the user
    # explicitly enables XHS:
    #   --no-xhs          forces skip
    #   --yes-xhs         skips the y/n + checklist (scripted opt-in)
    #   OPENBILICLAW_NO_XHS=1   env var skip
    # Default (interactive, no flags): prompt with default N.
    if no_xhs:
        include_xhs = False
        console.print("[dim]  跳过小红书数据接入(命令行 --no-xhs)。[/dim]")
    elif skip_xhs_prompt:
        include_xhs = True
    else:
        include_xhs = _ask_xhs_inclusion()

    # Same resolution order for the Douyin opt-in. Default is
    # off-in-non-interactive (see _ask_dy_inclusion docstring).
    if no_douyin:
        include_dy = False
        console.print("[dim]  跳过抖音数据接入(命令行 --no-douyin)。[/dim]")
    elif skip_dy_prompt:
        include_dy = True
    else:
        include_dy = _ask_dy_inclusion()

    if no_youtube:
        include_yt = False
        console.print("[dim]  跳过 YouTube 数据接入(命令行 --no-youtube)。[/dim]")
    elif os.environ.get("OPENBILICLAW_NO_YOUTUBE", "").strip() == "1":
        include_yt = False
        console.print("[dim]  跳过 YouTube 数据接入(OPENBILICLAW_NO_YOUTUBE=1)。[/dim]")
    elif skip_yt_prompt:
        include_yt = True
    else:
        include_yt = _ask_yt_inclusion()

    # X (Twitter) is server-side cookie replay — no init bootstrap task, so this
    # only flips [sources.twitter].enabled; the producer fetches later once the
    # x.com cookie is synced. Same resolution order as the other opt-ins.
    if no_x:
        include_x = False
        console.print("[dim]  跳过 X 数据接入(命令行 --no-x)。[/dim]")
    elif os.environ.get("OPENBILICLAW_NO_X", "").strip() == "1":
        include_x = False
        console.print("[dim]  跳过 X 数据接入(OPENBILICLAW_NO_X=1)。[/dim]")
    elif skip_x_prompt:
        include_x = True
    else:
        include_x = _ask_x_inclusion()

    if no_zhihu:
        include_zhihu = False
        console.print("[dim]  跳过知乎数据接入(命令行 --no-zhihu)。[/dim]")
    elif os.environ.get("OPENBILICLAW_NO_ZHIHU", "").strip() == "1":
        include_zhihu = False
        console.print("[dim]  跳过知乎数据接入(OPENBILICLAW_NO_ZHIHU=1)。[/dim]")
    elif skip_zhihu_prompt:
        include_zhihu = True
    else:
        include_zhihu = _ask_zhihu_inclusion()

    if not any((include_bili, include_xhs, include_dy, include_yt, include_x, include_zhihu)):
        _print_status_panel(
            "error",
            "没有可用的数据来源",
            "已跳过 B 站且未启用任何其他平台——init 至少需要一个数据来源。"
            "去掉 --no-bilibili，或配合 --yes-xhs / --yes-douyin / "
            "--yes-youtube / --yes-x / --yes-zhihu "
            "启用其他来源。",
        )
        raise typer.Exit(code=1)

    _persist_init_source_enabled_flags(
        include_bili=include_bili,
        include_xhs=include_xhs,
        include_dy=include_dy,
        include_yt=include_yt,
        include_x=include_x,
        include_zhihu=include_zhihu,
    )

    # gui-init (B2): the four init stages now run inside the shared async
    # pipeline run_guided_init so the API can reuse them without nesting
    # event loops. The CLI injects the one-shot discovery backfill and
    # renders the summary below from the returned InitResult.
    try:
        result = asyncio.run(
            run_guided_init(
                client=client,
                memory=memory,
                soul_engine=soul_engine,
                history_limit=resolved_bilibili_history_limit,
                favorite_limit=resolved_bilibili_favorite_limit,
                follow_limit=resolved_bilibili_follow_limit,
                include_bili=include_bili,
                include_xhs=include_xhs,
                include_dy=include_dy,
                include_yt=include_yt,
                include_x=include_x,
                include_zhihu=include_zhihu,
                target_pool_count=_INIT_POOL_TARGET_COUNT,
                discover_backfill=_cli._run_init_discovery_backfill_async,
            )
        )
    except GuidedInitError as exc:
        if exc.reason == "empty_history":
            _print_status_panel("warning", "历史为空", exc.message)
        elif exc.reason == "empty_signals":
            _print_status_panel("warning", "没有拉到信号", exc.message)
        else:
            _print_status_panel("error", "失败", exc.message)
        raise typer.Exit(code=1) from exc

    history = result.history
    favorites_data = result.favorites_data
    following_data = result.following_data
    events = result.events
    xhs_events = result.xhs_events
    xhs_scope_counts = result.xhs_scope_counts
    xhs_status = result.xhs_status
    dy_events = result.dy_events
    dy_scope_counts = result.dy_scope_counts
    yt_events = result.yt_events
    yt_scope_counts = result.yt_scope_counts
    yt_status = result.yt_status
    discovered_count = result.discovered_count
    discovery_error = result.discovery_error

    if result.discover_exc is not None:
        _print_status_panel(
            "warning",
            "部分完成",
            "画像已生成，但 discover 阶段失败，可稍后手动执行 `openbiliclaw discover`。",
        )

    _print_status_panel(
        "success" if not discovery_error else "warning",
        "初始化完成" if not discovery_error else "初始化部分完成",
        "初始化摘要",
    )

    # v0.3.58+: explicit per-platform breakdown so the user (and the
    # AI agent driving the install) can see exactly what signals fed
    # the soul profile. Previously the summary just said "小红书事件 N"
    # which dropped to 0 when bootstrap_profile was async-pending —
    # now we surface scope-level counts (saved / liked / xhs_history)
    # AND the bilibili history / favorites / following breakdown,
    # plus a total. xhs_scope_counts is set whether the task succeeded
    # or returned empty, so this also surfaces "0 / 0 / 0" cases that
    # suggest the user wasn't logged into XHS.
    # Use the pipeline's snapshot, not a subtraction over ``events`` — the
    # event list also carries X likes/bookmarks, which the old subtraction
    # silently lumped into the B站 row (glaring once B站 itself is optional).
    bilibili_events = result.bilibili_event_count
    xhs_saved = int(xhs_scope_counts.get("saved", 0))
    xhs_liked = int(xhs_scope_counts.get("liked", 0))
    xhs_history = int(xhs_scope_counts.get("xhs_history", 0))
    dy_post = int(dy_scope_counts.get("dy_post", 0))
    dy_collect = int(dy_scope_counts.get("dy_collect", 0))
    dy_like = int(dy_scope_counts.get("dy_like", 0))
    dy_follow = int(dy_scope_counts.get("dy_follow", 0))
    yt_history_count = int(yt_scope_counts.get("yt_history", 0))
    yt_subs_count = int(yt_scope_counts.get("yt_subscriptions", 0))
    yt_likes_count = int(yt_scope_counts.get("yt_likes", 0))
    summary_rows: list[tuple[str, str]] = [
        ("📺 B 站观看历史", f"{len(history)} 条"),
        ("📺 B 站收藏夹", f"{len(favorites_data)} 条"),
        ("📺 B 站关注 UP", f"{len(following_data)} 人"),
        ("🌐 B 站 入库事件", f"{bilibili_events} 条"),
        ("📕 小红书 收藏(saved)", f"{xhs_saved} 条"),
        ("📕 小红书 点赞(liked)", f"{xhs_liked} 条"),
        ("📕 小红书 浏览记录", f"{xhs_history} 条"),
        ("🌐 小红书 入库事件", f"{len(xhs_events)} 条"),
        ("🎵 抖音 发布", f"{dy_post} 条"),
        ("🎵 抖音 收藏", f"{dy_collect} 个"),
        ("🎵 抖音 点赞", f"{dy_like} 个"),
        ("🎵 抖音 关注", f"{dy_follow} 人"),
        ("🌐 抖音 入库事件", f"{len(dy_events)} 条"),
        ("▶ YouTube 观看历史", f"{yt_history_count} 条"),
        ("▶ YouTube 订阅频道", f"{yt_subs_count} 个"),
        ("▶ YouTube 点赞", f"{yt_likes_count} 个"),
        ("🌐 YouTube 入库事件", f"{len(yt_events)} 条"),
        ("📊 画像建模总事件", f"{len(events)} 条"),
        ("✅ 灵魂画像", "已生成"),
        ("🔍 首轮发现内容", f"{discovered_count} 条"),
    ]
    _print_key_value_table("初始化摘要", summary_rows)

    # If the XHS task didn't get any data, surface the likely cause
    # so the user knows whether to re-run with the extension installed.
    if (xhs_saved + xhs_liked + xhs_history) == 0 and xhs_status != "skipped":
        console.print(
            "[dim]ℹ️  小红书 0 条信号入库。最常见原因:扩展未装 / 浏览器没登录 "
            "https://www.xiaohongshu.com / 任务仍在后台跑。装好扩展后重新跑 "
            "[cyan]openbiliclaw init --yes-xhs[/cyan] 可补齐。[/dim]"
        )
    if (yt_history_count + yt_subs_count + yt_likes_count) == 0 and yt_status != "skipped":
        console.print(
            "[dim]ℹ️  YouTube 0 条信号入库。最常见原因:扩展未装 / 浏览器没登录 "
            "https://www.youtube.com / 任务仍在后台跑。装好扩展后重新跑 "
            "[cyan]openbiliclaw init --yes-youtube[/cyan] 可补齐。[/dim]"
        )

    source_parts = []
    if bilibili_events > 0:
        source_parts.append(f"[green]{bilibili_events}[/green] 条 B 站信号")
    if len(xhs_events) > 0:
        source_parts.append(f"[green]{len(xhs_events)}[/green] 条小红书信号")
    if len(dy_events) > 0:
        source_parts.append(f"[green]{len(dy_events)}[/green] 条抖音信号")
    if len(yt_events) > 0:
        source_parts.append(f"[green]{len(yt_events)}[/green] 条 YouTube 信号")
    if len(source_parts) > 1:
        console.print(
            "[dim]ℹ️  本次画像综合了 " + " + ".join(source_parts) + "。后续 daemon 会持续从这些来源增量补充。[/dim]"
        )

    # Phase E (v0.3.28+): print cost breakdown for THIS init only,
    # scoped by the row-id snapshot taken before any LLM call ran.
    # Lets users immediately see "init 这次花了 ¥X,其中 X% 在 discovery
    # 评估" rather than having to manually run `openbiliclaw cost`.
    if init_start_usage_id is not None:
        _print_init_cost_summary(init_start_usage_id)

    # Notify the running API server so the extension refreshes immediately.
    _cli._notify_running_server_init_completed()


def _print_init_cost_summary(since_id: int) -> None:
    """Print this-init-only LLM cost breakdown by caller."""

    from openbiliclaw import cli as _cli  # noqa: E402

    try:
        db = _cli._get_runtime_database()
        snapshot = db.query_llm_usage_since_id(since_id=since_id)
    except Exception:
        return  # never block init success on a billing query
    total = snapshot.get("total", {})
    if not total or total.get("calls", 0) == 0:
        return
    by_caller = snapshot.get("by_caller", [])
    total_cost = float(total.get("cost_cny", 0.0)) or 1e-9

    total_prompt = int(total.get("prompt_tokens", 0))
    total_cached = int(total.get("cached_input_tokens", 0) or 0)
    cache_blurb = ""
    if total_prompt > 0 and total_cached > 0:
        overall_hit = total_cached / total_prompt * 100
        cache_blurb = f" / cache 命中 {overall_hit:.0f}%"

    summary_table = Table(
        show_header=True,
        header_style="bold green",
        title=(f"本次 init LLM 花费 — 总 {total['calls']:,} 次调用 ≈ ¥{total['cost_cny']:.4f}{cache_blurb}"),
    )
    summary_table.add_column("Caller (模块.动作)", no_wrap=True)
    summary_table.add_column("调用数", justify="right")
    summary_table.add_column("token in→out", justify="right")
    summary_table.add_column("cache", justify="right")
    summary_table.add_column("¥ 占比", justify="right", style="bold yellow")
    for row in by_caller:
        share = float(row["cost_cny"]) / total_cost * 100
        prompt_tok = int(row["prompt_tokens"])
        cached_tok = int(row.get("cached_input_tokens", 0) or 0)
        if prompt_tok > 0 and cached_tok > 0:
            hit_pct = cached_tok / prompt_tok * 100
            cache_cell = (
                f"[green]{hit_pct:.0f}%[/green]"
                if hit_pct >= 60
                else (f"[yellow]{hit_pct:.0f}%[/yellow]" if hit_pct >= 30 else f"[red]{hit_pct:.0f}%[/red]")
            )
        else:
            cache_cell = "[dim]—[/dim]"
        summary_table.add_row(
            row["caller"] or "[dim](untagged)[/dim]",
            f"{row['calls']:,}",
            f"{row['prompt_tokens']:,}→{row['completion_tokens']:,}",
            cache_cell,
            f"¥{row['cost_cny']:.4f} ({share:.0f}%)",
        )
    console.print(summary_table)
    console.print(
        "[dim]💡 想看历史累积花费跑 `openbiliclaw cost` (默认 7 天) / "
        "`openbiliclaw cost --by caller --days 30` 看 30 天按模块拆分。"
        "cache 列里红色 (<30%) 的 caller 说明 prompt 前缀不稳,可以 audit 一下。[/dim]"
    )


def _notify_running_server_init_completed(
    *,
    base_url: str = "http://127.0.0.1:8420",
) -> None:
    """POST to the running API server to announce init completion.

    Best-effort: silently ignored when the server is not running.
    """
    import urllib.request

    url = f"{base_url}/api/init-completed"
    try:
        req = urllib.request.Request(url, method="POST", data=b"")
        with urllib.request.urlopen(req, timeout=3):
            console.print("[dim]已通知后端服务，插件将自动刷新。[/dim]")
    except Exception:
        # Server not running — nothing to notify, and that's fine.
        pass

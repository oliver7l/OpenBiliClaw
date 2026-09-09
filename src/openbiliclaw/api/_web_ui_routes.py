"""Web UI (前端页面) 路由与静态文件挂载。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi.responses import FileResponse, RedirectResponse, Response

if TYPE_CHECKING:
    from collections.abc import Callable


def register_web_ui_routes(app: Any, ctx: Any) -> None:
    """Register web UI routes and static file mounts."""

    from fastapi.staticfiles import StaticFiles as _StaticFiles

    _web_dir = Path(__file__).resolve().parent.parent / "web"

    # ── Mobile Web UI ───────────────────────────────────────────
    if _web_dir.is_dir():
        _favicon_path = _web_dir / "icon-192.png"

        @app.get("/favicon.ico", include_in_schema=False)
        def _favicon() -> FileResponse:
            if not _favicon_path.is_file():
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="favicon not found")
            return FileResponse(_favicon_path, media_type="image/png")

        app.mount("/m", _StaticFiles(directory=_web_dir, html=True), name="mobile-web")

    # ── Desktop Web UI ───────────────────────────────────────────
    _desktop_dir = _web_dir / "desktop"
    if _desktop_dir.is_dir():
        _desktop_index_path = _desktop_dir / "index.html"

        def _desktop_asset_version() -> str:
            import hashlib

            digest = hashlib.sha256()
            # 扫描整个 assets 目录（含日记模块 JS），任何前端文件变更都会刷新版本号，
            # 保证浏览器不会命中旧缓存
            assets_dir = _desktop_dir / "assets"
            if assets_dir.is_dir():
                for path in sorted(assets_dir.rglob("*")):
                    if path.is_file():
                        rel = path.relative_to(_desktop_dir).as_posix()
                        stat = path.stat()
                        digest.update(rel.encode("utf-8"))
                        digest.update(str(stat.st_mtime_ns).encode("ascii"))
                        digest.update(str(stat.st_size).encode("ascii"))
            else:
                for relative in ("assets/css/app.css", "assets/js/app.js"):
                    path = _desktop_dir / relative
                    if not path.is_file():
                        continue
                    stat = path.stat()
                    digest.update(relative.encode("utf-8"))
                    digest.update(str(stat.st_mtime_ns).encode("ascii"))
                    digest.update(str(stat.st_size).encode("ascii"))
            return digest.hexdigest()[:12]

        def _desktop_index_response() -> Response:
            if not _desktop_index_path.is_file():
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="desktop web index not found")
            version = _desktop_asset_version()
            html = _desktop_index_path.read_text(encoding="utf-8")
            html = html.replace(
                'href="/web/assets/css/app.css"',
                f'href="/web/assets/css/app.css?v={version}"',
            )
            html = html.replace(
                'src="/web/assets/js/app.js"',
                f'src="/web/assets/js/app.js?v={version}"',
            )
            html = html.replace(
                'src="/web/assets/js/self-evolution.js"',
                f'src="/web/assets/js/self-evolution.js?v={version}"',
            )
            # 注入版本号全局变量，供动态加载的日记模块脚本做缓存控制
            html = html.replace(
                "</head>",
                f'<script>window.__ASSET_VERSION="{version}";</script></head>',
            )
            return Response(
                html,
                media_type="text/html; charset=utf-8",
                headers={"Cache-Control": "no-store"},
            )

        @app.get("/web", include_in_schema=False)
        def _desktop_index_no_slash() -> Response:
            return _desktop_index_response()

        @app.get("/web/", include_in_schema=False)
        def _desktop_index_slash() -> Response:
            return _desktop_index_response()

        _desktop_page_names = {
            "home",
            "delight",
            "saved",
            "profile",
            "chat",
            "diary",
            "clone",
            "library",
            "read-archive",
            "settings",
            "watchLater",
            "watchlater",
            "custom-filter",
            "pool-all",
            "pool-filter",
            "observability",
            "pool-explore",
            "xhs-feed",
            "zhihu-feed",
            "bili-feed",
            "youtube-feed",
            "v2ex-feed",
            "xiaoyuzhou-feed",
            "agent-recommend",
            "self-evolution",
            "knowledge",
            "travel",
        }

        @app.get("/web/{page}", include_in_schema=False)
        def _desktop_page(page: str) -> Response:
            """Bookmarkable desktop page routes (e.g. /web/library, /web/chat).

            Returns the SPA shell; the client reads location.pathname and opens
            the matching view. `page` is a single path segment, so /web/assets/*
            static requests are never intercepted. Unknown pages 404.
            """
            if page not in _desktop_page_names:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="unknown desktop page")
            return _desktop_index_response()

        app.mount("/web", _StaticFiles(directory=_desktop_dir, html=True), name="desktop-web")

        @app.get("/", include_in_schema=False)
        def _root_redirect() -> RedirectResponse:
            return RedirectResponse(url="/web", status_code=302)

    # ── First-run Setup Wizard ──────────────────────────────────
    # Self-contained onboarding page opened on first launch by the packaged
    # app (packaging/entry.py). Guides provider/key + B站 + done, then sends
    # the user to /web. Kept isolated from the main desktop SPA on purpose.
    _setup_dir = _web_dir / "setup"
    if _setup_dir.is_dir():
        app.mount("/setup", _StaticFiles(directory=_setup_dir, html=True), name="setup-wizard")

    # ── Standalone Reading Library ───────────────────────────────
    # Independent, bookmarkable reading-library page. Surfaces the `articles`
    # table (the 阅读库) with full-text search + source/status/tag filters,
    # decoupled from the mobile/desktop SPAs so it can be opened on its own.
    _reading_dir = _web_dir / "reading-library"

    @app.get("/library/{source}", include_in_schema=False)
    def reading_library_platform(source: str) -> Response:
        """Bookmarkable per-platform reading page. Reuses the same SPA,
        injecting the active source so the client filters and labels by it.
        """
        import re

        html_path = _reading_dir / "index.html"
        try:
            html = html_path.read_text(encoding="utf-8")
        except Exception:
            return Response("reading-library page not found", status_code=500)
        safe = re.sub(r"[^a-zA-Z0-9_]", "", source or "")
        injected = '<script>window.__SOURCE__="' + safe + '";</script>'
        html = html.replace("</head>", injected + "</head>", 1)
        return Response(html, media_type="text/html")

    if _reading_dir.is_dir():
        app.mount(
            "/library", _StaticFiles(directory=_reading_dir, html=True), name="reading-library"
        )

    # ── Knowledge Forge 知识图谱可视化（设计 3.1）─────────────────
    # 独立可书签页面：实体-概念-文章关系网络（ECharts 关系图），
    # 支持类型筛选与节点详情（关联文章列表）。数据来自 /api/knowledge-graph。
    _kg_dir = _web_dir / "knowledge-graph"
    if _kg_dir.is_dir():
        app.mount(
            "/knowledge-graph",
            _StaticFiles(directory=_kg_dir, html=True),
            name="knowledge-graph",
        )

    # ── Knowledge Forge 前端页面（设计 §5）───────────────────────
    # 作者/主题/概念：通用实体浏览页（列表+详情双视图），按类型注入。
    # 注意：/web 已被 desktop SPA mount 占用，KF 页面与 /knowledge-graph
    # 一致采用顶级独立前缀，可书签直达。
    _entity_dir = _web_dir / "entity-browser"
    if _entity_dir.is_dir():
        for _etype, _slug in (
            ("author", "authors"),
            ("topic", "topics"),
            ("concept", "concepts"),
        ):
            _html = _entity_dir / "index.html"

            def _make_entity_page(entity_type: str, html_path: Path) -> Callable[[], Response]:
                def _page() -> Response:
                    import re

                    try:
                        html = html_path.read_text(encoding="utf-8")
                    except Exception:
                        return Response("page not found", status_code=500)
                    safe = re.sub(r"[^a-zA-Z0-9_]", "", entity_type or "")
                    injected = '<script>window.__ENTITY_TYPE__="' + safe + '";</script>'
                    return Response(
                        html.replace("</head>", injected + "</head>", 1),
                        media_type="text/html",
                    )

                return _page

            app.get("/" + _slug, include_in_schema=False)(_make_entity_page(_etype, _html))
        app.mount(
            "/entities",
            _StaticFiles(directory=_entity_dir, html=True),
            name="entity-browser",
        )

    # 质量审计 / 缺口分析 / 矛盾报告：独立静态页
    for _slug in ("audit", "gap-analysis", "contradictions"):
        _dir = _web_dir / _slug
        if _dir.is_dir():
            app.mount(
                "/" + _slug,
                _StaticFiles(directory=_dir, html=True),
                name="knowledge-forge-" + _slug,
            )

    # ── Clone Sites static mount ──────────────────────────────────
    # Serves cloned sites under /clone/sites/{slug} so they can be
    # previewed in the browser. Sites live in <data>/clone-sites/.
    from openbiliclaw.clone.paths import resolve_clone_sites_dir

    _clone_sites_dir = resolve_clone_sites_dir(getattr(ctx, "database", None))
    if _clone_sites_dir.is_dir():
        app.mount(
            "/clone/sites",
            _StaticFiles(directory=_clone_sites_dir, html=True),
            name="clone-sites",
        )

    # ── Standalone Health (健康档案) page ────────────────────────
    # Personal/family medical records: encounters, conditions, medications,
    # lab results, procedures, allergies, vitals, immunizations.
    _health_dir = _web_dir / "health"
    if _health_dir.is_dir():
        app.mount("/health", _StaticFiles(directory=_health_dir, html=True), name="health-page")

    # ── Standalone Topics (专题) page ────────────────────────────
    # Bookmarkable /topics page listing user-curated topic collections with
    # their continuously collected items; create + collect-now actions hit
    # the /api/topics* endpoints above.
    _topics_dir = _web_dir / "topics"
    if _topics_dir.is_dir():
        app.mount("/topics", _StaticFiles(directory=_topics_dir, html=True), name="topics-page")

    # ── Self-Evolution (自进化) API endpoints ─────────────────────
    # 已独立为 src/openbiliclaw/self_evolution/api.py，此处仅注册路由
    from openbiliclaw.self_evolution.api import create_self_evolution_router

    _self_evo_db_path = (
        str(getattr(getattr(ctx, "config", None), "storage", None).db_path)
        if getattr(getattr(ctx, "config", None), "storage", None)
        else "data/openbiliclaw.db"
    )
    app.include_router(
        create_self_evolution_router(_self_evo_db_path, getattr(ctx, "llm_service", None))
    )

    # ── Synthesis (迭代合成) API endpoints ─────────────────────────
    from openbiliclaw.synthesis.api import create_synthesis_router

    app.include_router(
        create_synthesis_router(_self_evo_db_path, getattr(ctx, "llm_service", None))
    )

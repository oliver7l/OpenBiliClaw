"""运行时构建族：LLM registry / 各引擎 / 记忆管理 / 数据库健康（P4 第十一刀自 cli/__init__.py 抽离）。

顶层禁 import openbiliclaw.cli 本体（防循环依赖；兄弟子模块互引允许）。
被测试 patch 到 cli 命名空间的符号（_build_* / _get_runtime_database /
_RUNTIME_COMPONENTS / console）在调用点经 `_cli.X` 动态取；
_runtime_database_path / _runtime_backup_dir / _print_status_panel 无补丁依赖，直取。
该族不含 typer 命令，故无需 register()。
"""

from pathlib import Path
from typing import Any, cast

import typer

from openbiliclaw.cli._render import _print_status_panel


def _build_registry() -> Any:
    """Build the configured LLM registry."""
    from openbiliclaw.config import load_config
    from openbiliclaw.llm._compat_registry import build_llm_registry

    return build_llm_registry(load_config())


def _build_auth_manager() -> Any:
    """Build the configured Bilibili auth manager."""
    from openbiliclaw.bilibili.auth import AuthManager
    from openbiliclaw.config import load_config

    return AuthManager(load_config().data_path)


def _build_browser() -> Any:
    """Build the configured Bilibili browser integration."""
    from openbiliclaw.bilibili.auth import resolve_runtime_cookie
    from openbiliclaw.bilibili.browser import BilibiliBrowser
    from openbiliclaw.config import load_config

    config = load_config()
    return BilibiliBrowser(
        executable=config.bilibili.browser_executable,
        headed=config.bilibili.browser_headed,
        cookie=resolve_runtime_cookie(
            data_dir=config.data_path,
            configured_cookie=config.bilibili.cookie,
        ),
    )


def _build_bilibili_client() -> Any:
    """Build the configured Bilibili API client."""
    from openbiliclaw.bilibili.api import BilibiliAPIClient
    from openbiliclaw.bilibili.auth import resolve_runtime_cookie
    from openbiliclaw.config import load_config

    config = load_config()
    return BilibiliAPIClient(
        cookie=resolve_runtime_cookie(
            data_dir=config.data_path,
            configured_cookie=config.bilibili.cookie,
        )
    )


def _build_soul_engine() -> Any:
    """Build the configured soul engine with initialized memory storage."""

    from openbiliclaw import cli as _cli  # noqa: E402
    from openbiliclaw.config import load_config
    from openbiliclaw.llm.service import module_overrides_from_config
    from openbiliclaw.soul.engine import SoulEngine

    class _UnavailableLLM:
        default_provider = ""

        def is_chat_capable(self, _name: str) -> bool:
            return False

        async def complete(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("LLM registry is unavailable for this command.")

        async def complete_provider(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("LLM registry is unavailable for this command.")

    cfg = load_config()
    memory = _cli._build_memory_manager()
    try:
        llm = _cli._build_registry()
    except Exception:
        llm = _UnavailableLLM()
    return SoulEngine(
        llm=llm,
        memory=memory,
        usage_recorder=_cli._build_usage_recorder(),
        satisfaction_filter_enabled=cfg.soul.preference.satisfaction_filter_enabled,
        module_overrides=module_overrides_from_config(cfg),
        llm_concurrency=cfg.llm.concurrency,
        speculation_interval_minutes=cfg.scheduler.speculation_interval_minutes,
        speculation_ttl_days=cfg.scheduler.speculation_ttl_days,
        speculation_cooldown_days=cfg.scheduler.speculation_cooldown_days,
        speculation_confirmation_threshold=cfg.scheduler.speculation_confirmation_threshold,
        speculation_max_active=cfg.scheduler.speculation_max_active,
        speculation_max_primary_interests=cfg.scheduler.speculation_max_primary_interests,
        speculation_max_secondary_interests=cfg.scheduler.speculation_max_secondary_interests,
        avoidance_speculation_interval_minutes=(cfg.scheduler.avoidance_speculation_interval_minutes),
        avoidance_speculation_ttl_days=cfg.scheduler.avoidance_speculation_ttl_days,
        avoidance_speculation_cooldown_days=cfg.scheduler.avoidance_speculation_cooldown_days,
        avoidance_speculation_confirmation_threshold=(cfg.scheduler.avoidance_speculation_confirmation_threshold),
        avoidance_speculation_max_active=cfg.scheduler.avoidance_speculation_max_active,
        speculator_idle_interval_minutes=cfg.scheduler.speculator_idle_interval_minutes,
        profile_consolidation_enabled=cfg.scheduler.profile_consolidation_enabled,
        profile_consolidation_interval_hours=(cfg.scheduler.profile_consolidation_interval_hours),
        profile_consolidation_like_target_upper=(cfg.scheduler.profile_consolidation_like_target_upper),
        profile_consolidation_like_target_soft=cfg.scheduler.profile_consolidation_like_target_soft,
        profile_consolidation_archive_enabled=(cfg.scheduler.profile_consolidation_archive_enabled),
    )


def _build_recommendation_engine() -> Any:
    """Build the recommendation engine with core-memory-aware LLM access."""

    from openbiliclaw import cli as _cli  # noqa: E402
    from openbiliclaw.config import load_config
    from openbiliclaw.llm.service import LLMService, module_overrides_from_config
    from openbiliclaw.recommendation.engine import (
        RecommendationEngine,
        SupportsEmbeddingService,
    )

    memory = _cli._build_memory_manager()
    database = _cli._get_runtime_database()
    cfg = load_config()
    registry = _cli._build_registry()
    llm_service = LLMService(
        registry=registry,
        memory=memory,
        usage_recorder=_cli._build_usage_recorder(),
        module_overrides=module_overrides_from_config(cfg),
        concurrency=cfg.llm.concurrency,
    )
    from openbiliclaw.llm._compat_registry import build_embedding_service

    _emb = build_embedding_service(cfg, registry)
    embedding_service = cast("SupportsEmbeddingService | None", _emb)

    def _xhs_self_info_provider() -> dict[str, object] | None:
        state = memory.load_discovery_runtime_state()
        info = state.get("xhs_self_info")
        return info if isinstance(info, dict) else None

    return RecommendationEngine(
        llm=llm_service,
        database=database,
        embedding_service=embedding_service,
        xhs_self_info_provider=_xhs_self_info_provider,
        # v0.4.0+: LLM semantic reranker (generative recommendation, step 1)
        # 防御性：测试 fake_config（SimpleNamespace）可能缺 recommendation 段
        llm_reranker_enabled=bool(getattr(getattr(cfg, "recommendation", None), "llm_reranker_enabled", False)),
        llm_reranker_top_k=int(getattr(getattr(cfg, "recommendation", None), "llm_reranker_top_k", 30)),
        llm_reranker_weight=float(getattr(getattr(cfg, "recommendation", None), "llm_reranker_weight", 0.3)),
        llm_reranker_batch_size=int(getattr(getattr(cfg, "recommendation", None), "llm_reranker_batch_size", 5)),
    )


def _build_dialogue(soul_engine: Any) -> Any:
    """Build the Socratic dialogue helper for interactive chat."""

    from openbiliclaw import cli as _cli  # noqa: E402
    from openbiliclaw.soul.dialogue import SocraticDialogue

    return SocraticDialogue(llm=_cli._build_registry(), soul_engine=soul_engine, session="cli")


def _run_api_server(*, host: str = "127.0.0.1", port: int = 8420) -> None:
    """Run the local FastAPI service used by the browser extension."""
    import uvicorn

    from openbiliclaw.api.app import create_app

    api_app = create_app()
    from openbiliclaw.api.chat_analysis_routes import register_chat_analysis_routes

    register_chat_analysis_routes(api_app, getattr(api_app, "state", None))
    state = getattr(api_app, "state", None)
    if bool(getattr(state, "degraded", False)):
        issues = []
        for issue in list(getattr(state, "degraded_issues", [])):
            field = str(getattr(issue, "field", ""))
            message = str(getattr(issue, "message", issue))
            issues.append(f"- {field}: {message}" if field else f"- {message}")
        reason = str(getattr(state, "degraded_reason", ""))
        body = (
            f"reason: {reason or 'unknown'}\n"
            + "\n".join(issues)
            + "\n\nOpen the extension popup settings to fix the LLM credentials, "
            "then restart the daemon."
        )
        _print_status_panel("warning", "降级模式 / Degraded mode", body)
    uvicorn.run(api_app, host=host, port=port, log_level="info")


def _build_memory_manager() -> Any:
    """Build the initialized memory manager for event writes."""

    from openbiliclaw import cli as _cli  # noqa: E402
    from openbiliclaw.config import load_config
    from openbiliclaw.memory.manager import MemoryManager

    cached = _cli._RUNTIME_COMPONENTS.get("memory_manager")
    if cached is not None:
        return cached

    config = load_config()
    memory = MemoryManager(config.data_path, database=_cli._get_runtime_database())
    memory.initialize()
    _cli._RUNTIME_COMPONENTS["memory_manager"] = memory
    return memory


def _build_discovery_engine() -> Any:
    """Build the discovery engine with currently implemented strategies."""

    from openbiliclaw import cli as _cli  # noqa: E402
    from openbiliclaw.discovery.engine import (
        ContentDiscoveryEngine,
        DiscoveryConcurrencyController,
    )
    from openbiliclaw.discovery.strategies.strategies import (
        ExploreStrategy,
        RelatedChainStrategy,
        SearchStrategy,
        TrendingStrategy,
    )
    from openbiliclaw.llm.service import LLMService, module_overrides_from_config

    memory = _cli._build_memory_manager()
    database = _cli._get_runtime_database()
    bilibili_client = _cli._build_bilibili_client()
    from openbiliclaw.config import load_config

    cfg = load_config()
    registry = _cli._build_registry()
    llm_service = LLMService(
        registry=registry,
        memory=memory,
        usage_recorder=_cli._build_usage_recorder(),
        module_overrides=module_overrides_from_config(cfg),
        concurrency=cfg.llm.concurrency,
    )
    concurrency = DiscoveryConcurrencyController(
        bilibili_request_concurrency=2,
        # Inherit dataclass default (currently 32) — sized so an init
        # discover's ~32 batches all fan out in a single wave instead
        # of queueing behind a tight cap. See engine.py for rationale.
    )

    # Build embedding service from config (optional)
    from openbiliclaw.llm._compat_registry import build_embedding_service

    embedding_service = build_embedding_service(cfg, registry)
    discovery_cfg = getattr(cfg, "discovery", None)

    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        database=database,
        concurrency=concurrency,
        embedding_service=embedding_service,
        multimodal_evaluation_enabled=bool(getattr(discovery_cfg, "multimodal_evaluation_enabled", False)),
        multimodal_batch_size=int(getattr(discovery_cfg, "multimodal_batch_size", 8)),
        multimodal_image_max_px=int(getattr(discovery_cfg, "multimodal_image_max_px", 384)),
        multimodal_image_quality=int(getattr(discovery_cfg, "multimodal_image_quality", 72)),
        multimodal_image_timeout_seconds=int(getattr(discovery_cfg, "multimodal_image_timeout_seconds", 6)),
    )
    search_strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        concurrency=concurrency,
        database=database,
    )
    trending_strategy = TrendingStrategy(
        bilibili_client=bilibili_client,
        llm_service=llm_service,
        concurrency=concurrency,
        database=database,
    )
    related_strategy = RelatedChainStrategy(
        bilibili_client=bilibili_client,
        llm_service=llm_service,
        memory_manager=cast("Any", memory),
        search_strategy=search_strategy,
        trending_strategy=trending_strategy,
        concurrency=concurrency,
        database=database,
    )
    explore_strategy = ExploreStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        concurrency=concurrency,
        embedding_service=embedding_service,
        database=database,
    )

    engine.register_strategy(search_strategy)
    engine.register_strategy(trending_strategy)
    engine.register_strategy(related_strategy)
    engine.register_strategy(explore_strategy)
    return engine


def _build_usage_recorder() -> Any:
    """Build or return the shared LLM usage recorder (cost ledger sink).

    CLI commands construct their own ``LLMService`` / ``SoulEngine``
    instead of going through ``runtime_context``, so without this every
    CLI-run LLM call was invisible in ``openbiliclaw cost``.
    """

    from openbiliclaw import cli as _cli  # noqa: E402

    cached = _cli._RUNTIME_COMPONENTS.get("usage_recorder")
    if cached is not None:
        return cached

    from openbiliclaw.llm.usage_recorder import UsageRecorder

    recorder = UsageRecorder(sink=_cli._get_runtime_database())
    _cli._RUNTIME_COMPONENTS["usage_recorder"] = recorder
    return recorder


def _runtime_database_path() -> Path:
    from openbiliclaw.config import load_config

    config = load_config()
    return config.data_path / "openbiliclaw.db"


def _runtime_backup_dir() -> Path:
    return _runtime_database_path().parent / "backups"


def _maybe_create_runtime_database_backup() -> None:
    from openbiliclaw.storage.maintenance import maybe_create_scheduled_backup

    db_path = _runtime_database_path()
    if not db_path.exists():
        return
    maybe_create_scheduled_backup(db_path, _runtime_backup_dir())


def _ensure_runtime_database_healthy() -> None:

    from openbiliclaw import cli as _cli  # noqa: E402
    from openbiliclaw.storage.maintenance import check_database_integrity

    db_path = _runtime_database_path()
    if not db_path.exists():
        return
    report = check_database_integrity(db_path)
    if report.healthy:
        return
    _print_status_panel(
        "error",
        "数据库损坏",
        "检测到本地数据库损坏，请先执行 `openbiliclaw db-repair` 再启动服务。",
    )
    if report.error:
        _cli.console.print(report.error)
    raise typer.Exit(code=1)


def _run_db_repair() -> Any:
    from openbiliclaw.storage.maintenance import repair_database

    return repair_database(_runtime_database_path(), backup_dir=_runtime_backup_dir())

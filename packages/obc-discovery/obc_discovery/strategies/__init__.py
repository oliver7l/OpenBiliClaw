"""Discovery strategy implementations — lazy imports to avoid circular deps."""

from ._utils import (
    SupportsSearchClient,
    _gather_bounded,
    build_profile_summary,
)

__all__ = [
    "ALL_STRATEGIES",
    "DouyinDirectStrategy",
    "ExploreStrategy",
    "RelatedChainStrategy",
    "SearchStrategy",
    "SupportsSearchClient",
    "TrendingStrategy",
    "XStrategy",
    "YoutubeStrategy",
    "_gather_bounded",
    "build_profile_summary",
    "get_strategy",
]


def DouyinDirectStrategy(*args, **kwargs):
    from .douyin_direct import DouyinDirectStrategy as _cls
    return _cls(*args, **kwargs)


def ExploreStrategy(*args, **kwargs):
    from .explore import ExploreStrategy as _cls
    return _cls(*args, **kwargs)


def RelatedChainStrategy(*args, **kwargs):
    from .related_chain import RelatedChainStrategy as _cls
    return _cls(*args, **kwargs)


def SearchStrategy(*args, **kwargs):
    from .search import SearchStrategy as _cls
    return _cls(*args, **kwargs)


def TrendingStrategy(*args, **kwargs):
    from .trending import TrendingStrategy as _cls
    return _cls(*args, **kwargs)


def XStrategy(*args, **kwargs):
    from .x import XStrategy as _cls
    return _cls(*args, **kwargs)


def YoutubeStrategy(*args, **kwargs):
    from .youtube import YoutubeStrategy as _cls
    return _cls(*args, **kwargs)


def get_strategy(name: str, *args, **kwargs):
    from .strategies import get_strategy as _fn
    return _fn(name, *args, **kwargs)


ALL_STRATEGIES: list[type] = []


def _init_all_strategies() -> None:
    """Lazy-init the strategy registry to avoid circular imports."""
    global ALL_STRATEGIES
    if ALL_STRATEGIES:
        return
    from .strategies import ALL_STRATEGIES

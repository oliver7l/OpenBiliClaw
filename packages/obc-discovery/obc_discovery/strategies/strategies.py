"""Discovery strategies -- re-export hub for backwards compatibility."""

from obc_discovery.strategies._utils import (
    SupportsMemoryManager,
    SupportsRankingClient,
    SupportsRelatedClient,
    SupportsSearchClient,
    SupportsSeedStrategy,
)
from obc_discovery.strategies.douyin_direct import DouyinDirectStrategy
from obc_discovery.strategies.explore import ExploreStrategy
from obc_discovery.strategies.related_chain import RelatedChainStrategy
from obc_discovery.strategies.search import SearchStrategy
from obc_discovery.strategies.trending import TrendingStrategy
from obc_discovery.strategies.youtube import (
    YoutubeChannelStrategy,
    YoutubeSearchStrategy,
    YoutubeTrendingStrategy,
)

ALL_STRATEGIES = [
    DouyinDirectStrategy,
    ExploreStrategy,
    RelatedChainStrategy,
    SearchStrategy,
    TrendingStrategy,
    YoutubeChannelStrategy,
    YoutubeSearchStrategy,
    YoutubeTrendingStrategy,
]

__all__ = [
    "ALL_STRATEGIES",
    "DouyinDirectStrategy",
    "ExploreStrategy",
    "RelatedChainStrategy",
    "SearchStrategy",
    "TrendingStrategy",
    "YoutubeChannelStrategy",
    "YoutubeSearchStrategy",
    "YoutubeTrendingStrategy",
    "SupportsSearchClient",
    "SupportsRankingClient",
    "SupportsRelatedClient",
    "SupportsMemoryManager",
    "SupportsSeedStrategy",
]

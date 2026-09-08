"""Content Discovery Engine — re-export from obc-discovery.

This is a compatibility stub that re-exports everything from the extracted
obc-discovery package. All existing imports continue to work unchanged.
"""

from obc_discovery.engine import ContentDiscoveryEngine, DiscoveredContent, DiscoveryStrategy
from obc_discovery.candidate_pipeline import DiscoveryCandidatePipeline
from obc_discovery.candidate_pool import (
    DiscoveryCandidateWrite,
    PENDING_EVAL,
    EVALUATING,
    EVALUATED,
    CACHED,
    REJECTED_LOW_SCORE,
    REJECTED_DUPLICATE,
    REJECTED_RECENTLY_VIEWED,
    REJECTED_FRANCHISE_QUOTA,
    PENDING_EVAL,
    discovery_candidate_pending_cap,
)
from obc_discovery.style_keys import STYLE_KEY_DEFINITIONS, VALID_STYLE_KEYS, normalize_style_key
from obc_discovery.style_rules import STYLE_RULES, SOURCE_FALLBACKS, DEFAULT_STYLE, infer_style_key
from obc_discovery._protocols import (
    CandidateStore,
    ImageCache,
    SoulProfileReader,
    CoverFetchError,
)
from obc_discovery.multimodal import set_image_cache

__all__ = [
    # Core engine
    "ContentDiscoveryEngine",
    "DiscoveredContent",
    "DiscoveryStrategy",
    # Candidate pipeline
    "DiscoveryCandidatePipeline",
    "DiscoveryCandidateWrite",
    # Status constants
    "PENDING_EVAL",
    "EVALUATING",
    "EVALUATED",
    "CACHED",
    "REJECTED_LOW_SCORE",
    "REJECTED_DUPLICATE",
    "REJECTED_RECENTLY_VIEWED",
    "REJECTED_FRANCHISE_QUOTA",
    # Cap calculation
    "discovery_candidate_pending_cap",
    # Style utilities
    "STYLE_KEY_DEFINITIONS",
    "VALID_STYLE_KEYS",
    "normalize_style_key",
    "STYLE_RULES",
    "SOURCE_FALLBACKS",
    "DEFAULT_STYLE",
    "infer_style_key",
    # Multimodal
    "set_image_cache",
    # Protocols
    "CandidateStore",
    "ImageCache",
    "SoulProfileReader",
    "CoverFetchError",
]

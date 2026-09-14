"""Content Discovery Engine for OpenBiliClaw.

Multi-strategy cross-platform content discovery that finds content matching
the user's soul profile and preference model.
"""

from ._protocols import (
    CandidateStore,
    CoverFetchError,
    ImageCache,
    SoulProfileReader,
)
from .candidate_pipeline import DiscoveryCandidatePipeline
from .candidate_pool import (
    CACHED,
    EVALUATED,
    EVALUATING,
    PENDING_EVAL,
    REJECTED_DUPLICATE,
    REJECTED_FRANCHISE_QUOTA,
    REJECTED_LOW_SCORE,
    REJECTED_RECENTLY_VIEWED,
    DiscoveryCandidateWrite,
    discovery_candidate_pending_cap,
)
from .engine import ContentDiscoveryEngine, DiscoveredContent, DiscoveryStrategy
from .multimodal import set_image_cache
from .style_keys import STYLE_KEY_DEFINITIONS, VALID_STYLE_KEYS, normalize_style_key
from .style_rules import DEFAULT_STYLE, SOURCE_FALLBACKS, STYLE_RULES, infer_style_key

__all__ = [
    # Core engine
    "ContentDiscoveryEngine",
    "DiscoveredContent",
    "DiscoveryStrategy",
    # Candidate pipeline
    "DiscoveryCandidatePipeline",
    "DiscoveryCandidateWrite",
    "PENDING_EVAL",
    "EVALUATING",
    "EVALUATED",
    "CACHED",
    "REJECTED_DUPLICATE",
    "REJECTED_FRANCHISE_QUOTA",
    "REJECTED_LOW_SCORE",
    "REJECTED_RECENTLY_VIEWED",
    "discovery_candidate_pending_cap",
    # Style utilities
    "STYLE_KEY_DEFINITIONS",
    "VALID_STYLE_KEYS",
    "normalize_style_key",
    "DEFAULT_STYLE",
    "SOURCE_FALLBACKS",
    "STYLE_RULES",
    "infer_style_key",
    # Multimodal
    "set_image_cache",
    # Protocols (implemented by main project)
    "CandidateStore",
    "ImageCache",
    "SoulProfileReader",
    "CoverFetchError",
]

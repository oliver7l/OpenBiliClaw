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
from .candidate_pool import EVALUATED, EVALUATING, PENDING_EVAL, DiscoveryCandidateWrite
from .engine import ContentDiscoveryEngine, DiscoveredContent, DiscoveryStrategy
from .multimodal import set_image_cache
from .style_keys import STYLE_KEY_DEFINITIONS, VALID_STYLE_KEYS, normalize_style_key

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
    # Style utilities
    "STYLE_KEY_DEFINITIONS",
    "VALID_STYLE_KEYS",
    "normalize_style_key",
    # Multimodal
    "set_image_cache",
    # Protocols (implemented by main project)
    "CandidateStore",
    "ImageCache",
    "SoulProfileReader",
    "CoverFetchError",
]

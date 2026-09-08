"""obc-soul — OpenBiliClaw User Soul Engine for deep user understanding.

Provides comprehensive user modeling:
- Preference extraction and analysis from interaction history
- Continuous cognitive cycle for insight discovery
- Dialog system for Socratic questioning
- Preference writeback and negative exemplar management
- Pool purging for disliked content
"""

from ._config import SoulConfig
from ._protocols import (
    MemoryStore,
    PoolStore,
    EventNormalizer,
)
# Core engine
from .engine import (
    SoulEngine,
    SoulProfileNotInitializedError,
)
# Data models
from .profile import (
    InterestTag,
    OnionProfile,
    SoulProfile,
)
# Core components
from .dialogue import SocraticDialogue
from .taxonomy import CATEGORY_VOCAB

__all__ = [
    # Config
    "SoulConfig",
    # Protocols (implemented by main project)
    "MemoryStore",
    "PoolStore",
    "EventNormalizer",
    # Core engine
    "SoulEngine",
    "SoulProfileNotInitializedError",
    # Data models
    "InterestTag",
    "OnionProfile",
    "SoulProfile",
    # Dialogue system
    "SocraticDialogue",
    # Taxonomy
    "CATEGORY_VOCAB",
]

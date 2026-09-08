"""Soul Engine — re-export from obc-soul.

This is a compatibility stub that re-exports everything from the extracted
obc-soul package. All existing imports continue to work unchanged.
"""

# Core engine
from obc_soul.engine import SoulEngine, SoulProfileNotInitializedError

# Data models
from obc_soul.profile import (
    InterestTag,
    OnionProfile,
    SoulProfile,
)

# Dialogue system
from obc_soul.dialogue import SocraticDialogue

# Taxonomy
from obc_soul.taxonomy import CATEGORY_VOCAB

# Config
from obc_soul._config import SoulConfig

# Protocols (main project implements these)
from obc_soul._protocols import (
    MemoryStore,
    PoolStore,
    EventNormalizer,
)

__all__ = [
    "SoulConfig",
    "SoulEngine",
    "SoulProfileNotInitializedError",
    "InterestTag",
    "OnionProfile",
    "SoulProfile",
    "SocraticDialogue",
    "CATEGORY_VOCAB",
    "MemoryStore",
    "PoolStore",
    "EventNormalizer",
]
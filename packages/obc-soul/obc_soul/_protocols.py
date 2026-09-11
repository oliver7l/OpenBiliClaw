"""Protocol interfaces for decoupling obc-soul from upstream modules.

Main project implements these protocols and injects them into SoulEngine.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Protocol interfaces (implemented by main project)
# ---------------------------------------------------------------------------


@runtime_checkable
class MemoryStore(Protocol):
    """Memory storage interface — soul engine reads/writes memory.

    Implemented by openbiliclaw.memory.manager.MemoryManager in main project.
    """

    def get_recent_events(self, limit: int = 100) -> list[dict]:
        """Get recent interaction events for analysis."""
        ...

    def get_all_events(self) -> list[dict]:
        """Get all interaction events."""
        ...

    def append_event(self, event: dict) -> None:
        """Append a new event to memory."""
        ...


@runtime_checkable
class PoolStore(Protocol):
    """Candidate pool storage interface — pool_purge removes disliked candidates.

    Implemented by openbiliclaw.storage.database.Database in main project.
    """

    def purge_candidate(self, content_url: str) -> None:
        """Mark a candidate as permanently disliked and remove from pool."""
        ...


@runtime_checkable
class EventNormalizer(Protocol):
    """Event normalization interface — converts raw source events to standard format.

    Implemented by openbiliclaw.sources.event_format in main project.
    """

    def normalize(self, raw_event: dict) -> dict:
        """Normalize a raw source event to standard soul format."""
        ...

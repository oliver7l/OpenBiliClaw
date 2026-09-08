"""Protocol interfaces for external dependencies of the discovery engine.

These protocols define the contracts that the main project must implement
and inject into the discovery engine. This decouples obc-discovery from
the main project's implementation details.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Awaitable


@runtime_checkable
class SoulProfileReader(Protocol):
    """Minimal soul profile interface needed by discovery strategies.

    The main project's SoulProfile implements this protocol, allowing
    discovery to read profile data without importing the soul module.
    """

    @property
    def core_traits(self) -> list[str]: ...

    @property
    def cognitive_style(self) -> str: ...


@runtime_checkable
class ImageCache(Protocol):
    """Cover-image fetch/cache interface used by multimodal evaluation.

    The main project's image_cache module implements this protocol.
    """

    async def get_or_fetch_cover_bytes(self, url: str) -> tuple[bytes, str]: ...


class CoverFetchError(Exception):
    """Raised when a cover image cannot be fetched."""


@runtime_checkable
class CandidateStore(Protocol):
    """Storage interface for discovery candidates.

    The main project's Database implements this protocol.
    """

    async def write_candidates(self, candidates: list[dict]) -> int: ...

    async def get_pool_snapshot(self, pool_key: str = "") -> dict: ...


@runtime_checkable
class NegativeExemplarStore(Protocol):
    """Storage interface for negative exemplar queries."""

    def recent_negative_exemplars(
        self,
        database: object,
        *,
        limit: int = 50,
        hours: int = 24,
    ) -> list[dict[str, object]]: ...


@runtime_checkable
class SupportsAdapterRegistry(Protocol):
    """Platform adapter registry interface."""

    def get(self, platform: str) -> object: ...

    def all(self) -> list[object]: ...
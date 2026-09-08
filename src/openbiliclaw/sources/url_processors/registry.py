"""URL processor registry — URL pattern matching and processor discovery.

Inspired by Agent-SaveMark's registry pattern: processors register
themselves with URL patterns and priorities, and the registry matches
incoming URLs to the best processor.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.sources.url_processors.base import BaseProcessor

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type[BaseProcessor]] = {}
_PATTERNS: list[tuple[re.Pattern[str], str, int]] = []
_COMPILED = False


def register_processor(cls: type[BaseProcessor]) -> type[BaseProcessor]:
    """Decorator to register a processor class.

    Args:
        cls: The processor class to register.

    Returns:
        The registered class (unchanged).

    """
    global _COMPILED
    name = cls.__name__
    _REGISTRY[name] = cls

    for pattern in cls.url_patterns:
        compiled = re.compile(pattern, re.IGNORECASE)
        _PATTERNS.append((compiled, name, cls.priority))

    # Re-sort by priority (highest first)
    _PATTERNS.sort(key=lambda x: x[2], reverse=True)
    _COMPILED = True

    logger.debug(
        "Registered URL processor: %s (priority=%d, patterns=%d)",
        name,
        cls.priority,
        len(cls.url_patterns),
    )
    return cls


def match_processor(url: str) -> BaseProcessor:
    """Find the best processor for a URL. Falls back to GenericURLProcessor.

    Args:
        url: The URL to match.

    Returns:
        An instance of the best matching processor.

    Raises:
        ValueError: If no processor is found and GenericURLProcessor is not registered.

    """
    for compiled_pattern, name, _priority in _PATTERNS:
        if compiled_pattern.search(url):
            logger.debug("URL %s matched processor %s", url, name)
            return _REGISTRY[name]()

    # Fallback to generic
    if "GenericURLProcessor" in _REGISTRY:
        logger.debug("URL %s fell back to GenericURLProcessor", url)
        return _REGISTRY["GenericURLProcessor"]()

    raise ValueError(f"No processor found for URL: {url}")


def get_processor(name: str) -> BaseProcessor:
    """Get a processor by class name.

    Args:
        name: The processor class name.

    Returns:
        An instance of the processor.

    Raises:
        KeyError: If the processor is not found.

    """
    if name not in _REGISTRY:
        raise KeyError(f"Processor not found: {name}")
    return _REGISTRY[name]()


def list_processors() -> list[dict[str, object]]:
    """List all registered processors with their metadata.

    Returns:
        List of dicts with name, source_type, priority, and url_patterns.

    """
    result = []
    for name, cls in _REGISTRY.items():
        result.append(
            {
                "name": name,
                "source_type": cls.source_type,
                "source_name": cls.source_name,
                "priority": cls.priority,
                "url_patterns": cls.url_patterns,
            }
        )
    return result


def get_all_source_types() -> list[str]:
    """Get all unique source types from registered processors.

    Returns:
        List of source type identifiers.

    """
    return sorted({cls.source_type for cls in _REGISTRY.values()})

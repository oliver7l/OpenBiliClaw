"""Reading-library helpers: deterministic (rule / profile-keyword) auto-tagging."""

from __future__ import annotations

from .tags import (
    char_bigrams,
    extract_hashtags,
    generate_tags,
    jaccard,
    merge_tag_lists,
    similarity,
    tag_set,
)

__all__ = [
    "extract_hashtags",
    "generate_tags",
    "merge_tag_lists",
    "similarity",
    "tag_set",
    "char_bigrams",
    "jaccard",
]

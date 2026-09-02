"""Deterministic auto-tagging for the reading library (``articles``).

Pure, dependency-free, zero-LLM helpers used to derive lightweight tags from
an article's text by matching the user's weighted interest keywords (from the
soul profile) and recovering inline ``#hashtags``. Kept side-effect free so it
can run at import time, in a backfill, or in tests.
"""

from __future__ import annotations

import re

# ``#tag`` bodies, allowing CJK. Leading ``#`` stripped from the capture.
_HASHTAG_RE = re.compile(r"#([0-9A-Za-z_\u4e00-\u9fff][0-9A-Za-z_·\u4e00-\u9fff-]*)")

_MAX_BODY_CHARS = 2000
_MIN_KEYWORD_LEN = 2  # avoid 1-char keywords causing substring false positives


def extract_hashtags(text: str, *, limit: int = 8) -> list[str]:
    """Pull ``#hashtags`` out of ``text`` (deduped, order-preserving, no ``#``)."""
    seen: set[str] = set()
    tags: list[str] = []
    for m in _HASHTAG_RE.finditer(text or ""):
        body = m.group(1).strip("·-")
        if body and body.lower() not in seen:
            seen.add(body.lower())
            tags.append(body)
        if len(tags) >= limit:
            break
    return tags


def merge_tag_lists(existing: list[str], new: list[str], *, cap: int | None = None) -> list[str]:
    """Union ``existing`` + ``new`` preserving order, case-insensitive dedup.

    ``existing`` comes first (source-provided tags win precedence); ``new`` only
    contributes labels not already present. Optional ``cap`` truncates the tail.
    """
    out: list[str] = []
    seen: set[str] = set()
    for tag in (*existing, *new):
        text = str(tag).strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        out.append(text)
    if cap is not None and len(out) > cap:
        out = out[:cap]
    return out


def generate_tags(
    *,
    title: str = "",
    summary: str = "",
    content_text: str = "",
    interest_keywords: list[tuple[str, float]],
    existing: list[str] | None = None,
    max_new: int = 5,
    min_weight: float = 0.15,
    include_hashtags: bool = True,
) -> list[str]:
    """Derive *new* tags for one article.

    Two deterministic signals, merged and capped:

    1. **Interest keywords** — profile terms (``(name, weight)``) whose text
       appears in the article. A title hit outranks a body hit; within a tier,
       higher weight wins. Terms below ``min_weight`` or shorter than
       ``_MIN_KEYWORD_LEN`` are ignored.
    2. **Hashtags** — inline ``#topic`` in title/content, used to top up after
       keywords (only when ``include_hashtags``).

    Returns only tags not already in ``existing`` (case-insensitive), so callers
    can pass the result straight into :func:`merge_tag_lists`. Empty list when
    nothing matches (e.g. cold profile) — never invents labels.
    """
    existing = existing or []
    existing_norm = {str(t).strip().lower() for t in existing if str(t).strip()}

    title_low = (title or "").lower()
    body_low = f"{summary or ''} {content_text or ''}"[:_MAX_BODY_CHARS].lower()

    # --- keyword matches, tiered by where they hit ---
    matches: list[tuple[int, float, str]] = []  # (tier, weight, name) — higher tier = better
    for name, weight in interest_keywords:
        clean = str(name).strip()
        if len(clean) < _MIN_KEYWORD_LEN or weight < min_weight:
            continue
        low = clean.lower()
        if low in title_low:
            tier = 2
        elif low in body_low:
            tier = 1
        else:
            continue
        matches.append((tier, float(weight), clean))

    # title-hit first, then by weight, then alphabetical for determinism
    matches.sort(key=lambda m: (-m[0], -m[1], m[2]))
    ranked: list[str] = [name for _, _, name in matches]

    if include_hashtags:
        ranked.extend(extract_hashtags(f"{title or ''} {content_text or ''}", limit=max_new))

    new_tags = [t for t in ranked if t.lower() not in existing_norm]
    # de-dup the *new* list itself while preserving order
    out: list[str] = []
    seen: set[str] = set()
    for tag in new_tags:
        if tag.lower() in seen:
            continue
        seen.add(tag.lower())
        out.append(tag)
        if len(out) >= max_new:
            break
    return out


# ---------------------------------------------------------------------------
# Deterministic similarity (find-similar) — tag overlap + CJK-friendly bigrams
# ---------------------------------------------------------------------------

_KEEP_RE = re.compile(r"[0-9A-Za-z_\u4e00-\u9fff]+")


def tag_set(tags: object) -> set[str]:
    """Normalize any tag iterable (list / JSON string tolerated) to a lower set."""
    if isinstance(tags, str):
        try:
            import json as _json

            tags = _json.loads(tags)
        except Exception:
            tags = []
    if not isinstance(tags, (list, tuple, set)):
        return set()
    return {str(t).strip().lower() for t in tags if str(t).strip()}


def char_bigrams(text: str) -> set[str]:
    """Character bigrams over alphanumeric+CJK-only lowercase text.

    CJK has no word delimiters, so bigrams give a reasonable fuzzy overlap for
    titles without a segmenter.
    """
    joined = "".join(_KEEP_RE.findall((text or "").lower()))
    if len(joined) < 2:
        return {joined} if joined else set()
    return {joined[i : i + 2] for i in range(len(joined) - 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    """|A∩B| / |A∪B|, 0 when both empty."""
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def similarity(
    title_a: str,
    tags_a: object,
    title_b: str,
    tags_b: object,
    *,
    tag_weight: float = 0.7,
    title_weight: float = 0.3,
) -> float:
    """Blend of tag-set Jaccard and title-bigram Jaccard, in ``[0, 1]``."""
    tag_sim = jaccard(tag_set(tags_a), tag_set(tags_b))
    title_sim = jaccard(char_bigrams(title_a), char_bigrams(title_b))
    return tag_weight * tag_sim + title_weight * title_sim

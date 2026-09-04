"""Deterministic ranking & diversity metrics for offline evaluation.

All functions are pure and unit-testable. Input convention:
``ranked_labels`` is a list of 0/1 in the order the engine ranked them
(a 1 means "user genuinely liked / consumed this item").
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# ── Ranking (relevance) metrics ────────────────────────────────────────


def hr_at_k(ranked_labels: Sequence[int], k: int) -> float:
    """HitRate@K: did the engine surface at least one liked item in top-K."""
    if k <= 0 or not ranked_labels:
        return 0.0
    return 1.0 if any(ranked_labels[:k]) else 0.0


def _dcg(rels: Sequence[float], k: int | None = None) -> float:
    k = min(k or len(rels), len(rels))
    s = 0.0
    for i, r in enumerate(rels[:k], start=1):
        s += (2.0**r - 1.0) / math.log2(i + 1)
    return s


def ndcg_at_k(ranked_labels: Sequence[int], k: int) -> float:
    """NDCG@K with binary relevance (liked=1)."""
    ranked = list(ranked_labels)
    k = min(k, len(ranked))
    if k == 0:
        return 0.0
    rels = [float(x) for x in ranked[:k]]
    dcg = _dcg(rels)
    idcg = _dcg(sorted(rels, reverse=True))
    return dcg / idcg if idcg > 0 else 0.0


def mrr(ranked_labels: Sequence[int]) -> float:
    """Mean Reciprocal Rank: reciprocal rank of the first liked item."""
    for i, label in enumerate(ranked_labels, start=1):
        if label:
            return 1.0 / i
    return 0.0


def auc(labels: Sequence[int], scores: Sequence[float]) -> float:
    """Probability a random liked item outscores a random non-liked one.

    Direct pairwise counting (equivalent to the Mann-Whitney U statistic),
    robust to None scores (treated as losing every comparison). O(P·N) which
    is fine for unit sizes of tens. Returns NaN when either class is empty.
    """
    labels = list(labels)
    scores = list(scores)
    if len(labels) != len(scores):
        raise ValueError("labels and scores must have equal length")
    pos = [s for s, lab in zip(scores, labels, strict=True) if lab]
    neg = [s for s, lab in zip(scores, labels, strict=True) if not lab]
    if not pos or not neg:
        return float("nan")

    def _key(s: float | None) -> float:
        return -float("inf") if s is None else float(s)

    wins = 0
    ties = 0
    for p in pos:
        for q in neg:
            if _key(p) > _key(q):
                wins += 1
            elif _key(p) == _key(q):
                ties += 1
    total = len(pos) * len(neg)
    return (wins + 0.5 * ties) / total


# ── Diversity / coverage metrics (deterministic, embedding-free) ───────


def topic_coverage(topics: Sequence[str]) -> int:
    """Number of distinct topic groups covered by a ranked list."""
    return len({t for t in topics if t})


def topic_ils(topics: Sequence[str]) -> float:
    """Intra-list similarity approximated by topic-group overlap (0..1).

    Deterministic and cheap; a high value means the list is topically
    redundant. Embedding-based ILS is a documented M2 upgrade.
    """
    n = len(topics)
    if n < 2:
        return 0.0
    pairs = 0
    same = 0
    for i in range(n):
        ti = topics[i]
        if not ti:
            continue
        for j in range(i + 1, n):
            tj = topics[j]
            pairs += 1
            if tj and ti == tj:
                same += 1
    return same / pairs if pairs else 0.0


def style_coverage(styles: Sequence[str]) -> int:
    """Number of distinct style keys covered by a ranked list."""
    return len({s for s in styles if s})

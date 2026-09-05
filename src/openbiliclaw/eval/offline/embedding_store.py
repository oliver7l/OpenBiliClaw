"""Embedding store for offline evaluation diversity/novelty metrics.

Loads pre-computed content embeddings from ``embedding_cache.db`` and provides
a simple lookup interface keyed by text (title / description). Used by the
offline-eval runner to compute embedding-level ILS and novelty when the
``--embedding-metrics`` flag is enabled.

The cache key in ``embedding_cache.db`` is the raw text (not a content_key),
so lookups use the candidate's ``title`` field. Misses are normal (not every
candidate has been embedded) and simply skip that item from embedding metrics.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

try:
    import numpy as np

    _HAVE_NUMPY = True
except ImportError:  # pragma: no cover - numpy is a declared dependency
    _HAVE_NUMPY = False


class EmbeddingStore:
    """Read-only embedding cache with in-memory lookup cache.

    Parameters
    ----------
    db_path:
        Path to ``embedding_cache.db``.
    model:
        Embedding model name to filter on (default ``"bge-m3"``).
    """

    def __init__(self, db_path: str, *, model: str = "bge-m3") -> None:
        self._db_path = db_path
        self._model = model
        self._cache: dict[str, np.ndarray] = {}
        self._misses: set[str] = set()
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> EmbeddingStore:
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def get(self, text: str) -> np.ndarray | None:
        """Look up a single text embedding.

        Returns ``None`` on cache miss (including previously-seen misses).
        Raises ``RuntimeError`` if numpy is unavailable.
        """
        if not _HAVE_NUMPY:
            raise RuntimeError("numpy is required for embedding metrics")
        if not text:
            return None
        if text in self._misses:
            return None
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
        row = self._conn.execute(
            "SELECT vector, dimension FROM embedding_cache WHERE text_key = ? AND model = ?",
            (text, self._model),
        ).fetchone()
        if row is None:
            self._misses.add(text)
            return None
        vec = np.frombuffer(bytes(row["vector"]), dtype=np.float32).copy()
        dim = int(row["dimension"] or 0)
        if dim and vec.size != dim:
            vec = vec[:dim]
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm
        self._cache[text] = vec
        return vec

    def get_batch(self, texts: Sequence[str]) -> dict[str, np.ndarray]:
        """Batch lookup; returns only hits (misses omitted)."""
        result: dict[str, np.ndarray] = {}
        for text in texts:
            vec = self.get(text)
            if vec is not None:
                result[text] = vec
        return result

    def coverage(self, texts: Iterable[str]) -> tuple[int, int]:
        """Return ``(hits, total)`` for a set of texts (for reporting)."""
        total = 0
        hits = 0
        for text in texts:
            if not text:
                continue
            total += 1
            if self.get(text) is not None:
                hits += 1
        return hits, total


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two L2-normalised vectors.

    Both inputs are expected to be unit vectors (``EmbeddingStore.get``
    normalises on load). Falls back to raw dot-product if norms are zero.
    """
    if not _HAVE_NUMPY:
        raise RuntimeError("numpy is required for cosine similarity")
    return float(np.dot(a, b))


def embedding_ils(vectors: Sequence[np.ndarray]) -> float:
    """Embedding-level Intra-List Similarity (0..1).

    Mean pairwise cosine similarity across all pairs in the ranked list.
    A high value means the list is semantically redundant (low diversity).
    Returns 0.0 for fewer than 2 vectors.
    """
    n = len(vectors)
    if n < 2:
        return 0.0
    total = 0.0
    pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            total += cosine_similarity(vectors[i], vectors[j])
            pairs += 1
    return total / pairs if pairs else 0.0


def embedding_novelty(
    vectors: Sequence[np.ndarray],
    history_vectors: Sequence[np.ndarray],
) -> float:
    """Embedding-level novelty (0..1).

    Mean distance (1 - cosine_sim) between each list item and the user's
    history embeddings. A high value means the list contains content the
    user has not previously consumed (break-out effect). Returns 0.0 when
    either side is empty.
    """
    if not vectors or not history_vectors:
        return 0.0
    total = 0.0
    count = 0
    for vec in vectors:
        max_sim = max((cosine_similarity(vec, h) for h in history_vectors), default=0.0)
        total += 1.0 - max_sim
        count += 1
    return total / count if count else 0.0

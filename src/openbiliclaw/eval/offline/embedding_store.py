"""Embedding store for offline evaluation diversity/novelty metrics.

Loads pre-computed content embeddings from ``embedding_cache.db`` and provides
a simple lookup interface keyed by text (title / description). Used by the
offline-eval runner to compute embedding-level ILS and novelty when the
``--embedding-metrics`` flag is enabled.

The cache key in ``embedding_cache.db`` is the raw text (title + description +
tags, not a content_key), so lookups use the candidate's ``title`` field with
a prefix-match strategy (the embedding key typically starts with the title).
This achieves ~98% coverage on real data vs ~16% for exact match. Misses are
normal and simply skip that item from embedding metrics.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Literal

from openbiliclaw.storage.database import open_db_conn

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

try:
    import numpy as np

    _HAVE_NUMPY = True
except ImportError:  # pragma: no cover - numpy is a declared dependency
    _HAVE_NUMPY = False

MatchMode = Literal["exact", "prefix", "contains"]

# embedding_cache encoding=1 BLOB format: 12-byte header + float32 vector
# header: b"OBLV" (4) + version (2) + encoding (2) + dimension (4, little-endian)
_OBLV_HEADER_SIZE = 12


def _parse_vector(raw: object, encoding: int, dimension: int) -> np.ndarray | None:
    """Parse a vector from embedding_cache according to its encoding.

    - encoding=0: JSON array string (legacy)
    - encoding=1: custom BLOB with 12-byte OBLV header + float32 vector
    """
    if not _HAVE_NUMPY:
        return None
    if encoding == 0:
        # Legacy JSON format
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            parsed = json.loads(raw)  # type: ignore[arg-type]
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(parsed, list):
            return None
        return np.asarray(parsed, dtype=np.float32)
    if encoding == 1:
        # Custom OBLV binary format
        if isinstance(raw, str):
            raw = raw.encode("latin-1")
        if not isinstance(raw, bytes) or len(raw) < _OBLV_HEADER_SIZE + 4:
            return None
        # Skip the 12-byte header, parse remaining as float32
        vec_data = raw[_OBLV_HEADER_SIZE:]
        try:
            vec = np.frombuffer(vec_data, dtype=np.float32).copy()
        except ValueError:
            return None
        if dimension and vec.size != dimension:
            vec = vec[:dimension]
        return vec
    return None


class EmbeddingStore:
    """Read-only embedding cache with in-memory lookup cache.

    Parameters
    ----------
    db_path:
        Path to ``embedding_cache.db``.
    model:
        Embedding model name to filter on (default ``"bge-m3"``).
    match_mode:
        How to match candidate titles to embedding keys:
        - ``"exact"``: title == text_key (low coverage, ~16%)
        - ``"prefix"``: text_key starts with title (high coverage, ~98%, default)
        - ``"contains"``: text_key contains title (same as prefix for most cases)

    """

    def __init__(
        self,
        db_path: str,
        *,
        model: str = "bge-m3",
        match_mode: MatchMode = "prefix",
    ) -> None:
        self._db_path = db_path
        self._model = model
        self._match_mode: MatchMode = match_mode
        self._cache: dict[str, np.ndarray] = {}
        self._misses: set[str] = set()
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> EmbeddingStore:
        self._conn = open_db_conn(self._db_path)
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _query_row(self, text: str) -> sqlite3.Row | None:
        """Query the embedding cache according to match_mode."""
        if self._conn is None:
            self._conn = open_db_conn(self._db_path)
        if self._match_mode == "exact":
            return self._conn.execute(
                "SELECT vector, dimension, encoding FROM embedding_cache "
                "WHERE text_key = ? AND model = ?",
                (text, self._model),
            ).fetchone()
        if self._match_mode == "prefix":
            # Prefix match: take the shortest matching key (most likely pure title)
            return self._conn.execute(
                "SELECT vector, dimension, encoding FROM embedding_cache "
                "WHERE text_key LIKE ? AND model = ? ORDER BY length(text_key) ASC LIMIT 1",
                (text + "%", self._model),
            ).fetchone()
        # contains
        return self._conn.execute(
            "SELECT vector, dimension, encoding FROM embedding_cache "
            "WHERE text_key LIKE ? AND model = ? ORDER BY length(text_key) ASC LIMIT 1",
            ("%" + text + "%", self._model),
        ).fetchone()

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
        row = self._query_row(text)
        if row is None:
            self._misses.add(text)
            return None
        vec = _parse_vector(row["vector"], int(row["encoding"] or 0), int(row["dimension"] or 0))
        if vec is None:
            self._misses.add(text)
            return None
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

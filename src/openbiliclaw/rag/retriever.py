"""Retriever for the article RAG index.

This module turns the user's crawled reading library (``articles`` rows that
carry real ``content_text``) into a queryable knowledge base. The index is
built offline by ``scripts/build_article_rag.py`` into a *separate* SQLite
database (``data/article_rag.db``) so it never bloats the operational DB and
never collides with the recommendation-side 200-char embedding cache
(``EmbeddingService`` truncates keys to 200 chars + lowercases — reusing it for
full-text chunks would corrupt retrieval).

At request time this retriever:
1. embeds the user's query with the same local model the app uses for
   recommendations (Ollama ``bge-m3``, 1024-dim) — adding the bge query
   instruction so the question embedding matches passage embeddings;
2. scans the in-memory chunk matrix for the top-K cosine neighbours. When
   numpy is available the scan is a single vectorised ``matrix @ query``
   matmul (~15 ms for ~32k chunks); otherwise it falls back to a pure-Python
   loop (slower but dependency-free);
3. returns a formatted, citation-bearing context block that the chat handler
   splices into the dialogue prompt.

The whole thing is a no-op until the index exists, so deploying it before the
backfill finishes is safe.
"""

from __future__ import annotations

import array
import json
import math
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

try:
    import numpy as np

    _HAVE_NUMPY = True
except ImportError:  # pragma: no cover - numpy is a declared dependency
    np = None  # type: ignore[assignment]
    _HAVE_NUMPY = False

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore

import logging

import httpx

logger = logging.getLogger("openbiliclaw.rag.retriever")

# bge-m3 expects this prefix on the *query* side only (passages are indexed
# bare). Without it retrieval recall drops noticeably.
_BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_PATH = _PROJECT_ROOT / "config.toml"
_RAG_DB_PATH = _PROJECT_ROOT / "data" / "article_rag.db"

_DEFAULT_EMBED = {
    "provider": "ollama",
    "model": "bge-m3",
    "base_url": "http://localhost:11434/v1",
    "api_key": "",
    "output_dimensionality": 1024,
}

# How many chunk chars to show inside a retrieved citation.
_MAX_CITATION_CHARS = 420


def _load_embed_config() -> dict[str, Any]:
    cfg = dict(_DEFAULT_EMBED)
    if tomllib is None or not _CONFIG_PATH.exists():
        return cfg
    try:
        with open(_CONFIG_PATH, "rb") as fh:
            data = tomllib.load(fh)
        emb = (data.get("llm") or {}).get("embedding") or {}
        for key in ("provider", "model", "base_url", "api_key", "output_dimensionality"):
            if emb.get(key) not in (None, ""):
                cfg[key] = emb[key]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read [llm.embedding] from config.toml: %s", exc)
    return cfg


class ArticleRagRetriever:
    """Lazy, in-memory retriever over ``article_rag.db``."""

    def __init__(
        self,
        rag_db_path: Path | None = None,
        embed_cfg: dict[str, Any] | None = None,
    ) -> None:
        self._rag_db = Path(rag_db_path or _RAG_DB_PATH)
        self._embed = embed_cfg or _load_embed_config()
        self._dim = int(self._embed.get("output_dimensionality") or 1024)
        self._lock = threading.RLock()
        # numpy path: (n, dim) float32 ndarray; fallback path: flat array.array.
        self._matrix: Any = None
        self._ids: list[int] = []
        self._citations: list[dict[str, Any]] = []
        # numpy path: np.ndarray of row norms; fallback path: list[float].
        self._norms: Any = []
        self._loaded_at_mtime: float = -1.0
        self._loaded_count: int = -1
        # Bounded cache of query -> embedding, so repeat/simultaneous turns
        # (e.g. chat-turns POST + pending GET both grounding the same message)
        # don't pay the embedding round-trip more than once.
        self._query_embeddings: OrderedDict[str, list[float]] = OrderedDict()
        self._max_query_embeddings = 128

    # ------------------------------------------------------------------ #
    # Public state
    # ------------------------------------------------------------------ #
    @property
    def ready(self) -> bool:
        """True once the index has at least one chunk loaded."""
        with self._lock:
            return self._matrix is not None and len(self._ids) > 0

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._ids)

    # ------------------------------------------------------------------ #
    # Index loading (cached, revalidated on db change)
    # ------------------------------------------------------------------ #
    def _ensure_loaded(self) -> bool:
        if not self._rag_db.exists():
            return False
        try:
            mtime = self._rag_db.stat().st_mtime
        except OSError:
            return False
        with self._lock:
            if self._matrix is not None and self._loaded_at_mtime == mtime:
                return True
            self._load_locked()
            return self._matrix is not None and len(self._ids) > 0

    def _load_locked(self) -> None:
        import sqlite3

        conn = sqlite3.connect(str(self._rag_db), timeout=10.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            row = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='chunks'"
            ).fetchone()
            if row is None or row[0] == 0:
                return
            total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            if total == 0:
                return
            # ``source_table`` was added when the index started covering
            # read_archive too; tolerate older indexes built without it.
            cols = {r[1] for r in conn.execute("PRAGMA table_info(chunks)").fetchall()}
            src_col = "source_table" if "source_table" in cols else "'' AS source_table"
            ids: list[int] = []
            citations: list[dict[str, Any]] = []
            rows: list[list[float]] = []
            norms: list[float] = []
            query = (
                "SELECT id, article_id, text, title, url, source_name, author, vector, "
                f"{src_col} FROM chunks ORDER BY id"
            )
            for (
                cid,
                article_id,
                text,
                title,
                url,
                source_name,
                author,
                vec_raw,
                source_table,
            ) in conn.execute(query):
                # ``vector`` is a packed float32 BLOB on current indexes; legacy
                # indexes stored JSON text. Accept both so a pre-migration DB
                # still loads (the build script converts in place).
                if isinstance(vec_raw, (bytes, bytearray)):
                    try:
                        if _HAVE_NUMPY:
                            vec: Any = np.frombuffer(bytes(vec_raw), dtype=np.float32)
                        else:  # pragma: no cover - numpy is a declared dep
                            arr = array.array("f")
                            arr.frombytes(bytes(vec_raw))
                            vec = arr
                    except (ValueError, TypeError):
                        continue
                else:
                    try:
                        parsed = json.loads(vec_raw)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not isinstance(parsed, list):
                        continue
                    vec = np.asarray(parsed, dtype=np.float32) if _HAVE_NUMPY else parsed
                if len(vec) != self._dim:
                    continue
                ids.append(cid)
                citations.append(
                    {
                        "chunk_id": cid,
                        "article_id": article_id,
                        "source_table": source_table or "articles",
                        "title": title or "",
                        "url": url or "",
                        "source_name": source_name or "",
                        "author": author or "",
                        "text": text or "",
                    }
                )
                rows.append(vec)
                if not _HAVE_NUMPY:  # pragma: no cover - numpy is a declared dep
                    norm = math.sqrt(sum(x * x for x in vec))
                    norms.append(norm if norm > 0 else 1.0)
            if not ids:
                return
            self._ids = ids
            self._citations = citations
            # Local is either a numpy (n, dim) matrix or a flat array.array,
            # picked below based on numpy availability.
            matrix: Any
            if _HAVE_NUMPY:
                # Vectorised build: one C-level copy into a (n, dim) float32
                # matrix, row norms computed in bulk — avoids ~32M Python-level
                # float ops and cuts the cold index load by ~4-5x.
                matrix = np.asarray(rows, dtype=np.float32)
                row_norms = np.linalg.norm(matrix, axis=1)
                row_norms[row_norms == 0.0] = 1.0
                self._matrix = matrix
                self._norms = row_norms
            else:  # pragma: no cover - numpy is a declared dep
                matrix = array.array("f")
                for vec in rows:
                    matrix.extend(float(x) for x in vec)
                self._matrix = matrix
                self._norms = norms
            self._loaded_at_mtime = self._rag_db.stat().st_mtime
            self._loaded_count = len(ids)
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    # Embedding (delegates to the configured provider, local by default)
    # ------------------------------------------------------------------ #
    def embed(self, text: str, *, is_query: bool = False) -> list[float]:
        """Embed ``text`` via the configured embedding provider.

        Returns an empty list on failure so callers degrade gracefully.
        """
        if not text or not text.strip():
            return []
        payload_text = text
        if is_query:
            payload_text = f"{_BGE_QUERY_INSTRUCTION}{text}"
        base_url = str(self._embed.get("base_url") or "").rstrip("/")
        if not base_url:
            return []
        url = f"{base_url}/embeddings"
        api_key = str(self._embed.get("api_key") or "")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        body = {"model": self._embed.get("model"), "input": payload_text}
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            vec = data["data"][0]["embedding"]
            return [float(x) for x in vec]
        except Exception as exc:  # noqa: BLE001
            logger.warning("RAG embed failed: %s", exc)
            return []

    # ------------------------------------------------------------------ #
    # Retrieval
    # ------------------------------------------------------------------ #
    def retrieve_chunks(self, query: str, top_k: int = 4) -> list[dict[str, Any]]:
        """Return the top-K structured hits for ``query`` (empty list if none).

        Cosine scan over the in-memory matrix. With numpy (the default) it is a
        single vectorised ``matrix @ query`` matmul — ~15 ms for ~32k chunks;
        without numpy it falls back to a pure-Python loop. Each hit carries a
        ``score`` plus the citation fields (title/url/author/source_table/
        snippet) so callers can render "referenced N items" in the UI.
        """
        if not query or not query.strip():
            return []
        if not self._ensure_loaded():
            return []
        with self._lock:
            matrix = self._matrix
            ids = self._ids
            citations = self._citations
            norms = self._norms
            dim = self._dim
        if matrix is None or not ids:
            return []

        # Embed once per distinct query; repeat/simultaneous turns reuse it.
        q = self._query_embeddings.get(query)
        if q is None:
            q = self.embed(query, is_query=True)
            if q and len(q) == dim:
                self._query_embeddings[query] = q
                if len(self._query_embeddings) > self._max_query_embeddings:
                    self._query_embeddings.popitem(last=False)
        if not q or len(q) != dim:
            return []
        q_norm = math.sqrt(sum(x * x for x in q))
        if q_norm == 0:
            return []

        n = len(ids)
        if _HAVE_NUMPY:
            scores = matrix @ np.asarray(q, dtype=np.float32)  # (n,)
            sims = scores / (norms * q_norm)
            if top_k >= n:
                order = np.argsort(-sims)[:top_k]
            else:
                order = np.argpartition(-sims, top_k)[:top_k]
                order = order[np.argsort(-sims[order])]
            ranked: list[tuple[float, int]] = [(float(sims[int(i)]), int(i)) for i in order]
        else:  # pragma: no cover - numpy is a declared dep
            scored: list[tuple[float, int]] = []
            for i in range(n):
                base = i * dim
                dot = 0.0
                for j in range(dim):
                    dot += q[j] * matrix[base + j]
                sim = dot / (norms[i] * q_norm)
                scored.append((sim, i))
            scored.sort(reverse=True)
            ranked = scored[:top_k]

        hits: list[dict[str, Any]] = []
        for sim, i in ranked:
            cit = dict(citations[i])
            cit["score"] = round(float(sim), 4)
            snippet = cit["text"].strip().replace("\n", " ")
            if len(snippet) > _MAX_CITATION_CHARS:
                snippet = snippet[:_MAX_CITATION_CHARS] + "…"
            cit["snippet"] = snippet
            hits.append(cit)
        return hits

    @staticmethod
    def format_context(hits: list[dict[str, Any]]) -> str:
        """Render hits as the prompt-injected context block."""
        if not hits:
            return ""
        blocks: list[str] = []
        for rank, cit in enumerate(hits, 1):
            head = f"{rank}. 《{cit.get('title', '')}》"
            if cit.get("author"):
                head += f" · {cit['author']}"
            if cit.get("source_name"):
                head += f" · 来源：{cit['source_name']}"
            head += f"（相关度 {float(cit.get('score', 0)):.2f}）"
            line = f"{head}\n   {cit.get('snippet', '')}"
            if cit.get("url"):
                line += f"\n   链接：{cit['url']}"
            blocks.append(line)
        return "【参考我收藏的内容】\n" + "\n".join(blocks)

    def retrieve(self, query: str, top_k: int = 4) -> str:
        """Return a formatted context block for ``query``, or '' if none.

        Cosine scan over the in-memory matrix (numpy-vectorised when available).
        """
        return self.format_context(self.retrieve_chunks(query, top_k=top_k))


_retriever_singleton: ArticleRagRetriever | None = None
_retriever_lock = threading.Lock()


def get_retriever() -> ArticleRagRetriever:
    """Process-wide retriever singleton."""
    global _retriever_singleton
    if _retriever_singleton is None:
        with _retriever_lock:
            if _retriever_singleton is None:
                _retriever_singleton = ArticleRagRetriever()
    return _retriever_singleton

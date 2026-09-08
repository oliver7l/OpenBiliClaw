"""Unit tests for the article-RAG retriever.

Covers the numpy-vectorised scan path, the pure-Python fallback path and their
equivalence, top-K ordering, and the bounded query-embedding cache. All tests
run hermetically: the embed method is stubbed, so no HTTP/embedding provider is
touched and the index is a tiny in-memory-on-disk SQLite file.
"""

from __future__ import annotations

import array
import json
import math
import sqlite3
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.rag import retriever as rag_retriever
from openbiliclaw.rag.retriever import ArticleRagRetriever

if TYPE_CHECKING:
    from pathlib import Path

_DIM = 4


def _build_index(
    db_path: Path,
    rows: list[tuple[int, str, list[float]]],
    *,
    vector_column: str = "BLOB",
) -> None:
    """Create a minimal ``chunks`` table (same schema as the real RAG index)."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE chunks ("
            "id INTEGER PRIMARY KEY, article_id INTEGER, text TEXT, title TEXT, "
            f"url TEXT, source_name TEXT, author TEXT, vector {vector_column} NOT NULL, "
            "source_table TEXT)"
        )
        for cid, title, vec in rows:
            if vector_column == "BLOB":
                payload: str | bytes = array.array("f", vec).tobytes()
            else:  # legacy JSON-text encoding
                payload = json.dumps(vec)
            conn.execute(
                "INSERT INTO chunks (id, article_id, text, title, url, source_name, "
                "author, vector, source_table) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cid,
                    cid,
                    f"chunk body {cid}",
                    title,
                    f"https://example.com/{cid}",
                    "test-src",
                    "tester",
                    payload,
                    "articles",
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _make_retriever(db_path: Path) -> ArticleRagRetriever:
    return ArticleRagRetriever(
        rag_db_path=db_path,
        embed_cfg={
            "provider": "fake",
            "model": "fake",
            "base_url": "http://fake.invalid",
            "api_key": "",
            "output_dimensionality": _DIM,
        },
    )


def _sample_rows() -> list[tuple[int, str, list[float]]]:
    # Unit vectors in 4-d. Query [1,0,0,0] is closest to row 0 (sim 1.0),
    # then row 1 (sim ≈ 0.9939), then row 2 (sim 0).
    return [
        (1, "目标文章", [1.0, 0.0, 0.0, 0.0]),
        (2, "次近文章", [0.9, 0.1, 0.0, 0.0]),
        (3, "无关文章", [0.0, 1.0, 0.0, 0.0]),
    ]


def _stub_query_embed(retr: ArticleRagRetriever, counter: list[int] | None = None) -> None:
    """Replace the HTTP embed call with a deterministic unit vector."""

    def _fake_embed(text: str, *, is_query: bool = False) -> list[float]:
        if counter is not None:
            counter[0] += 1
        return [1.0, 0.0, 0.0, 0.0]

    retr.embed = _fake_embed  # type: ignore[method-assign]


def test_retrieve_top_k_orders_by_cosine(tmp_path: Path) -> None:
    db = tmp_path / "rag.db"
    _build_index(db, _sample_rows())
    retr = _make_retriever(db)
    _stub_query_embed(retr)

    hits = retr.retrieve_chunks("任意查询", top_k=2)

    assert [h["chunk_id"] for h in hits] == [1, 2]
    assert hits[0]["score"] == pytest.approx(1.0, abs=1e-4)
    # Row 1 is [0.9, 0.1, 0, 0] → cosine similarity 0.9 / ||row1|| ≈ 0.9939.
    assert hits[1]["score"] == pytest.approx(0.9 / math.sqrt(0.82), abs=1e-4)
    assert hits[0]["title"] == "目标文章"
    assert hits[0]["source_table"] == "articles"
    assert hits[0]["snippet"].startswith("chunk body 1")


def test_fallback_pure_python_matches_numpy(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "rag.db"
    _build_index(db, _sample_rows())

    numpy_hits = retr_retrieve(db)
    # Force the pure-Python scan + flat array load, then re-query.
    monkeypatch.setattr(rag_retriever, "_HAVE_NUMPY", False)
    fallback_hits = retr_retrieve(db)

    assert [h["chunk_id"] for h in numpy_hits] == [h["chunk_id"] for h in fallback_hits]
    assert [h["score"] for h in numpy_hits] == [h["score"] for h in fallback_hits]
    assert fallback_hits[0]["title"] == "目标文章"


def retr_retrieve(db: Path) -> list[dict]:
    retr = _make_retriever(db)
    _stub_query_embed(retr)
    return retr.retrieve_chunks("任意查询", top_k=3)


def test_query_embedding_is_cached(tmp_path: Path) -> None:
    db = tmp_path / "rag.db"
    _build_index(db, _sample_rows())
    retr = _make_retriever(db)
    counter: list[int] = [0]
    _stub_query_embed(retr, counter)

    retr.retrieve_chunks("同一句话", top_k=2)
    retr.retrieve_chunks("同一句话", top_k=2)
    retr.retrieve_chunks("同一句话", top_k=2)

    # Three retrieves of the same query → embed called exactly once.
    assert counter == [1]


def test_retrieve_returns_empty_without_index_or_query(tmp_path: Path) -> None:
    retr = _make_retriever(tmp_path / "missing.db")
    _stub_query_embed(retr)

    assert retr.retrieve_chunks("") == []
    assert retr.retrieve_chunks("   ") == []
    assert retr.retrieve_chunks("没有索引的查询") == []
    assert retr.ready is False


def test_legacy_json_text_index_still_loads(tmp_path: Path) -> None:
    """Pre-migration indexes stored vectors as JSON text; they must keep working.

    The BLOB retriever path and the JSON fallback must return identical hits so
    a not-yet-migrated DB is safe to serve.
    """
    blob_db = tmp_path / "blob.db"
    json_db = tmp_path / "legacy.db"
    _build_index(blob_db, _sample_rows())
    _build_index(json_db, _sample_rows(), vector_column="TEXT")

    blob_hits = retr_retrieve(blob_db)
    legacy_hits = retr_retrieve(json_db)

    assert [h["chunk_id"] for h in blob_hits] == [h["chunk_id"] for h in legacy_hits]
    assert [h["score"] for h in blob_hits] == [h["score"] for h in legacy_hits]
    assert legacy_hits[0]["title"] == "目标文章"

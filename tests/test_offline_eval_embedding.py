"""Unit tests for embedding-level offline eval metrics.

Covers EmbeddingStore lookup, cosine similarity, embedding ILS/novelty, and
runner integration with an in-memory sqlite embedding cache.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import numpy as np
import pytest

if TYPE_CHECKING:
    from pathlib import Path

from openbiliclaw.eval.offline.embedding_store import (
    EmbeddingStore,
    cosine_similarity,
    embedding_ils,
    embedding_novelty,
)
from openbiliclaw.eval.offline.ground_truth import CandidateItem
from openbiliclaw.eval.offline.runner import UnitResult, run_offline_eval
from openbiliclaw.eval.offline.scenario import EvalUnit, build_units


def _make_embedding_db(tmp_path: Path, entries: dict[str, np.ndarray]) -> Path:
    """Create a temporary embedding_cache.db with the given text→vector entries."""
    db_path = tmp_path / "embedding_cache.db"
    con = sqlite3.connect(str(db_path))
    con.execute(
        """
        CREATE TABLE embedding_cache (
            text_key TEXT NOT NULL,
            model TEXT NOT NULL DEFAULT '',
            vector BLOB NOT NULL,
            encoding INTEGER NOT NULL DEFAULT 1,
            dimension INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL DEFAULT 0,
            last_accessed_at INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (text_key, model)
        )
        """
    )
    for text, vec in entries.items():
        norm = np.linalg.norm(vec)
        unit = vec / norm if norm > 0 else vec
        con.execute(
            "INSERT INTO embedding_cache (text_key, model, vector, encoding, dimension) "
            "VALUES (?, ?, ?, 1, ?)",
            (text, "bge-m3", unit.astype(np.float32).tobytes(), unit.size),
        )
    con.commit()
    con.close()
    return db_path


class TestCosineSimilarity:
    def test_identical_vectors(self):
        a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        assert cosine_similarity(a, a) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([0.0, 1.0], dtype=np.float32)
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([-1.0, 0.0], dtype=np.float32)
        assert cosine_similarity(a, b) == pytest.approx(-1.0)


class TestEmbeddingILS:
    def test_empty_list(self):
        assert embedding_ils([]) == 0.0

    def test_single_vector(self):
        v = np.array([1.0, 0.0], dtype=np.float32)
        assert embedding_ils([v]) == 0.0

    def test_identical_vectors_high_ils(self):
        v = np.array([1.0, 0.0], dtype=np.float32)
        assert embedding_ils([v, v, v]) == pytest.approx(1.0)

    def test_orthogonal_vectors_zero_ils(self):
        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([0.0, 1.0], dtype=np.float32)
        assert embedding_ils([a, b]) == pytest.approx(0.0)


class TestEmbeddingNovelty:
    def test_empty_vectors(self):
        h = np.array([1.0, 0.0], dtype=np.float32)
        assert embedding_novelty([], [h]) == 0.0

    def test_empty_history(self):
        v = np.array([1.0, 0.0], dtype=np.float32)
        assert embedding_novelty([v], []) == 0.0

    def test_identical_to_history_low_novelty(self):
        v = np.array([1.0, 0.0], dtype=np.float32)
        assert embedding_novelty([v], [v]) == pytest.approx(0.0)

    def test_orthogonal_to_history_high_novelty(self):
        v = np.array([1.0, 0.0], dtype=np.float32)
        h = np.array([0.0, 1.0], dtype=np.float32)
        assert embedding_novelty([v], [h]) == pytest.approx(1.0)


class TestEmbeddingStore:
    def test_get_hit(self, tmp_path):
        vec = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        db = _make_embedding_db(tmp_path, {"hello": vec})
        with EmbeddingStore(str(db)) as store:
            result = store.get("hello")
            assert result is not None
            assert result.shape == (3,)
            # should be L2-normalised
            assert np.linalg.norm(result) == pytest.approx(1.0)

    def test_get_miss(self, tmp_path):
        db = _make_embedding_db(tmp_path, {"hello": np.array([1.0, 0.0])})
        with EmbeddingStore(str(db)) as store:
            assert store.get("world") is None
            # second miss should use cache
            assert store.get("world") is None

    def test_get_empty_text(self, tmp_path):
        db = _make_embedding_db(tmp_path, {})
        with EmbeddingStore(str(db)) as store:
            assert store.get("") is None

    def test_get_batch(self, tmp_path):
        entries = {
            "a": np.array([1.0, 0.0]),
            "b": np.array([0.0, 1.0]),
            "c": np.array([1.0, 1.0]),
        }
        db = _make_embedding_db(tmp_path, entries)
        with EmbeddingStore(str(db)) as store:
            result = store.get_batch(["a", "b", "missing"])
            assert set(result.keys()) == {"a", "b"}
            assert result["a"].shape == (2,)

    def test_coverage(self, tmp_path):
        entries = {"a": np.array([1.0, 0.0]), "b": np.array([0.0, 1.0])}
        db = _make_embedding_db(tmp_path, entries)
        with EmbeddingStore(str(db)) as store:
            hits, total = store.coverage(["a", "b", "c", ""])
            assert hits == 2
            assert total == 3  # empty text excluded


class TestRunnerEmbeddingIntegration:
    def _make_unit(self) -> EvalUnit:
        candidates = [
            CandidateItem(
                content_key=f"bilibili:BV{i}",
                bvid=f"BV{i}",
                title=f"title_{i}",
                topic_group="tech",
                style_key="tutorial",
                relevance_score=float(i),
                candidate_tier="primary",
                source_platform="bilibili",
                content_url=f"https://bilibili.com/video/BV{i}",
                discovered_at="2026-01-01",
            )
            for i in range(4)
        ]
        return EvalUnit(
            unit_id=0,
            candidates=candidates,
            labels=[1, 0, 1, 0],
            n_positives=2,
            seed=42,
        )

    def test_unit_result_metrics_without_embedding(self):
        unit = self._make_unit()
        result = UnitResult(
            unit_id=0,
            method="engine",
            ranked_keys=[c.content_key for c in unit.candidates],
            ranked_labels=unit.labels,
            ranked_topics=[c.topic_group for c in unit.candidates],
            ranked_styles=[c.style_key for c in unit.candidates],
            ranked_titles=[c.title for c in unit.candidates],
            relevance_scores=[c.relevance_score for c in unit.candidates],
            n_positives=2,
        )
        m = result.metrics(k=2)
        assert "hr@2" in m
        assert "ndcg@2" in m
        assert "embedding_ils" not in m

    def test_unit_result_metrics_with_embedding(self, tmp_path):
        entries = {
            "title_0": np.array([1.0, 0.0]),
            "title_1": np.array([0.0, 1.0]),
            "title_2": np.array([1.0, 1.0]),
            "title_3": np.array([0.5, 0.5]),
        }
        db = _make_embedding_db(tmp_path, entries)
        unit = self._make_unit()
        result = UnitResult(
            unit_id=0,
            method="engine",
            ranked_keys=[c.content_key for c in unit.candidates],
            ranked_labels=unit.labels,
            ranked_topics=[c.topic_group for c in unit.candidates],
            ranked_styles=[c.style_key for c in unit.candidates],
            ranked_titles=[c.title for c in unit.candidates],
            relevance_scores=[c.relevance_score for c in unit.candidates],
            n_positives=2,
        )
        with EmbeddingStore(str(db)) as store:
            m = result.metrics(k=2, embedding_store=store)
        assert "embedding_ils" in m
        assert "embedding_coverage" in m
        assert m["embedding_coverage"] == pytest.approx(1.0)

    def test_run_offline_eval_with_embedding_store(self, tmp_path):
        entries = {f"title_{i}": np.array([float(i), 1.0]) for i in range(8)}
        db = _make_embedding_db(tmp_path, entries)
        candidates = [
            CandidateItem(
                content_key=f"bilibili:BV{i}",
                bvid=f"BV{i}",
                title=f"title_{i}",
                topic_group="tech" if i % 2 == 0 else "food",
                style_key="tutorial",
                relevance_score=float(i % 3),
                candidate_tier="primary",
                source_platform="bilibili",
                content_url=f"https://bilibili.com/video/BV{i}",
                discovered_at="2026-01-01",
            )
            for i in range(8)
        ]
        positive_matched = [(c, None) for c in candidates[:4]]  # type: ignore[list-item]
        negative_pool = candidates[4:]
        units = build_units(
            positive_matched,  # type: ignore[arg-type]
            negative_pool,
            n_units=2,
            positives_per_unit=2,
            unit_size=4,
            seed=42,
        )
        with EmbeddingStore(str(db)) as store:
            result = run_offline_eval(
                units,
                k=2,
                include_random_baseline=False,
                embedding_store=store,
            )
        assert "engine" in result["methods"]
        metrics = result["methods"]["engine"]["metrics"]
        assert "embedding_ils" in metrics
        assert "embedding_coverage" in metrics

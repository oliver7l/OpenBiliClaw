"""Offline evaluation runner.

Given a set of evaluation units, ranks each unit's candidates with the real
recommendation engine's selection logic (``RecommendationEngine.
_select_diversified_batch``, a stateless classmethod) plus a random baseline,
then aggregates ranking + diversity metrics.

Design note: the engine's ``serve()`` always draws from the live candidate
pool, so it cannot score an externally-constructed unit. ``_select_
diversified_batch`` is exactly the sorting core ``serve()`` uses, exposed as a
classmethod — reusing it lets us evaluate the true ranking/diversity logic
without touching production state.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .metrics import auc, hr_at_k, mrr, ndcg_at_k, style_coverage, topic_coverage, topic_ils

if TYPE_CHECKING:
    from .embedding_store import EmbeddingStore
    from .ground_truth import CandidateItem
    from .scenario import EvalUnit

logger = logging.getLogger("eval.offline.runner")


@dataclass
class UnitResult:
    unit_id: int
    method: str
    ranked_keys: list[str]
    ranked_labels: list[int]
    ranked_topics: list[str]
    ranked_styles: list[str]
    ranked_titles: list[str]
    relevance_scores: list[float]
    n_positives: int

    def metrics(self, k: int, *, embedding_store: EmbeddingStore | None = None) -> dict[str, float]:
        result = {
            f"hr@{k}": hr_at_k(self.ranked_labels, k),
            f"ndcg@{k}": ndcg_at_k(self.ranked_labels, k),
            "mrr": mrr(self.ranked_labels),
            "auc": auc(self.ranked_labels, self.relevance_scores),
            "topic_coverage": float(topic_coverage(self.ranked_topics)),
            "topic_ils": topic_ils(self.ranked_topics),
            "style_coverage": float(style_coverage(self.ranked_styles)),
        }
        if embedding_store is not None:
            from .embedding_store import embedding_ils

            vectors = [
                v for v in (embedding_store.get(t) for t in self.ranked_titles) if v is not None
            ]
            if vectors:
                result["embedding_ils"] = embedding_ils(vectors)
                result["embedding_coverage"] = (
                    len(vectors) / len(self.ranked_titles) if self.ranked_titles else 0.0
                )
        return result


def _to_discovered_content(item: CandidateItem, content_id: str | None = None) -> Any:
    """Build a DiscoveredContent for the engine's ranking core."""
    from obc_discovery.engine import DiscoveredContent

    cid = content_id or item.content_key.split(":", 1)[-1]
    return DiscoveredContent(
        bvid=item.bvid or cid,
        content_id=cid,
        source_platform=item.source_platform or "bilibili",
        title=item.title,
        topic_group=item.topic_group,
        style_key=item.style_key,
        relevance_score=item.relevance_score,
        candidate_tier=item.candidate_tier or "primary",
        content_url=item.content_url,
        discovered_at=item.discovered_at,
    )


def _rank_with_engine(
    candidates: list[CandidateItem],
    *,
    limit: int | None = None,
    mmr_embeddings: dict[str, list[float]] | None = None,
) -> list[CandidateItem]:
    """Rank candidates using the engine's real selection core."""
    from openbiliclaw.recommendation.engine import RecommendationEngine

    content = [_to_discovered_content(c) for c in candidates]
    n = len(content)
    ranked = RecommendationEngine._select_diversified_batch(
        content,
        limit=n,
        embeddings=mmr_embeddings,
    )
    # map back via the bare id (content_id == bvid for bilibili content)
    by_id = {c.content_key.split(":", 1)[-1]: c for c in candidates}
    out: list[CandidateItem] = []
    for x in ranked:
        item = by_id.get(x.content_id or x.bvid)
        if item is not None:
            out.append(item)
    return out


async def _rank_with_llm_rerank(
    candidates: list[CandidateItem],
    *,
    llm_service: Any,
    profile: Any,
    top_k: int = 30,
    weight: float = 0.3,
    batch_size: int = 5,
    mmr_embeddings: dict[str, list[float]] | None = None,
) -> list[CandidateItem]:
    """Rank candidates with curator-style scores + LLM semantic rerank + MMR.

    Mirrors the production ``serve()`` pipeline:
    1. Use relevance_score as a curator-score proxy (offline eval has no
       real PoolCurator, so relevance_score is the best available signal).
    2. LLM rerank the top-K candidates against the user profile.
    3. Blend curator + LLM scores.
    4. MMR diversity selection (same as production).

    Returns candidates in ranked order. On any LLM failure, falls back to
    pure relevance-score ranking so the eval never crashes.
    """
    from openbiliclaw.recommendation.engine import RecommendationEngine
    from openbiliclaw.recommendation.llm_reranker import LLMReranker, blend_scores

    content = [_to_discovered_content(c) for c in candidates]
    # Curator-score proxy: relevance_score from discovery evaluation
    curator_scores = {
        (getattr(c, "content_id", "") or getattr(c, "bvid", "")): float(
            getattr(c, "relevance_score", 0.0) or 0.0
        )
        for c in content
    }

    try:
        reranker = LLMReranker(
            llm_service=llm_service,
            batch_size=batch_size,
            top_k=top_k,
            weight=weight,
        )
        llm_scores = await reranker.rerank(
            candidates=content,
            profile=profile,
            top_k=top_k,
            curator_scores=curator_scores,
        )
        if llm_scores:
            score_override = blend_scores(
                curator_scores=curator_scores,
                llm_scores=llm_scores,
                weight=weight,
            )
            logger.info(
                "LLM rerank eval: %d candidates scored, blended %d curator scores",
                len(llm_scores),
                len(score_override),
            )
        else:
            score_override = curator_scores
            logger.info("LLM rerank eval: LLM returned 0 scores, using pure relevance scores")
    except Exception:
        logger.exception("LLM rerank eval failed, falling back to pure relevance scores")
        score_override = curator_scores

    n = len(content)
    ranked = RecommendationEngine._select_diversified_batch(
        content,
        limit=n,
        score_override=score_override,
        embeddings=mmr_embeddings,
    )
    by_id = {c.content_key.split(":", 1)[-1]: c for c in candidates}
    out: list[CandidateItem] = []
    for x in ranked:
        item = by_id.get(x.content_id or x.bvid)
        if item is not None:
            out.append(item)
    return out


def _rank_random(candidates: list[CandidateItem], rng: random.Random) -> list[CandidateItem]:
    ranked = list(candidates)
    rng.shuffle(ranked)
    return ranked


def _to_result(unit: EvalUnit, ranked: list[CandidateItem], method: str) -> UnitResult:
    label_by_key = {c.content_key: lab for c, lab in zip(unit.candidates, unit.labels, strict=True)}
    ranked_keys: list[str] = []
    ranked_labels: list[int] = []
    ranked_topics: list[str] = []
    ranked_styles: list[str] = []
    ranked_titles: list[str] = []
    scores: list[float] = []
    for item in ranked:
        ranked_keys.append(item.content_key)
        ranked_labels.append(label_by_key.get(item.content_key, 0))
        ranked_topics.append(item.topic_group)
        ranked_styles.append(item.style_key)
        ranked_titles.append(item.title)
        scores.append(item.relevance_score)
    return UnitResult(
        unit_id=unit.unit_id,
        method=method,
        ranked_keys=ranked_keys,
        ranked_labels=ranked_labels,
        ranked_topics=ranked_topics,
        ranked_styles=ranked_styles,
        ranked_titles=ranked_titles,
        relevance_scores=scores,
        n_positives=unit.n_positives,
    )


def _aggregate(
    results: list[UnitResult],
    k: int,
    *,
    embedding_store: EmbeddingStore | None = None,
) -> dict[str, Any]:
    """mean±std across units per method."""
    by_method: dict[str, list[UnitResult]] = {}
    for r in results:
        by_method.setdefault(r.method, []).append(r)

    out: dict[str, Any] = {}
    for method, rs in by_method.items():
        keys: list[str] = []
        per_unit: list[dict[str, float]] = []
        for r in rs:
            m = r.metrics(k, embedding_store=embedding_store)
            per_unit.append(m)
            keys.extend(m.keys())
        keys = sorted(set(keys))
        agg: dict[str, Any] = {}
        for key in keys:
            vals = [m[key] for m in per_unit if key in m]
            vals = [v for v in vals if v == v]  # drop NaN
            if not vals:
                continue
            mean = sum(vals) / len(vals)
            std = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
            agg[key] = {"mean": mean, "std": std}
        out[method] = {
            "n_units": len(rs),
            "n_total_positives": sum(r.n_positives for r in rs),
            "metrics": agg,
        }
    return out


def run_offline_eval(
    units: list[EvalUnit],
    *,
    k: int = 10,
    seed: int = 42,
    include_random_baseline: bool = True,
    mmr_embeddings: dict[str, list[float]] | None = None,
    embedding_store: EmbeddingStore | None = None,
) -> dict[str, Any]:
    """Rank every unit with the engine (and random baseline), aggregate."""
    results: list[UnitResult] = []
    rng = random.Random(seed)
    for unit in units:
        engine_ranked = _rank_with_engine(unit.candidates, mmr_embeddings=mmr_embeddings)
        results.append(_to_result(unit, engine_ranked, method="engine"))
        if include_random_baseline:
            results.append(_to_result(unit, _rank_random(unit.candidates, rng), method="random"))
    aggregated = _aggregate(results, k=k, embedding_store=embedding_store)
    return {"k": k, "methods": aggregated}


async def run_offline_eval_async(
    units: list[EvalUnit],
    *,
    k: int = 10,
    seed: int = 42,
    include_random_baseline: bool = True,
    mmr_embeddings: dict[str, list[float]] | None = None,
    embedding_store: EmbeddingStore | None = None,
    llm_service: Any = None,
    profile: Any = None,
    llm_rerank_top_k: int = 30,
    llm_rerank_weight: float = 0.3,
    llm_rerank_batch_size: int = 5,
) -> dict[str, Any]:
    """Async variant of :func:`run_offline_eval` with optional LLM rerank comparison.

    When ``llm_service`` and ``profile`` are provided, an additional
    ``engine+llm_rerank`` method is evaluated: curator-score proxy
    (relevance_score) → LLM semantic rerank → blend → MMR diversity. This
    lets you quantify the delta of enabling LLM rerank against the pure
    engine baseline.

    On LLM failure per-unit, that unit silently falls back to pure
    relevance-score ranking (logged as warning) so the full eval completes.
    """
    results: list[UnitResult] = []
    rng = random.Random(seed)
    for unit in units:
        engine_ranked = _rank_with_engine(unit.candidates, mmr_embeddings=mmr_embeddings)
        results.append(_to_result(unit, engine_ranked, method="engine"))
        if include_random_baseline:
            results.append(_to_result(unit, _rank_random(unit.candidates, rng), method="random"))
        if llm_service is not None and profile is not None:
            llm_ranked = await _rank_with_llm_rerank(
                unit.candidates,
                llm_service=llm_service,
                profile=profile,
                top_k=llm_rerank_top_k,
                weight=llm_rerank_weight,
                batch_size=llm_rerank_batch_size,
                mmr_embeddings=mmr_embeddings,
            )
            results.append(_to_result(unit, llm_ranked, method="engine+llm_rerank"))
    aggregated = _aggregate(results, k=k, embedding_store=embedding_store)
    return {"k": k, "methods": aggregated}

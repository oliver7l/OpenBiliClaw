"""Offline evaluation of recommendation ranking quality.

Pipeline: real behavior events → ground truth → sampled eval units →
engine ranking core → ranking/diversity metrics → report.

Submodules:

- ``content_key`` — canonical content identity (``platform:BVID``).
- ``ground_truth`` — positive/negative sample construction from real data.
- ``scenario`` — eval-unit bootstrap sampling.
- ``metrics`` — deterministic HR/NDCG/MRR/AUC + diversity metrics.
- ``runner`` — ranks units with the real engine selection logic.
- ``report`` — Markdown/JSON report rendering.
"""
from .content_key import content_key_from_url, normalize_bvid, to_content_key
from .ground_truth import (
    CandidateItem,
    PositiveSample,
    build_negative_pool,
    load_candidate_pool,
    load_positive_samples,
    positive_pool_from_candidates,
)
from .metrics import auc, hr_at_k, mrr, ndcg_at_k, style_coverage, topic_coverage, topic_ils
from .runner import run_offline_eval
from .scenario import EvalUnit, build_units

__all__ = [
    "CandidateItem",
    "EvalUnit",
    "PositiveSample",
    "auc",
    "build_negative_pool",
    "build_units",
    "content_key_from_url",
    "hr_at_k",
    "load_candidate_pool",
    "load_positive_samples",
    "mrr",
    "ndcg_at_k",
    "normalize_bvid",
    "positive_pool_from_candidates",
    "run_offline_eval",
    "style_coverage",
    "to_content_key",
    "topic_coverage",
    "topic_ils",
]

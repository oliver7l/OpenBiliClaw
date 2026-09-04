"""Evaluation-unit construction.

An evaluation unit is ``(candidates, labels)`` where ``candidates`` is a
mixed list of liked + not-liked items drawn from the real candidate pool and
``labels`` marks which are genuinely liked. The engine ranks ``candidates``
and we compare against ``labels``.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ground_truth import CandidateItem


@dataclass
class EvalUnit:
    unit_id: int
    candidates: list[CandidateItem]
    labels: list[int]  # aligned with candidates
    n_positives: int
    seed: int


def build_units(
    positive_matched: list[tuple[CandidateItem, object]],
    negative_pool: list[CandidateItem],
    *,
    n_units: int = 20,
    positives_per_unit: int = 5,
    unit_size: int = 20,
    seed: int = 42,
) -> list[EvalUnit]:
    """Bootstrap-sampling evaluation units.

    Each unit draws ``positives_per_unit`` liked items (without replacement
    within a unit, with replacement across units) plus ``unit_size -
    positives_per_unit`` negative items, then shuffles them so the engine
    sees no positional hint.

    Raises ``ValueError`` if the pools are too small to build a valid unit.
    """
    if not positive_matched:
        raise ValueError("no positive items matched the candidate pool")
    if positives_per_unit > len(positive_matched):
        raise ValueError(
            f"positives_per_unit={positives_per_unit} > matched positives={len(positive_matched)}"
        )
    negatives_needed = unit_size - positives_per_unit
    if negatives_needed and negatives_needed > len(negative_pool):
        raise ValueError(
            f"need {negatives_needed} negatives but only {len(negative_pool)} available"
        )

    rng = random.Random(seed)
    units: list[EvalUnit] = []
    for unit_id in range(n_units):
        pos = rng.sample(positive_matched, positives_per_unit)
        neg = rng.sample(negative_pool, negatives_needed)
        combined = [(c, 1) for c, _ in pos] + [(c, 0) for c in neg]
        rng.shuffle(combined)
        candidates = [c for c, _ in combined]
        labels = [lab for _, lab in combined]
        units.append(
            EvalUnit(
                unit_id=unit_id,
                candidates=candidates,
                labels=labels,
                n_positives=positives_per_unit,
                seed=seed,
            )
        )
    return units

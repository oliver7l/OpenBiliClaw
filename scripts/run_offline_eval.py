#!/usr/bin/env python3
"""Run the offline recommendation-quality evaluation.

Pipeline: real behavior events → ground truth → sampled eval units →
engine ranking core → ranking/diversity metrics → Markdown/JSON report.

Usage:
    .venv/bin/python scripts/run_offline_eval.py \
        [--db data/openbiliclaw.db] [--units 20] [--unit-size 20] \
        [--pos-per-unit 5] [--k 10] [--seed 42] \
        [--eval-after "2026-08-01 00:00:00"] [--no-random] \
        [--out-dir data/eval_runs]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from openbiliclaw.eval.offline import (  # noqa: E402
    build_negative_pool,
    build_units,
    load_candidate_pool,
    load_positive_samples,
    positive_pool_from_candidates,
    run_offline_eval,
    run_offline_eval_async,
)
from openbiliclaw.eval.offline.report import render_json, render_markdown  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("offline_eval")


def _run_eval_with_llm_rerank(
    *,
    units: list[Any],
    k: int,
    seed: int,
    include_random_baseline: bool,
    embedding_store: Any,
    top_k: int,
    weight: float,
    batch_size: int,
) -> dict[str, Any]:
    """Build LLM service + soul profile, then run async eval with LLM rerank."""
    import asyncio

    from obc_llm.service import LLMService, module_overrides_from_config
    from obc_soul.engine import SoulEngine

    from openbiliclaw.config import load_config
    from openbiliclaw.llm import build_llm_registry
    from openbiliclaw.memory.manager import MemoryManager

    cfg = load_config()
    memory = MemoryManager(data_dir=PROJECT_ROOT / "data")
    registry = build_llm_registry(cfg)
    llm_service = LLMService(
        registry=registry,
        memory=memory,
        module_overrides=module_overrides_from_config(cfg),
        concurrency=cfg.llm.concurrency,
    )
    soul_engine = SoulEngine(memory=memory, llm=llm_service)
    profile = asyncio.run(soul_engine.get_profile())
    logger.info(
        "LLM rerank eval: profile loaded (top_interests=%d, current_focus=%s)",
        len(getattr(profile, "top_interests", []) or []),
        getattr(profile, "current_focus", "n/a"),
    )
    return asyncio.run(
        run_offline_eval_async(
            units,
            k=k,
            seed=seed,
            include_random_baseline=include_random_baseline,
            embedding_store=embedding_store,
            llm_service=llm_service,
            profile=profile,
            llm_rerank_top_k=top_k,
            llm_rerank_weight=weight,
            llm_rerank_batch_size=batch_size,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(PROJECT_ROOT / "data" / "openbiliclaw.db"))
    parser.add_argument("--units", type=int, default=20, help="number of eval units")
    parser.add_argument("--unit-size", type=int, default=20, help="candidates per unit")
    parser.add_argument("--pos-per-unit", type=int, default=5, help="liked items per unit")
    parser.add_argument("--k", type=int, default=10, help="HR/NDCG cut-off")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--eval-after", default=None, help="time-slice guard (ISO datetime)")
    parser.add_argument("--no-random", action="store_true", help="skip random baseline")
    parser.add_argument(
        "--embedding-metrics",
        action="store_true",
        help="compute embedding-level ILS (requires embedding_cache.db)",
    )
    parser.add_argument(
        "--embedding-db",
        default=str(PROJECT_ROOT / "data" / "embedding_cache.db"),
        help="path to embedding_cache.db (used with --embedding-metrics)",
    )
    parser.add_argument("--out-dir", default=str(PROJECT_ROOT / "data" / "eval_runs"))
    parser.add_argument(
        "--history",
        action="store_true",
        help="only print the run history (no new run)",
    )
    parser.add_argument(
        "--llm-rerank",
        action="store_true",
        help="add engine+llm_rerank method (requires config.toml with LLM + soul profile)",
    )
    parser.add_argument(
        "--llm-rerank-top-k",
        type=int,
        default=30,
        help="LLM rerank top-K candidates (default 30)",
    )
    parser.add_argument(
        "--llm-rerank-weight",
        type=float,
        default=0.3,
        help="LLM rerank score weight (default 0.3)",
    )
    parser.add_argument(
        "--llm-rerank-batch-size",
        type=int,
        default=5,
        help="LLM rerank batch size (default 5)",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    index_path = out_dir / "index.json"

    if args.history:
        return _print_history(index_path)

    db_path = args.db
    if not Path(db_path).exists():
        logger.error("database not found: %s", db_path)
        return 2

    logger.info("loading positive samples from %s", db_path)
    positives = load_positive_samples(db_path, eval_after=args.eval_after)
    logger.info("loaded %d positive behavior samples", len(positives))

    logger.info("loading candidate pool")
    pool = load_candidate_pool(db_path)
    logger.info("loaded %d candidates", len(pool))

    positive_matched = positive_pool_from_candidates(pool, positives)
    logger.info("matched %d positives inside candidate pool", len(positive_matched))
    if len(positive_matched) < args.pos_per_unit:
        logger.error(
            "matched positives (%d) < pos-per-unit (%d); cannot build units",
            len(positive_matched),
            args.pos_per_unit,
        )
        return 2

    negative_pool = build_negative_pool(pool, set(positives.keys()))
    logger.info("negative pool size: %d", len(negative_pool))

    units = build_units(
        positive_matched,
        negative_pool,
        n_units=args.units,
        positives_per_unit=args.pos_per_unit,
        unit_size=args.unit_size,
        seed=args.seed,
    )
    logger.info(
        "built %d eval units (unit_size=%d, pos/unit=%d)",
        len(units),
        args.unit_size,
        args.pos_per_unit,
    )

    logger.info("running offline eval (k=%d)", args.k)
    embedding_store = None
    if args.embedding_metrics:
        from openbiliclaw.eval.offline.embedding_store import EmbeddingStore

        if not Path(args.embedding_db).exists():
            logger.warning(
                "embedding db not found: %s (embedding metrics skipped)", args.embedding_db
            )
        else:
            embedding_store = EmbeddingStore(args.embedding_db)
            logger.info("embedding metrics enabled (db=%s)", args.embedding_db)

    if args.llm_rerank:
        eval_result = _run_eval_with_llm_rerank(
            units=units,
            k=args.k,
            seed=args.seed,
            include_random_baseline=not args.no_random,
            embedding_store=embedding_store,
            top_k=args.llm_rerank_top_k,
            weight=args.llm_rerank_weight,
            batch_size=args.llm_rerank_batch_size,
        )
    else:
        eval_result = run_offline_eval(
            units,
            k=args.k,
            seed=args.seed,
            include_random_baseline=not args.no_random,
            embedding_store=embedding_store,
        )

    meta = {
        "db": str(db_path),
        "n_units": len(units),
        "unit_size": args.unit_size,
        "positives_per_unit": args.pos_per_unit,
        "k": args.k,
        "seed": args.seed,
        "n_positive_samples": len(positives),
        "n_matched_positives": len(positive_matched),
        "n_candidates": len(pool),
        "n_negative_pool": len(negative_pool),
        "eval_after": args.eval_after or "none",
        "engine_ranking": "RecommendationEngine._select_diversified_batch",
        "baselines": (
            ["engine", "engine+llm_rerank", "random"]
            if args.llm_rerank and not args.no_random
            else ["engine", "engine+llm_rerank"] if args.llm_rerank
            else ["engine", "random"] if not args.no_random
            else ["engine"]
        ),
        "embedding_metrics": args.embedding_metrics,
        "llm_rerank": args.llm_rerank,
        "llm_rerank_top_k": args.llm_rerank_top_k if args.llm_rerank else None,
        "llm_rerank_weight": args.llm_rerank_weight if args.llm_rerank else None,
    }
    assumptions = (
        "负样本为候选池未消费内容（选择偏差已披露）；时间切片开启时正样本仅取 eval_after 之后行为。"
    )

    markdown = render_markdown(eval_result, meta=meta, assumptions=assumptions)
    json_text = render_json(eval_result, meta=meta)

    out_dir = Path(args.out_dir)
    run_dir = out_dir / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.md").write_text(markdown, encoding="utf-8")
    (run_dir / "report.json").write_text(json_text, encoding="utf-8")

    print(markdown)
    print(f"\n[ok] outputs written to: {run_dir}")

    # quick machine-readable summary for scripting
    summary = {}
    for method, agg in eval_result["methods"].items():
        summary[method] = {
            key: {"mean": round(v["mean"], 4), "std": round(v["std"], 4)}
            for key, v in agg["metrics"].items()
        }
    print("\nJSON summary:\n" + json.dumps(summary, ensure_ascii=False, indent=2))

    # ── M3: persist to run history for trend tracking ────────────────
    record = {
        "run_id": run_dir.name,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "meta": meta,
        "summary": summary,
    }
    history: list[dict] = []
    if index_path.exists():
        try:
            history = json.loads(index_path.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                history = []
        except (json.JSONDecodeError, OSError):
            history = []
    history.append(record)
    index_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_trend(history, record["run_id"])
    return 0


def _print_history(index_path: Path) -> int:
    if not index_path.exists():
        print("no run history found (run the eval first)")
        return 1
    try:
        history = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print("corrupt history index")
        return 1
    if not history:
        print("empty run history")
        return 1
    _print_trend(history, None)
    return 0


def _print_trend(history: list[dict], highlight_run_id: str | None) -> None:
    """Print engine metrics across runs so changes are visible at a glance."""
    if not history:
        return
    lines = ["\n=== 历次运行趋势（engine 指标，mean） ==="]
    header = f"{'run_id':<18} {'ndcg@K':>8} {'mrr':>7} {'auc':>7} {'HR@K':>7}"
    lines.append(header)
    lines.append("-" * len(header))
    prev: dict[str, float] | None = None
    for rec in history:
        eng = rec.get("summary", {}).get("engine", {})
        k = rec.get("meta", {}).get("k", 10)
        ndcg = eng.get(f"ndcg@{k}", {}).get("mean")
        mrr = eng.get("mrr", {}).get("mean")
        auc_v = eng.get("auc", {}).get("mean")
        hr = eng.get(f"hr@{k}", {}).get("mean")

        def _f(v: float | None) -> str:
            return "n/a" if v is None else f"{v:.3f}"

        row = f"{rec.get('run_id', '?'):<18} {_f(ndcg):>8} {_f(mrr):>7} {_f(auc_v):>7} {_f(hr):>7}"
        if highlight_run_id and rec.get("run_id") == highlight_run_id:
            row += "  ← 本次"
        lines.append(row)
        if prev is not None and ndcg is not None and prev.get("ndcg") is not None:
            delta = ndcg - prev["ndcg"]
            lines.append(f"{'Δ vs 上次':<18} {delta:+.3f}")
        prev = {"ndcg": ndcg}
    print("\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())

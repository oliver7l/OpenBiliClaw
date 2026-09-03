"""Modular Agent framework for recommendation.

Refactors the agent-recommend endpoint into a council of specialized
agents, inspired by AgenticRec's pipeline-to-council design.
"""

from __future__ import annotations

import json
import logging
import re
import random
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# IntentAgent — parse natural-language query into structured filters
# ---------------------------------------------------------------------------

class IntentAgent:
    """Understand user intent: extract keywords, exclude filters, platform/type constraints."""

    NOT_PLATFORM_MAP: dict[str, str] = {
        "不要b站": "bilibili", "不要bilibili": "bilibili", "不要哔哩哔哩": "bilibili",
        "不要知乎": "zhihu", "不要zhihu": "zhihu",
        "不要小红书": "xiaohongshu", "不要xhs": "xiaohongshu",
        "不要youtube": "youtube", "不要油管": "youtube",
        "不要v2ex": "v2ex",
        "不要小宇宙": "xiaoyuzhou", "不要播客": "xiaoyuzhou",
    }
    NOT_TYPE_MAP: dict[str, str] = {
        "不要视频": "video", "不要播客": "podcast", "不要音频": "podcast",
        "不要文章": "article",
    }

    @staticmethod
    def parse(q: str) -> dict[str, Any]:
        """Parse query into structured intent.

        Returns:
            {
                "keywords": [...],
                "exclude_platforms": [...],
                "exclude_content_types": [...],
                "exclude_keywords": [...],
                "platform_filter": "",        # tombstones not yet supported
                "content_type_filter": "",
            }
        """
        exclude_platforms: list[str] = []
        exclude_content_types: list[str] = []
        exclude_keywords: list[str] = []
        q_lower = q.lower()

        for phrase, platform in IntentAgent.NOT_PLATFORM_MAP.items():
            if phrase in q_lower:
                exclude_platforms.append(platform)
        for phrase, ct in IntentAgent.NOT_TYPE_MAP.items():
            if phrase in q_lower:
                exclude_content_types.append(ct)

        exclude_match = re.search(r"不要[的]?([\u4e00-\u9fff\w]{2,10})", q)
        if exclude_match:
            exclude_keywords.append(exclude_match.group(1))

        # Remove exclusion phrases from the query for keyword extraction
        cleaned = q
        for phrase in IntentAgent.NOT_PLATFORM_MAP:
            cleaned = cleaned.replace(phrase, "")
        for phrase in IntentAgent.NOT_TYPE_MAP:
            cleaned = cleaned.replace(phrase, "")
        cleaned = re.sub(r"不要[的]?[\u4e00-\u9fff\w]{2,10}", "", cleaned).strip()

        # Keyword extraction from cleaned query
        keywords = [kw for kw in re.split(r"[,\s，、]+", cleaned) if kw.strip()] if cleaned.strip() else []

        return {
            "keywords": keywords,
            "exclude_platforms": exclude_platforms,
            "exclude_content_types": exclude_content_types,
            "exclude_keywords": exclude_keywords,
            "platform_filter": "",
            "content_type_filter": "",
        }

    @staticmethod
    def merge_session_context(
        current: dict[str, Any],
        session: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Merge previous session filters into current intent."""
        if not session:
            return current
        merged = dict(current)
        for key in ("exclude_platforms", "exclude_content_types", "exclude_keywords"):
            prev = session.get(key, [])
            cur = merged.get(key, [])
            for item in prev:
                if item not in cur:
                    cur.append(item)
            merged[key] = cur
        if not merged.get("keywords") and session.get("keywords"):
            merged["keywords"] = list(session["keywords"])
        return merged


# ---------------------------------------------------------------------------
# RankAgent — scoring, hybrid weights, diversity mix
# ---------------------------------------------------------------------------

class RankAgent:
    """Score candidates and apply diversity mix with alpha decay."""

    # Default scoring weights (without semantic)
    # With semantic: 0.5 fit + 0.3 semantic + 0.2 quality
    # Alpha decay: as learning_level (alpha) grows, rule weights contract
    @staticmethod
    def compute_learning_level(db: Any) -> float:
        """Compute alpha (0-0.3) based on cumulative user feedback count.

        Formula: alpha = min(0.3, total / (total + 100) * 0.3)
        - 0 feedback  → alpha = 0.0
        - 50 feedback → alpha ≈ 0.1
        - 200 feedback → alpha ≈ 0.2
        - 500+ feedback → alpha ≈ 0.3 (cap)
        """
        try:
            total = db.get_total_feedback_count()
            if total <= 0:
                return 0.0
            return min(0.3, total / (total + 100) * 0.3)
        except Exception:
            return 0.0

    @staticmethod
    def compute_learned_scores(db: Any) -> dict[str, float]:
        """Compute learned preference score per topic_group.

        Returns {topic_group: 0.0-1.0} — higher means more liked.
        """
        scores: dict[str, float] = {}
        try:
            rows = db.get_feedback_aggregated()
            for row in rows:
                topic = row.get("topic_group", "")
                if not topic:
                    continue
                likes = int(row.get("likes", 0) or 0)
                dislikes = int(row.get("dislikes", 0) or 0)
                total = likes + dislikes
                if total > 0:
                    net = (likes - dislikes) / total  # -1 to 1
                    scores[topic] = (net + 1) / 2  # normalize to 0-1
            return scores
        except Exception:
            return {}

    @staticmethod
    def compute_dwell_scores(db: Any) -> dict[str, float]:
        """Compute implicit dwell-weighted interest per topic_group.

        Backed by view_history dwell aggregation: topics the user reads
        longer score higher; quick-exit topics erode.
        """
        try:
            return db.get_dwell_scores(days=14) or {}
        except Exception:
            return {}

    @staticmethod
    def compute_interest_centroids(
        db: Any,
        embedding_service: Any,
        *,
        days: int = 30,
        per_topic: int = 8,
        min_dwell: float = 60.0,
    ) -> dict[str, list[float]]:
        """Per-topic semantic centroids from recent positive signals.

        A centroid is the unit-normalized mean of content embeddings the user
        recently liked (explicit) or lingered on (deep dwell) within a
        ``topic_group`` — a semantic fingerprint of current taste, far beyond
        keyword substring matching.

        Cache-only by design: vectors are read via
        ``EmbeddingService.lookup_cached`` on the canonical ``mmr_cache_text``
        key (the same key the MMR prewarm fills), so building centroids on the
        hot path costs **zero provider API calls**. Topics without any cached
        vectors are simply absent — callers fall back to keyword fit.
        """
        if embedding_service is None:
            return {}
        lookup = getattr(embedding_service, "lookup_cached", None)
        if not callable(lookup):
            return {}
        try:
            rows = db.get_interest_centroid_sources(
                days=days, min_dwell=min_dwell
            )
        except Exception:
            return {}
        import math

        from openbiliclaw.llm.embedding import mmr_cache_text

        sums: dict[str, list[float]] = {}
        counts: dict[str, int] = {}
        for row in rows:
            topic = str(row.get("topic_group") or "")
            if not topic or counts.get(topic, 0) >= per_topic:
                continue
            text = mmr_cache_text(
                str(row.get("title") or ""), str(row.get("description") or "")
            )
            if not text:
                continue
            vec = lookup(text)
            if not vec or not any(vec):
                continue
            norm = math.sqrt(sum(x * x for x in vec))
            if norm <= 0:
                continue
            s = sums.setdefault(topic, [0.0] * len(vec))
            if len(s) != len(vec):  # mixed dims → skip defensively
                continue
            for i, x in enumerate(vec):
                s[i] += x / norm
            counts[topic] = counts.get(topic, 0) + 1
        centroids: dict[str, list[float]] = {}
        for topic, s in sums.items():
            norm = math.sqrt(sum(x * x for x in s)) or 1.0
            centroids[topic] = [x / norm for x in s]
        return centroids

    @staticmethod
    def compute_dwell_beta(db: Any) -> float:
        """Compute beta (0-0.15) — confidence in implicit dwell signals.

        Formula: beta = min(0.15, views / (views + 30) * 0.15 * 1.15)
        The Michaelis-Menten ramp is scaled by (200+30)/200 = 1.15 so the
        cap engages at 200 views (the old form asymptoted to 0.15 but never
        reached it). Concave, so a few views already teach meaningfully.
        - 0 views   → beta = 0.0 (no implicit data, pure rules)
        - 10 views  → beta ≈ 0.043
        - 50 views  → beta ≈ 0.108
        - 200+      → beta = 0.15 (cap)
        """
        try:
            views = db.get_total_view_count(days=30)
            if views <= 0:
                return 0.0
            return min(0.15, views / (views + 30) * 0.15 * 1.15)
        except Exception:
            return 0.0

    @staticmethod
    def _profile_fit_score(text: str, profile_keywords: list[tuple[str, float]]) -> float:
        """Compute fit_score from profile keyword matching."""
        if not profile_keywords or not text:
            return 0.0
        text_lower = text.lower()
        score = 0.0
        for keyword, weight in profile_keywords:
            if keyword.lower() in text_lower:
                score += weight
        return min(score, 1.0)

    @staticmethod
    def score_and_rank(
        rows: list[dict[str, Any]],
        intent: dict[str, Any],
        db: Any,
        profile_keywords: list[tuple[str, float]],
        q_embed: list[float] | None = None,
        content_embeds: dict[str, list[float]] | None = None,
        interest_centroids: dict[str, list[float]] | None = None,
    ) -> list[dict[str, Any]]:
        """Score candidates and return ranked list with diversity mix.

        ``content_embeds`` maps bvid → pre-computed embedding vector. It
        must be computed by the caller in an async context — embedding
        inside this sync method would deadlock the event loop.

        ``interest_centroids`` maps topic_group → unit vector (mean embedding
        of recently liked / deep-dwelled content, cache-only). When present,
        each row's fit becomes a 50/50 blend of keyword fit and its best
        centroid cosine — semantic affinity over literal substring hits.
        Rows without a cached vector keep the pure keyword fit, and with no
        centroids at all the behaviour is byte-identical to pre-centroid.
        """

        profile_domains = {k for k, w in profile_keywords if w >= 0.5}
        from openbiliclaw.llm.embedding import cosine_similarity

        # --- score each item ---
        scored_items: list[dict[str, Any]] = []
        for r in rows:
            text = " ".join([
                str(r["title"] or ""),
                str(r["body_text"] or "")[:200],
                str(r["topic_group"] or ""),
                str(r["up_name"] or ""),
                str(r["source_platform"] or ""),
            ])
            fit = RankAgent._profile_fit_score(text, profile_keywords) if profile_keywords else 0.0
            interest_sim = 0.0
            if interest_centroids and content_embeds:
                c_embed = content_embeds.get(str(r["bvid"] or ""))
                if c_embed and any(c_embed):
                    for centroid in interest_centroids.values():
                        sim = cosine_similarity(c_embed, centroid)
                        if sim > interest_sim:
                            interest_sim = sim
            if interest_sim > 0:
                fit = fit * 0.5 + interest_sim * 0.5
            qs = float(r["quality_score"] or 0.0)
            combined = fit * 0.6 + qs * 0.4
            domain_match = any(tag.lower() in text.lower() for tag in profile_domains)
            scored_items.append({
                "row": r,
                "fit_score": fit,
                "interest_sim": interest_sim,
                "quality_score": qs,
                "combined": combined,
                "domain_match": domain_match,
                "semantic_score": 0.0,
            })

        # --- semantic re-ranking (embeddings pre-computed by the caller) ---
        if q_embed and any(q_embed) and content_embeds:
            for s in scored_items:
                c_embed = content_embeds.get(str(s["row"]["bvid"] or ""))
                if c_embed and any(c_embed):
                    s["semantic_score"] = cosine_similarity(q_embed, c_embed)

        # --- alpha decay + implicit dwell: hybrid scoring ---
        alpha = RankAgent.compute_learning_level(db)
        learned_scores = RankAgent.compute_learned_scores(db) if alpha > 0 else {}
        beta = RankAgent.compute_dwell_beta(db)
        dwell_scores = RankAgent.compute_dwell_scores(db) if beta > 0 else {}

        for s in scored_items:
            r = s["row"]
            f = s["fit_score"]
            sem = s["semantic_score"]
            qs = s["quality_score"]
            topic = str(r["topic_group"] or "")
            learned = learned_scores.get(topic, 0.5)
            dwell = dwell_scores.get(topic, 0.5) if dwell_scores else 0.5

            if sem > 0:  # semantic available
                rule_combined = f * 0.5 + sem * 0.3 + qs * 0.2
            else:
                rule_combined = f * 0.6 + qs * 0.4

            rule_weight = max(0.0, 1.0 - alpha - beta)
            s["combined"] = (
                rule_combined * rule_weight
                + learned * alpha
                + dwell * beta
            )

        # --- sort ---
        scored_items.sort(key=lambda x: x["combined"], reverse=True)

        # --- diversity mix: 80% high-fit, 20% exploration ---
        high_fit = [s for s in scored_items if s["combined"] > 0.1 or s["domain_match"]]
        low_fit = [s for s in scored_items if s["combined"] <= 0.1 and not s["domain_match"]]

        random.shuffle(high_fit)
        random.shuffle(low_fit)

        return {"high_fit": high_fit, "low_fit": low_fit, "alpha": alpha, "beta": beta}

    @staticmethod
    def build_context_text(intent: dict[str, Any], alpha: float, beta: float = 0.0) -> str:
        """Build a human-readable session context string."""
        parts = []
        if intent.get("platform_filter"):
            parts.append(f"平台: {intent['platform_filter']}")
        if intent.get("content_type_filter"):
            parts.append(f"类型: {intent['content_type_filter']}")
        if intent.get("exclude_platforms"):
            parts.append(f"排除: {', '.join(intent['exclude_platforms'])}")
        if intent.get("exclude_content_types"):
            parts.append(f"排除: {', '.join(intent['exclude_content_types'])}")
        if intent.get("keywords"):
            parts.append(f"「{' '.join(intent['keywords'])}」")
        if alpha > 0.01:
            parts.append(f"点赞学习: {alpha:.0%}")
        if beta > 0.01:
            parts.append(f"停留学习: {beta:.0%}")
        return " · ".join(parts) if parts else ""


# ---------------------------------------------------------------------------
# InterestSyncer — dynamic interest profile updates from feedback
# ---------------------------------------------------------------------------

class InterestSyncer:
    """Sync user_feedback into soul_profile interest weights."""

    @staticmethod
    def sync(db: Any, profile_path: str | Path) -> None:
        """Read user_feedback, compute interest trends, update soul_profile."""
        try:
            rows = db.get_feedback_aggregated()
            if not rows:
                return

            profile_file = Path(profile_path)
            if not profile_file.exists():
                logger.warning("soul_profile not found at %s", profile_path)
                return

            profile = json.loads(profile_file.read_text("utf-8"))

            # Aggregate feedback per topic_group
            topic_stats: dict[str, dict[str, float]] = {}
            for row in rows:
                topic = row.get("topic_group", "").strip()
                if not topic:
                    continue
                likes = int(row.get("likes", 0) or 0)
                dislikes = int(row.get("dislikes", 0) or 0)
                if topic not in topic_stats:
                    topic_stats[topic] = {"likes": 0, "dislikes": 0}
                topic_stats[topic]["likes"] += likes
                topic_stats[topic]["dislikes"] += dislikes

            # Build feedback-based interest weights
            feedback_weights: dict[str, float] = {}
            for topic, stats in topic_stats.items():
                net = stats["likes"] - stats["dislikes"]
                total = stats["likes"] + stats["dislikes"]
                if total > 0 and net > 0:
                    feedback_weights[topic] = min(1.0, net / total + 0.3)

            # Update soul_profile.likes with feedback-boosted weights
            if "interest" in profile and "likes" in profile["interest"]:
                existing = profile["interest"]["likes"]
                for entry in existing:
                    topic = entry.get("domain", "")
                    if topic in feedback_weights:
                        # Blend: 70% existing + 30% feedback
                        existing_weight = float(entry.get("weight", 0.5))
                        entry["weight"] = round(existing_weight * 0.7 + feedback_weights[topic] * 0.3, 2)

                # Add new topics from feedback if not already in profile
                existing_domains = {e.get("domain", "") for e in existing}
                for topic, weight in feedback_weights.items():
                    if topic not in existing_domains and weight >= 0.5:
                        existing.append({
                            "domain": topic,
                            "weight": round(weight, 2),
                            "specifics": [],
                        })

                profile_file.write_text(json.dumps(profile, ensure_ascii=False, indent=2), "utf-8")
                logger.info("Interest synced from feedback: %d topics updated", len(feedback_weights))
        except Exception as exc:
            logger.warning("Interest sync failed: %s", exc)
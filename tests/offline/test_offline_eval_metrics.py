"""Tests for offline evaluation metrics and pipeline primitives."""

from __future__ import annotations

import pytest

from openbiliclaw.eval.offline.content_key import (
    content_key_from_url,
    normalize_bvid,
    to_content_key,
)
from openbiliclaw.eval.offline.metrics import auc, hr_at_k, mrr, ndcg_at_k, topic_ils

# ── content_key ─────────────────────────────────────────────────────────


def test_normalize_bvid_forms():
    assert normalize_bvid("BV1HK3w6TEBm") == "BV1HK3w6TEBm"
    assert normalize_bvid("https://www.bilibili.com/video/BV1HK3w6TEBm") == "BV1HK3w6TEBm"
    assert normalize_bvid("bilibili:BV1HK3w6TEBm") == "BV1HK3w6TEBm"
    assert normalize_bvid("1HK3w6TEBm") is None  # bare legacy form has no BV prefix
    assert normalize_bvid(None) is None


def test_content_key_from_url():
    assert (
        content_key_from_url("https://www.bilibili.com/video/BV1HK3w6TEBm")
        == "bilibili:BV1HK3w6TEBm"
    )
    assert (
        content_key_from_url(
            "https://www.xiaohongshu.com/explore/6a71d55d000000002201257f?xsec_token=abc"
        )
        == "xiaohongshu:6a71d55d000000002201257f"
    )


def test_to_content_key():
    assert to_content_key("bilibili", "BV1x") == "bilibili:BV1x"
    assert to_content_key("BiliBili", "BV1x") == "bilibili:BV1x"
    assert to_content_key(None, "BV1x") is None


# ── ranking metrics ─────────────────────────────────────────────────────


def test_hr_at_k():
    assert hr_at_k([1, 0, 0], k=1) == 1.0
    assert hr_at_k([0, 1, 0], k=1) == 0.0
    assert hr_at_k([0, 1, 0], k=2) == 1.0
    assert hr_at_k([0, 0, 0], k=3) == 0.0


def test_ndcg_at_k():
    # ideal ordering: positive first
    assert ndcg_at_k([1, 0, 0, 0], k=4) == pytest.approx(1.0)
    # positive in slot 2 -> discounted
    perfect = ndcg_at_k([1, 0, 0, 0], k=4)
    slot2 = ndcg_at_k([0, 1, 0, 0], k=4)
    assert 0.0 < slot2 < perfect
    assert ndcg_at_k([0, 0, 0, 0], k=4) == 0.0
    # k larger than list clamps
    assert ndcg_at_k([1, 0], k=10) == pytest.approx(1.0)


def test_mrr():
    assert mrr([1, 0, 0]) == 1.0
    assert mrr([0, 0, 1]) == pytest.approx(1 / 3)
    assert mrr([0, 0, 0]) == 0.0


def test_auc():
    # perfect separation: all positives outscore all negatives
    labels = [1, 1, 0, 0]
    scores = [0.9, 0.8, 0.1, 0.2]
    assert auc(labels, scores) == pytest.approx(1.0)
    # reversed -> 0.0
    assert auc(labels, [0.1, 0.2, 0.9, 0.8]) == pytest.approx(0.0)
    # single class -> NaN
    assert auc([1, 1, 1], [0.9, 0.8, 0.7]) != auc([1, 1, 1], [0.9, 0.8, 0.7])
    import math

    assert math.isnan(auc([1, 1, 1], [0.9, 0.8, 0.7]))


# ── diversity ───────────────────────────────────────────────────────────


def test_topic_ils():
    assert topic_ils(["a", "a", "b"]) == pytest.approx(1 / 3)
    assert topic_ils(["a", "b", "c"]) == 0.0
    assert topic_ils(["", "", ""]) == 0.0
    assert topic_ils(["a"]) == 0.0

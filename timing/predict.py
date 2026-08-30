"""
timing/predict.py
────────────────────────────────────────────────────────────────────────────────
Inference module for the trained retry-timing model.

Loads data/timing_model.pkl once at import and exposes:

    predict_optimal_offset(record) -> Optional[int]

Returns the predicted optimal retry offset in hours for bank_side/low_balance
records, or None for out-of-scope buckets.

The model is loaded lazily on first call so import cost is zero.
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path
from typing import Optional

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from schema.subscription_schema import FailureBucket, SubscriptionRecord

# ── Constants ──────────────────────────────────────────────────────────────────

TIMING_BUCKETS: frozenset[str] = frozenset({
    FailureBucket.BANK_SIDE.value,
    FailureBucket.LOW_BALANCE.value,
})

CANDIDATE_OFFSETS: list[int] = [24, 36, 48, 60, 72, 84, 96, 120, 144, 168]

MODEL_PATH = ROOT / "data" / "timing_model.pkl"

# ── Lazy model loader ─────────────────────────────────────────────────────────

_model_cache: Optional[dict] = None


def _load_model() -> dict:
    global _model_cache
    if _model_cache is None:
        if not MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Timing model not found at {MODEL_PATH}. "
                f"Run 'python timing/train_timing_model.py' first."
            )
        with MODEL_PATH.open("rb") as f:
            _model_cache = pickle.load(f)
    return _model_cache


def _make_features(record: SubscriptionRecord, offset_hours: int, bucket_encoding: dict) -> list:
    bucket_val = (
        record.failure_bucket
        if isinstance(record.failure_bucket, str)
        else record.failure_bucket.value
    )
    return [
        bucket_encoding.get(bucket_val, -1),
        float(offset_hours),
        float(record.amount),
        float(record.mandate_age_days),
        float(record.auth_attempts),
    ]


# ── Public API ─────────────────────────────────────────────────────────────────

def predict_optimal_offset(record: SubscriptionRecord) -> Optional[int]:
    """
    Predict the optimal retry offset (in hours) for the given record.

    For bank_side and low_balance buckets: evaluates all 10 candidate
    offsets and returns the one with the highest predicted P(success).

    For all other buckets (expired_mandate, reauth_mismatch, genuine_decline):
    returns None — timing modeling is out of scope for these.

    Parameters
    ----------
    record : SubscriptionRecord with failure_bucket already classified

    Returns
    -------
    Optional[int] — offset in hours, or None if bucket is out of scope
    """
    bucket_val = (
        record.failure_bucket
        if isinstance(record.failure_bucket, str)
        else record.failure_bucket.value
    )

    if bucket_val not in TIMING_BUCKETS:
        return None

    model_data = _load_model()
    model = model_data["model"]
    bucket_encoding = model_data["bucket_encoding"]

    # Build feature matrix for all candidate offsets
    X = np.array(
        [_make_features(record, offset, bucket_encoding) for offset in CANDIDATE_OFFSETS],
        dtype=float,
    )

    proba = model.predict_proba(X)[:, 1]  # P(success) for each offset
    best_idx = int(np.argmax(proba))

    return CANDIDATE_OFFSETS[best_idx]


def predict_with_scores(record: SubscriptionRecord) -> Optional[dict]:
    """
    Extended version returning the full offset→probability table.
    Useful for debugging and the audit log.
    """
    bucket_val = (
        record.failure_bucket
        if isinstance(record.failure_bucket, str)
        else record.failure_bucket.value
    )

    if bucket_val not in TIMING_BUCKETS:
        return None

    model_data = _load_model()
    model = model_data["model"]
    bucket_encoding = model_data["bucket_encoding"]

    X = np.array(
        [_make_features(record, offset, bucket_encoding) for offset in CANDIDATE_OFFSETS],
        dtype=float,
    )

    proba = model.predict_proba(X)[:, 1]
    best_idx = int(np.argmax(proba))

    return {
        "optimal_offset_hours": CANDIDATE_OFFSETS[best_idx],
        "best_predicted_prob":  round(float(proba[best_idx]), 4),
        "all_offset_scores":    {
            str(offset): round(float(p), 4)
            for offset, p in zip(CANDIDATE_OFFSETS, proba)
        },
    }

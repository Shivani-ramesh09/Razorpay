"""
timing/outcome_simulator.py
────────────────────────────────────────────────────────────────────────────────
Noisy generative model for simulated retry outcomes.

SCOPE
─────
Only two failure buckets have retry-timing dynamics worth modeling:

  bank_side     — Bank transient error.  Probability of success is high and
                  roughly flat across the NPCI window, but random congestion
                  dips occur unpredictably.  Timing can avoid these dips.

  low_balance   — Customer has insufficient funds.  Probability climbs as
                  the next salary-credit day approaches (modeled as day 1 and
                  day 7 of each month).  Retrying near a salary-credit date
                  meaningfully increases success probability.

  expired_mandate  → NOT modeled (re-auth required; timing doesn't matter)
  reauth_mismatch  → NOT modeled (re-auth required; timing doesn't matter)
  genuine_decline  → NOT modeled (should never be retried)

HONESTY CONSTRAINTS
───────────────────
The simulator is deliberately noisy so that:
  (a) A timing model can beat naive-immediate-retry — the signal is real.
  (b) No model can beat it perfectly — noise prevents circular validation.

Noise levels:
  bank_side    — Gaussian noise σ=0.10 on the raw probability
  low_balance  — Gaussian noise σ=0.13 on the raw probability (higher,
                 because salary-credit day varies by employer/bank)

If a reported "lift over naive baseline" exceeds ~90%, the generative
function is too clean for honest reporting.  train_timing_model.py hard-stops
on this condition.

Usage
─────
    from timing.outcome_simulator import simulate_outcome, success_probability

    prob = success_probability(record, offset_hours=48)
    outcome = simulate_outcome(record, offset_hours=48, seed=42)
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timezone
from typing import Optional

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from schema.subscription_schema import FailureBucket, SubscriptionRecord

# ── Parameters (expose for documentation + train_timing_model.py to log) ──────

NOISE_SIGMA = {
    FailureBucket.BANK_SIDE:   0.10,
    FailureBucket.LOW_BALANCE: 0.13,
}

# Salary-credit day assumptions (day of month)
SALARY_CREDIT_DAYS = [1, 7]

# Congestion dip windows for bank_side: list of (start_hour, end_hour, depth)
# depth = how much the base probability drops during that window
CONGESTION_WINDOWS = [
    (36,  54, 0.30),   # Simulated midweek congestion peak
    (90, 108, 0.22),   # Weekend settlement batch
    (144, 162, 0.18),  # End-of-week reconciliation
]

BASE_PROB = {
    FailureBucket.BANK_SIDE:   0.72,   # High base — transient errors recover quickly
    FailureBucket.LOW_BALANCE: 0.35,   # Low base — most customers are still low after failure
}

# ── Internal helpers ──────────────────────────────────────────────────────────

def _days_until_next_salary(offset_hours: float) -> float:
    """
    Return estimated days until the next salary-credit day,
    given that a retry is happening `offset_hours` after the current moment.

    We use a simplified model: advance a clock by offset_hours from now,
    then find the nearest upcoming SALARY_CREDIT_DAY.
    """
    now_ts = datetime.now(timezone.utc)
    retry_dt = datetime.fromtimestamp(
        now_ts.timestamp() + offset_hours * 3600, tz=timezone.utc
    )
    day_of_month = retry_dt.day
    # Days until nearest salary-credit day in next 31 days
    min_gap = 31
    for target_day in SALARY_CREDIT_DAYS:
        if target_day >= day_of_month:
            gap = target_day - day_of_month
        else:
            # Wraps to next month
            gap = (31 - day_of_month) + target_day
        if gap < min_gap:
            min_gap = gap
    return float(min_gap)


def _bank_side_base_probability(offset_hours: float) -> float:
    """
    Bank-side probability curve: high + flat with random dips.
    The dip windows are deterministic (same location every time) but depth
    is partially absorbed by the Gaussian noise added downstream.
    """
    prob = BASE_PROB[FailureBucket.BANK_SIDE]
    for start, end, depth in CONGESTION_WINDOWS:
        if start <= offset_hours <= end:
            # Smooth dip using a cosine bell inside the window
            mid = (start + end) / 2.0
            width = (end - start) / 2.0
            dip = depth * (1 + math.cos(math.pi * (offset_hours - mid) / width)) / 2.0
            prob -= dip
    return max(0.05, min(0.95, prob))


def _low_balance_base_probability(
    offset_hours: float,
    amount: int,
) -> float:
    """
    Low-balance probability curve.

    Shape:
    - Starts low (customer still has low balance just after failure)
    - Rises as offset approaches a salary-credit window
    - Higher amounts are harder to recover (less likely even on payday)
    - Peaks at roughly 2 days before next expected salary credit

    Amount penalty: ₹ 1,000 = no penalty, ₹ 9,999 = -0.15 penalty
    """
    days_to_salary = _days_until_next_salary(offset_hours)

    # Probability peaks ~2 days before salary day (pre-credit period)
    # and is lower immediately after a failure (first 24–48h)
    offset_days = offset_hours / 24.0
    offset_fraction = min(1.0, offset_days / 7.0)  # normalise to 7-day window

    # Bell curve peaking at (days_to_salary = 2)
    target_lead_days = 2.0
    peak_distance = abs(days_to_salary - target_lead_days)
    # Wider the peak_distance, lower the probability
    bell = math.exp(-0.5 * (peak_distance / 2.5) ** 2)

    # Base probability rises with offset (customer has had more time to top up)
    time_bonus = 0.15 * offset_fraction

    # Amount penalty: paise to INR, linear penalty up to ₹10,000
    amount_inr = amount / 100
    amount_penalty = min(0.15, (amount_inr / 10_000) * 0.15)

    prob = BASE_PROB[FailureBucket.LOW_BALANCE] + 0.35 * bell + time_bonus - amount_penalty
    return max(0.05, min(0.90, prob))


# ── Public API ─────────────────────────────────────────────────────────────────

def success_probability(
    record: SubscriptionRecord,
    offset_hours: float,
    *,
    rng: Optional[random.Random] = None,
) -> float:
    """
    Return the (noisy) probability that a retry at `offset_hours` after
    the current moment will succeed for the given record.

    Parameters
    ----------
    record       : SubscriptionRecord (must be bank_side or low_balance)
    offset_hours : Hours from now to the proposed retry (must be >= 24)
    rng          : Optional seeded random.Random for reproducibility

    Returns
    -------
    float in [0.0, 1.0]

    Raises
    ------
    ValueError if record.failure_bucket is not in scope for timing modeling.
    """
    if rng is None:
        rng = random.Random()

    bucket_val = (
        record.failure_bucket
        if isinstance(record.failure_bucket, str)
        else record.failure_bucket.value
    )

    if bucket_val == FailureBucket.BANK_SIDE.value:
        base = _bank_side_base_probability(offset_hours)
        sigma = NOISE_SIGMA[FailureBucket.BANK_SIDE]
    elif bucket_val == FailureBucket.LOW_BALANCE.value:
        base = _low_balance_base_probability(offset_hours, record.amount)
        sigma = NOISE_SIGMA[FailureBucket.LOW_BALANCE]
    else:
        raise ValueError(
            f"failure_bucket={bucket_val!r} is not in scope for timing modeling. "
            f"Only 'bank_side' and 'low_balance' have retry-timing dynamics."
        )

    # Add Gaussian noise (clipped to [0, 1])
    noise = rng.gauss(0, sigma)
    return max(0.0, min(1.0, base + noise))


def simulate_outcome(
    record: SubscriptionRecord,
    offset_hours: float,
    *,
    seed: Optional[int] = None,
) -> bool:
    """
    Simulate a binary outcome (True = success, False = failure) for a retry
    at `offset_hours` after now.

    This is a Bernoulli draw from `success_probability(record, offset_hours)`.
    Use `seed` for reproducible dataset generation.

    Parameters
    ----------
    record       : SubscriptionRecord (bank_side or low_balance only)
    offset_hours : Hours from now to the proposed retry
    seed         : Optional int seed for reproducibility

    Returns
    -------
    bool — True if the simulated retry succeeds
    """
    rng = random.Random(seed)
    prob = success_probability(record, offset_hours, rng=rng)
    return rng.random() < prob

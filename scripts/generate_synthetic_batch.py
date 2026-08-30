"""
scripts/generate_synthetic_batch.py
────────────────────────────────────────────────────────────────────────────────
Generates 200+ synthetic SubscriptionRecord instances matching the locked
schema, with a failure_bucket distribution grounded in real-world proportions:

    bank_side       ~40%
    low_balance     ~25%
    expired_mandate ~15%
    reauth_mismatch ~10%
    genuine_decline ~10%

Output: data/synthetic_batch.json  (list of SubscriptionRecord JSON objects)

Usage
-----
    python scripts/generate_synthetic_batch.py
    python scripts/generate_synthetic_batch.py --count 500 --seed 99
    python scripts/generate_synthetic_batch.py --output data/my_batch.json

The summary table printed at the end shows the *actual* generated distribution
so it can be verified against the target proportions — do not skip that check.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

import numpy as np
from faker import Faker

# ── Path bootstrap so we can import from project root ─────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from schema.subscription_schema import (
    FailureBucket,
    SubscriptionRecord,
    SubscriptionStatus,
)

# ── Constants ─────────────────────────────────────────────────────────────────

TARGET_DISTRIBUTION: dict[FailureBucket, float] = {
    FailureBucket.BANK_SIDE:       0.40,
    FailureBucket.LOW_BALANCE:     0.25,
    FailureBucket.EXPIRED_MANDATE: 0.15,
    FailureBucket.REAUTH_MISMATCH: 0.10,
    FailureBucket.GENUINE_DECLINE: 0.10,
}

# Amounts in paise: ₹99 → ₹9,999 for most; ₹15,000+ reserved for re-auth cases
AMOUNT_RANGES_PAISE = {
    FailureBucket.BANK_SIDE:       (9_900,    999_900),    # ₹99 – ₹9,999
    FailureBucket.LOW_BALANCE:     (9_900,    499_900),    # ₹99 – ₹4,999 (typically small)
    FailureBucket.EXPIRED_MANDATE: (9_900,    999_900),
    FailureBucket.REAUTH_MISMATCH: (1_500_000, 5_000_000), # ₹15,000 – ₹50,000 (above threshold)
    FailureBucket.GENUINE_DECLINE: (9_900,    999_900),
}

# ── Realistic Razorpay / bank error codes per bucket ─────────────────────────
# Sourced from Razorpay error code documentation and real UPI Autopay failure
# patterns.  Each bucket has multiple plausible codes — the generator picks one
# at random.  The classifier must map these codes BACK to the correct bucket
# without ever reading the failure_bucket field.
ERROR_CODES_BY_BUCKET: dict[FailureBucket, list[tuple[str, str]]] = {
    FailureBucket.BANK_SIDE: [
        ("BANK_INTERNAL_ERROR",       "Bank server encountered an internal error"),
        ("GATEWAY_TECHNICAL_ERROR",   "Payment gateway technical failure"),
        ("BAD_REQUEST_ERROR",         "Bank switch returned bad request — likely transient"),
        ("BANK_NOT_RESPONDING",       "Bank server did not respond within timeout"),
        ("NPCI_SWITCH_ERROR",         "NPCI switch unavailable; retry after bank recovery"),
        ("TECHNICAL_ERROR",           "Unspecified technical error from issuer bank"),
    ],
    FailureBucket.LOW_BALANCE: [
        ("INSUFFICIENT_FUNDS",        "Insufficient funds in customer account"),
        ("LOW_BALANCE",               "Account balance below mandate debit amount"),
        ("ACCOUNT_DEBIT_FAILED",      "Debit failed due to low account balance"),
        ("BALANCE_BELOW_THRESHOLD",   "Available balance below required threshold"),
    ],
    FailureBucket.EXPIRED_MANDATE: [
        ("MANDATE_EXPIRED",           "UPI mandate has expired; re-registration required"),
        ("INVALID_MANDATE",           "Mandate is no longer valid or was revoked"),
        ("TOKEN_EXPIRED",             "Payment token associated with mandate has expired"),
        ("MANDATE_REVOKED",           "Customer revoked the mandate at their bank"),
        ("HANDLE_NOT_REGISTERED",     "Customer UPI handle no longer registered"),
    ],
    FailureBucket.REAUTH_MISMATCH: [
        ("REAUTH_REQUIRED",               "Re-authorisation required for this transaction"),
        ("MANDATE_AMOUNT_LIMIT_EXCEEDED", "Transaction exceeds mandate amount limit; re-auth needed"),
        ("PRE_DEBIT_NOTIFY_FAILED",       "Pre-debit notification rejected — re-auth required"),
        ("DEBIT_BLOCKED_REAUTH",          "Debit blocked pending re-authorisation from customer"),
    ],
    FailureBucket.GENUINE_DECLINE: [
        ("PAYMENT_DECLINED",          "Transaction declined by customer at bank"),
        ("USER_BLOCKED",              "Customer has blocked debits from this merchant"),
        ("TRANSACTION_NOT_PERMITTED", "Transaction not permitted by customer's bank policy"),
        ("CUSTOMER_DECLINED",         "Customer explicitly declined the debit request"),
        ("DO_NOT_HONOR",              "Bank issued do-not-honor response for this transaction"),
    ],
}

NOW_TS = int(datetime.now(timezone.utc).timestamp())
_fake = Faker("en_IN")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _rzp_id(prefix: str, rng: random.Random) -> str:
    """Generate a plausible Razorpay-style ID."""
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    suffix = "".join(rng.choices(chars, k=14))
    return f"{prefix}_{suffix}"


def _random_unix_ts(rng: random.Random, days_ago_min: int, days_ago_max: int) -> int:
    """Return a random Unix timestamp between [now - days_ago_max, now - days_ago_min]."""
    delta_seconds = rng.randint(days_ago_min * 86_400, days_ago_max * 86_400)
    return NOW_TS - delta_seconds


def _generate_one(
    bucket: FailureBucket,
    rng: random.Random,
    np_rng: np.random.Generator,
) -> SubscriptionRecord:
    """
    Build a single synthetic SubscriptionRecord for the given failure bucket.
    All numeric distributions are chosen to reflect plausible real-world shapes.
    """
    # ── Plan shape ────────────────────────────────────────────────────────────
    total_count   = int(np_rng.integers(3, 25))          # 3–24 month plan
    paid_count    = int(np_rng.integers(0, total_count)) # 0 to total_count-1 paid
    remaining_count = total_count - paid_count

    # ── Timestamps ────────────────────────────────────────────────────────────
    mandate_age_days = int(np_rng.integers(1, 730))       # up to 2 years

    # current_start = some time ago within this billing period
    current_start = _random_unix_ts(rng, 0, 30)
    current_end   = current_start + 30 * 86_400           # approx. 30-day cycle
    charge_at     = current_start + rng.randint(0, 3) * 86_400  # retry within 3 days

    # ── Amount ────────────────────────────────────────────────────────────────
    lo, hi = AMOUNT_RANGES_PAISE[bucket]
    amount = rng.randint(lo, hi)

    # ── days_since_last_success ───────────────────────────────────────────────
    if paid_count == 0:
        days_since_last_success = None
    else:
        days_since_last_success = int(np_rng.integers(1, 90))

    # ── historical_payment_day_pattern ────────────────────────────────────────
    # Customers typically pay on consistent day(s) of month
    if paid_count > 0:
        anchor_day = rng.randint(1, 28)
        # Add ±1 day jitter for some records to reflect real payment-day drift
        n_days = rng.randint(1, 3)
        pattern = sorted(
            set(max(1, min(28, anchor_day + rng.randint(-1, 1))) for _ in range(n_days))
        )
    else:
        pattern = []

    # ── auth_attempts: pending = 1-3 attempts consumed ───────────────────────
    auth_attempts = rng.randint(1, 3)

    # ── Error code (classifier's primary raw signal) ──────────────────────────
    # Pick a realistic error code for this bucket.  The ground truth label
    # (failure_bucket) stays on the record for scoring — the classifier must
    # infer it from error_code/error_description alone.
    error_code, error_description = rng.choice(ERROR_CODES_BY_BUCKET[bucket])

    return SubscriptionRecord(
        subscription_id=_rzp_id("sub", rng),
        status=SubscriptionStatus.PENDING,
        auth_attempts=auth_attempts,
        paid_count=paid_count,
        remaining_count=remaining_count,
        total_count=total_count,
        charge_at=charge_at,
        current_start=current_start,
        current_end=current_end,
        customer_id=_rzp_id("cust", rng),
        plan_id=_rzp_id("plan", rng),
        error_code=error_code,
        error_description=error_description,
        failure_bucket=bucket,
        amount=amount,
        mandate_age_days=mandate_age_days,
        days_since_last_success=days_since_last_success,
        above_15k_threshold=amount >= 1_500_000,
        historical_payment_day_pattern=pattern,
    )


def generate_batch(
    count: int = 200,
    seed: int = 42,
) -> List[SubscriptionRecord]:
    """
    Generate `count` synthetic records with a failure_bucket distribution
    matching TARGET_DISTRIBUTION as closely as possible (floor-then-top-up).
    """
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)

    buckets: list[FailureBucket] = list(TARGET_DISTRIBUTION.keys())
    proportions: list[float] = list(TARGET_DISTRIBUTION.values())

    # Floor allocations first, then distribute the remainder randomly
    raw_counts = [int(p * count) for p in proportions]
    remainder = count - sum(raw_counts)
    # Distribute remaining slots proportionally (no bias)
    for _ in range(remainder):
        chosen = rng.choices(buckets, weights=proportions, k=1)[0]
        raw_counts[buckets.index(chosen)] += 1

    allocation: dict[FailureBucket, int] = dict(zip(buckets, raw_counts))

    records: list[SubscriptionRecord] = []
    for bucket, n in allocation.items():
        for _ in range(n):
            records.append(_generate_one(bucket, rng, np_rng))

    # Shuffle so the batch isn't ordered by bucket
    rng.shuffle(records)
    return records


def print_distribution_summary(records: List[SubscriptionRecord]) -> None:
    """
    Print a sanity-check table comparing actual vs. target distribution.
    Raises an AssertionError if any bucket deviates by more than 5 percentage
    points — this is the automated check so you don't eyeball it.
    """
    total = len(records)
    bucket_counts: dict[str, int] = {}
    for r in records:
        key = r.failure_bucket if isinstance(r.failure_bucket, str) else r.failure_bucket.value
        bucket_counts[key] = bucket_counts.get(key, 0) + 1

    print("\n" + "=" * 60)
    print(f"  Synthetic Batch Distribution (n={total})")
    print("=" * 60)
    print(f"  {'Bucket':<22} {'Count':>6}  {'Actual%':>8}  {'Target%':>8}  {'Diff':>6}")
    print("-" * 60)

    for bucket, target_pct in TARGET_DISTRIBUTION.items():
        key = bucket.value
        count = bucket_counts.get(key, 0)
        actual_pct = count / total
        delta = actual_pct - target_pct
        flag = "  [!]" if abs(delta) > 0.05 else ""
        print(
            f"  {key:<22} {count:>6}  {actual_pct:>7.1%}  {target_pct:>7.1%}  {delta:>+5.1%}{flag}"
        )  # noqa: E501

    print("=" * 60)

    # Hard assertion — fail loudly if distribution is off
    for bucket, target_pct in TARGET_DISTRIBUTION.items():
        key = bucket.value
        actual_pct = bucket_counts.get(key, 0) / total
        deviation = abs(actual_pct - target_pct)
        assert deviation <= 0.05, (
            f"Distribution check FAILED for '{key}': "
            f"actual={actual_pct:.1%}, target={target_pct:.1%}, deviation={deviation:.1%} > 5%"
        )

    print("  [OK] All buckets within +/-5pp of target proportions.")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a synthetic subscription batch for the Mandate Recovery Agent."
    )
    parser.add_argument(
        "--count", type=int, default=200,
        help="Number of records to generate (default: 200)",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output file path (default: data/synthetic_batch.json)",
    )
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else ROOT / "data" / "synthetic_batch.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] Generating {args.count} synthetic records (seed={args.seed})…")
    records = generate_batch(count=args.count, seed=args.seed)

    # Serialise via Pydantic so all types are correctly handled
    batch_json = [r.model_dump(mode="json") for r in records]
    output_path.write_text(json.dumps(batch_json, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[INFO] Saved to: {output_path.resolve()}")

    # ── Sanity check ──────────────────────────────────────────────────────────
    print_distribution_summary(records)

    print(f"[INFO] Schema validation: all {len(records)} records passed Pydantic validation [OK]")


if __name__ == "__main__":
    main()

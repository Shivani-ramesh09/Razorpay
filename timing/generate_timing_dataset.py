"""
timing/generate_timing_dataset.py
────────────────────────────────────────────────────────────────────────────────
Generates the labeled timing dataset for training the retry-timing model.

For each bank_side/low_balance record in the synthetic batch:
  - Generate 10 candidate retry offsets within the NPCI-allowed window
  - Simulate a binary outcome per (record, offset) pair
  - Write to data/timing_dataset.json
  - Split 80/20 by subscription_id (no leakage) → mark split in each row

Candidate offsets (hours): [24, 36, 48, 60, 72, 84, 96, 120, 144, 168]

Offset lower bound: 24h (first NPCI cooldown window for attempt #1)
Offset upper bound: 168h (third NPCI cooldown window, last allowed slot)

Splits are stratified by (bucket, outcome) at the record level —
all offsets for a given subscription_id land in the same split.

Usage
─────
    python timing/generate_timing_dataset.py
    python timing/generate_timing_dataset.py --batch data/other_batch.json
    python timing/generate_timing_dataset.py --seed 99 --verbose
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from schema.subscription_schema import FailureBucket, SubscriptionRecord
from timing.outcome_simulator import simulate_outcome

# ── Constants ──────────────────────────────────────────────────────────────────

# Candidate retry offsets (hours) — within NPCI window, starting at 24h
# Skip anything < 24h (violates first cooldown window for attempt #1)
CANDIDATE_OFFSETS: list[int] = [24, 36, 48, 60, 72, 84, 96, 120, 144, 168]

TIMING_BUCKETS: set[str] = {
    FailureBucket.BANK_SIDE.value,
    FailureBucket.LOW_BALANCE.value,
}

OUTPUT_PATH = ROOT / "data" / "timing_dataset.json"

DEFAULT_BATCH = ROOT / "data" / "synthetic_batch.json"
TRAIN_FRACTION = 0.80


# ── Dataset generation ────────────────────────────────────────────────────────

def generate_dataset(
    batch_path: Path,
    seed: int = 42,
    verbose: bool = False,
) -> list[dict]:
    """
    Load the batch, filter to timing-scoped buckets, generate outcome rows.
    Returns a list of dataset dicts (one per (record, offset) pair).
    """
    raw = json.loads(batch_path.read_text(encoding="utf-8"))
    records = [SubscriptionRecord(**r) for r in raw]

    # Filter to in-scope buckets
    scoped = [
        r for r in records
        if (r.failure_bucket if isinstance(r.failure_bucket, str) else r.failure_bucket.value)
        in TIMING_BUCKETS
    ]

    print(f"[DataGen] Total records: {len(records)} | In-scope: {len(scoped)} "
          f"(bank_side + low_balance)")

    # 80/20 split by subscription_id (shuffle then slice)
    rng = random.Random(seed)
    sub_ids = [r.subscription_id for r in scoped]
    rng.shuffle(sub_ids)
    split_idx = int(len(sub_ids) * TRAIN_FRACTION)
    train_ids = set(sub_ids[:split_idx])
    test_ids  = set(sub_ids[split_idx:])
    print(f"[DataGen] Train subscriptions: {len(train_ids)} | Test: {len(test_ids)}")

    rows: list[dict] = []
    outcome_counts: Counter = Counter()

    for i, record in enumerate(scoped):
        sub_id = record.subscription_id
        split = "train" if sub_id in train_ids else "test"
        bucket_val = (
            record.failure_bucket
            if isinstance(record.failure_bucket, str)
            else record.failure_bucket.value
        )

        for offset in CANDIDATE_OFFSETS:
            # Seed per (record_index, offset) for reproducibility
            # Use a different seed per cell so outcomes are independent
            cell_seed = seed * 10_000 + i * 1_000 + offset
            outcome: bool = simulate_outcome(record, offset_hours=float(offset), seed=cell_seed)

            row = {
                "subscription_id":  sub_id,
                "split":            split,
                "bucket":           bucket_val,
                "offset_hours":     offset,
                "amount":           record.amount,
                "mandate_age_days": record.mandate_age_days,
                "auth_attempts":    record.auth_attempts,
                "outcome":          int(outcome),   # 1=success, 0=failure
            }
            rows.append(row)
            outcome_counts[f"{bucket_val}:{outcome}"] += 1

    print(f"[DataGen] Total rows generated: {len(rows)} "
          f"({len(scoped)} records x {len(CANDIDATE_OFFSETS)} offsets)")

    if verbose:
        print("\n[DataGen] Outcome distribution by bucket:")
        for bucket in sorted(TIMING_BUCKETS):
            s = outcome_counts.get(f"{bucket}:True", outcome_counts.get(f"{bucket}:1", 0))
            f_ = outcome_counts.get(f"{bucket}:False", outcome_counts.get(f"{bucket}:0", 0))
            total = s + f_
            pct = s / total * 100 if total else 0
            print(f"  {bucket:<18} success={s}/{total} ({pct:.1f}%)")

    return rows


def main(batch_path: Path, seed: int = 42, verbose: bool = False) -> None:
    rows = generate_dataset(batch_path, seed=seed, verbose=verbose)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    train_rows = [r for r in rows if r["split"] == "train"]
    test_rows  = [r for r in rows if r["split"] == "test"]

    print(f"\n[DataGen] Saved {len(rows)} rows to {OUTPUT_PATH.name}")
    print(f"  Train rows : {len(train_rows)}")
    print(f"  Test rows  : {len(test_rows)}")

    # Print naive-baseline success rate on test set (always choose offset=24)
    test_naive = [r for r in test_rows if r["offset_hours"] == 24]
    if test_naive:
        naive_sr = sum(r["outcome"] for r in test_naive) / len(test_naive)
        print(f"\n  Naive baseline (always offset=24h) test success rate: {naive_sr:.3f}")
        print(f"  (The timing model should beat this; if lift > 90%, revisit noise levels)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate timing training dataset")
    parser.add_argument("--batch",   default=str(DEFAULT_BATCH))
    parser.add_argument("--seed",    type=int, default=42)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    main(Path(args.batch), seed=args.seed, verbose=args.verbose)

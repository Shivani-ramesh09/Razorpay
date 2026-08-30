"""
timing/train_timing_model.py
────────────────────────────────────────────────────────────────────────────────
Trains a gradient-boosted timing model predicting success probability
from (bucket, offset_hours, amount, mandate_age_days, auth_attempts).

Model: LightGBM LGBMClassifier (installed, faster than sklearn GBM on tabular)
Fallback: sklearn GradientBoostingClassifier if lightgbm is unavailable.

Evaluation — TWO strategies on held-out test set:
──────────────────────────────────────────────────
  Naive baseline: always retry at offset=24h (earliest NPCI slot)
                  → success rate = mean(outcome) at offset=24 per record

  Model strategy: for each test record, pick the offset with the highest
                  predicted P(success) across all 10 candidate offsets
                  → success rate = mean(simulated outcome at that argmax offset)

  Lift = (model_rate - naive_rate) / naive_rate * 100

Anti-inflation guard:
  If lift > 90%: print [WARNING] + hard-stop (sys.exit(1))
  This signals that the generative function in outcome_simulator.py is too
  clean and needs more noise before the result can be honestly reported.

Outputs:
  data/timing_model.pkl         — serialised fitted model
  data/timing_model_meta.json   — feature names, eval metrics, noise params

Usage
─────
    python timing/train_timing_model.py
    python timing/train_timing_model.py --dataset data/timing_dataset.json
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── Model selection ───────────────────────────────────────────────────────────
try:
    from lightgbm import LGBMClassifier
    MODEL_BACKEND = "lightgbm"
except ImportError:
    from sklearn.ensemble import GradientBoostingClassifier as LGBMClassifier  # type: ignore
    MODEL_BACKEND = "sklearn_gbm"

from timing.outcome_simulator import NOISE_SIGMA

# ── Paths ──────────────────────────────────────────────────────────────────────
DATASET_PATH = ROOT / "data" / "timing_dataset.json"
MODEL_PATH   = ROOT / "data" / "timing_model.pkl"
META_PATH    = ROOT / "data" / "timing_model_meta.json"

FEATURES = ["bucket_encoded", "offset_hours", "amount", "mandate_age_days", "auth_attempts"]
BUCKET_ENCODING = {"bank_side": 0, "low_balance": 1}

LIFT_WARNING_THRESHOLD = 90.0   # %


# ── Feature engineering ───────────────────────────────────────────────────────

def _to_feature_matrix(rows: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    X, y = [], []
    for row in rows:
        bucket_enc = BUCKET_ENCODING.get(row["bucket"], -1)
        X.append([
            bucket_enc,
            row["offset_hours"],
            row["amount"],
            row["mandate_age_days"],
            row["auth_attempts"],
        ])
        y.append(row["outcome"])
    return np.array(X, dtype=float), np.array(y, dtype=float)


# ── Evaluation helpers ────────────────────────────────────────────────────────

def _evaluate(model, test_rows: list[dict]) -> dict:
    """
    Two-strategy evaluation on the test set.

    Returns dict with naive_success_rate, model_success_rate, lift_pct.
    """
    # Group test rows by subscription_id
    by_sub: dict[str, list[dict]] = {}
    for row in test_rows:
        by_sub.setdefault(row["subscription_id"], []).append(row)

    naive_outcomes = []
    model_outcomes = []

    for sub_id, rows in by_sub.items():
        # Sort by offset for determinism
        rows_sorted = sorted(rows, key=lambda r: r["offset_hours"])

        # Naive baseline: always pick offset=24
        naive_row = next((r for r in rows_sorted if r["offset_hours"] == 24), rows_sorted[0])
        naive_outcomes.append(naive_row["outcome"])

        # Model: pick argmax of predicted P(success)
        X_sub, _ = _to_feature_matrix(rows_sorted)
        proba = model.predict_proba(X_sub)[:, 1]  # P(success)
        best_idx = int(np.argmax(proba))
        best_row = rows_sorted[best_idx]
        model_outcomes.append(best_row["outcome"])

    naive_rate = float(np.mean(naive_outcomes))
    model_rate = float(np.mean(model_outcomes))
    lift_pct   = ((model_rate - naive_rate) / naive_rate * 100.0
                  if naive_rate > 0 else 0.0)

    return {
        "naive_success_rate":  round(naive_rate, 4),
        "model_success_rate":  round(model_rate, 4),
        "lift_pct":            round(lift_pct, 2),
        "n_test_subscriptions": len(by_sub),
    }


# ── Training ──────────────────────────────────────────────────────────────────

def train(dataset_path: Path = DATASET_PATH) -> None:
    # ── Load dataset ─────────────────────────────────────────────────────────
    rows = json.loads(dataset_path.read_text(encoding="utf-8"))
    train_rows = [r for r in rows if r["split"] == "train"]
    test_rows  = [r for r in rows if r["split"] == "test"]

    print(f"\n[Train] Loaded {len(rows)} rows | train={len(train_rows)} | test={len(test_rows)}")
    print(f"[Train] Model backend: {MODEL_BACKEND}")

    X_train, y_train = _to_feature_matrix(train_rows)

    # Overall class balance
    pos_rate = y_train.mean()
    print(f"[Train] Training class balance: {pos_rate:.3f} positive (success)")

    # ── Fit model ─────────────────────────────────────────────────────────────
    if MODEL_BACKEND == "lightgbm":
        model = LGBMClassifier(
            n_estimators=200,
            learning_rate=0.05,
            max_depth=4,
            num_leaves=15,
            random_state=42,
            verbose=-1,  # Suppress lightgbm output
        )
    else:
        model = LGBMClassifier(
            n_estimators=200,
            learning_rate=0.05,
            max_depth=4,
            random_state=42,
        )

    model.fit(X_train, y_train)
    print(f"[Train] Model fitted on {len(train_rows)} rows.")

    # ── Evaluate ──────────────────────────────────────────────────────────────
    eval_results = _evaluate(model, test_rows)

    print(f"\n[Eval] Naive baseline (always offset=24h): "
          f"{eval_results['naive_success_rate']:.3f} ({eval_results['naive_success_rate']*100:.1f}%)")
    print(f"[Eval] Model strategy (argmax offset)    : "
          f"{eval_results['model_success_rate']:.3f} ({eval_results['model_success_rate']*100:.1f}%)")
    print(f"[Eval] Lift over naive baseline          : {eval_results['lift_pct']:+.1f}%")
    print(f"       (Test subscriptions: {eval_results['n_test_subscriptions']})")

    # ── Anti-inflation guard ──────────────────────────────────────────────────
    if eval_results["lift_pct"] > LIFT_WARNING_THRESHOLD:
        print(f"\n[WARNING] Lift={eval_results['lift_pct']:.1f}% exceeds {LIFT_WARNING_THRESHOLD}% threshold.")
        print(f"          The generative function in timing/outcome_simulator.py")
        print(f"          may be too clean (too predictable).")
        print(f"          Increase NOISE_SIGMA values and regenerate the dataset before reporting.")
        sys.exit(1)  # Hard stop — do not save an overfit model
    else:
        print(f"\n[OK] Lift is within honest range (< {LIFT_WARNING_THRESHOLD}%). Model is valid.")

    # ── Save model ────────────────────────────────────────────────────────────
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MODEL_PATH.open("wb") as f:
        pickle.dump({"model": model, "features": FEATURES, "bucket_encoding": BUCKET_ENCODING}, f)
    print(f"[Train] Model saved to {MODEL_PATH.name}")

    # ── Save metadata ─────────────────────────────────────────────────────────
    meta = {
        "model_backend":    MODEL_BACKEND,
        "features":         FEATURES,
        "bucket_encoding":  BUCKET_ENCODING,
        "hyperparameters": {
            "n_estimators":  200,
            "learning_rate": 0.05,
            "max_depth":     4,
        },
        "noise_sigma": {
            k.value if hasattr(k, "value") else k: v
            for k, v in NOISE_SIGMA.items()
        },
        "evaluation": eval_results,
        "candidate_offsets": [24, 36, 48, 60, 72, 84, 96, 120, 144, 168],
        "lift_warning_threshold_pct": LIFT_WARNING_THRESHOLD,
        "honesty_note": (
            "Lift is derived from a noisy generative function, not real data. "
            "The noise prevents circular validation. "
            "Actual recovery uplift on real data will be validated in production."
        ),
    }
    META_PATH.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[Train] Metadata saved to {META_PATH.name}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Train the retry-timing model")
    parser.add_argument("--dataset", default=str(DATASET_PATH))
    args = parser.parse_args()
    train(Path(args.dataset))

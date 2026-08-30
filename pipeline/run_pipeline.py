"""
pipeline/run_pipeline.py
────────────────────────────────────────────────────────────────────────────────
Day 2 end-to-end pipeline runner.

Loads data/synthetic_batch.json, processes every record through:
    1. Failure Classifier  (error_code → FailureBucket, no failure_bucket peek)
    2. Baseline Action Picker (bucket → default action)
    3. Guardrail Validator  (enforce NPCI/RBI hard rules)
    4. Action Executor STUB  (logs the action; no real API calls)

Writes to:
    data/audit_log.jsonl    — one JSONL entry per record
    data/day2_summary.json  — aggregate stats

Prints at end of run:
    - Classifier accuracy vs. ground-truth failure_bucket
    - Per-class precision / recall summary
    - Action counts per bucket
    - Guardrail override count + rules that fired

Usage
-----
    python pipeline/run_pipeline.py
    python pipeline/run_pipeline.py --batch data/my_batch.json --verbose
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from schema.subscription_schema import FailureBucket, SubscriptionRecord
from classifier.rules_classifier import classify, explain as classifier_explain
from actions.baseline_action_picker import pick_action_with_rationale
from guardrails.validator import validate
from timing.predict import predict_optimal_offset
from agent.llm_agent import propose as llm_propose

# ── Paths ──────────────────────────────────────────────────────────────────────
DEFAULT_BATCH  = ROOT / "data" / "synthetic_batch.json"
AUDIT_LOG_PATH = ROOT / "data" / "audit_log.jsonl"
SUMMARY_PATH   = ROOT / "data" / "day2_summary.json"

# ── Stub executor ──────────────────────────────────────────────────────────────

def _execute_action_stub(
    subscription_id: str,
    final_action: str,
    verbose: bool = False,
) -> dict:
    """
    Action execution stub — logs the intent, makes NO real API calls.
    Returns a dict that gets written to the audit log.
    Day 5 replaces this with real channel dispatchers.
    """
    stub_result = {
        "executed": False,  # Stub: never actually executed
        "stub_note": f"[DAY 2 STUB] Would execute '{final_action}' for {subscription_id}",
    }
    if verbose:
        print(f"    [STUB] {subscription_id}: {final_action}")
    return stub_result


# ── Scoring helpers ────────────────────────────────────────────────────────────

def _compute_accuracy_and_confusion(
    ground_truths: list[str],
    predictions: list[str],
    labels: list[str],
) -> dict:
    """
    Return per-class TP/FP/FN counts plus overall accuracy.
    Avoids importing sklearn — keeps Day 2 dependencies minimal.
    """
    correct = sum(g == p for g, p in zip(ground_truths, predictions))
    accuracy = correct / len(ground_truths) if ground_truths else 0.0

    # Per-class counts
    per_class: dict[str, dict] = {}
    for label in labels:
        tp = sum(g == label and p == label for g, p in zip(ground_truths, predictions))
        fp = sum(g != label and p == label for g, p in zip(ground_truths, predictions))
        fn = sum(g == label and p != label for g, p in zip(ground_truths, predictions))
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1        = (2 * precision * recall / (precision + recall)
                     if (precision + recall) > 0 else 0.0)
        per_class[label] = {
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision, 3),
            "recall":    round(recall,    3),
            "f1":        round(f1,        3),
        }

    return {"accuracy": round(accuracy, 4), "per_class": per_class}


# ── Main pipeline ──────────────────────────────────────────────────────────────

def run_pipeline(batch_path: Path, verbose: bool = False) -> dict:
    """
    Execute the full Day 2 pipeline on all records in batch_path.
    Returns the summary dict (also written to SUMMARY_PATH).
    """
    # ── Load batch ────────────────────────────────────────────────────────────
    raw_records = json.loads(batch_path.read_text(encoding="utf-8"))
    records = [SubscriptionRecord(**r) for r in raw_records]
    total = len(records)
    print(f"\n[Pipeline] Loaded {total} records from {batch_path.name}")
    print(f"[Pipeline] Writing audit log -> {AUDIT_LOG_PATH.name}\n")

    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    # ── Tracking ──────────────────────────────────────────────────────────────
    ground_truths: list[str] = []
    predictions:   list[str] = []
    action_counts: Counter   = Counter()
    override_counts: Counter = Counter()  # rule_id → count
    total_overrides = 0

    audit_entries: list[dict] = []

    # ── Per-record processing ─────────────────────────────────────────────────
    for i, record in enumerate(records):
        # Ground truth (never fed into the classifier)
        ground_truth = (
            record.failure_bucket
            if isinstance(record.failure_bucket, str)
            else record.failure_bucket.value
        )
        ground_truths.append(ground_truth)

        # ── 1. Classify ───────────────────────────────────────────────────────
        classification_info = classifier_explain(record)
        classified_bucket_str: str = classification_info["classified_bucket"]
        classified_bucket = FailureBucket(classified_bucket_str)
        predictions.append(classified_bucket_str)

        # ── 1.5. Predict timing ───────────────────────────────────────────────
        optimal_offset = predict_optimal_offset(record)

        # ── 2. Pick action (LLM with Baseline Fallback) ───────────────────────
        context_vars = {
            "predicted_optimal_offset_hours": optimal_offset
        }
        try:
            llm_proposal = llm_propose(record, context_vars)
            proposed_action = llm_proposal.proposed_action
            action_rationale = llm_proposal.reasoning
            proposed_channel = llm_proposal.proposed_channel
            confidence = llm_proposal.confidence
        except Exception as e:
            if verbose and i < 5:
                print(f"  [{i+1:3d}] LLM failed ({e}), falling back to baseline.")
            action_info = pick_action_with_rationale(classified_bucket)
            proposed_action = action_info["action"]
            action_rationale = action_info["rationale"]
            proposed_channel = "N/A"
            confidence = "N/A"

        # ── 3. Guardrail validation ───────────────────────────────────────────
        # last_attempt_ts: use charge_at as a proxy for previous attempt time
        # (on a real event this would be the exact failed-charge timestamp)
        last_attempt_ts: Optional[int] = record.charge_at

        result = validate(record, proposed_action, last_attempt_ts=last_attempt_ts)

        final_action = result.final_action
        guardrail_approved = result.approved
        rule_triggered = result.rule_triggered
        guardrail_reason = result.reason

        if not guardrail_approved:
            total_overrides += 1
            if rule_triggered:
                override_counts[rule_triggered] += 1

        # ── 4. Action executor stub ───────────────────────────────────────────
        exec_result = _execute_action_stub(record.subscription_id, final_action, verbose)

        action_counts[final_action] += 1

        # ── 5. Build audit entry ──────────────────────────────────────────────
        entry = {
            "timestamp":          datetime.now(timezone.utc).isoformat(),
            "subscription_id":    record.subscription_id,
            "ground_truth_bucket": ground_truth,          # kept for scoring only
            "classified_bucket":  classified_bucket_str,
            "classification_path": classification_info["classification_path"],
            "error_code":         record.error_code,
            "proposed_action":    proposed_action,
            "guardrail_approved": guardrail_approved,
            "rule_triggered":     rule_triggered,
            "final_action":       final_action,
            "guardrail_reason":   guardrail_reason,
            "action_rationale":   action_rationale,
            "proposed_channel":   proposed_channel,
            "confidence":         confidence,
            "execution_stub":     exec_result["stub_note"],
            # Key fields for Day 3+ model features
            "amount":             record.amount,
            "above_15k":          record.above_15k_threshold,
            "auth_attempts":      record.auth_attempts,
            "mandate_age_days":   record.mandate_age_days,
            "predicted_optimal_offset_hours": optimal_offset,
        }
        audit_entries.append(entry)

        if verbose and i < 5:  # Print first 5 records in verbose mode
            print(f"  [{i+1:3d}] {record.subscription_id[:24]} | "
                  f"GT={ground_truth:<18} | CL={classified_bucket_str:<18} | "
                  f"{'OK' if classified_bucket_str == ground_truth else 'MISS'} | "
                  f"action={final_action} | approved={guardrail_approved}")

    # ── Write audit log ───────────────────────────────────────────────────────
    with AUDIT_LOG_PATH.open("w", encoding="utf-8") as f:
        for entry in audit_entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ── Compute scoring ───────────────────────────────────────────────────────
    labels = [b.value for b in FailureBucket if b != FailureBucket.NONE]
    scoring = _compute_accuracy_and_confusion(ground_truths, predictions, labels)

    # ── Build summary ─────────────────────────────────────────────────────────
    summary = {
        "run_timestamp":      datetime.now(timezone.utc).isoformat(),
        "batch_file":         str(batch_path.name),
        "total_records":      total,
        "classifier": {
            "accuracy":       scoring["accuracy"],
            "accuracy_pct":   f"{scoring['accuracy'] * 100:.1f}%",
            "per_class":      scoring["per_class"],
        },
        "guardrail": {
            "total_overrides":    total_overrides,
            "override_rate":      round(total_overrides / total, 4),
            "rules_fired":        dict(override_counts),
        },
        "action_distribution": dict(action_counts),
        "audit_log_path":     str(AUDIT_LOG_PATH),
    }

    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Print summary ─────────────────────────────────────────────────────────
    _print_summary(summary)

    return summary


def _print_summary(summary: dict) -> None:
    cls = summary["classifier"]
    grd = summary["guardrail"]
    acts = summary["action_distribution"]

    print("\n" + "=" * 65)
    print("  DAY 2 PIPELINE RUN COMPLETE")
    print("=" * 65)
    print(f"\n  Records processed : {summary['total_records']}")
    print(f"  Classifier accuracy: {cls['accuracy_pct']}  ({cls['accuracy']:.4f})")

    print("\n  Per-class classifier performance:")
    print(f"  {'Bucket':<22} {'Prec':>6}  {'Recall':>6}  {'F1':>6}  {'TP':>4}  {'FP':>4}  {'FN':>4}")
    print("  " + "-" * 58)
    for label, m in cls["per_class"].items():
        print(f"  {label:<22} {m['precision']:>6.3f}  {m['recall']:>6.3f}  "
              f"{m['f1']:>6.3f}  {m['tp']:>4d}  {m['fp']:>4d}  {m['fn']:>4d}")

    print(f"\n  Guardrail overrides: {grd['total_overrides']} / {summary['total_records']} "
          f"({grd['override_rate']*100:.1f}%)")
    if grd["rules_fired"]:
        for rule, count in sorted(grd["rules_fired"].items(), key=lambda x: -x[1]):
            print(f"    {rule:<30} {count} override(s)")
    else:
        print("    (no overrides — all proposed actions passed)")

    print("\n  Final action distribution:")
    for action, count in sorted(acts.items(), key=lambda x: -x[1]):
        bar = "#" * (count // 5)
        print(f"  {action:<26} {count:>4}  {bar}")

    print(f"\n  Audit log  : data/audit_log.jsonl  ({summary['total_records']} entries)")
    print(f"  Summary    : data/day2_summary.json")
    print("=" * 65 + "\n")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run Day 2 pipeline: classify → pick action → validate → log"
    )
    parser.add_argument(
        "--batch", type=str, default=str(DEFAULT_BATCH),
        help=f"Path to batch JSON file (default: {DEFAULT_BATCH.name})",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print first 5 record details during run",
    )
    args = parser.parse_args()

    run_pipeline(Path(args.batch), verbose=args.verbose)

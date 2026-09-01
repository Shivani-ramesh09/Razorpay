"""
pipeline/run_pipeline.py
────────────────────────────────────────────────────────────────────────────────
Day 4 end-to-end pipeline runner.

Loads data/synthetic_batch.json, processes every record through:
    1. Failure Classifier  (error_code → FailureBucket)
    2. Predictive Timing   (optimal retry offset in hours)
    3. Action Selection    (Groq openai/gpt-oss-120b LLM Agent with graceful
                            fallback to baseline_action_picker on error)
    4. Guardrail Validator (active override layer enforcing NPCI/RBI hard rules)
    5. Action Executor STUB (logs intent; no real external calls)

Writes to:
    data/audit_log.jsonl    — one JSONL entry per record (with decision_source)
    data/day2_summary.json  — aggregate run summary
    data/day4_summary.json  — aggregate run summary (Day 4)

Usage
-----
    python pipeline/run_pipeline.py
    python pipeline/run_pipeline.py --batch data/synthetic_batch.json --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from schema.subscription_schema import FailureBucket, SubscriptionRecord
from classifier.rules_classifier import classify, explain as classifier_explain
from actions.baseline_action_picker import pick_action_with_rationale
from guardrails.validator import validate
from timing.predict import predict_optimal_offset
from agent.llm_agent import propose as llm_propose
from actions.action_executor import execute_action
from actions.p2p_ledger import reset_ledger, get_ledger_stats

# ── Paths ──────────────────────────────────────────────────────────────────────
DEFAULT_BATCH   = ROOT / "data" / "synthetic_batch.json"
AUDIT_LOG_PATH  = ROOT / "data" / "audit_log.jsonl"
SUMMARY_PATH_D2 = ROOT / "data" / "day2_summary.json"
SUMMARY_PATH_D4 = ROOT / "data" / "day4_summary.json"
SUMMARY_PATH_D5 = ROOT / "data" / "day5_summary.json"


# ── Scoring helpers ────────────────────────────────────────────────────────────

def _compute_accuracy_and_confusion(
    ground_truths: list[str],
    predictions: list[str],
    labels: list[str],
) -> dict:
    """
    Return per-class TP/FP/FN counts plus overall accuracy.
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

def run_pipeline(
    batch_path: Path,
    verbose: bool = False,
    pace_delay_sec: float = 2.5,
) -> dict:
    """
    Execute the full Day 4 pipeline on all records in batch_path.
    Enforces minimum 2.5s spacing between calls to stay under Groq 30 RPM and 8000 TPM limit.
    Returns the summary dict (also written to summary files).
    """
    # Enforce minimum 2.5s pacing to stay strictly within 30 RPM / 8000 TPM
    effective_pace = max(2.5, pace_delay_sec)
    # ── Load batch ────────────────────────────────────────────────────────────
    raw_records = json.loads(batch_path.read_text(encoding="utf-8"))
    records = [SubscriptionRecord(**r) for r in raw_records]
    total = len(records)
    print(f"\n[Pipeline] Loaded {total} records from {batch_path.name}")
    print(f"[Pipeline] Writing audit log -> {AUDIT_LOG_PATH.name}\n")

    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    # ── Tracking ──────────────────────────────────────────────────────────────
    reset_ledger()
    ground_truths: list[str] = []
    predictions:   list[str] = []
    action_counts: Counter   = Counter()
    execution_channel_counts: Counter = Counter()
    decision_source_counts: Counter = Counter()
    confidence_counts: Counter = Counter()
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

        # ── 2. Pick action (LLM with Graceful Baseline Fallback) ──────────────
        context_vars = {
            "predicted_optimal_offset_hours": optimal_offset,
            "opt_out": getattr(record, "opt_out", False),
            "above_15k_threshold": getattr(record, "above_15k_threshold", False),
        }

        try:
            llm_proposal = llm_propose(record, context_vars, raise_on_error=True)
            proposed_action = llm_proposal.proposed_action
            action_rationale = llm_proposal.reasoning
            proposed_channel = llm_proposal.proposed_channel
            confidence = llm_proposal.confidence
            decision_source = "llm"
        except Exception as e:
            if verbose or i < 5:
                print(f"  [{i+1:3d}] LLM proposal failed ({e}), falling back to baseline.")
            action_info = pick_action_with_rationale(classified_bucket, record=record)
            proposed_action = action_info["action"]
            action_rationale = action_info["rationale"]
            proposed_channel = "N/A"
            confidence = "N/A"
            decision_source = "baseline_fallback"

        decision_source_counts[decision_source] += 1
        confidence_counts[confidence] += 1

        # ── 3. Guardrail validation (Active Override Layer) ───────────────────
        # last_attempt_ts: use charge_at as a proxy for previous attempt time
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

        # ── 4. Action execution (Mock Multi-Channel Dispatch) ─────────────────
        receipt = execute_action(final_action, record, proposed_channel)

        action_counts[final_action] += 1
        execution_channel_counts[receipt.channel] += 1

        # A dispatch is considered fully successful ('ok') only if:
        # 1. guardrail approved the proposed action (no regulatory override needed)
        # 2. receipt status is 'dispatched'
        dispatch_ok = bool(guardrail_approved and receipt.status == "dispatched")

        # ── 5. Build audit entry ──────────────────────────────────────────────
        entry = {
            "timestamp":          datetime.now(timezone.utc).isoformat(),
            "subscription_id":    record.subscription_id,
            "ground_truth_bucket": ground_truth,
            "classified_bucket":  classified_bucket_str,
            "classification_path": classification_info["classification_path"],
            "error_code":         record.error_code,
            "decision_source":    decision_source,       # 'llm' or 'baseline_fallback'
            "proposed_action":    proposed_action,
            "guardrail_approved": guardrail_approved,
            "rule_triggered":     rule_triggered,
            "final_action":       final_action,
            "guardrail_reason":   guardrail_reason,
            "action_rationale":   action_rationale,
            "proposed_channel":   proposed_channel,
            "confidence":         confidence,
            "mock_reference_id":  receipt.mock_reference_id,
            "execution_status":   receipt.status,
            "execution_channel":  receipt.channel,
            "dispatch_ok":        dispatch_ok,
            "above_15k":          receipt.above_15k if receipt.above_15k is not None else record.above_15k_threshold,
            "execution_details":  receipt.details,
            "amount":             record.amount,
            "auth_attempts":      record.auth_attempts,
            "mandate_age_days":   record.mandate_age_days,
            "predicted_optimal_offset_hours": optimal_offset,
        }
        audit_entries.append(entry)

        # Write incrementally to disk
        mode = "w" if i == 0 else "a"
        with AUDIT_LOG_PATH.open(mode, encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # Progress printing
        if verbose or (i + 1) % 10 == 0 or (i + 1) == total or (i < 5) or not dispatch_ok:
            print(f"  [{i+1:3d}/{total}] {record.subscription_id[:20]:<20} | "
                  f"CL={classified_bucket_str:<16} | src={decision_source:<17} | "
                  f"action={final_action:<20} | ref={receipt.mock_reference_id:<17} | ok={str(dispatch_ok):<5}", flush=True)

        time.sleep(effective_pace)

    # ── Compute scoring ───────────────────────────────────────────────────────
    labels = [b.value for b in FailureBucket if b != FailureBucket.NONE]
    scoring = _compute_accuracy_and_confusion(ground_truths, predictions, labels)

    # ── Isolate LLM-only low_balance metrics ─────────────────────────────────
    llm_low_bal = [e for e in audit_entries if e["classified_bucket"] == "low_balance" and e["decision_source"] == "llm"]
    repeat_failures = [e for e in llm_low_bal if e["auth_attempts"] >= 2]
    first_time_failures = [e for e in llm_low_bal if e["auth_attempts"] == 1]

    repeat_nudges = sum(1 for e in repeat_failures if e["proposed_action"] == "promise_to_pay_nudge")
    repeat_retries = sum(1 for e in repeat_failures if e["proposed_action"] == "delayed_retry")
    first_retries = sum(1 for e in first_time_failures if e["proposed_action"] == "delayed_retry")
    first_nudges = sum(1 for e in first_time_failures if e["proposed_action"] == "promise_to_pay_nudge")

    llm_low_bal_isolation = {
        "total_llm_low_balance": len(llm_low_bal),
        "repeat_failures_attempts_gte_2": {
            "total": len(repeat_failures),
            "promise_to_pay_nudge": repeat_nudges,
            "delayed_retry": repeat_retries,
            "nudge_rate_pct": f"{(repeat_nudges / len(repeat_failures))*100:.1f}%" if repeat_failures else "0.0%",
        },
        "first_time_failures_attempts_1": {
            "total": len(first_time_failures),
            "delayed_retry": first_retries,
            "promise_to_pay_nudge": first_nudges,
            "delayed_retry_rate_pct": f"{(first_retries / len(first_time_failures))*100:.1f}%" if first_time_failures else "0.0%",
        },
    }

    failed_dispatches = [
        {
            "subscription_id": e["subscription_id"],
            "decision_source": e["decision_source"],
            "proposed_action": e["proposed_action"],
            "final_action": e["final_action"],
            "rule_triggered": e["rule_triggered"],
            "reason": e["guardrail_reason"],
            "execution_status": e["execution_status"],
        }
        for e in audit_entries
        if not e.get("dispatch_ok", True)
    ]
    successful_dispatches_count = len(audit_entries) - len(failed_dispatches)
    receipt_coverage_pct = f"{(successful_dispatches_count / total) * 100:.1f}%" if total else "0.0%"

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
        "decision_source_breakdown": {
            "llm":                decision_source_counts["llm"],
            "baseline_fallback":  decision_source_counts["baseline_fallback"],
            "llm_rate_pct":       f"{(decision_source_counts['llm'] / total) * 100:.1f}%" if total else "0.0%",
        },
        "llm_low_balance_isolation": llm_low_bal_isolation,
        "confidence_distribution": dict(confidence_counts),
        "guardrail": {
            "total_overrides":    total_overrides,
            "override_rate":      round(total_overrides / total, 4),
            "rules_fired":        dict(override_counts),
        },
        "action_distribution": dict(action_counts),
        "execution_summary": {
            "total_dispatched": len(audit_entries),
            "successful_dispatches": successful_dispatches_count,
            "failed_dispatches_count": len(failed_dispatches),
            "failed_dispatches": failed_dispatches,
            "receipt_coverage_pct": receipt_coverage_pct,
            "by_channel": dict(execution_channel_counts),
            "reauth_above_15k_count": sum(
                1 for e in audit_entries if e.get("final_action") == "reauth_request" and e.get("above_15k") is True
            ),
        },
        "p2p_ledger_summary": get_ledger_stats(),
        "audit_log_path":     str(AUDIT_LOG_PATH),
        "notes": [
            "Confidence note: observed 'high' across standard cases with low variation — recommend testing with deliberately ambiguous cases before relying on confidence for downstream branching."
        ],
    }

    SUMMARY_PATH_D2.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    SUMMARY_PATH_D4.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    SUMMARY_PATH_D5.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Print summary ─────────────────────────────────────────────────────────
    _print_summary(summary)

    return summary


def _print_summary(summary: dict) -> None:
    cls = summary["classifier"]
    src = summary["decision_source_breakdown"]
    grd = summary["guardrail"]
    acts = summary["action_distribution"]
    conf = summary.get("confidence_distribution", {})
    llm_lb = summary.get("llm_low_balance_isolation", {})
    exec_sum = summary.get("execution_summary", {})
    p2p_sum = summary.get("p2p_ledger_summary", {})

    print("\n" + "=" * 70)
    print("  DAY 5 PIPELINE RUN COMPLETE (LLM + Guardrails + Multi-Channel Execution)")
    print("=" * 70)
    print(f"\n  Records processed      : {summary['total_records']}")
    print(f"  Classifier accuracy    : {cls['accuracy_pct']}  ({cls['accuracy']:.4f})")

    print(f"\n  Decision Source Breakdown:")
    print(f"    • LLM (Groq)         : {src['llm']:>4} ({src['llm_rate_pct']})")
    print(f"    • Baseline Fallback  : {src['baseline_fallback']:>4}")

    if llm_lb:
        rf = llm_lb.get("repeat_failures_attempts_gte_2", {})
        ff = llm_lb.get("first_time_failures_attempts_1", {})
        print(f"\n  Isolated LLM Decisioning for low_balance (n={llm_lb.get('total_llm_low_balance', 0)}):")
        print(f"    • Repeat failures (attempts >= 2, n={rf.get('total', 0)}):")
        print(f"        - promise_to_pay_nudge : {rf.get('promise_to_pay_nudge', 0)} ({rf.get('nudge_rate_pct', 'N/A')})")
        print(f"        - delayed_retry        : {rf.get('delayed_retry', 0)}")
        print(f"    • First-time failures (attempts == 1, n={ff.get('total', 0)}):")
        print(f"        - delayed_retry        : {ff.get('delayed_retry', 0)} ({ff.get('delayed_retry_rate_pct', 'N/A')})")
        print(f"        - promise_to_pay_nudge : {ff.get('promise_to_pay_nudge', 0)}")

    if conf:
        print(f"\n  Confidence Distribution:")
        for c_val, count in sorted(conf.items(), key=lambda x: -x[1]):
            print(f"    • {c_val:<18} : {count:>4}")

    print("\n  Per-class classifier performance:")
    print(f"  {'Bucket':<22} {'Prec':>6}  {'Recall':>6}  {'F1':>6}  {'TP':>4}  {'FP':>4}  {'FN':>4}")
    print("  " + "-" * 60)
    for label, m in cls["per_class"].items():
        print(f"  {label:<22} {m['precision']:>6.3f}  {m['recall']:>6.3f}  "
              f"{m['f1']:>6.3f}  {m['tp']:>4d}  {m['fp']:>4d}  {m['fn']:>4d}")

    print(f"\n  Guardrail overrides: {grd['total_overrides']} / {summary['total_records']} "
          f"({grd['override_rate']*100:.1f}%)")
    if grd["rules_fired"]:
        for rule, count in sorted(grd["rules_fired"].items(), key=lambda x: -x[1]):
            print(f"    {rule:<32} {count} override(s)")
    else:
        print("    (no overrides — all proposed actions passed)")

    print("\n  Final action distribution:")
    for action, count in sorted(acts.items(), key=lambda x: -x[1]):
        bar = "#" * (count // 5)
        print(f"  {action:<26} {count:>4}  {bar}")

    if exec_sum:
        print(f"\n  Multi-Channel Execution Receipts:")
        print(f"    • Total receipts processed  : {exec_sum.get('total_dispatched', 0)} / {summary['total_records']}")
        print(f"    • Successful Dispatches (ok): {exec_sum.get('successful_dispatches', 0)} ({exec_sum.get('receipt_coverage_pct', '0%')})")
        print(f"    • Failed Dispatches         : {exec_sum.get('failed_dispatches_count', 0)}")
        if exec_sum.get("failed_dispatches"):
            print(f"      Failed Dispatches Details:")
            for fd in exec_sum["failed_dispatches"]:
                print(f"        - {fd['subscription_id']} | src={fd['decision_source']} | proposed={fd['proposed_action']} -> final={fd['final_action']} | rule={fd.get('rule_triggered')}")
                print(f"          reason: {fd.get('reason')}")
        print(f"    • Re-auth >₹15k (RBI Tagged): {exec_sum.get('reauth_above_15k_count', 0)}")
        print(f"    • Dispatch by Channel:")
        for ch, count in sorted(exec_sum.get("by_channel", {}).items(), key=lambda x: -x[1]):
            print(f"        - {ch:<16} : {count:>4}")

    if p2p_sum:
        print(f"\n  Promise-to-Pay (P2P) Ledger Tracking:")
        print(f"    • Total Nudges Logged       : {p2p_sum.get('total_nudges', 0)}")
        print(f"    • Commitment Rate           : {p2p_sum.get('commitment_rate_pct', '0%')}")
        print(f"    • Total Value Addressed     : ₹{p2p_sum.get('total_value_addressed_inr', 0):,.2f}")
        print(f"    • Simulated Outcomes:")
        for out_name, count in sorted(p2p_sum.get("outcomes", {}).items(), key=lambda x: -x[1]):
            print(f"        - {out_name:<24} : {count:>4}")

    print(f"\n  ⚠️  Notice on Model Confidence:")
    for note in summary.get("notes", []):
        print(f"    {note}")

    print(f"\n  Audit log  : data/audit_log.jsonl  ({summary['total_records']} entries)")
    print(f"  P2P Ledger : data/p2p_ledger.json  ({p2p_sum.get('total_nudges', 0)} entries)")
    print(f"  Summary    : data/day5_summary.json")
    print("=" * 70 + "\n")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run Day 5 pipeline: classify → timing → LLM action → guardrail validate → multi-channel execute → log"
    )
    parser.add_argument(
        "--batch", type=str, default=str(DEFAULT_BATCH),
        help=f"Path to batch JSON file (default: {DEFAULT_BATCH.name})",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print first 5 record details during run",
    )
    parser.add_argument(
        "--pace", type=float, default=2.2,
        help="Delay in seconds between records to pace API calls (default: 2.2s)",
    )
    args = parser.parse_args()

    run_pipeline(Path(args.batch), verbose=args.verbose, pace_delay_sec=args.pace)

"""
agent/run_manual_eval.py
────────────────────────────────────────────────────────────────────────────────
Runs the standalone LLM agent (Groq / openai/gpt-oss-120b) against 15 hand-crafted
test cases and prints the proposed actions and reasoning for manual review.

Validates schema compliance and specifically flags hard constraint violations
(e.g., opt_out=True -> stand_down).

Usage:
    python agent/run_manual_eval.py
"""

from __future__ import annotations

import functools
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

# Force unbuffered printing
print = functools.partial(print, flush=True)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip("'\"")
                if k and k not in os.environ:
                    os.environ[k] = v

from schema.subscription_schema import SubscriptionRecord
from agent.llm_agent import propose, DEFAULT_MODEL

TEST_CASES_PATH = ROOT / "agent" / "test_cases_manual.json"

VALID_ACTIONS = {
    "delayed_retry",
    "reauth_request",
    "promise_to_pay_nudge",
    "stand_down",
    "escalate_to_human",
}
VALID_CHANNELS = {"upi_autopay", "sms", "whatsapp", "email", "human_agent"}
VALID_CONFIDENCES = {"high", "medium", "low"}


def check_hard_constraints(
    case_id: int,
    scenario: str,
    record: SubscriptionRecord,
    context: dict,
    proposal_action: str,
) -> list[str]:
    """
    Checks if proposal violates explicit prompt rules.
    Returns a list of violation messages.
    """
    violations = []
    opt_out = context.get("opt_out", getattr(record, "opt_out", False))
    bucket = (
        record.failure_bucket
        if isinstance(record.failure_bucket, str)
        else record.failure_bucket.value
    )
    above_15k = context.get(
        "above_15k_threshold",
        getattr(record, "above_15k_threshold", False) or (record.amount >= 1500000 if record.amount else False),
    )

    # Constraint 1: opt_out -> MUST be stand_down
    if opt_out and proposal_action != "stand_down":
        violations.append(
            f"Rule 1 Violation (Opt-out): opt_out=True but proposed_action='{proposal_action}' (Expected 'stand_down')"
        )

    # Constraint 2: genuine_decline -> MUST be stand_down
    if bucket == "genuine_decline" and proposal_action != "stand_down":
        violations.append(
            f"Rule 5 Violation (Genuine Decline): bucket='genuine_decline' but proposed_action='{proposal_action}' (Expected 'stand_down')"
        )

    # Constraint 3: above 15k + reauth_mismatch -> MUST be reauth_request
    if above_15k and bucket == "reauth_mismatch" and proposal_action != "reauth_request":
        violations.append(
            f"Rule 2 Violation (RBI >15k limit): above_15k=True & reauth_mismatch but proposed_action='{proposal_action}' (Expected 'reauth_request')"
        )

    # Constraint 4: max attempts / halted -> stand_down or escalate_to_human
    if (record.auth_attempts >= 3 or record.status == "halted") and proposal_action not in {
        "stand_down",
        "escalate_to_human",
    }:
        violations.append(
            f"Rule 3 Violation (Max attempts/halted): attempts={record.auth_attempts}, status={record.status} but proposed_action='{proposal_action}' (Expected 'stand_down' or 'escalate_to_human')"
        )

def check_reasoning_groundedness(
    record: SubscriptionRecord,
    context: dict,
    reasoning: str,
) -> list[str]:
    """
    Lightweight check to flag if reasoning text mentions specifics not traceable
    to the record fields (e.g. invented bank names, fake claims).
    """
    flags = []
    text_lower = reasoning.lower()

    # 1. Check for specific bank names not present in error_description
    known_banks = ["hdfc", "sbi", "icici", "axis", "kotak", "pnb", "bob", "yes bank", "indusind"]
    for bank in known_banks:
        if bank in text_lower:
            err_desc = (record.error_description or "").lower()
            if bank not in err_desc:
                flags.append(
                    f"Ungrounded Bank Reference: Mentions bank '{bank.upper()}' which is not in record fields."
                )

    # 2. Check for invented customer backstory/claims
    invented_patterns = [
        ("customer called", "claims customer called"),
        ("customer promised", "claims customer made a promise"),
        ("salary credited", "claims salary was credited"),
        ("credit score", "claims credit score details"),
        ("lost phone", "claims lost phone"),
    ]
    for pattern, desc in invented_patterns:
        if pattern in text_lower and pattern not in (record.error_description or "").lower():
            flags.append(f"Ungrounded Backstory: Reasoning {desc} not found in input data.")

    return flags


def main():
    if not TEST_CASES_PATH.exists():
        print(f"Error: {TEST_CASES_PATH} not found.")
        sys.exit(1)

    model_name = os.getenv("GROQ_MODEL", DEFAULT_MODEL)
    api_key = os.getenv("GROQ_API_KEY")

    has_real_key = bool(api_key and api_key.strip() != "your_groq_api_key_here")

    print("=" * 80)
    print(" MANDATE RECOVERY AGENT — MANUAL EVALUATION")
    print(f" Model       : {model_name}")
    print(f" Provider    : Groq API (OpenAI-compatible)")
    print(f" API Key     : {'Configured (' + api_key[:6] + '...' + api_key[-4:] + ')' if has_real_key else '[NOT SET / PLACEHOLDER - Please set GROQ_API_KEY in .env]'}")
    print("=" * 80)

    if not has_real_key:
        print("\n[ACTION REQUIRED] To execute live requests against Groq:")
        print("1. Open .env")
        print("2. Replace `GROQ_API_KEY=your_groq_api_key_here` with your real Groq API key (gsk_...)")
        print("3. Run `python agent/run_manual_eval.py`")
        print("=" * 80)
        sys.exit(1)

    cases = json.loads(TEST_CASES_PATH.read_text(encoding="utf-8"))
    print(f"Loaded {len(cases)} manual test cases.\n")

    results_summary = []

    for idx, case in enumerate(cases):
        case_id = case["id"]
        scenario = case["scenario"]
        print("=" * 80)
        print(f"CASE {case_id:2d}: {scenario}")
        print("-" * 80)

        raw = dict(case["record"])
        raw["subscription_id"] = "sub_" + raw["subscription_id"][4:].replace("_", "")
        raw.setdefault("customer_id", "cust_dummy1")
        raw.setdefault("plan_id", "plan_dummy1")
        raw.setdefault("paid_count", 1)
        rem = raw.get("remaining_count", 0)
        raw.setdefault("total_count", rem + 2)
        raw.setdefault("above_15k_threshold", raw.get("amount", 0) >= 1500000)

        record = SubscriptionRecord(**raw)
        context = dict(case.get("context", {}))

        print(f"  Subscription ID : {record.subscription_id}")
        print(f"  Failure Bucket  : {record.failure_bucket}")
        print(f"  Auth Attempts   : {record.auth_attempts} (Status: {record.status})")
        print(f"  Amount (paise)  : ₹{record.amount / 100:,.2f} ({record.amount})")
        if "predicted_optimal_offset_hours" in context:
            print(f"  Predicted Timing: +{context['predicted_optimal_offset_hours']}h")
        if context.get("opt_out", getattr(record, "opt_out", False)):
            print(f"  Opt Out         : TRUE (Hard Constraint: Must Stand Down)")
        if getattr(record, "above_15k_threshold", False):
            print(f"  Above 15k Limit : TRUE (Hard Constraint: Must Re-auth)")

        print(f"\n  [Groq Request] Calling {model_name}...")
        start_t = time.time()
        proposal = propose(record, context)
        elapsed = time.time() - start_t

        data = asdict(proposal)
        print(f"  [Response ({elapsed:.2f}s)]:")
        print(f"    proposed_action  : {data['proposed_action']}")
        print(f"    proposed_channel : {data['proposed_channel']}")
        print(f"    confidence       : {data['confidence']}")
        print(f"    reasoning        : {data['reasoning']}")

        # Schema validations
        schema_issues = []
        if data["proposed_action"] not in VALID_ACTIONS:
            schema_issues.append(f"Invalid proposed_action: {data['proposed_action']}")
        if data["proposed_channel"] not in VALID_CHANNELS:
            schema_issues.append(f"Invalid proposed_channel: {data['proposed_channel']}")
        if data["confidence"] not in VALID_CONFIDENCES:
            schema_issues.append(f"Invalid confidence: {data['confidence']}")

        if schema_issues:
            for issue in schema_issues:
                print(f"  [SCHEMA ERROR] {issue}")

        # Check for API error
        is_api_error = "Error getting proposal" in data["reasoning"] or "GROQ_API_KEY not configured" in data["reasoning"]

        # Check hard constraints
        violations = check_hard_constraints(
            case_id, scenario, record, context, data["proposed_action"]
        )

        # Check reasoning groundedness (Rule 6)
        groundedness_flags = check_reasoning_groundedness(record, context, data["reasoning"])

        status_flag = "PASS"
        if is_api_error:
            status_flag = "API_ERROR"
            print(f"\n  ❌  [API ERROR] {data['reasoning']}")
        elif violations:
            status_flag = "VIOLATION"
            for v in violations:
                print(f"\n  ⚠️  [HARD CONSTRAINT VIOLATION] {v}")
        elif schema_issues:
            status_flag = "INVALID_SCHEMA"
        else:
            print(f"\n  ✅ [CONSTRAINT CHECK] Passed all prompt rules.")

        if groundedness_flags:
            for flag in groundedness_flags:
                print(f"  🔍 [GROUNDEDNESS WARNING] {flag}")

        results_summary.append({
            "id": case_id,
            "scenario": scenario,
            "bucket": record.failure_bucket if isinstance(record.failure_bucket, str) else record.failure_bucket.value,
            "action": data["proposed_action"],
            "channel": data["proposed_channel"],
            "confidence": data["confidence"],
            "reasoning": data["reasoning"],
            "status": status_flag,
            "violations": violations,
        })

        print("=" * 80 + "\n")

        # Rate-limiting: Groq allows 30 RPM -> 2.5s delay keeps rate at ~24 RPM safely
        if idx < len(cases) - 1:
            time.sleep(2.5)

    # ── Final Comparison Table ────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print(f" EVALUATION SUMMARY — Model: {model_name}")
    print("=" * 100)
    print(f"{'ID':<4} | {'Scenario':<28} | {'Bucket':<16} | {'Action':<22} | {'Channel':<13} | {'Conf':<6} | {'Status'}")
    print("-" * 100)
    violation_count = 0
    for r in results_summary:
        status_display = "✅ PASS" if r["status"] == "PASS" else f"❌ {r['status']}"
        if r["status"] != "PASS":
            violation_count += 1
        print(f"{r['id']:<4} | {r['scenario'][:28]:<28} | {r['bucket'][:16]:<16} | {r['action'][:22]:<22} | {r['channel'][:13]:<13} | {r['confidence'][:6]:<6} | {status_display}")

    print("=" * 100)
    print(f"Total Cases: {len(results_summary)} | Passed: {len(results_summary) - violation_count} | Violations: {violation_count}")

    # Highlight Case 7 specifically
    case7 = next((r for r in results_summary if r["id"] == 7), None)
    if case7:
        print("\n" + "─" * 80)
        print(" CASE 7 FOCUS (Opt-out Hard Constraint Check):")
        print(f"   Model Proposed Action : {case7['action']}")
        print(f"   Model Reasoning       : {case7['reasoning']}")
        if case7["action"] == "stand_down":
            print("   Verdict               : ✅ SUCCESS — Model correctly obeyed opt_out=True rule and stood down.")
        else:
            print(f"   Verdict               : ❌ VIOLATION — Model failed prompt constraint! (Proposed: '{case7['action']}', Expected: 'stand_down')")
        print("─" * 80)

    print("=" * 100 + "\n")


if __name__ == "__main__":
    main()

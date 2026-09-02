"""
demo_scenarios/scenario_a_guardrail_restraint.py
────────────────────────────────────────────────────────────────────────────────
DEMO SCENARIO A: Guardrail Restraint (Active Override of Over-Aggressive Retry)

Showcases the deterministic Guardrail Validator intercepting an illegal retry
proposal on an exhausted subscription (auth_attempts = 3) and forcefully
overriding it to compliant stand_down per NPCI rules.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from schema.subscription_schema import (
    FailureBucket,
    SubscriptionRecord,
    SubscriptionStatus,
)
from guardrails.validator import validate
from actions.action_executor import execute_action


def run_scenario_a():
    print("\n" + "═" * 78)
    print("  DEMO SCENARIO A: GUARDRAIL RESTRAINT & REGULATORY OVERRIDE")
    print("  Theme: 'Safety First — The agent is powerful, but guardrails are absolute.'")
    print("═" * 78)

    # 1. Manually construct an exhausted subscription record
    record = SubscriptionRecord(
        subscription_id="sub_DemoExhausted001",
        customer_id="cust_RahulSharma98",
        plan_id="plan_EnterpriseSaasAnnual",
        status=SubscriptionStatus.PENDING,
        auth_attempts=3,  # NPCI MAX REACHED (3 attempts already made)
        paid_count=4,
        remaining_count=8,
        total_count=12,
        amount=149900,  # ₹1,499.00
        failure_bucket=FailureBucket.BANK_SIDE,
        error_code="BANK_SYSTEM_ERROR",
        error_description="Bank switch unavailable during batch window",
        mandate_age_days=180,
        above_15k_threshold=False,
    )

    bucket_val = record.failure_bucket.value if hasattr(record.failure_bucket, "value") else str(record.failure_bucket)
    status_val = record.status.value if hasattr(record.status, "value") else str(record.status)

    print("\n[1] INCOMING SUBSCRIPTION STATE:")
    print(f"    • Subscription ID  : {record.subscription_id}")
    print(f"    • Amount           : ₹{record.amount / 100:,.2f}")
    print(f"    • Failure Bucket   : {bucket_val} ({record.error_code})")
    print(f"    • Auth Attempts    : {record.auth_attempts} / 3 (NPCI Maximum Reached)")
    print(f"    • Cycle Status     : {status_val}")

    # 2. Simulated over-aggressive proposal (e.g. from an agent or baseline picker)
    proposed_action = "delayed_retry"
    proposed_channel = "upi_autopay"

    print("\n[2] OVER-AGGRESSIVE PROPOSAL (Pre-Guardrail):")
    print(f"    • Proposed Action  : '{proposed_action}'")
    print(f"    • Proposed Channel : '{proposed_channel}'")
    print("    • Intent           : Blindly schedule a 4th automated debit attempt.")
    print("    ⚠️  VIOLATION RISK  : NPCI allows max 3 automated retries per cycle.")
    print("                         Firing a 4th attempt causes bank rejection & penalty.")

    # 3. Guardrail validation
    print("\n[3] GUARDRAIL VALIDATION IN PROGRESS...")
    time.sleep(0.3)
    result = validate(record, proposed_action)

    print("\n[4] GUARDRAIL INTERCEPTION VERDICT:")
    print(f"    • Approved         : {result.approved}  ❌ [PROPOSAL REJECTED]")
    print(f"    • Rule Triggered   : {result.rule_triggered} (Authority: NPCI)")
    print(f"    • Override Reason  : {result.reason}")
    print(f"    • Enforced Action  : '{result.final_action}' ✅ [COMPLIANT ACTION]")

    # 4. Action execution
    receipt = execute_action(result.final_action, record, channel="internal_system")

    print("\n[5] COMPLIANT EXECUTION RECEIPT:")
    print(f"    • Executed Action  : {receipt.action}")
    print(f"    • Channel          : {receipt.channel}")
    print(f"    • Reference ID     : {receipt.mock_reference_id}")
    print(f"    • Dispatch Status  : {receipt.status}")
    print(f"    • Audit Log Entry  : Logged with override flag for compliance review.")

    print("\n" + "─" * 78)
    print("  KEY TAKEAWAY FOR MERCHANTS & RAZORPAY:")
    print("  The Guardrail layer acts as a deterministic safety circuit-breaker.")
    print("  No matter what an LLM or fallback proposes, illegal actions CANNOT execute.")
    print("═" * 78 + "\n")


if __name__ == "__main__":
    run_scenario_a()

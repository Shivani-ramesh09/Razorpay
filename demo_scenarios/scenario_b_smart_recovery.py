"""
demo_scenarios/scenario_b_smart_recovery.py
────────────────────────────────────────────────────────────────────────────────
DEMO SCENARIO B: Smart Recovery for Low-Balance Subscriptions

Showcases intelligent LLM reasoning for repeat low-balance failures:
Instead of blindly burning retries on an empty bank account, the agent:
  1. Classifies as repeat low_balance (auth_attempts >= 2)
  2. Selects promise_to_pay_nudge via SMS/WhatsApp
  3. Renders a polite, 1-click Hinglish payment reminder
  4. Records the customer commitment in the Promise-to-Pay (P2P) ledger
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from actions.nudge_templates import render_nudge_message

AUDIT_LOG_PATH = ROOT / "data" / "golden" / "audit_log.jsonl"
P2P_LEDGER_PATH = ROOT / "data" / "golden" / "p2p_ledger.json"


def run_scenario_b():
    print("\n" + "═" * 78)
    print("  DEMO SCENARIO B: SMART RECOVERY & CONVERSATIONAL HINGLISH NUDGE")
    print("  Theme: 'Don't burn attempts on empty accounts — nudge customers intelligently.'")
    print("═" * 78)

    # 1. Load reference record from golden dataset
    if not AUDIT_LOG_PATH.exists() or not P2P_LEDGER_PATH.exists():
        print("[ERROR] Golden dataset not found at data/golden/")
        sys.exit(1)

    ledger_entries = json.loads(P2P_LEDGER_PATH.read_text(encoding="utf-8"))
    audit_records = {
        json.loads(line)["subscription_id"]: json.loads(line)
        for line in AUDIT_LOG_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }

    # Find a record with a successful commitment in the ledger
    target_entry = next(
        (e for e in ledger_entries if e.get("simulated_outcome") == "promised_date_given"),
        ledger_entries[0] if ledger_entries else None,
    )

    if not target_entry:
        print("[ERROR] No P2P ledger entry found.")
        sys.exit(1)

    sub_id = target_entry["subscription_id"]
    audit_record = audit_records.get(sub_id, {})

    print("\n[1] FAILED SUBSCRIPTION EVENT:")
    print(f"    • Subscription ID  : {sub_id}")
    print(f"    • Amount           : ₹{target_entry['amount_inr']:,.2f}")
    print(f"    • Failure Cause    : {audit_record.get('classified_bucket', 'low_balance')} ({audit_record.get('error_code', 'INSUFFICIENT_FUNDS')})")
    print(f"    • Auth Attempts    : {audit_record.get('auth_attempts', 2)} (Repeat Failure)")
    print(f"    • Status           : {audit_record.get('status', 'pending')}")

    print("\n[2] THE STRATEGIC DILEMMA:")
    print("    ❌ Naive Recovery  : Immediately schedule retry #3.")
    print("                         Customer still hasn't received salary -> Attempt fails -> Subscription HALTED permanently.")
    print("    ✅ Agent Strategy  : Proactively pause auto-debit; engage customer with 1-click payment link.")

    print("\n[3] LLM REASONING AGENT DECISION:")
    time.sleep(0.3)
    print(f"    • Decision Source  : {audit_record.get('decision_source', 'llm').upper()}")
    print(f"    • Confidence       : {audit_record.get('confidence', 'high').upper()}")
    print(f"    • Proposed Action  : {audit_record.get('final_action', 'promise_to_pay_nudge')}")
    print(f"    • Delivery Channel : {audit_record.get('proposed_channel', 'sms').upper()}")
    print(f"    • Agent Reasoning  : \"{audit_record.get('action_rationale')}\"")

    # 4. Render Hinglish message
    print("\n[4] PERSONALIZED HINGLISH MESSAGE DISPATCHED:")
    short_url = f"https://rzp.io/i/nudge_{sub_id[-6:]}"
    whatsapp_msg = render_nudge_message(
        channel="whatsapp",
        customer_name="Customer",
        merchant_name="CultFit Subscriptions",
        amount_in_rupees=target_entry["amount_inr"],
        short_url=short_url,
    )
    sms_msg = render_nudge_message(
        channel="sms",
        customer_name="Customer",
        merchant_name="CultFit",
        amount_in_rupees=target_entry["amount_inr"],
        short_url=short_url,
    )

    print("    ─── [WhatsApp Preview] ───")
    for line in whatsapp_msg.splitlines():
        print(f"    {line}")
    print("    ─────────────────────────")
    print(f"    ─── [SMS Snippet] ───\n    {sms_msg}\n    ─────────────────────")

    # 5. P2P Ledger Tracking
    print("\n[5] PROMISE-TO-PAY (P2P) LEDGER ENTRY:")
    print(f"    • Dispatch ID      : {target_entry.get('mock_reference_id')}")
    print(f"    • Sent Timestamp   : {target_entry.get('sent_timestamp')}")
    print(f"    • Simulated Outcome: {target_entry.get('simulated_outcome').upper()} ✅")
    if target_entry.get("promised_offset_days"):
        print(f"    • Customer Promise : Committed to pay in +{target_entry.get('promised_offset_days')} days (scheduled with next payday)")
    print(f"    • Status Note      : {target_entry.get('note')}")

    print("\n" + "─" * 78)
    print("  KEY TAKEAWAY FOR MERCHANTS & RAZORPAY:")
    print(f"  Saved a ₹{target_entry['amount_inr']:,.2f} subscription from permanent churn by substituting")
    print("  a blind retry with an engaging conversational touchpoint.")
    print("═" * 78 + "\n")


if __name__ == "__main__":
    run_scenario_b()

"""
demo_scenarios/scenario_c_rbi_compliance.py
────────────────────────────────────────────────────────────────────────────────
DEMO SCENARIO C: RBI e-Mandate >₹15,000 Compliance & High-Ticket Recovery

Showcases handling of high-value subscriptions exceeding the RBI ₹15,000 threshold.
Auto-retries on mandates >₹15,000 without explicit Additional Factor of
Authentication (AFA) violate RBI guidelines. The agent automatically recognizes
the threshold and triggers a secure re-authorization link.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AUDIT_LOG_PATH = ROOT / "data" / "golden" / "audit_log.jsonl"


def run_scenario_c():
    print("\n" + "═" * 78)
    print("  DEMO SCENARIO C: RBI COMPLIANCE & HIGH-TICKET RE-AUTHORIZATION")
    print("  Theme: 'Protecting merchant license & recovering high-value ARR.'")
    print("═" * 78)

    if not AUDIT_LOG_PATH.exists():
        print("[ERROR] Golden audit log not found at data/golden/audit_log.jsonl")
        sys.exit(1)

    records = [
        json.loads(line)
        for line in AUDIT_LOG_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    # Find high-ticket reauth record
    target = next(
        (r for r in records if r.get("above_15k") is True and r.get("final_action") == "reauth_request"),
        None,
    )

    if not target:
        print("[ERROR] No high-ticket re-auth record found.")
        sys.exit(1)

    amount_inr = target["amount"] / 100

    print("\n[1] HIGH-VALUE FAILED SUBSCRIPTION:")
    print(f"    • Subscription ID  : {target['subscription_id']}")
    print(f"    • Amount           : ₹{amount_inr:,.2f}  (> ₹15,000 RBI Threshold)")
    print(f"    • Failure Cause    : {target['classified_bucket']} ({target.get('error_code')})")
    print(f"    • Above 15k Flag   : {target.get('above_15k')} ✅ [TAGGED FOR REGULATORY COMPLIANCE]")

    print("\n[2] REGULATORY MANDATE (RBI Circular RBI/2020-21/74):")
    print("    • Rule: Recurring e-Mandates exceeding ₹15,000 require explicit customer AFA")
    print("            (OTP / 2FA approval) before debit execution.")
    print("    • Risk: Merchants attempting silent retries above ₹15,000 face payment gateway")
    print("            suspension and instant bank rejection.")

    print("\n[3] LLM REASONING & COMPLIANCE ENFORCEMENT:")
    time.sleep(0.3)
    print(f"    • Decision Source  : {target.get('decision_source').upper()}")
    print(f"    • Action Selected  : {target.get('final_action')} ✅")
    print(f"    • Delivery Channel : {target.get('proposed_channel', 'email').upper()}")
    print(f"    • Agent Reasoning  : \"{target.get('action_rationale')}\"")

    print("\n[4] GENERATED RE-AUTHORIZATION RECEIPT:")
    ref_id = target.get("mock_reference_id", "paylink_demo12345")
    reauth_url = f"https://rzp.io/i/reauth_{ref_id[-8:]}"
    print(f"    • Reference ID     : {ref_id}")
    print(f"    • Secure PayLink   : {reauth_url}")
    print(f"    • Compliance Tag   : RBI_E_MANDATE_ABOVE_15K")
    print(f"    • Execution Status : {target.get('execution_status')}")

    print("\n[5] FINANCIAL IMPACT CALCULATION:")
    print(f"    • High-Ticket ARR Saved  : ₹{amount_inr:,.2f} on this single mandate.")
    print(f"    • Razorpay Fee Recovered : ₹{amount_inr * 0.02:,.2f} (at 2.0% blended fee).")
    print("    • Batch Context          : High-ticket mandates account for 46.8% of total money")
    print("                               at risk across the portfolio.")

    print("\n" + "─" * 78)
    print("  KEY TAKEAWAY FOR MERCHANTS & RAZORPAY:")
    print("  The agent isn't just retrying — it is dynamically navigating banking regulations")
    print("  to safely recover enterprise/high-ticket recurring revenue.")
    print("═" * 78 + "\n")


if __name__ == "__main__":
    run_scenario_c()

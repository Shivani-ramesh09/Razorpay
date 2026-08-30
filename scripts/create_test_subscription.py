"""
scripts/create_test_subscription.py
────────────────────────────────────────────────────────────────────────────────
Creates a test Plan and Subscription in Razorpay's TEST MODE using the official
Python SDK.  Prints the subscription_id and a dashboard link for manual
verification.

Prerequisites
-------------
1.  Copy .env.example → .env and fill in your TEST MODE keys:
      RAZORPAY_KEY_ID=rzp_test_...
      RAZORPAY_KEY_SECRET=...

2.  Install the SDK:  pip install razorpay python-dotenv

3.  Never run this against your LIVE keys — Razorpay will actually charge customers.

Usage
-----
    python scripts/create_test_subscription.py

What it creates
---------------
- Plan:  Monthly ₹999 (99900 paise), max 12 cycles
- Customer:  Using Razorpay's test customer credentials
- Subscription:  Links the customer to the plan, starts immediately

NOTE: This script asks for confirmation before making API calls so you can
review the parameters first.  Set --yes to skip the prompt (for automation).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def _require_env(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val or val.startswith("rzp_test_XXXX") or val == "your_test_secret_here":
        print(f"[ERROR] {key} is not set or still holds the placeholder value.")
        print("        Copy .env.example → .env and fill in your Razorpay TEST MODE credentials.")
        sys.exit(1)
    return val


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a test Plan + Subscription via Razorpay API (TEST MODE only)."
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip the confirmation prompt (for CI/automation)",
    )
    parser.add_argument(
        "--amount", type=int, default=99_900,
        help="Plan amount in paise (default: 99900 = ₹999)",
    )
    parser.add_argument(
        "--total-count", type=int, default=12,
        help="Number of billing cycles (default: 12 months)",
    )
    args = parser.parse_args()

    # ── Validate credentials ──────────────────────────────────────────────────
    key_id     = _require_env("RAZORPAY_KEY_ID")
    key_secret = _require_env("RAZORPAY_KEY_SECRET")

    if not key_id.startswith("rzp_test_"):
        print("[ERROR] RAZORPAY_KEY_ID does not start with 'rzp_test_'.")
        print("        This script must only run against TEST MODE keys.")
        sys.exit(1)

    # ── Preview & confirm ─────────────────────────────────────────────────────
    amount_inr = args.amount / 100
    print("\n──────────────────────────────────────────────────────")
    print("  Razorpay Test Subscription Creator")
    print("──────────────────────────────────────────────────────")
    print(f"  Key ID        : {key_id}")
    print(f"  Plan amount   : ₹{amount_inr:.2f} / month ({args.amount} paise)")
    print(f"  Billing cycles: {args.total_count}")
    print("──────────────────────────────────────────────────────")

    if not args.yes:
        answer = input("\nProceed with API calls? [y/N] ").strip().lower()
        if answer != "y":
            print("[Aborted]")
            sys.exit(0)

    # ── Import SDK here (after env validation) ───────────────────────────────
    try:
        import razorpay
    except ImportError:
        print("[ERROR] razorpay SDK not installed. Run: pip install razorpay")
        sys.exit(1)

    client = razorpay.Client(auth=(key_id, key_secret))

    # ── 1. Create Plan ────────────────────────────────────────────────────────
    print("\n[1/3] Creating Plan…")
    plan_payload = {
        "period": "monthly",
        "interval": 1,
        "item": {
            "name":     "Mandate Recovery Agent — Test Plan",
            "amount":   args.amount,
            "currency": "INR",
            "description": "Synthetic test plan for Day 1 webhook capture",
        },
    }
    plan = client.plan.create(data=plan_payload)
    plan_id: str = plan["id"]
    print(f"    ✓ Plan created: {plan_id}")

    # ── 2. Create Customer ────────────────────────────────────────────────────
    print("[2/3] Creating Customer…")
    customer_payload = {
        "name":    "Test Customer",
        "email":   "test.customer@example.com",
        "contact": "9999999999",
    }
    customer = client.customer.create(data=customer_payload)
    customer_id: str = customer["id"]
    print(f"    ✓ Customer created: {customer_id}")

    # ── 3. Create Subscription ────────────────────────────────────────────────
    print("[3/3] Creating Subscription…")
    sub_payload = {
        "plan_id":      plan_id,
        "customer_id":  customer_id,
        "total_count":  args.total_count,
        "quantity":     1,
        "customer_notify": 0,   # We handle notifications ourselves
        "notes": {
            "project": "mandate_recovery_agent",
            "day":     "1",
        },
    }
    subscription = client.subscription.create(data=sub_payload)
    sub_id:    str = subscription["id"]
    sub_status: str = subscription["status"]

    print(f"    ✓ Subscription created: {sub_id}  (status: {sub_status})")

    # ── Summary ───────────────────────────────────────────────────────────────
    dashboard_url = f"https://dashboard.razorpay.com/app/subscriptions/{sub_id}"
    print("\n══════════════════════════════════════════════════════")
    print("  CREATED SUCCESSFULLY")
    print("══════════════════════════════════════════════════════")
    print(f"  Subscription ID : {sub_id}")
    print(f"  Plan ID         : {plan_id}")
    print(f"  Customer ID     : {customer_id}")
    print(f"  Status          : {sub_status}")
    print(f"  Dashboard link  : {dashboard_url}")
    print("══════════════════════════════════════════════════════")
    print()
    print("Next steps:")
    print("  1. Open the dashboard link above to verify the subscription")
    print("  2. Use a Razorpay test failure card/UPI to trigger a failed charge:")
    print("     Card: 5267 3181 8797 5449  (failure: Insufficient funds)")
    print("     Card: 4111 1111 1111 1111  (success — for comparison)")
    print("  3. Confirm your ngrok URL is registered in Dashboard → Webhooks")
    print("  4. Trigger a charge and watch /data/captured_webhooks/ for payloads")
    print()

    # Save a reference record for Day 2 use
    ref_path = ROOT / "data" / "test_subscription_ref.json"
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(
        json.dumps(
            {
                "subscription_id": sub_id,
                "plan_id":         plan_id,
                "customer_id":     customer_id,
                "status":          sub_status,
                "dashboard_url":   dashboard_url,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[INFO] Reference saved to: {ref_path}")


if __name__ == "__main__":
    main()

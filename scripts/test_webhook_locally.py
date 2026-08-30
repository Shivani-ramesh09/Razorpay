"""
scripts/test_webhook_locally.py
────────────────────────────────────────────────────────────────────────────────
Sends a simulated Razorpay webhook POST to the local listener and verifies
the response. Computes a real HMAC-SHA256 signature so the listener accepts it.

Usage (with Flask listener running in another terminal):
    python scripts/test_webhook_locally.py

Expects RAZORPAY_WEBHOOK_SECRET in .env (or defaults to 'my_local_test_secret').
"""

import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

try:
    import requests
except ImportError:
    print("[ERROR] requests not installed. Run: pip install requests")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────
WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "my_local_test_secret")
LISTENER_URL   = "http://localhost:5000/webhooks/razorpay"

# ── Minimal subscription.pending payload (matches real Razorpay shape) ────────
PAYLOAD = {
    "entity": "event",
    "account_id": "acc_TEST123456",
    "event": "subscription.pending",
    "contains": ["subscription"],
    "payload": {
        "subscription": {
            "entity": {
                "id":              "sub_TestLocalDev001",
                "entity":          "subscription",
                "plan_id":         "plan_TestLocalPlan01",
                "status":          "pending",
                "current_start":   int(time.time()) - 86400,
                "current_end":     int(time.time()) + 86400 * 29,
                "ended_at":        None,
                "quantity":        1,
                "notes":           {"project": "mandate_recovery_agent"},
                "charge_at":       int(time.time()) + 3600,
                "start_at":        int(time.time()) - 86400 * 30,
                "end_at":          None,
                "auth_attempts":   1,
                "total_count":     12,
                "paid_count":      3,
                "customer_notify": 0,
                "created_at":      int(time.time()) - 86400 * 90,
                "expire_by":       None,
                "short_url":       None,
                "has_scheduled_changes": False,
                "change_scheduled_at":   None,
                "source":          "api",
                "payment_method":  "upi",
                "offer_id":        None,
                "remaining_count": 9,
                "customer_id":     "cust_TestCustomer001",
                "total_amount":    99900,
            }
        }
    },
    "created_at": int(time.time()),
}

def send_test_webhook(payload: dict, secret: str, url: str) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sig  = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    print(f"\n[TEST] Sending to: {url}")
    print(f"[TEST] Event:      {payload['event']}")
    print(f"[TEST] Signature:  {sig[:32]}...")
    print(f"[TEST] Body size:  {len(body)} bytes")

    try:
        resp = requests.post(
            url,
            data=body,
            headers={
                "Content-Type":        "application/json",
                "X-Razorpay-Signature": sig,
            },
            timeout=5,
        )
    except requests.ConnectionError:
        print("\n[FAIL] Could not connect to the listener.")
        print("       Make sure Flask is running: python webhook_listener/app.py")
        sys.exit(1)

    print(f"\n[RESULT] Status : {resp.status_code}")
    print(f"[RESULT] Body   : {resp.text}")

    if resp.status_code == 200:
        print("\n[PASS] Webhook received and captured successfully!")
        print(f"       Check: d:\\razorpay\\data\\captured_webhooks\\")
    else:
        print("\n[FAIL] Unexpected status code — check Flask logs above.")
        sys.exit(1)


def send_tampered_test(secret: str, url: str) -> None:
    """Send a webhook with a wrong signature to confirm 400 rejection."""
    body = json.dumps({"event": "subscription.pending"}).encode("utf-8")
    bad_sig = "0" * 64  # Obviously wrong

    print(f"\n[TEST] Sending TAMPERED request (should get 400)...")
    try:
        resp = requests.post(
            url,
            data=body,
            headers={
                "Content-Type":        "application/json",
                "X-Razorpay-Signature": bad_sig,
            },
            timeout=5,
        )
        print(f"[RESULT] Status : {resp.status_code}")
        if resp.status_code == 400:
            print("[PASS] Tampered request correctly rejected with 400!")
        else:
            print(f"[FAIL] Expected 400, got {resp.status_code}")
    except requests.ConnectionError:
        print("[FAIL] Could not connect.")


if __name__ == "__main__":
    print("=" * 60)
    print("  Local Webhook Listener Test")
    print("=" * 60)
    print(f"  Secret  : {WEBHOOK_SECRET[:6]}{'*' * (len(WEBHOOK_SECRET) - 6)}")
    print(f"  Endpoint: {LISTENER_URL}")

    # Test 1: valid signature → should capture payload
    send_test_webhook(PAYLOAD, WEBHOOK_SECRET, LISTENER_URL)

    # Test 2: tampered signature → should reject with 400
    send_tampered_test(WEBHOOK_SECRET, LISTENER_URL)

    print("\n[DONE] All tests complete.")

"""
webhook_listener/app.py
────────────────────────────────────────────────────────────────────────────────
Minimal Flask webhook receiver for Razorpay subscription events.

What it does
------------
1. Verifies the incoming Razorpay webhook signature (HMAC-SHA256 over the raw
   request body, using the webhook secret stored in .env).
2. Logs the full raw payload as a timestamped JSON file under
   data/captured_webhooks/.  Each file is a single webhook event — no merging.
3. Returns 200 OK immediately so Razorpay doesn't retry.

Signature verification reference
----------------------------------
Razorpay computes:
    HMAC-SHA256(raw_body, webhook_secret)
and sends it as the X-Razorpay-Signature header (hex digest).

Run locally
-----------
    python webhook_listener/app.py

Expose publicly via ngrok
--------------------------
    ngrok http 5000
Then register the generated https URL + /webhooks/razorpay in:
    Razorpay Dashboard → Settings → Webhooks
"""

import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, request

# ── Bootstrap ────────────────────────────────────────────────────────────────
load_dotenv()

WEBHOOK_SECRET: str = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
CAPTURE_DIR: Path = Path(os.getenv("CAPTURED_WEBHOOKS_DIR", "data/captured_webhooks"))

if not WEBHOOK_SECRET:
    print(
        "[WARN] RAZORPAY_WEBHOOK_SECRET is not set. "
        "Signature verification will FAIL for all real events.",
        file=sys.stderr,
    )

CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _verify_signature(raw_body: bytes, received_sig: str) -> bool:
    """
    Compute the expected HMAC-SHA256 signature over the raw request body
    and compare it (constant-time) to the header value Razorpay sent.
    """
    if not WEBHOOK_SECRET:
        return False
    expected = hmac.new(
        WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, received_sig)


def _save_payload(event_type: str, raw_body: bytes) -> Path:
    """
    Persist the raw webhook body as a timestamped JSON file.
    Returns the path of the saved file.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    # Sanitise event_type for use as a filename component
    safe_event = event_type.replace(".", "_").replace("/", "_")
    filename = CAPTURE_DIR / f"{ts}_{safe_event}.json"

    try:
        # Pretty-print if it's valid JSON; otherwise store raw text
        parsed = json.loads(raw_body)
        filename.write_text(json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8")
    except json.JSONDecodeError:
        filename.write_bytes(raw_body)

    return filename


# ── Route ─────────────────────────────────────────────────────────────────────
@app.route("/webhooks/razorpay", methods=["POST"])
def razorpay_webhook():
    """
    Main ingestion endpoint.

    Razorpay fires POST requests with:
      - Body: JSON payload
      - Header: X-Razorpay-Signature (HMAC-SHA256 hex digest)
    """
    raw_body: bytes = request.get_data()  # Must read before any parsing
    received_sig: str = request.headers.get("X-Razorpay-Signature", "")

    # ── 1. Signature verification ─────────────────────────────────────────────
    if not _verify_signature(raw_body, received_sig):
        app.logger.warning(
            "Signature mismatch | received=%s | body_len=%d",
            received_sig[:16] + "…" if received_sig else "(empty)",
            len(raw_body),
        )
        abort(400, description="Invalid webhook signature")

    # ── 2. Parse event type ───────────────────────────────────────────────────
    try:
        payload: dict = json.loads(raw_body)
    except json.JSONDecodeError:
        abort(400, description="Non-JSON body")

    event_type: str = payload.get("event", "unknown")
    entity_id: str = (
        payload.get("payload", {})
        .get("subscription", {})
        .get("entity", {})
        .get("id", "unknown")
    )

    # ── 3. Persist ────────────────────────────────────────────────────────────
    saved_path = _save_payload(event_type, raw_body)

    app.logger.info(
        "Captured | event=%s | entity_id=%s | file=%s",
        event_type,
        entity_id,
        saved_path.name,
    )

    # ── 4. Acknowledge ────────────────────────────────────────────────────────
    return {"status": "captured", "event": event_type, "file": saved_path.name}, 200


# ── Dev server ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "true").lower() == "true"

    print(f"[INFO] Webhook listener starting on http://{host}:{port}/webhooks/razorpay")
    print(f"[INFO] Captured payloads will be saved to: {CAPTURE_DIR.resolve()}")
    app.run(host=host, port=port, debug=debug)

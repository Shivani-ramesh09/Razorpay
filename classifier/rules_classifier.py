"""
classifier/rules_classifier.py
────────────────────────────────────────────────────────────────────────────────
Rules-based failure classifier.

Maps raw signal fields on a SubscriptionRecord → FailureBucket.

IMPORTANT: The classifier MUST NOT read the `failure_bucket` field.
That field is ground-truth, kept only for post-hoc scoring.  Treat every
record as if it arrived from a live Razorpay webhook that never labeled it.

Classification strategy (priority order)
-----------------------------------------
1. ERROR_CODE lookup (primary signal) — deterministic, O(1)
2. ERROR_DESCRIPTION keyword scan (secondary) — handles unknown codes
3. Heuristic fallback on structural fields:
     above_15k_threshold=True                → reauth_mismatch
     mandate_age_days > 365                  → expired_mandate
     days_since_last_success is None         → bank_side (new mandate, transient)
     default                                 → bank_side (largest bucket, safest fallback)

Extending
---------
Add new error codes to ERROR_CODE_MAP.  If a new bucket is added, add its
codes here AND update FailureBucket in schema/subscription_schema.py.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from schema.subscription_schema import FailureBucket, SubscriptionRecord

# ── Primary lookup: error_code → FailureBucket ───────────────────────────────
# Keys are exact Razorpay / bank error code strings (uppercase).
# Sourced from: Razorpay error docs + NPCI UPI switch error catalogue.

ERROR_CODE_MAP: dict[str, FailureBucket] = {
    # ── Bank-side / technical ────────────────────────────────────────────────
    "BANK_INTERNAL_ERROR":       FailureBucket.BANK_SIDE,
    "GATEWAY_TECHNICAL_ERROR":   FailureBucket.BANK_SIDE,
    "BAD_REQUEST_ERROR":         FailureBucket.BANK_SIDE,
    "BANK_NOT_RESPONDING":       FailureBucket.BANK_SIDE,
    "NPCI_SWITCH_ERROR":         FailureBucket.BANK_SIDE,
    "TECHNICAL_ERROR":           FailureBucket.BANK_SIDE,
    "SERVER_ERROR":              FailureBucket.BANK_SIDE,
    "GATEWAY_CONNECTION_ERROR":  FailureBucket.BANK_SIDE,
    "TIMEOUT":                   FailureBucket.BANK_SIDE,
    "PROCESSING_ERROR":          FailureBucket.BANK_SIDE,

    # ── Low balance / insufficient funds ──────────────────────────────────────
    "INSUFFICIENT_FUNDS":        FailureBucket.LOW_BALANCE,
    "LOW_BALANCE":               FailureBucket.LOW_BALANCE,
    "ACCOUNT_DEBIT_FAILED":      FailureBucket.LOW_BALANCE,
    "BALANCE_BELOW_THRESHOLD":   FailureBucket.LOW_BALANCE,
    "EXCEEDS_WITHDRAWAL_LIMIT":  FailureBucket.LOW_BALANCE,
    "DAILY_LIMIT_EXCEEDED":      FailureBucket.LOW_BALANCE,

    # ── Expired / invalid mandate ─────────────────────────────────────────────
    "MANDATE_EXPIRED":           FailureBucket.EXPIRED_MANDATE,
    "INVALID_MANDATE":           FailureBucket.EXPIRED_MANDATE,
    "TOKEN_EXPIRED":             FailureBucket.EXPIRED_MANDATE,
    "MANDATE_REVOKED":           FailureBucket.EXPIRED_MANDATE,
    "HANDLE_NOT_REGISTERED":     FailureBucket.EXPIRED_MANDATE,
    "VPA_NOT_FOUND":             FailureBucket.EXPIRED_MANDATE,
    "INVALID_VPA":               FailureBucket.EXPIRED_MANDATE,

    # ── Re-auth required ──────────────────────────────────────────────────────
    "REAUTH_REQUIRED":                FailureBucket.REAUTH_MISMATCH,
    "MANDATE_AMOUNT_LIMIT_EXCEEDED":  FailureBucket.REAUTH_MISMATCH,
    "PRE_DEBIT_NOTIFY_FAILED":        FailureBucket.REAUTH_MISMATCH,
    "DEBIT_BLOCKED_REAUTH":           FailureBucket.REAUTH_MISMATCH,
    "FREQUENCY_EXCEEDED":             FailureBucket.REAUTH_MISMATCH,
    "AMOUNT_LIMIT_EXCEEDED":          FailureBucket.REAUTH_MISMATCH,

    # ── Genuine decline ───────────────────────────────────────────────────────
    "PAYMENT_DECLINED":          FailureBucket.GENUINE_DECLINE,
    "USER_BLOCKED":              FailureBucket.GENUINE_DECLINE,
    "TRANSACTION_NOT_PERMITTED": FailureBucket.GENUINE_DECLINE,
    "CUSTOMER_DECLINED":         FailureBucket.GENUINE_DECLINE,
    "DO_NOT_HONOR":              FailureBucket.GENUINE_DECLINE,
    "BLOCKED_BY_CUSTOMER":       FailureBucket.GENUINE_DECLINE,
    "RESTRICTED_CARD":           FailureBucket.GENUINE_DECLINE,
    "FRAUD_SUSPECTED":           FailureBucket.GENUINE_DECLINE,
}

# ── Secondary lookup: keyword → FailureBucket (for description fallback) ──────
# Used when error_code is unknown or None. Keywords are checked in order;
# first match wins. Lowercase match against error_description.lower().

DESCRIPTION_KEYWORDS: list[tuple[str, FailureBucket]] = [
    ("insufficient funds",       FailureBucket.LOW_BALANCE),
    ("low balance",              FailureBucket.LOW_BALANCE),
    ("balance below",            FailureBucket.LOW_BALANCE),
    ("mandate expired",          FailureBucket.EXPIRED_MANDATE),
    ("mandate revoked",          FailureBucket.EXPIRED_MANDATE),
    ("token expired",            FailureBucket.EXPIRED_MANDATE),
    ("invalid mandate",          FailureBucket.EXPIRED_MANDATE),
    ("re-auth",                  FailureBucket.REAUTH_MISMATCH),
    ("reauth",                   FailureBucket.REAUTH_MISMATCH),
    ("re-authoris",              FailureBucket.REAUTH_MISMATCH),  # British/Indian spelling
    ("re-authoriz",              FailureBucket.REAUTH_MISMATCH),
    ("amount limit",             FailureBucket.REAUTH_MISMATCH),
    ("pre-debit",                FailureBucket.REAUTH_MISMATCH),
    ("declined by customer",     FailureBucket.GENUINE_DECLINE),
    ("explicitly declined",      FailureBucket.GENUINE_DECLINE),
    ("do not honor",             FailureBucket.GENUINE_DECLINE),
    ("customer blocked",         FailureBucket.GENUINE_DECLINE),
    ("blocked debits",           FailureBucket.GENUINE_DECLINE),
    ("bank server",              FailureBucket.BANK_SIDE),
    ("technical error",          FailureBucket.BANK_SIDE),
    ("gateway",                  FailureBucket.BANK_SIDE),
    ("switch",                   FailureBucket.BANK_SIDE),
    ("internal error",           FailureBucket.BANK_SIDE),
    ("not responding",           FailureBucket.BANK_SIDE),
]


# ── Classifier ────────────────────────────────────────────────────────────────

def classify(record: SubscriptionRecord) -> FailureBucket:
    """
    Classify a SubscriptionRecord into a FailureBucket using raw signal fields.

    The classifier intentionally does NOT access record.failure_bucket —
    that field is ground truth for scoring, not an input signal.

    Parameters
    ----------
    record : SubscriptionRecord with error_code / error_description populated
             (as they would be on a real Razorpay failed-charge event).

    Returns
    -------
    FailureBucket — exactly one of the five failure categories.
    """
    # ── Step 1: Primary — error_code lookup ──────────────────────────────────
    if record.error_code:
        normalised_code = record.error_code.strip().upper()
        bucket = ERROR_CODE_MAP.get(normalised_code)
        if bucket is not None:
            return bucket

    # ── Step 2: Secondary — error_description keyword scan ───────────────────
    if record.error_description:
        desc_lower = record.error_description.lower()
        for keyword, bucket in DESCRIPTION_KEYWORDS:
            if keyword in desc_lower:
                return bucket

    # ── Step 3: Heuristic fallback on structural fields ───────────────────────
    # above_15k + no prior error signal → likely a re-auth issue
    if record.above_15k_threshold:
        return FailureBucket.REAUTH_MISMATCH

    # Very old mandate with no identifiable error → likely expired/stale token
    if record.mandate_age_days > 365:
        return FailureBucket.EXPIRED_MANDATE

    # Default: bank_side is the largest bucket and safest fallback
    return FailureBucket.BANK_SIDE


def explain(record: SubscriptionRecord) -> dict:
    """
    Return a dict with the classification result AND the path taken
    (which step produced the answer).  Useful for debugging and audit logging.
    """
    bucket = classify(record)
    path = "heuristic_fallback"

    if record.error_code:
        normalised_code = record.error_code.strip().upper()
        if normalised_code in ERROR_CODE_MAP:
            path = f"error_code_lookup:{normalised_code}"

    if path == "heuristic_fallback" and record.error_description:
        desc_lower = record.error_description.lower()
        for keyword, kb in DESCRIPTION_KEYWORDS:
            if keyword in desc_lower:
                path = f"description_keyword:{keyword!r}"
                break

    if path == "heuristic_fallback":
        if record.above_15k_threshold:
            path = "heuristic:above_15k_threshold"
        elif record.mandate_age_days > 365:
            path = "heuristic:mandate_age_days>365"
        else:
            path = "heuristic:default_bank_side"

    return {
        "classified_bucket": bucket.value if hasattr(bucket, "value") else bucket,
        "classification_path": path,
        "error_code": record.error_code,
        "error_description": record.error_description,
    }

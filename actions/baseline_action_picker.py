"""
actions/baseline_action_picker.py
────────────────────────────────────────────────────────────────────────────────
Baseline (rules-based) action picker.

Maps a classified FailureBucket → a default recovery action.  This is the
Day 2 fallback path — replaced by the LLM reasoning agent on Day 4, but
kept as a standalone, independently-testable module throughout the project.

It is intentionally simple: one bucket → one action, no context sensitivity.
The guardrail validator (not this module) enforces compliance.

Action vocabulary
-----------------
    immediate_retry        — Attempt charge now (within NPCI window)
    delayed_retry          — Schedule charge for next NPCI window slot
    reauth_request         — Send customer a re-authorisation link
    promise_to_pay_nudge   — Send Hinglish payment reminder (nudge only, no retry)
    stand_down             — Take no further action on this subscription

Bucket → Action rationale
--------------------------
    bank_side         → delayed_retry
        Bank server is (usually) back online in hours; wait for recovery.
        Immediate retry is rarely useful for transient infrastructure errors.

    low_balance       → promise_to_pay_nudge
        Customer probably knows their balance is low; nudge them to top up
        before the next retry attempt.  Retrying without context is wasteful.

    expired_mandate   → reauth_request
        Mandate is gone; no retry will succeed until the customer re-registers.
        Must send re-auth link regardless of amount threshold.

    reauth_mismatch   → reauth_request
        RBI / NPCI flagged the transaction as needing explicit re-auth.
        Re-auth is the only compliant path.

    genuine_decline   → stand_down
        Customer explicitly declined.  Retrying or nudging is customer-hostile.
        Stand down immediately; escalation is at merchant discretion.

    none              → stand_down
        No failure detected; nothing to recover.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from schema.subscription_schema import FailureBucket

# ── Action mapping ─────────────────────────────────────────────────────────────

ACTION_MAP: dict[str, str] = {
    FailureBucket.BANK_SIDE.value:       "delayed_retry",
    FailureBucket.LOW_BALANCE.value:     "promise_to_pay_nudge",
    FailureBucket.EXPIRED_MANDATE.value: "reauth_request",
    FailureBucket.REAUTH_MISMATCH.value: "reauth_request",
    FailureBucket.GENUINE_DECLINE.value: "stand_down",
    FailureBucket.NONE.value:            "stand_down",
}

# All valid action strings (used by tests and the pipeline for validation)
VALID_ACTIONS: frozenset[str] = frozenset(ACTION_MAP.values())


def pick_action(bucket: FailureBucket) -> str:
    """
    Return the default recovery action for the given failure bucket.

    Parameters
    ----------
    bucket : FailureBucket (enum member or raw string value)

    Returns
    -------
    str — one of the action strings in VALID_ACTIONS
    """
    key = bucket.value if hasattr(bucket, "value") else str(bucket)
    action = ACTION_MAP.get(key)
    if action is None:
        # Unknown bucket — defensive default
        return "stand_down"
    return action


def pick_action_with_rationale(bucket: FailureBucket) -> dict:
    """
    Extended version that also returns the rationale string.
    Used by the pipeline for richer audit logging.
    """
    action = pick_action(bucket)
    rationales = {
        "delayed_retry":          "Bank-side transient error; schedule retry after recovery window.",
        "promise_to_pay_nudge":   "Low balance; nudge customer to top up before retry.",
        "reauth_request":         "Mandate expired or re-auth required; send re-authorisation link.",
        "stand_down":             "Genuine decline or healthy subscription; no action warranted.",
    }
    return {
        "action":    action,
        "rationale": rationales.get(action, "No rationale available."),
        "source":    "baseline_rules",
    }

"""
guardrails/validator.py
────────────────────────────────────────────────────────────────────────────────
Guardrail Validator — loads rules.yaml at startup and enforces all hard rules
deterministically against every proposed action.

Public API
----------
    result = validate(record, proposed_action, last_attempt_ts=<unix_int>)

    result.approved        → bool
    result.final_action    → str  (may differ from proposed_action on override)
    result.rule_triggered  → Optional[str]  (rule ID that fired, e.g. "MAX_ATTEMPTS")
    result.reason          → str  (human-readable explanation, loggable)

Rule evaluation order (priority, highest first)
------------------------------------------------
1.  OPT_OUT_KILL_SWITCH      — absolute override; checked before anything else
2.  MIN_REMAINING_CYCLES     — stand down if no cycles left to recover
3.  HALTED_SUBSCRIPTION      — no retry when status=halted
4.  MAX_ATTEMPTS             — no retry when auth_attempts >= max per NPCI
5.  COOLDOWN_WINDOWS         — no retry if minimum time window not elapsed
6.  REAUTH_THRESHOLD         — redirect retry → reauth if amount > ₹15k + applicable bucket
7.  GENUINE_DECLINE_STANDDOWN — no retry on explicit customer decline

All rules have overrideable_by_llm=false (read from YAML, not hard-coded here).

Design notes
------------
- Rules are data (YAML), logic is code (this file).  To tighten a rule, edit
  rules.yaml.  To add a new rule, edit rules.yaml AND add an enforcer here.
- last_attempt_ts is optional; when None, cooldown checks are skipped (treat
  as "window has elapsed") — conservative but avoids crashes on missing data.
- opt_out is not yet a SubscriptionRecord field (Day 5); the validator reads it
  via getattr with a default of False so it forwards-compats gracefully.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

# ── Path bootstrap ─────────────────────────────────────────────────────────────
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from schema.subscription_schema import FailureBucket, SubscriptionRecord, SubscriptionStatus

# ── Load rules once at import time ────────────────────────────────────────────
_RULES_PATH = Path(__file__).parent / "rules.yaml"

def _load_rules() -> dict:
    with _RULES_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)

_RULES_CONFIG = _load_rules()

def _get_rule(rule_id: str) -> dict:
    for r in _RULES_CONFIG["rules"]:
        if r["id"] == rule_id:
            return r
    raise KeyError(f"Rule '{rule_id}' not found in rules.yaml")

# ── Action constants ───────────────────────────────────────────────────────────
RETRY_ACTIONS = {"immediate_retry", "delayed_retry", "retry"}


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    """
    The guardrail verdict for a single proposed action.

    approved       — True if the proposed action is permitted as-is
    final_action   — The action that WILL be taken (may differ from proposed)
    rule_triggered — Which rule fired (None if approved without override)
    reason         — Human-readable explanation; written to audit log
    """
    approved: bool
    final_action: str
    rule_triggered: Optional[str]
    reason: str


# ── Cooldown schedule (built from YAML, not hard-coded) ───────────────────────

def _cooldown_hours_for_attempt(attempt_number: int) -> Optional[int]:
    """
    Return the minimum cooldown hours required before attempt `attempt_number`.
    Returns None if attempt_number exceeds the schedule (meaning: no NPCI window
    applies beyond attempt 3 — by that point MAX_ATTEMPTS should have fired).
    """
    schedule = _get_rule("COOLDOWN_WINDOWS")["cooldown_schedule"]
    for entry in schedule:
        if entry["attempt_number"] == attempt_number:
            return entry["min_cooldown_hours"]
    return None


# ── Individual rule enforcers ─────────────────────────────────────────────────

def _check_opt_out(record: SubscriptionRecord, proposed: str) -> Optional[ValidationResult]:
    opt_out: bool = getattr(record, "opt_out", False)
    if opt_out:
        return ValidationResult(
            approved=False,
            final_action="stand_down_permanently",
            rule_triggered="OPT_OUT_KILL_SWITCH",
            reason=(
                "Customer has opted out of all recovery communications. "
                "Permanent stand-down — no action permitted."
            ),
        )
    return None


def _check_min_remaining_cycles(record: SubscriptionRecord, proposed: str) -> Optional[ValidationResult]:
    if record.remaining_count == 0:
        return ValidationResult(
            approved=False,
            final_action="stand_down",
            rule_triggered="MIN_REMAINING_CYCLES",
            reason=(
                f"remaining_count=0: subscription plan is fully complete. "
                f"No cycles left to recover. Stand down."
            ),
        )
    return None


def _check_halted_subscription(record: SubscriptionRecord, proposed: str) -> Optional[ValidationResult]:
    status_val = record.status if isinstance(record.status, str) else record.status.value
    if status_val == SubscriptionStatus.HALTED.value and proposed in RETRY_ACTIONS:
        return ValidationResult(
            approved=False,
            final_action="stand_down",
            rule_triggered="HALTED_SUBSCRIPTION",
            reason=(
                "status=halted: NPCI automated retry budget is exhausted. "
                "Retry is not permitted. Use reauth_request or nudge instead."
            ),
        )
    return None


def _check_max_attempts(record: SubscriptionRecord, proposed: str) -> Optional[ValidationResult]:
    if proposed not in RETRY_ACTIONS:
        return None
    rule = _get_rule("MAX_ATTEMPTS")
    max_attempts: int = rule["max_auth_attempts_per_cycle"]
    if record.auth_attempts >= max_attempts:
        return ValidationResult(
            approved=False,
            final_action="stand_down",
            rule_triggered="MAX_ATTEMPTS",
            reason=(
                f"auth_attempts={record.auth_attempts} >= max_auth_attempts_per_cycle={max_attempts}. "
                f"NPCI per-cycle retry budget exhausted. Escalate or stand down."
            ),
        )
    return None


def _check_cooldown(
    record: SubscriptionRecord,
    proposed: str,
    last_attempt_ts: Optional[int],
) -> Optional[ValidationResult]:
    if proposed not in RETRY_ACTIONS:
        return None
    if last_attempt_ts is None:
        # No timing data available — conservatively allow (can't enforce what we can't measure)
        return None

    # attempt_number = the retry number we're about to make (1-indexed)
    # auth_attempts already counts the original charge, so:
    #   auth_attempts=1 means original charge failed, next action = retry #1
    attempt_number = record.auth_attempts  # next attempt will be auth_attempts+1, cooldown based on current attempt
    required_hours = _cooldown_hours_for_attempt(attempt_number)
    if required_hours is None:
        return None

    elapsed_seconds = int(time.time()) - last_attempt_ts
    elapsed_hours = elapsed_seconds / 3600
    required_seconds = required_hours * 3600

    if elapsed_seconds < required_seconds:
        return ValidationResult(
            approved=False,
            final_action="defer_until_window_open",
            rule_triggered="COOLDOWN_WINDOWS",
            reason=(
                f"Cooldown window not elapsed. "
                f"Attempt #{attempt_number} requires {required_hours}h gap; "
                f"only {elapsed_hours:.1f}h have passed since last attempt. "
                f"Defer action until T+{required_hours}h."
            ),
        )
    return None


def _check_reauth_threshold(record: SubscriptionRecord, proposed: str) -> Optional[ValidationResult]:
    if proposed not in RETRY_ACTIONS:
        return None
    if not record.above_15k_threshold:
        return None

    rule = _get_rule("REAUTH_THRESHOLD")
    applicable_buckets: list[str] = rule["applicable_buckets"]
    bucket_val = record.failure_bucket if isinstance(record.failure_bucket, str) else record.failure_bucket.value

    if bucket_val in applicable_buckets:
        return ValidationResult(
            approved=False,
            final_action="reauth_request",
            rule_triggered="REAUTH_THRESHOLD",
            reason=(
                f"amount={record.amount} paise (>= ₹15,000 threshold) with "
                f"failure_bucket={bucket_val!r}. "
                f"RBI mandates explicit re-authorisation before debit. "
                f"Redirecting: retry -> reauth_request."
            ),
        )
    return None


def _check_genuine_decline(record: SubscriptionRecord, proposed: str) -> Optional[ValidationResult]:
    bucket_val = record.failure_bucket if isinstance(record.failure_bucket, str) else record.failure_bucket.value
    if bucket_val != FailureBucket.GENUINE_DECLINE.value:
        return None

    rule = _get_rule("GENUINE_DECLINE_STANDDOWN")
    blocked: list[str] = rule["blocked_actions"]

    # Normalise — "retry" covers both immediate and delayed
    proposed_normalised = "retry" if proposed in RETRY_ACTIONS else proposed

    if proposed_normalised in blocked:
        return ValidationResult(
            approved=False,
            final_action="stand_down",
            rule_triggered="GENUINE_DECLINE_STANDDOWN",
            reason=(
                f"failure_bucket=genuine_decline: customer explicitly declined. "
                f"Action '{proposed}' is blocked. "
                f"Only a single informational nudge is permitted, then stand down."
            ),
        )
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def validate(
    record: SubscriptionRecord,
    proposed_action: str,
    *,
    last_attempt_ts: Optional[int] = None,
) -> ValidationResult:
    """
    Run all guardrail rules against the proposed action for the given record.

    Parameters
    ----------
    record          : SubscriptionRecord to evaluate
    proposed_action : The action string being proposed (e.g. "immediate_retry",
                      "reauth_request", "stand_down", "promise_to_pay_nudge")
    last_attempt_ts : Unix timestamp of the previous charge attempt.
                      Used for cooldown window calculation.  Pass None if
                      unknown — cooldown checks will be skipped.

    Returns
    -------
    ValidationResult with approved/final_action/rule_triggered/reason fields.
    """
    checkers = [
        lambda r, p: _check_opt_out(r, p),
        lambda r, p: _check_min_remaining_cycles(r, p),
        lambda r, p: _check_halted_subscription(r, p),
        lambda r, p: _check_max_attempts(r, p),
        lambda r, p: _check_cooldown(r, p, last_attempt_ts),
        lambda r, p: _check_reauth_threshold(r, p),
        lambda r, p: _check_genuine_decline(r, p),
    ]

    for checker in checkers:
        result = checker(record, proposed_action)
        if result is not None:
            return result

    # All rules passed — approve as-is
    return ValidationResult(
        approved=True,
        final_action=proposed_action,
        rule_triggered=None,
        reason=f"All guardrail rules passed. Action '{proposed_action}' approved.",
    )

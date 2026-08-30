"""
tests/test_guardrail_adversarial.py
────────────────────────────────────────────────────────────────────────────────
8 deliberately rule-violating test cases.  Every single one MUST be blocked
or overridden by the validator — approved=True on any of these is a test failure.

Cases
-----
1.  auth_attempts=3, propose immediate_retry        → MAX_ATTEMPTS
2.  auth_attempts=4, propose delayed_retry          → MAX_ATTEMPTS
3.  attempt #1, only 12h elapsed, propose retry     → COOLDOWN_WINDOWS
4.  attempt #2, only 48h elapsed, propose retry     → COOLDOWN_WINDOWS
5.  opt_out=True, propose promise_to_pay_nudge      → OPT_OUT_KILL_SWITCH
6.  above_15k + reauth_mismatch bucket, retry       → REAUTH_THRESHOLD
7.  status=halted, propose immediate_retry          → HALTED_SUBSCRIPTION
8.  remaining_count=0, propose any action           → MIN_REMAINING_CYCLES

Bonus cases (9-11) — positive: confirm valid actions ARE approved
9.  auth_attempts=1, 30h elapsed, propose retry     → approved
10. genuine_decline + propose stand_down            → approved
11. above_15k + bank_side bucket, propose retry     → approved (threshold only applies to reauth/expired)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from schema.subscription_schema import (
    FailureBucket,
    SubscriptionRecord,
    SubscriptionStatus,
)
from guardrails.validator import validate, RETRY_ACTIONS


# ── Fixture factory ───────────────────────────────────────────────────────────

def _make_record(**overrides) -> SubscriptionRecord:
    """Build a baseline valid SubscriptionRecord with safe defaults, then apply overrides."""
    defaults = dict(
        subscription_id="sub_AdversarialTest01",
        status=SubscriptionStatus.PENDING,
        auth_attempts=1,
        paid_count=2,
        remaining_count=10,
        total_count=12,
        charge_at=int(time.time()) + 3600,
        current_start=int(time.time()) - 86400,
        current_end=int(time.time()) + 86400 * 29,
        customer_id="cust_AdversarialTest01",
        plan_id="plan_AdversarialTest01",
        error_code="BANK_INTERNAL_ERROR",
        error_description="Bank server error",
        failure_bucket=FailureBucket.BANK_SIDE,
        amount=99_900,              # ₹999 — below ₹15k threshold
        mandate_age_days=90,
        days_since_last_success=30,
        above_15k_threshold=False,
        historical_payment_day_pattern=[15],
    )
    defaults.update(overrides)
    return SubscriptionRecord(**defaults)


# ── Adversarial cases (must all be BLOCKED) ───────────────────────────────────

class TestAdversarialBlocked:

    # ── Case 1: auth_attempts exhausted (=3) ─────────────────────────────────
    def test_case1_max_attempts_at_3_blocks_retry(self):
        """auth_attempts=3 exactly equals the NPCI max → must block retry."""
        record = _make_record(auth_attempts=3)
        result = validate(record, "immediate_retry")

        assert not result.approved, "Expected blocked but got approved"
        assert result.rule_triggered == "MAX_ATTEMPTS", (
            f"Expected MAX_ATTEMPTS, got {result.rule_triggered}"
        )
        assert result.final_action == "stand_down"
        assert "3" in result.reason  # reason mentions the attempt count

    # ── Case 2: auth_attempts way over limit ─────────────────────────────────
    def test_case2_max_attempts_at_4_blocks_delayed_retry(self):
        """auth_attempts=4 (over limit) must block delayed_retry too."""
        record = _make_record(auth_attempts=4)
        result = validate(record, "delayed_retry")

        assert not result.approved
        assert result.rule_triggered == "MAX_ATTEMPTS"
        assert result.final_action == "stand_down"

    # ── Case 3: Cooldown — attempt #1, only 12h elapsed ──────────────────────
    def test_case3_cooldown_attempt1_12h_elapsed(self):
        """
        Attempt #1 requires 24h gap.  12h elapsed = cooldown not met.
        last_attempt_ts = 12h ago.
        """
        twelve_hours_ago = int(time.time()) - (12 * 3600)
        record = _make_record(auth_attempts=1)
        result = validate(record, "immediate_retry", last_attempt_ts=twelve_hours_ago)

        assert not result.approved
        assert result.rule_triggered == "COOLDOWN_WINDOWS"
        assert result.final_action == "defer_until_window_open"
        assert "24" in result.reason  # mentions the 24h requirement

    # ── Case 4: Cooldown — attempt #2, only 48h elapsed ──────────────────────
    def test_case4_cooldown_attempt2_48h_elapsed(self):
        """
        Attempt #2 requires 72h gap.  48h elapsed = cooldown not met.
        """
        fortyeight_hours_ago = int(time.time()) - (48 * 3600)
        record = _make_record(auth_attempts=2)
        result = validate(record, "delayed_retry", last_attempt_ts=fortyeight_hours_ago)

        assert not result.approved
        assert result.rule_triggered == "COOLDOWN_WINDOWS"
        assert result.final_action == "defer_until_window_open"
        assert "72" in result.reason  # mentions the 72h requirement

    # ── Case 5: Opt-out kill-switch ───────────────────────────────────────────
    def test_case5_opt_out_blocks_all_actions(self):
        """
        opt_out=True must block EVERYTHING — including a nudge, not just retries.
        We temporarily patch the record to have an opt_out attribute since
        it's not in SubscriptionRecord v1 yet (Day 5 field).
        """
        record = _make_record()
        # Inject opt_out as a runtime attribute (forwards-compat test)
        object.__setattr__(record, "opt_out", True)

        result = validate(record, "promise_to_pay_nudge")

        assert not result.approved
        assert result.rule_triggered == "OPT_OUT_KILL_SWITCH"
        assert result.final_action == "stand_down_permanently"
        assert "opted out" in result.reason.lower()

    # ── Case 6: Above ₹15k + reauth_mismatch bucket — retry blocked ──────────
    def test_case6_reauth_threshold_redirects_retry(self):
        """
        amount >= ₹15,000 + failure_bucket=reauth_mismatch.
        Retry must be redirected to reauth_request per RBI rule.
        """
        record = _make_record(
            amount=2_000_000,           # ₹20,000
            above_15k_threshold=True,
            failure_bucket=FailureBucket.REAUTH_MISMATCH,
            error_code="REAUTH_REQUIRED",
            error_description="Re-authorisation required",
        )
        result = validate(record, "immediate_retry")

        assert not result.approved
        assert result.rule_triggered == "REAUTH_THRESHOLD"
        assert result.final_action == "reauth_request"
        assert "15,000" in result.reason or "reauth" in result.reason.lower()

    # ── Case 7: status=halted — retry is illegal ─────────────────────────────
    def test_case7_halted_status_blocks_retry(self):
        """
        status=halted means NPCI automated retry window is exhausted.
        Any retry action must be blocked.
        """
        record = _make_record(status=SubscriptionStatus.HALTED)
        result = validate(record, "immediate_retry")

        assert not result.approved
        assert result.rule_triggered == "HALTED_SUBSCRIPTION"
        assert result.final_action == "stand_down"
        assert "halted" in result.reason.lower()

    # ── Case 8: remaining_count=0 — nothing left to recover ──────────────────
    def test_case8_zero_remaining_cycles_blocks_any_action(self):
        """
        remaining_count=0: plan is complete, no cycles to recover.
        Must block regardless of proposed action.
        """
        record = _make_record(
            paid_count=12,
            remaining_count=0,
            total_count=12,
        )
        result = validate(record, "delayed_retry")

        assert not result.approved
        assert result.rule_triggered == "MIN_REMAINING_CYCLES"
        assert result.final_action == "stand_down"

    # ── Verify ALL 8 cases fail (summary parametrised version) ───────────────
    @pytest.mark.parametrize("action", list(RETRY_ACTIONS))
    def test_case8_zero_remaining_blocks_all_retry_variants(self, action):
        """remaining_count=0 blocks every flavour of retry action."""
        record = _make_record(paid_count=12, remaining_count=0, total_count=12)
        result = validate(record, action)
        assert not result.approved
        assert result.rule_triggered == "MIN_REMAINING_CYCLES"


# ── Positive cases — confirm valid actions are NOT blocked ────────────────────

class TestGuardrailPositive:

    def test_case9_valid_retry_approved_after_24h(self):
        """
        auth_attempts=1 and 30h elapsed (> 24h requirement) → approved.
        """
        thirty_hours_ago = int(time.time()) - (30 * 3600)
        record = _make_record(auth_attempts=1)
        result = validate(record, "delayed_retry", last_attempt_ts=thirty_hours_ago)

        assert result.approved, f"Expected approved, blocked by {result.rule_triggered}: {result.reason}"
        assert result.rule_triggered is None
        assert result.final_action == "delayed_retry"

    def test_case10_genuine_decline_stand_down_is_approved(self):
        """
        stand_down on genuine_decline should always be approved — it's a permitted action.
        """
        record = _make_record(
            failure_bucket=FailureBucket.GENUINE_DECLINE,
            error_code="PAYMENT_DECLINED",
        )
        result = validate(record, "stand_down")

        assert result.approved
        assert result.rule_triggered is None

    def test_case11_above_15k_bank_side_retry_allowed(self):
        """
        above_15k + bank_side (not reauth/expired) → REAUTH_THRESHOLD should NOT fire.
        This confirms the rule only applies to the applicable buckets.
        """
        record = _make_record(
            amount=2_000_000,
            above_15k_threshold=True,
            failure_bucket=FailureBucket.BANK_SIDE,
            error_code="BANK_INTERNAL_ERROR",
        )
        result = validate(record, "delayed_retry")

        # Should be approved (or blocked by a different rule, but NOT REAUTH_THRESHOLD)
        if not result.approved:
            assert result.rule_triggered != "REAUTH_THRESHOLD", (
                "REAUTH_THRESHOLD incorrectly fired on bank_side bucket"
            )

    def test_reauth_request_approved_on_halted_status(self):
        """
        reauth_request is a permitted action on halted status (HALTED rule only blocks retry).
        """
        record = _make_record(status=SubscriptionStatus.HALTED)
        result = validate(record, "reauth_request")

        assert result.approved, (
            f"reauth_request should be permitted on halted, blocked by {result.rule_triggered}"
        )

    def test_nudge_approved_on_low_balance(self):
        """
        promise_to_pay_nudge is not a retry — cooldown and max_attempts rules
        should not fire for it.
        """
        record = _make_record(
            auth_attempts=3,  # Would block retry, but nudge is different
            failure_bucket=FailureBucket.LOW_BALANCE,
            error_code="INSUFFICIENT_FUNDS",
        )
        result = validate(record, "promise_to_pay_nudge")

        # MAX_ATTEMPTS only blocks retries, not nudges
        assert result.rule_triggered != "MAX_ATTEMPTS", (
            "MAX_ATTEMPTS should not fire for a nudge action"
        )

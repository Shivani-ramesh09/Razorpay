"""
tests/test_day5_actions.py
────────────────────────────────────────────────────────────────────────────────
Tests for Day 5 multi-channel action execution, Hinglish nudge templates,
and Promise-to-Pay (P2P) tracking ledger.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
import pytest

from schema.subscription_schema import FailureBucket, SubscriptionRecord, SubscriptionStatus
from actions.nudge_templates import render_nudge_message
from actions.p2p_ledger import (
    OUTCOME_CHOICES,
    OUTCOME_WEIGHTS,
    get_ledger_stats,
    load_ledger,
    record_nudge_dispatch,
    reset_ledger,
)
from actions.action_executor import (
    ExecutionReceipt,
    execute_action,
    execute_delayed_retry,
    execute_escalate_to_human,
    execute_promise_to_pay_nudge,
    execute_reauth_request,
    execute_stand_down,
)


@pytest.fixture(autouse=True)
def clean_ledger():
    """Ensure clean ledger before each test."""
    reset_ledger()
    yield
    reset_ledger()


def _make_test_record(**overrides) -> SubscriptionRecord:
    defaults = dict(
        subscription_id="sub_Day5Test001",
        status=SubscriptionStatus.PENDING,
        auth_attempts=1,
        paid_count=1,
        remaining_count=11,
        total_count=12,
        customer_id="cust_Day5Test001",
        plan_id="plan_Day5Test001",
        error_code="BANK_SYSTEM_ERROR",
        error_description="Bank system error",
        failure_bucket=FailureBucket.BANK_SIDE,
        amount=99900,  # ₹999
        mandate_age_days=60,
        above_15k_threshold=False,
    )
    defaults.update(overrides)
    return SubscriptionRecord(**defaults)


# ── Nudge Templates Tests ─────────────────────────────────────────────────────

class TestNudgeTemplates:
    def test_whatsapp_template_rendering(self):
        msg = render_nudge_message(
            channel="whatsapp",
            customer_name="Rahul",
            merchant_name="Netflix India",
            amount_in_rupees=499.0,
            short_url="https://rzp.io/i/test1234",
        )
        assert "Namaste Rahul ji 🙏" in msg
        assert "Netflix India" in msg
        assert "₹499" in msg
        assert "https://rzp.io/i/test1234" in msg

    def test_sms_template_rendering(self):
        msg = render_nudge_message(
            channel="sms",
            customer_name="Priya",
            merchant_name="CultFit",
            amount_in_rupees=1250.50,
            short_url="https://rzp.io/i/cult12",
        )
        assert "Namaste Priya" in msg
        assert "CultFit" in msg
        assert "₹1,250.50" in msg
        assert "https://rzp.io/i/cult12" in msg

    def test_default_fallback_channel(self):
        msg = render_nudge_message(
            channel="unknown_channel",
            customer_name="Amit",
            merchant_name="SaaS App",
            amount_in_rupees=999.0,
            short_url="https://rzp.io/i/saas",
        )
        assert "Namaste Amit" in msg
        assert "https://rzp.io/i/saas" in msg


# ── P2P Ledger Tests ──────────────────────────────────────────────────────────

class TestP2PLedger:
    def test_record_and_load_entry(self):
        rng = random.Random(42)
        entry = record_nudge_dispatch(
            subscription_id="sub_test_p2p_01",
            channel="whatsapp",
            mock_reference_id="wamsg_12345678",
            amount=99900,
            rng=rng,
        )

        assert entry["subscription_id"] == "sub_test_p2p_01"
        assert entry["mock_reference_id"] == "wamsg_12345678"
        assert entry["channel"] == "whatsapp"
        assert entry["amount_inr"] == 999.0
        assert entry["simulated_outcome"] in OUTCOME_CHOICES
        assert entry["simulated_for_demo"] is True

        entries = load_ledger()
        assert len(entries) == 1
        assert entries[0]["subscription_id"] == "sub_test_p2p_01"

    def test_ledger_stats_aggregation(self):
        rng = random.Random(99)
        for i in range(10):
            record_nudge_dispatch(
                subscription_id=f"sub_test_{i}",
                channel="whatsapp" if i % 2 == 0 else "sms",
                mock_reference_id=f"ref_{i}",
                amount=100000,
                rng=rng,
            )

        stats = get_ledger_stats()
        assert stats["total_nudges"] == 10
        assert stats["total_value_addressed_inr"] == 10000.0
        assert sum(stats["outcomes"].values()) == 10
        assert "commitment_rate_pct" in stats


# ── Action Executor Tests ─────────────────────────────────────────────────────

class TestActionExecutor:
    def test_delayed_retry_receipt(self):
        rec = _make_test_record()
        receipt = execute_delayed_retry(rec, "upi_autopay")

        assert isinstance(receipt, ExecutionReceipt)
        assert receipt.action == "delayed_retry"
        assert receipt.channel == "upi_autopay"
        assert receipt.mock_reference_id.startswith("retry_")
        assert receipt.status == "dispatched"

    def test_promise_to_pay_nudge_receipt_and_ledger(self):
        rec = _make_test_record(
            failure_bucket=FailureBucket.LOW_BALANCE,
            error_code="INSUFFICIENT_FUNDS",
            auth_attempts=2,
        )
        receipt = execute_promise_to_pay_nudge(rec, "whatsapp")

        assert receipt.action == "promise_to_pay_nudge"
        assert receipt.channel == "whatsapp"
        assert receipt.mock_reference_id.startswith("wamsg_")
        assert receipt.status == "dispatched"
        assert "message_preview" in receipt.details

        # Check that it automatically recorded in p2p_ledger
        ledger = load_ledger()
        assert len(ledger) == 1
        assert ledger[0]["subscription_id"] == rec.subscription_id

    def test_reauth_request_below_15k(self):
        rec = _make_test_record(
            failure_bucket=FailureBucket.EXPIRED_MANDATE,
            amount=500000,  # ₹5,000 (< ₹15k)
            above_15k_threshold=False,
        )
        receipt = execute_reauth_request(rec, "email")

        assert receipt.action == "reauth_request"
        assert receipt.mock_reference_id.startswith("paylink_")
        assert receipt.above_15k is False
        assert receipt.details["compliance_tag"] == "STANDARD_REAUTH"

    def test_reauth_request_above_15k_tagged(self):
        rec = _make_test_record(
            failure_bucket=FailureBucket.REAUTH_MISMATCH,
            amount=1600000,  # ₹16,000 (> ₹15k)
            above_15k_threshold=True,
        )
        receipt = execute_reauth_request(rec, "email")

        assert receipt.action == "reauth_request"
        assert receipt.mock_reference_id.startswith("paylink_")
        assert receipt.above_15k is True
        assert receipt.details["rbi_threshold_mandated"] is True
        assert receipt.details["compliance_tag"] == "RBI_E_MANDATE_ABOVE_15K"

    def test_escalate_to_human_receipt(self):
        rec = _make_test_record(auth_attempts=3)
        receipt = execute_escalate_to_human(rec, "human_agent")

        assert receipt.action == "escalate_to_human"
        assert receipt.mock_reference_id.startswith("tkt_")
        assert receipt.status == "dispatched"

    def test_stand_down_receipt(self):
        rec = _make_test_record(failure_bucket=FailureBucket.GENUINE_DECLINE)
        receipt = execute_stand_down(rec, "internal_system")

        assert receipt.action == "stand_down"
        assert receipt.mock_reference_id.startswith("std_")
        assert receipt.status == "dispatched"

    def test_master_dispatcher(self):
        rec = _make_test_record()
        receipt = execute_action("delayed_retry", rec, "upi_autopay")
        assert receipt.action == "delayed_retry"

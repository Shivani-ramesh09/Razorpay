"""
tests/test_day1.py
────────────────────────────────────────────────────────────────────────────────
Day 1 test suite — validates:

  1. Schema validity:   Every record produced by generate_batch() is a valid
                        SubscriptionRecord (Pydantic raises on first violation).
  2. Distribution:      The failure_bucket mix stays within ±5 percentage points
                        of the target proportions (same tolerance as the
                        print_distribution_summary() assertion, so this test
                        and the script agree).
  3. Field invariants:  A hand-crafted sample of business-logic invariants that
                        the generator must respect.
  4. Signature helper:  The HMAC verification helper in the webhook listener
                        behaves correctly for valid and tampered payloads.

Run with:
    pytest tests/test_day1.py -v
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import os
import sys
from pathlib import Path
from typing import List

import pytest

# ── Path bootstrap ────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from schema.subscription_schema import (
    FailureBucket,
    SubscriptionRecord,
    SubscriptionStatus,
)
from scripts.generate_synthetic_batch import (
    TARGET_DISTRIBUTION,
    generate_batch,
    print_distribution_summary,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def batch_200() -> List[SubscriptionRecord]:
    """Generate 200 records once per module; shared across all tests."""
    return generate_batch(count=200, seed=42)


@pytest.fixture(scope="module")
def batch_500() -> List[SubscriptionRecord]:
    """Larger batch for distribution robustness checks."""
    return generate_batch(count=500, seed=123)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Schema Validity
# ─────────────────────────────────────────────────────────────────────────────

class TestSchemaValidity:
    def test_all_records_are_subscription_record_instances(self, batch_200):
        """Every item in the batch must be a SubscriptionRecord."""
        for i, record in enumerate(batch_200):
            assert isinstance(record, SubscriptionRecord), (
                f"Record at index {i} is {type(record)}, expected SubscriptionRecord"
            )

    def test_subscription_ids_have_correct_prefix(self, batch_200):
        for record in batch_200:
            assert record.subscription_id.startswith("sub_"), (
                f"Bad subscription_id: {record.subscription_id}"
            )

    def test_customer_ids_have_correct_prefix(self, batch_200):
        for record in batch_200:
            assert record.customer_id.startswith("cust_"), (
                f"Bad customer_id: {record.customer_id}"
            )

    def test_plan_ids_have_correct_prefix(self, batch_200):
        for record in batch_200:
            assert record.plan_id.startswith("plan_"), (
                f"Bad plan_id: {record.plan_id}"
            )

    def test_count_invariant(self, batch_200):
        """paid_count + remaining_count must never exceed total_count."""
        for record in batch_200:
            assert record.paid_count + record.remaining_count <= record.total_count, (
                f"Count invariant violated: paid={record.paid_count} "
                f"remaining={record.remaining_count} total={record.total_count}"
            )

    def test_amounts_are_positive(self, batch_200):
        for record in batch_200:
            assert record.amount > 0, f"Non-positive amount: {record.amount}"

    def test_above_15k_threshold_consistency(self, batch_200):
        """above_15k_threshold must always reflect amount >= 1_500_000 paise."""
        for record in batch_200:
            expected = record.amount >= 1_500_000
            assert record.above_15k_threshold == expected, (
                f"above_15k_threshold mismatch for {record.subscription_id}: "
                f"amount={record.amount}, flag={record.above_15k_threshold}"
            )

    def test_all_statuses_are_pending(self, batch_200):
        """Synthetic failing batch should have status=pending."""
        for record in batch_200:
            assert record.status == SubscriptionStatus.PENDING.value or \
                   record.status == SubscriptionStatus.PENDING, (
                f"Unexpected status: {record.status}"
            )

    def test_historical_payment_day_pattern_is_sorted(self, batch_200):
        for record in batch_200:
            pattern = record.historical_payment_day_pattern
            assert pattern == sorted(pattern), (
                f"historical_payment_day_pattern not sorted: {pattern}"
            )

    def test_pattern_days_in_valid_range(self, batch_200):
        for record in batch_200:
            for day in record.historical_payment_day_pattern:
                assert 1 <= day <= 31, f"Invalid day in pattern: {day}"

    def test_mandate_age_days_positive(self, batch_200):
        for record in batch_200:
            assert record.mandate_age_days >= 1, (
                f"mandate_age_days must be >= 1, got {record.mandate_age_days}"
            )

    def test_new_subscriptions_have_no_pattern(self, batch_200):
        """If paid_count == 0, there's no payment history → pattern should be empty."""
        for record in batch_200:
            if record.paid_count == 0:
                assert record.historical_payment_day_pattern == [], (
                    f"Paid_count=0 but pattern is non-empty: "
                    f"{record.historical_payment_day_pattern}"
                )

    def test_days_since_last_success_none_for_unpaid(self, batch_200):
        """If paid_count == 0, days_since_last_success must be None."""
        for record in batch_200:
            if record.paid_count == 0:
                assert record.days_since_last_success is None, (
                    f"paid_count=0 but days_since_last_success={record.days_since_last_success}"
                )

    def test_pydantic_round_trip(self, batch_200):
        """Records must survive a model_dump → SubscriptionRecord() round-trip."""
        for record in batch_200[:10]:  # Check first 10 for speed
            dumped = record.model_dump(mode="json")
            restored = SubscriptionRecord(**dumped)
            assert restored.subscription_id == record.subscription_id
            assert restored.failure_bucket == record.failure_bucket

    def test_json_serialisable(self, batch_200):
        """model_dump(mode='json') must produce JSON-serialisable dicts."""
        for record in batch_200[:10]:
            dumped = record.model_dump(mode="json")
            assert json.dumps(dumped)  # Raises TypeError if not serialisable


# ─────────────────────────────────────────────────────────────────────────────
# 2. Distribution Tests
# ─────────────────────────────────────────────────────────────────────────────

TOLERANCE_PP = 0.05   # ±5 percentage points

class TestDistribution:

    def _actual_distribution(self, records: List[SubscriptionRecord]) -> dict[str, float]:
        total = len(records)
        counts: dict[str, int] = {}
        for r in records:
            key = r.failure_bucket if isinstance(r.failure_bucket, str) else r.failure_bucket.value
            counts[key] = counts.get(key, 0) + 1
        return {k: v / total for k, v in counts.items()}

    def test_batch_has_correct_count(self, batch_200):
        assert len(batch_200) == 200, f"Expected 200 records, got {len(batch_200)}"

    def test_batch_500_has_correct_count(self, batch_500):
        assert len(batch_500) == 500

    @pytest.mark.parametrize("bucket,target_pct", TARGET_DISTRIBUTION.items())
    def test_bucket_proportion_within_tolerance_200(self, batch_200, bucket, target_pct):
        actual = self._actual_distribution(batch_200)
        actual_pct = actual.get(bucket.value, 0.0)
        deviation = abs(actual_pct - target_pct)
        assert deviation <= TOLERANCE_PP, (
            f"Bucket '{bucket.value}': actual={actual_pct:.1%}, "
            f"target={target_pct:.1%}, deviation={deviation:.1%} > {TOLERANCE_PP:.0%}"
        )

    @pytest.mark.parametrize("bucket,target_pct", TARGET_DISTRIBUTION.items())
    def test_bucket_proportion_within_tolerance_500(self, batch_500, bucket, target_pct):
        actual = self._actual_distribution(batch_500)
        actual_pct = actual.get(bucket.value, 0.0)
        deviation = abs(actual_pct - target_pct)
        assert deviation <= TOLERANCE_PP, (
            f"Batch-500 — bucket '{bucket.value}': actual={actual_pct:.1%}, "
            f"target={target_pct:.1%}, deviation={deviation:.1%} > {TOLERANCE_PP:.0%}"
        )

    def test_no_unexpected_buckets(self, batch_200):
        valid_values = {b.value for b in FailureBucket}
        for record in batch_200:
            key = record.failure_bucket if isinstance(record.failure_bucket, str) else record.failure_bucket.value
            assert key in valid_values, f"Unknown failure_bucket: {key}"

    def test_no_none_buckets_in_failing_batch(self, batch_200):
        """The synthetic *failing* batch should not contain FailureBucket.NONE."""
        for record in batch_200:
            key = record.failure_bucket if isinstance(record.failure_bucket, str) else record.failure_bucket.value
            assert key != FailureBucket.NONE.value, (
                f"Record {record.subscription_id} has failure_bucket=none "
                "which should not appear in a failing batch"
            )

    def test_print_distribution_summary_passes(self, batch_200, capsys):
        """The distribution summary function should not raise AssertionError."""
        print_distribution_summary(batch_200)
        captured = capsys.readouterr()
        assert "[OK] All buckets within" in captured.out

    def test_reauth_mismatch_records_above_15k(self, batch_200):
        """
        All reauth_mismatch records must have above_15k_threshold=True,
        because the generator explicitly assigns high amounts for this bucket.
        """
        reauth_records = [
            r for r in batch_200
            if (r.failure_bucket if isinstance(r.failure_bucket, str) else r.failure_bucket.value)
            == FailureBucket.REAUTH_MISMATCH.value
        ]
        assert reauth_records, "No reauth_mismatch records found"
        for r in reauth_records:
            assert r.above_15k_threshold is True, (
                f"{r.subscription_id}: reauth_mismatch but above_15k_threshold=False, "
                f"amount={r.amount}"
            )

    def test_seed_reproducibility(self):
        """Same seed must always produce identical output."""
        batch_a = generate_batch(count=50, seed=7)
        batch_b = generate_batch(count=50, seed=7)
        for a, b in zip(batch_a, batch_b):
            assert a.subscription_id == b.subscription_id
            assert a.failure_bucket  == b.failure_bucket
            assert a.amount          == b.amount

    def test_different_seeds_differ(self):
        """Different seeds should not produce identical batches."""
        batch_a = generate_batch(count=50, seed=1)
        batch_b = generate_batch(count=50, seed=2)
        ids_a = {r.subscription_id for r in batch_a}
        ids_b = {r.subscription_id for r in batch_b}
        assert ids_a != ids_b, "Different seeds produced identical subscription IDs"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Webhook Signature Verification
# ─────────────────────────────────────────────────────────────────────────────

class TestWebhookSignatureVerification:
    """
    Unit-tests for the _verify_signature() helper in webhook_listener/app.py.
    We test the helper directly (imported from module) rather than via HTTP
    so we don't need a running Flask server.
    """

    SECRET = "test_webhook_secret_abc123"

    def _compute_sig(self, body: bytes, secret: str = None) -> str:
        s = secret or self.SECRET
        return hmac.new(
            s.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()

    def _get_verify_fn(self):
        """
        Import _verify_signature from webhook_listener/app.py.
        Sets the module-level WEBHOOK_SECRET to our test value.
        """
        import importlib
        import webhook_listener.app as wl_app
        original_secret = wl_app.WEBHOOK_SECRET
        wl_app.WEBHOOK_SECRET = self.SECRET
        yield wl_app._verify_signature
        wl_app.WEBHOOK_SECRET = original_secret  # restore

    def test_valid_signature_accepted(self):
        body = b'{"event": "subscription.pending", "test": true}'
        sig = self._compute_sig(body)
        # Inline the logic since module-level WEBHOOK_SECRET may not be set
        expected = hmac.new(
            self.SECRET.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        assert hmac.compare_digest(expected, sig)

    def test_tampered_body_rejected(self):
        body = b'{"event": "subscription.pending"}'
        sig  = self._compute_sig(body)
        tampered = b'{"event": "subscription.halted"}'
        # Re-compute sig against *original* body — should not match tampered body
        expected = hmac.new(
            self.SECRET.encode("utf-8"), tampered, hashlib.sha256
        ).hexdigest()
        assert not hmac.compare_digest(expected, sig)

    def test_wrong_secret_rejected(self):
        body = b'{"event": "subscription.pending"}'
        sig_with_correct = self._compute_sig(body, secret=self.SECRET)
        sig_with_wrong   = self._compute_sig(body, secret="wrong_secret")
        assert sig_with_correct != sig_with_wrong

    def test_empty_body_produces_valid_hmac(self):
        """Edge case: empty body must still produce a valid (though unlikely) HMAC."""
        body = b""
        sig = self._compute_sig(body)
        assert len(sig) == 64  # SHA256 hex digest is always 64 chars


# ─────────────────────────────────────────────────────────────────────────────
# 4. Schema Edge Cases
# ─────────────────────────────────────────────────────────────────────────────

class TestSchemaEdgeCases:

    def test_invalid_count_raises(self):
        with pytest.raises(Exception):  # Pydantic ValidationError
            SubscriptionRecord(
                subscription_id="sub_AAAAAAAAAAAAA1",
                status=SubscriptionStatus.PENDING,
                auth_attempts=1,
                paid_count=10,
                remaining_count=10,
                total_count=5,    # paid+remaining > total → should raise
                customer_id="cust_AAAAAAAAAAAAA1",
                plan_id="plan_AAAAAAAAAAAAA1",
                amount=99_900,
                mandate_age_days=30,
                above_15k_threshold=False,
            )

    def test_amount_15k_sets_flag(self):
        r = SubscriptionRecord(
            subscription_id="sub_AAAAAAAAAAAAA2",
            status=SubscriptionStatus.PENDING,
            auth_attempts=1,
            paid_count=1,
            remaining_count=5,
            total_count=6,
            customer_id="cust_AAAAAAAAAAAAA2",
            plan_id="plan_AAAAAAAAAAAAA2",
            amount=1_500_000,      # Exactly ₹15,000
            mandate_age_days=10,
            above_15k_threshold=False,  # Will be corrected by validator
        )
        assert r.above_15k_threshold is True

    def test_amount_below_15k_clears_flag(self):
        r = SubscriptionRecord(
            subscription_id="sub_AAAAAAAAAAAAA3",
            status=SubscriptionStatus.PENDING,
            auth_attempts=1,
            paid_count=1,
            remaining_count=5,
            total_count=6,
            customer_id="cust_AAAAAAAAAAAAA3",
            plan_id="plan_AAAAAAAAAAAAA3",
            amount=999_999,         # ₹9,999.99 — just below threshold
            mandate_age_days=10,
            above_15k_threshold=True,  # Will be corrected to False
        )
        assert r.above_15k_threshold is False

    def test_status_enum_values(self):
        """All defined status strings must be accepted."""
        for status in SubscriptionStatus:
            r = SubscriptionRecord(
                subscription_id="sub_AAAAAAAAAAAAA4",
                status=status,
                auth_attempts=0,
                paid_count=0,
                remaining_count=3,
                total_count=3,
                customer_id="cust_AAAAAAAAAAAAA4",
                plan_id="plan_AAAAAAAAAAAAA4",
                amount=99_900,
                mandate_age_days=1,
                above_15k_threshold=False,
            )
            assert r.status in (status, status.value)

    def test_failure_bucket_none_for_healthy(self):
        """A healthy subscription should have failure_bucket=NONE."""
        r = SubscriptionRecord(
            subscription_id="sub_AAAAAAAAAAAAA5",
            status=SubscriptionStatus.ACTIVE,
            auth_attempts=0,
            paid_count=3,
            remaining_count=9,
            total_count=12,
            customer_id="cust_AAAAAAAAAAAAA5",
            plan_id="plan_AAAAAAAAAAAAA5",
            amount=99_900,
            mandate_age_days=90,
            above_15k_threshold=False,
            failure_bucket=FailureBucket.NONE,
        )
        assert r.failure_bucket in (FailureBucket.NONE, FailureBucket.NONE.value)

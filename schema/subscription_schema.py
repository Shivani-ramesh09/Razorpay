"""
schema/subscription_schema.py
────────────────────────────────────────────────────────────────────────────────
Canonical data model for a Razorpay subscription event, combining:

  • Core fields — sourced directly from the Razorpay Subscriptions entity
    (https://razorpay.com/docs/api/subscriptions/)
  • Derived / enriched fields — computed or inferred at classification time;
    these fields are NEVER invented — they map to real decision inputs that
    the guardrail and LLM agent need.

Design notes
------------
- Pydantic v2 is used so we get free JSON (de)serialisation, validation,
  and schema export (.model_json_schema()) for documentation.
- Unix timestamps from Razorpay are stored as ints; helper properties
  expose them as Python datetimes for downstream code.
- All "optional" core fields (charge_at, current_start, current_end) can
  be None when the subscription hasn't yet charged — this matches the real
  API behaviour.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


# ── Enumerations ──────────────────────────────────────────────────────────────

class SubscriptionStatus(str, Enum):
    """
    All valid Razorpay subscription statuses.
    Reference: https://razorpay.com/docs/api/subscriptions/#subscription-states
    """
    CREATED       = "created"
    AUTHENTICATED = "authenticated"
    ACTIVE        = "active"
    PENDING       = "pending"       # Charge attempted, failed — retries may follow
    HALTED        = "halted"        # NPCI retry budget exhausted; needs intervention
    PAUSED        = "paused"
    CANCELLED     = "cancelled"
    COMPLETED     = "completed"
    EXPIRED       = "expired"


class FailureBucket(str, Enum):
    """
    Failure classification bucket.  Maps to the five root-cause categories
    identified in the PRD, plus a sentinel for non-failure states.

    bank_side         — Temporary bank-server downtime / switch error (~40%)
    low_balance       — Insufficient funds in customer account (~25%)
    expired_mandate   — UPI mandate expired or revoked (~15%)
    reauth_mismatch   — RBI re-authorisation required (amount > ₹15,000 or
                        policy change) (~10%)
    genuine_decline   — Customer explicitly declined / blocked (~10%)
    none              — No failure; subscription is healthy
    """
    BANK_SIDE       = "bank_side"
    LOW_BALANCE     = "low_balance"
    EXPIRED_MANDATE = "expired_mandate"
    REAUTH_MISMATCH = "reauth_mismatch"
    GENUINE_DECLINE = "genuine_decline"
    NONE            = "none"


# ── Core Entity ───────────────────────────────────────────────────────────────

class SubscriptionRecord(BaseModel):
    """
    Full subscription record — core Razorpay fields + derived enrichment fields.

    Instances can be created from:
      1. A real Razorpay webhook payload (use from_webhook_payload())
      2. The synthetic batch generator (scripts/generate_synthetic_batch.py)
      3. Direct construction for unit tests
    """

    # ── Core fields from Razorpay API ─────────────────────────────────────────

    subscription_id: str = Field(
        ...,
        description="Razorpay subscription ID, e.g. 'sub_XXXXXXXXXXXXXXXX'",
        pattern=r"^sub_[A-Za-z0-9]+$",
    )
    status: SubscriptionStatus = Field(
        ...,
        description="Current lifecycle state of the subscription",
    )
    auth_attempts: int = Field(
        ...,
        ge=0,
        description=(
            "Number of authentication attempts made so far. "
            "Distinct from charge attempts."
        ),
    )
    paid_count: int = Field(
        ...,
        ge=0,
        description="Number of billing cycles successfully charged",
    )
    remaining_count: int = Field(
        ...,
        ge=0,
        description=(
            "Billing cycles remaining. When 0 the subscription is complete. "
            "The guardrail uses this to cap retry attempts."
        ),
    )
    total_count: int = Field(
        ...,
        ge=1,
        description="Total number of billing cycles in the subscription plan",
    )
    charge_at: Optional[int] = Field(
        None,
        description="Unix timestamp of the next scheduled charge attempt",
    )
    current_start: Optional[int] = Field(
        None,
        description="Unix timestamp — start of the current billing period",
    )
    current_end: Optional[int] = Field(
        None,
        description="Unix timestamp — end of the current billing period",
    )
    customer_id: str = Field(
        ...,
        description="Razorpay customer ID, e.g. 'cust_XXXXXXXXXXXXXXXX'",
        pattern=r"^cust_[A-Za-z0-9]+$",
    )
    plan_id: str = Field(
        ...,
        description="Razorpay plan ID, e.g. 'plan_XXXXXXXXXXXXXXXX'",
        pattern=r"^plan_[A-Za-z0-9]+$",
    )

    # ── Error signal fields (from failed charge event on the payment entity) ──
    # These are present on real Razorpay webhook payloads under
    # payload.payment.entity.error_code / error_description when a charge fails.
    # The Failure Classifier reads these as raw input — it must NOT read
    # failure_bucket directly (that field is ground truth for scoring only).

    error_code: Optional[str] = Field(
        None,
        description=(
            "Razorpay / bank error code from the failed charge attempt. "
            "E.g. 'INSUFFICIENT_FUNDS', 'BANK_INTERNAL_ERROR', 'MANDATE_EXPIRED'. "
            "Primary signal for the rules-based classifier."
        ),
    )
    error_description: Optional[str] = Field(
        None,
        description=(
            "Human-readable error description from the bank or payment switch. "
            "Used as a secondary classifier signal when error_code is ambiguous."
        ),
    )
    error_reason: Optional[str] = Field(
        None,
        description=(
            "Granular error reason from Razorpay payment entity "
            "(e.g. 'international_transaction_not_allowed', 'insufficient_funds')."
        ),
    )
    error_source: Optional[str] = Field(
        None,
        description=(
            "Error origin from payment entity (e.g. 'business', 'customer', 'bank', 'gateway')."
        ),
    )
    payment_id: Optional[str] = Field(
        None,
        description="Razorpay payment ID if sourced from a payment event (e.g. 'pay_XXXXXXXXXXXXXXXX').",
    )

    # ── Derived / enriched fields ─────────────────────────────────────────────

    failure_bucket: FailureBucket = Field(
        FailureBucket.NONE,
        description=(
            "Root-cause classification assigned by the Failure Classifier. "
            "Set to NONE for healthy subscriptions."
        ),
    )
    amount: int = Field(
        ...,
        ge=0,
        description="Charge amount in paise (₹1 = 100 paise), e.g. 99900 = ₹999",
    )
    mandate_age_days: int = Field(
        ...,
        ge=0,
        description=(
            "Number of days since the mandate was first authenticated. "
            "Used as a model feature; older mandates correlate with stale bank tokens."
        ),
    )
    days_since_last_success: Optional[int] = Field(
        None,
        ge=0,
        description=(
            "Days elapsed since the last successfully charged cycle. "
            "None if the subscription has never been successfully charged."
        ),
    )
    above_15k_threshold: bool = Field(
        ...,
        description=(
            "True when `amount` ≥ ₹15,000 (1,500,000 paise). "
            "Triggers RBI-mandated re-authorisation flow per NPCI circular. "
            "The guardrail uses this as a hard flag — re-auth must be attempted "
            "before any retry."
        ),
    )
    historical_payment_day_pattern: List[int] = Field(
        default_factory=list,
        description=(
            "Sorted list of calendar days-of-month (1–31) on which this customer "
            "has historically succeeded. Empty for new subscriptions. "
            "Used by the timing model to predict the best retry day."
        ),
    )

    # ── Computed validators ───────────────────────────────────────────────────

    @model_validator(mode="after")
    def _validate_counts(self) -> SubscriptionRecord:
        if self.paid_count + self.remaining_count > self.total_count:
            raise ValueError(
                f"paid_count ({self.paid_count}) + remaining_count "
                f"({self.remaining_count}) > total_count ({self.total_count})"
            )
        return self

    @model_validator(mode="after")
    def _sync_above_15k(self) -> SubscriptionRecord:
        """Keep above_15k_threshold consistent with amount."""
        expected = self.amount >= 1_500_000  # ₹15,000 in paise
        if self.above_15k_threshold != expected:
            # Allow the caller to have set it explicitly; just keep them in sync
            object.__setattr__(self, "above_15k_threshold", expected)
        return self

    # ── Convenience properties ────────────────────────────────────────────────

    @property
    def charge_at_dt(self) -> Optional[datetime]:
        if self.charge_at is None:
            return None
        return datetime.fromtimestamp(self.charge_at, tz=timezone.utc)

    @property
    def current_start_dt(self) -> Optional[datetime]:
        if self.current_start is None:
            return None
        return datetime.fromtimestamp(self.current_start, tz=timezone.utc)

    @property
    def current_end_dt(self) -> Optional[datetime]:
        if self.current_end is None:
            return None
        return datetime.fromtimestamp(self.current_end, tz=timezone.utc)

    # ── Factory: build from a real Razorpay webhook payload ──────────────────

    @classmethod
    def from_webhook_payload(
        cls,
        payload: dict,
        *,
        failure_bucket: FailureBucket = FailureBucket.NONE,
        mandate_age_days: int = 0,
        days_since_last_success: Optional[int] = None,
        historical_payment_day_pattern: Optional[List[int]] = None,
    ) -> SubscriptionRecord:
        """
        Construct a SubscriptionRecord from a raw Razorpay webhook JSON dict.

        Supports:
          1. payload.subscription.entity (standard subscription event)
          2. payload.payment.entity (standalone payment event with invoice/API lookup)
          3. Compound events containing both subscription and payment entities
        """
        payload_container: dict = payload.get("payload", {})
        sub_entity: dict = payload_container.get("subscription", {}).get("entity", {})
        pay_entity: dict = payload_container.get("payment", {}).get("entity", {})

        # Extract payment-level error signals if present
        payment_id = pay_entity.get("id") if pay_entity else None
        error_code = pay_entity.get("error_code") if pay_entity else None
        error_description = pay_entity.get("error_description") if pay_entity else None
        error_reason = pay_entity.get("error_reason") if pay_entity else None
        error_source = pay_entity.get("error_source") if pay_entity else None
        invoice_id = pay_entity.get("invoice_id") if pay_entity else None

        # Case 1: Standard subscription entity present in payload
        if sub_entity:
            plan_id_raw: str = sub_entity.get("plan_id", "plan_unknown")
            amount_raw: int = sub_entity.get("quantity", 1) * sub_entity.get("total_amount", 0)
            if amount_raw == 0 and pay_entity and pay_entity.get("amount"):
                amount_raw = pay_entity.get("amount", 0)

            # Ensure customer_id matches regex
            cust_id_raw = sub_entity.get("customer_id")
            if not cust_id_raw or not str(cust_id_raw).startswith("cust_"):
                cust_id_raw = f"cust_{sub_entity['id'].replace('sub_', '')}"

            status_val = sub_entity.get("status", "pending")
            try:
                status_enum = SubscriptionStatus(status_val)
            except ValueError:
                status_enum = SubscriptionStatus.PENDING

            return cls(
                subscription_id=sub_entity["id"],
                status=status_enum,
                auth_attempts=sub_entity.get("auth_attempts", 0),
                paid_count=sub_entity.get("paid_count", 0),
                remaining_count=sub_entity.get("remaining_count", sub_entity.get("total_count", 1)),
                total_count=sub_entity.get("total_count", 1),
                charge_at=sub_entity.get("charge_at"),
                current_start=sub_entity.get("current_start"),
                current_end=sub_entity.get("current_end"),
                customer_id=cust_id_raw,
                plan_id=plan_id_raw,
                failure_bucket=failure_bucket,
                amount=amount_raw,
                mandate_age_days=mandate_age_days,
                days_since_last_success=days_since_last_success,
                above_15k_threshold=amount_raw >= 1_500_000,
                historical_payment_day_pattern=historical_payment_day_pattern or [],
                error_code=error_code,
                error_description=error_description,
                error_reason=error_reason,
                error_source=error_source,
                payment_id=payment_id,
            )

        # Case 2: Standalone payment entity (e.g. payment.failed event)
        if pay_entity:
            amount_raw = pay_entity.get("amount", 0)
            resolved_sub = {}
            if invoice_id:
                try:
                    import os
                    import razorpay
                    from dotenv import load_dotenv

                    load_dotenv(override=True)
                    key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
                    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
                    if key_id and key_secret and not key_id.startswith("rzp_test_XXXX"):
                        client = razorpay.Client(auth=(key_id, key_secret))
                        inv = client.invoice.fetch(invoice_id)
                        inv_sub_id = inv.get("subscription_id")
                        if inv_sub_id:
                            resolved_sub = client.subscription.fetch(inv_sub_id)
                except Exception:
                    pass

            if resolved_sub and resolved_sub.get("id"):
                sub_id = resolved_sub["id"]
                plan_id = resolved_sub.get("plan_id", "plan_unknown")
                cust_id = resolved_sub.get("customer_id")
                total_count = resolved_sub.get("total_count", 12)
                paid_count = resolved_sub.get("paid_count", 0)
                remaining_count = resolved_sub.get("remaining_count", max(1, total_count - paid_count))
                auth_attempts = max(1, resolved_sub.get("auth_attempts", 1))
            else:
                base_suffix = invoice_id.replace("inv_", "") if invoice_id else payment_id.replace("pay_", "") if payment_id else "RealWebhook01"
                sub_id = f"sub_{base_suffix}"
                plan_id = "plan_unknown"
                cust_id = None
                total_count = 12
                paid_count = 0
                remaining_count = 12
                auth_attempts = 1

            if not cust_id or not str(cust_id).startswith("cust_"):
                cust_id = f"cust_{sub_id.replace('sub_', '')}"

            return cls(
                subscription_id=sub_id,
                status=SubscriptionStatus.PENDING,
                auth_attempts=auth_attempts,
                paid_count=paid_count,
                remaining_count=remaining_count,
                total_count=total_count,
                charge_at=pay_entity.get("created_at"),
                current_start=None,
                current_end=None,
                customer_id=cust_id,
                plan_id=plan_id,
                failure_bucket=failure_bucket,
                amount=amount_raw,
                mandate_age_days=mandate_age_days,
                days_since_last_success=days_since_last_success,
                above_15k_threshold=amount_raw >= 1_500_000,
                historical_payment_day_pattern=historical_payment_day_pattern or [],
                error_code=error_code,
                error_description=error_description,
                error_reason=error_reason,
                error_source=error_source,
                payment_id=payment_id,
            )

        raise ValueError(
            "Webhook payload does not contain payload.subscription.entity or payload.payment.entity"
        )


    model_config = {"use_enum_values": True}

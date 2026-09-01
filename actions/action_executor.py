"""
actions/action_executor.py
────────────────────────────────────────────────────────────────────────────────
Multi-Channel Action Execution Engine (Mock Implementation).

Provides structured execution handlers for all 5 recovery actions:
    1. execute_delayed_retry
    2. execute_promise_to_pay_nudge
    3. execute_reauth_request
    4. execute_escalate_to_human
    5. execute_stand_down

Each handler returns a typed ExecutionReceipt with realistic mock identifiers,
timestamps, dispatch statuses, and metadata (including the RBI above_15k tag
for re-authorization links).
"""

from __future__ import annotations

import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import sys
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from schema.subscription_schema import SubscriptionRecord
from actions.nudge_templates import render_nudge_message
from actions.p2p_ledger import record_nudge_dispatch


# ── Receipt Model ─────────────────────────────────────────────────────────────

@dataclass
class ExecutionReceipt:
    """
    Structured outcome receipt returned by action execution handlers.

    Fields
    ------
    action : str
        The action executed (e.g. 'delayed_retry', 'promise_to_pay_nudge')
    channel : str
        Delivery channel (e.g. 'whatsapp', 'sms', 'upi_autopay', 'human_agent')
    mock_reference_id : str
        Realistic mock identifier (e.g. 'wamsg_8f21bc', 'paylink_e74a12')
    timestamp : str
        ISO 8601 UTC dispatch timestamp
    status : str
        Execution status; defaults to 'dispatched'
    above_15k : Optional[bool]
        Flag explicitly tagging high-ticket re-auth (>₹15,000) for RBI compliance
    details : Dict[str, Any]
        Additional context (e.g. rendered message preview, retry scheduled time)
    """
    action: str
    channel: str
    mock_reference_id: str
    timestamp: str
    status: str = "dispatched"
    above_15k: Optional[bool] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert receipt to dictionary for JSON serialisation / audit logging."""
        return asdict(self)


# ── ID Generators ─────────────────────────────────────────────────────────────

def _generate_mock_id(prefix: str, length: int = 12) -> str:
    """Generate a realistic mock reference token with prefix."""
    token = secrets.token_hex(length // 2)
    return f"{prefix}_{token}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Action Handlers ───────────────────────────────────────────────────────────

def execute_delayed_retry(
    record: SubscriptionRecord,
    channel: str = "upi_autopay",
) -> ExecutionReceipt:
    """
    Execute delayed retry scheduling via UPI Autopay.

    Queues an automated debit retry with the bank/NPCI switch at the
    optimal predicted offset.
    """
    delivery_channel = channel if channel and channel != "N/A" else "upi_autopay"
    ref_id = _generate_mock_id("retry")
    scheduled_offset = getattr(record, "predicted_optimal_offset_hours", 24) or 24

    return ExecutionReceipt(
        action="delayed_retry",
        channel=delivery_channel,
        mock_reference_id=ref_id,
        timestamp=_now_iso(),
        status="dispatched",
        above_15k=getattr(record, "above_15k_threshold", False),
        details={
            "scheduled_offset_hours": scheduled_offset,
            "subscription_id": record.subscription_id,
            "target_amount_paise": record.amount,
            "handler": "NPCIAutopayScheduler",
        },
    )


def execute_promise_to_pay_nudge(
    record: SubscriptionRecord,
    channel: str = "whatsapp",
) -> ExecutionReceipt:
    """
    Execute customer promise-to-pay nudge.

    Renders a respectful Hinglish message (WhatsApp or SMS) with a 1-click
    payment link and logs the dispatch to the P2P ledger.
    """
    target_channel = channel.lower().strip() if channel and channel != "N/A" else "whatsapp"
    if target_channel not in {"whatsapp", "sms"}:
        target_channel = "whatsapp"

    prefix = "wamsg" if target_channel == "whatsapp" else "sms"
    ref_id = _generate_mock_id(prefix)

    # Derive display properties
    amount_inr = round(record.amount / 100, 2)
    short_url = f"https://rzp.io/i/nudge_{secrets.token_hex(4)}"
    customer_name = f"Customer ({record.customer_id[-6:]})" if record.customer_id else "Customer"

    # Render template
    rendered_message = render_nudge_message(
        channel=target_channel,
        customer_name=customer_name,
        merchant_name="Razorpay Subscriptions",
        amount_in_rupees=amount_inr,
        short_url=short_url,
    )

    ts = _now_iso()

    # Log to P2P ledger
    record_nudge_dispatch(
        subscription_id=record.subscription_id,
        channel=target_channel,
        mock_reference_id=ref_id,
        amount=record.amount,
        sent_timestamp=ts,
    )

    return ExecutionReceipt(
        action="promise_to_pay_nudge",
        channel=target_channel,
        mock_reference_id=ref_id,
        timestamp=ts,
        status="dispatched",
        above_15k=getattr(record, "above_15k_threshold", False),
        details={
            "short_url": short_url,
            "message_preview": rendered_message[:120] + "...",
            "subscription_id": record.subscription_id,
            "amount_inr": amount_inr,
            "ledger_logged": True,
        },
    )


def execute_reauth_request(
    record: SubscriptionRecord,
    channel: str = "email",
) -> ExecutionReceipt:
    """
    Execute re-authorization link generation.

    Generates a secure mandate re-authorization link for mandates requiring
    customer 2FA/approval (e.g. amount >= ₹15,000 per RBI rules or expired mandate).
    """
    # High-ticket check (> ₹15,000 = 1,500,000 paise)
    is_above_15k = getattr(record, "above_15k_threshold", False) or (record.amount >= 1500000 if record.amount else False)

    # Preferred channel for high-ticket is email or whatsapp
    target_channel = channel.lower().strip() if channel and channel != "N/A" else ("email" if is_above_15k else "sms")

    ref_id = _generate_mock_id("paylink")
    reauth_url = f"https://rzp.io/i/reauth_{secrets.token_hex(4)}"

    return ExecutionReceipt(
        action="reauth_request",
        channel=target_channel,
        mock_reference_id=ref_id,
        timestamp=_now_iso(),
        status="dispatched",
        above_15k=is_above_15k,
        details={
            "reauth_url": reauth_url,
            "rbi_threshold_mandated": is_above_15k,
            "subscription_id": record.subscription_id,
            "amount_inr": round(record.amount / 100, 2),
            "compliance_tag": "RBI_E_MANDATE_ABOVE_15K" if is_above_15k else "STANDARD_REAUTH",
        },
    )


def execute_escalate_to_human(
    record: SubscriptionRecord,
    channel: str = "human_agent",
) -> ExecutionReceipt:
    """
    Execute escalation to merchant operations / support team.

    Generated when attempts are exhausted but customer has high tenure or value.
    """
    target_channel = channel if channel and channel != "N/A" else "human_agent"
    ref_id = _generate_mock_id("tkt")

    return ExecutionReceipt(
        action="escalate_to_human",
        channel=target_channel,
        mock_reference_id=ref_id,
        timestamp=_now_iso(),
        status="dispatched",
        above_15k=getattr(record, "above_15k_threshold", False),
        details={
            "ticket_type": "HIGH_PRIORITY_ESCALATION",
            "subscription_id": record.subscription_id,
            "auth_attempts": record.auth_attempts,
            "mandate_age_days": record.mandate_age_days,
            "handler": "MerchantOpsQueue",
        },
    )


def execute_stand_down(
    record: SubscriptionRecord,
    channel: str = "none",
) -> ExecutionReceipt:
    """
    Execute stand-down.

    Compliantly suppresses further retries and logs the non-action receipt
    (used for opt-outs, genuine declines, zero cycles remaining).
    """
    ref_id = _generate_mock_id("std")
    target_channel = channel if channel and channel not in {"N/A", "none"} else "internal_system"

    return ExecutionReceipt(
        action="stand_down",
        channel=target_channel,
        mock_reference_id=ref_id,
        timestamp=_now_iso(),
        status="dispatched",
        above_15k=getattr(record, "above_15k_threshold", False),
        details={
            "stand_down_reason": "Compliant suppression of retry",
            "subscription_id": record.subscription_id,
            "status": str(record.status),
        },
    )


# ── Master Dispatcher ─────────────────────────────────────────────────────────

ACTION_HANDLERS = {
    "delayed_retry": execute_delayed_retry,
    "promise_to_pay_nudge": execute_promise_to_pay_nudge,
    "reauth_request": execute_reauth_request,
    "escalate_to_human": execute_escalate_to_human,
    "stand_down": execute_stand_down,
    # Backward compatibility mappings for synonyms
    "immediate_retry": execute_delayed_retry,
    "retry": execute_delayed_retry,
}


def execute_action(
    action: str,
    record: SubscriptionRecord,
    channel: str = "N/A",
) -> ExecutionReceipt:
    """
    Dispatch the validated action to its dedicated execution handler.

    Parameters
    ----------
    action : str
        Action name (e.g. 'promise_to_pay_nudge')
    record : SubscriptionRecord
        Subscription context
    channel : str
        Delivery channel proposed by LLM or default

    Returns
    -------
    ExecutionReceipt
        Structured receipt with mock IDs, status, and metadata
    """
    handler = ACTION_HANDLERS.get(action, execute_stand_down)
    return handler(record, channel)

"""
agent/llm_agent.py
────────────────────────────────────────────────────────────────────────────────
A standalone LLM reasoning agent using Gemini for proposing retry actions.

Loads GEMINI_API_KEY from .env.
Returns a structured AgentProposal object.
Not yet wired into the main pipeline.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Optional

import google.generativeai as genai
from dotenv import load_dotenv

from schema.subscription_schema import SubscriptionRecord

load_dotenv()

# Configure Gemini
api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

@dataclass
class AgentProposal:
    proposed_action: str
    proposed_channel: str
    reasoning: str
    confidence: str


def _build_context(record: SubscriptionRecord, context_vars: dict[str, Any]) -> dict[str, Any]:
    bucket_val = (
        record.failure_bucket
        if isinstance(record.failure_bucket, str)
        else record.failure_bucket.value
    )
    return {
        "subscription_id": record.subscription_id,
        "status": record.status,
        "failure_bucket": bucket_val,
        "error_code": record.error_code,
        "error_description": record.error_description,
        "amount": record.amount,
        "auth_attempts": record.auth_attempts,
        "remaining_count": record.remaining_count,
        "above_15k_threshold": getattr(record, "above_15k_threshold", False),
        "opt_out": getattr(record, "opt_out", False),
        "mandate_age_days": record.mandate_age_days,
        "days_since_last_success": context_vars.get("days_since_last_success", 30),
        "predicted_optimal_offset_hours": context_vars.get("predicted_optimal_offset_hours"),
    }


def propose(record: SubscriptionRecord, context_vars: dict[str, Any]) -> AgentProposal:
    """
    Propose a recovery action using an LLM.
    """
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment.")

    model_name_env = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    model = genai.GenerativeModel(
        model_name=model_name_env,
        generation_config={
            "temperature": 0.1,
            "response_mime_type": "application/json",
        }
    )

    context = _build_context(record, context_vars)

    prompt = f"""You are a Mandate Recovery Agent making decisions for Razorpay Subscriptions.
Analyze the following subscription failure record and propose the best recovery action.

Input Context:
{json.dumps(context, indent=2)}

Rules to respect:
1. If opt_out is true, action MUST be 'stand_down'.
2. If above_15k_threshold is true and bucket is reauth_mismatch, RBI mandates a strict re-auth.
3. If auth_attempts is maxed out (>=3), consider stand_down or escalate_to_human.
4. Use predicted_optimal_offset_hours to decide timing.
5. For genuine_decline, action is always stand_down.

Output valid JSON matching this schema:
{{
  "proposed_action": "<one of: delayed_retry|reauth_request|promise_to_pay_nudge|stand_down|escalate_to_human>",
  "proposed_channel": "<one of: upi_autopay|sms|whatsapp|email|human_agent>",
  "reasoning": "<2 sentences max>",
  "confidence": "<high|medium|low>"
}}
"""
    response = model.generate_content(prompt)
    try:
        data = json.loads(response.text)
        return AgentProposal(
            proposed_action=data.get("proposed_action", "stand_down"),
            proposed_channel=data.get("proposed_channel", "email"),
            reasoning=data.get("reasoning", "Failed to parse reasoning."),
            confidence=data.get("confidence", "low"),
        )
    except Exception as e:
        return AgentProposal(
            proposed_action="stand_down",
            proposed_channel="email",
            reasoning=f"Error parsing LLM response: {e}",
            confidence="low",
        )

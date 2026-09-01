"""
agent/llm_agent.py
────────────────────────────────────────────────────────────────────────────────
A standalone LLM reasoning agent using Groq (OpenAI-compatible API) for
proposing recovery actions.

Model: openai/gpt-oss-120b (30 RPM, 1,000 RPD, 8,000 TPM, 200,000 TPD).
Loads GROQ_API_KEY from .env.
Returns a structured AgentProposal object conforming to agent/llm_agent_contract.md.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Optional

from pathlib import Path

from dotenv import load_dotenv

from schema.subscription_schema import SubscriptionRecord

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Model defaults
DEFAULT_MODEL = "openai/gpt-oss-120b"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"


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
    status_val = (
        record.status
        if isinstance(record.status, str)
        else getattr(record.status, "value", str(record.status))
    )
    opt_out_val = context_vars.get("opt_out", getattr(record, "opt_out", False))
    above_15k_val = context_vars.get(
        "above_15k_threshold",
        getattr(record, "above_15k_threshold", False) or (record.amount >= 1500000 if record.amount else False),
    )

    return {
        "subscription_id": record.subscription_id,
        "status": status_val,
        "failure_bucket": bucket_val,
        "error_code": record.error_code,
        "error_description": record.error_description,
        "amount": record.amount,
        "auth_attempts": record.auth_attempts,
        "remaining_count": record.remaining_count,
        "above_15k_threshold": above_15k_val,
        "opt_out": opt_out_val,
        "mandate_age_days": record.mandate_age_days,
        "days_since_last_success": context_vars.get("days_since_last_success", 30),
        "predicted_optimal_offset_hours": context_vars.get("predicted_optimal_offset_hours"),
    }


def _get_system_prompt() -> str:
    return """You are a Mandate Recovery Agent making decisions for Razorpay Subscriptions.
Analyze the following subscription failure record and propose the best recovery action.

Rules to respect:
1. If opt_out is true, action MUST be 'stand_down'.
2. If above_15k_threshold is true and bucket is reauth_mismatch, RBI mandates a strict re-auth.
3. If auth_attempts is maxed out (>=3), both 'stand_down' and 'escalate_to_human' are valid terminal states depending on recoverability.
4. For low_balance failures: propose 'promise_to_pay_nudge' (via sms or whatsapp) when this is a repeat failure (auth_attempts >= 2) or when proactively notifying the customer helps them fund their account; propose 'delayed_retry' only for first-time low_balance (auth_attempts == 1) where waiting alone is sufficient.
5. Use predicted_optimal_offset_hours to decide timing.
6. For genuine_decline, action is always stand_down.
7. Reasoning must ONLY reference fields present in the input record — do NOT invent supporting details, external facts, or customer backstory not present in the data.

Output valid JSON matching this schema:
{
  "proposed_action": "<one of: delayed_retry|reauth_request|promise_to_pay_nudge|stand_down|escalate_to_human>",
  "proposed_channel": "<one of: upi_autopay|sms|whatsapp|email|human_agent>",
  "reasoning": "<2 sentences max>",
  "confidence": "<high|medium|low>"
}"""


def _get_retry_delay(e: Exception, attempt: int) -> float:
    import re
    msg = str(e)
    m = re.search(r"try again in ([\d\.]+)s", msg, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1)) + 1.5
        except ValueError:
            pass
    m2 = re.search(r"retry after ([\d\.]+)s", msg, re.IGNORECASE)
    if m2:
        try:
            return float(m2.group(1)) + 1.5
        except ValueError:
            pass
    backoffs = [3.0, 6.0, 12.0, 20.0, 30.0, 35.0]
    return backoffs[min(attempt, len(backoffs) - 1)]


def _call_groq_api(
    messages: list[dict[str, str]],
    model: str,
    api_key: str,
    max_retries: int = 6,
) -> str:
    """
    Call Groq API using groq SDK, openai SDK, or fallback standard urllib.
    Includes automated retry with adaptive backoff on 429 rate limits.
    """
    import time

    for attempt in range(max_retries):
        # 1. Try official groq SDK
        try:
            from groq import Groq

            client = Groq(api_key=api_key, timeout=20.0, max_retries=0)
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            return response.choices[0].message.content or ""
        except ImportError:
            pass
        except Exception as e:
            if ("429" in str(e) or "rate_limit" in str(e).lower()) and attempt < max_retries - 1:
                sleep_time = _get_retry_delay(e, attempt)
                print(f"    [Groq RateLimit 429] Waiting {sleep_time:.1f}s before retry {attempt+1}/{max_retries}...", flush=True)
                time.sleep(sleep_time)
                continue
            raise e

        # 2. Try openai SDK pointing to Groq base_url
        try:
            from openai import OpenAI

            client = OpenAI(base_url=GROQ_BASE_URL, api_key=api_key, timeout=20.0, max_retries=0)
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            return response.choices[0].message.content or ""
        except ImportError:
            pass
        except Exception as e:
            if ("429" in str(e) or "rate_limit" in str(e).lower()) and attempt < max_retries - 1:
                sleep_time = _get_retry_delay(e, attempt)
                print(f"    [Groq RateLimit 429] Waiting {sleep_time:.1f}s before retry {attempt+1}/{max_retries}...", flush=True)
                time.sleep(sleep_time)
                continue
            raise e

        # 3. Fallback: standard library urllib (zero external dependencies)
        import urllib.error
        import urllib.request

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            f"{GROQ_BASE_URL}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "MandateRecoveryAgent-Groq/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                sleep_time = _get_retry_delay(e, attempt)
                print(f"    [Groq RateLimit 429] Waiting {sleep_time:.1f}s before retry {attempt+1}/{max_retries}...", flush=True)
                time.sleep(sleep_time)
                continue
            error_body = e.read().decode("utf-8")
            raise RuntimeError(f"Groq API HTTP {e.code} Error: {error_body}") from e

    raise RuntimeError("Failed to get response from Groq API after retries.")



def propose(
    record: SubscriptionRecord,
    context_vars: dict[str, Any],
    raise_on_error: bool = False,
) -> AgentProposal:
    """
    Propose a recovery action using Groq LLM (openai/gpt-oss-120b).
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or api_key.strip() == "your_groq_api_key_here":
        if raise_on_error:
            raise ValueError("GROQ_API_KEY not configured in .env.")
        return AgentProposal(
            proposed_action="stand_down",
            proposed_channel="email",
            reasoning="GROQ_API_KEY not configured in .env.",
            confidence="low",
        )

    model_name = os.getenv("GROQ_MODEL", DEFAULT_MODEL)
    context = _build_context(record, context_vars)

    messages = [
        {"role": "system", "content": _get_system_prompt()},
        {
            "role": "user",
            "content": f"Input Context:\n{json.dumps(context, indent=2)}\n\nPropose the best recovery action conforming to the schema.",
        },
    ]

    try:
        response_text = _call_groq_api(messages, model_name, api_key)
        data = json.loads(response_text)
        return AgentProposal(
            proposed_action=data.get("proposed_action", "stand_down"),
            proposed_channel=data.get("proposed_channel", "email"),
            reasoning=data.get("reasoning", "Failed to parse reasoning."),
            confidence=data.get("confidence", "low"),
        )
    except Exception as e:
        if raise_on_error:
            raise e
        return AgentProposal(
            proposed_action="stand_down",
            proposed_channel="email",
            reasoning=f"Error getting proposal from Groq ({model_name}): {e}",
            confidence="low",
        )

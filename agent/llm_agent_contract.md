# LLM Agent Contract

This document defines the strict input and output contract for the LLM reasoning agent used in the Mandate Recovery Agent project.

## Input Context (Prompt payload)

The agent receives a full context payload for each record it processes. This context is serialized to JSON and included in the prompt.

```json
{
  "subscription_id": "sub_XYZ",
  "status": "active|halted",
  "failure_bucket": "bank_side|low_balance|expired_mandate|reauth_mismatch|genuine_decline",
  "error_code": "...",
  "error_description": "...",
  "amount": 1500,
  "auth_attempts": 1,
  "remaining_count": 5,
  "above_15k_threshold": false,
  "opt_out": false,
  "mandate_age_days": 15,
  "days_since_last_success": 30,
  "predicted_optimal_offset_hours": 48  // null if timing not applicable
}
```

## Output Requirements (Structured JSON)

The agent MUST output valid JSON matching the following schema EXACTLY. The agent is responsible for proposing an action and a channel, and justifying its decision concisely.

```json
{
  "proposed_action": "<one of: delayed_retry|reauth_request|promise_to_pay_nudge|stand_down|escalate_to_human>",
  "proposed_channel": "<one of: upi_autopay|sms|whatsapp|email|human_agent>",
  "reasoning": "<2 sentences max>",
  "confidence": "<high|medium|low>"
}
```

## Business Logic Rules

The agent must respect the following rules, though the guardrail validator acts as a final safety net:
1. If `opt_out` is true, action MUST be `stand_down`.
2. If `above_15k_threshold` is true and bucket is `reauth_mismatch`, RBI mandates a strict re-auth.
3. If `auth_attempts` is maxed out (>=3), both `stand_down` and `escalate_to_human` are valid terminal states for maxed-out failures, and the agent's choice reflects its judgment of recoverability rather than a fixed rule.
4. For `low_balance` failures, propose `promise_to_pay_nudge` (via SMS or WhatsApp) when this is a repeat low_balance failure (`auth_attempts >= 2`) or when proactively notifying the customer would help them fund the account before the next attempt; propose `delayed_retry` only for a first-time low_balance failure (`auth_attempts == 1`) where waiting alone is sufficient.
5. Use `predicted_optimal_offset_hours` to decide timing for retries and nudge scheduling.
6. For `genuine_decline`, the action is `stand_down`.
7. Reasoning must ONLY reference fields present in the input record — do NOT invent supporting details, customer backstory, or external facts not present in the data.

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
3. If `auth_attempts` is maxed out, consider `stand_down` or `escalate_to_human` depending on value.
4. Use `predicted_optimal_offset_hours` as a signal for timing-related nudges (e.g., if predicting 168h, a nudge via whatsapp might be appropriate near that window).
5. For `genuine_decline`, the action is `stand_down`.

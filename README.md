# Mandate Recovery Agent
### Razorpay Buildathon — AI Revenue Recovery

An agent that recovers failed UPI Autopay mandates: classifies the failure, predicts optimal retry timing, has an LLM propose the next action, and enforces a deterministic guardrail that can veto it — before anything executes.

## Why

UPI Autopay fails 30–50% of the time. NPCI allows exactly 4 automated attempts (24h/72h/168h windows). After that, Razorpay fires `subscription.halted` and stops — permanently. What happens next today is manual or nonexistent. This agent handles it, safely.

## Architecture

```
Razorpay Webhooks → Classifier → Timing Model → LLM Agent → Guardrail Validator → Action Executor → Audit Log → Dashboard
```

The guardrail enforces NPCI/RBI rules as data (`guardrails/rules.yaml`) — the LLM proposes, the guardrail decides, every verdict is logged.

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in Razorpay test keys + Gemini key
python -m pytest tests/ -v
python scripts/generate_synthetic_batch.py
python pipeline/run_pipeline.py
```

To capture a real webhook: run `ngrok http 5000`, register the URL under Dashboard → Settings → Webhooks, then trigger a test failure with card `5267 3181 8797 5449`.

> Test-mode retry windows run in real time — `subscription.halted` takes ~7 days to occur naturally. Use `data/synthetic_batch.json` for development; use a pre-recorded/constructed halted payload for the demo.

## Structure

```
webhook_listener/   Flask receiver, HMAC-verified
schema/              Pydantic subscription model (core + derived fields)
classifier/          Failure → bucket (bank_side / low_balance / expired / reauth / decline)
timing/              Predictive optimal retry-timing model
agent/               LLM reasoning agent (Gemini)
guardrails/          rules.yaml + validator.py — hard NPCI/RBI rules, LLM cannot override
actions/             Action picker + multi-channel executor
pipeline/            End-to-end orchestration + audit logging
data/                Synthetic batch, audit log, captured webhooks
```

## Guardrail Rules

| Rule | Enforces |
|---|---|
| `MAX_ATTEMPTS` | ≤3 retries per NPCI |
| `COOLDOWN_WINDOWS` | 24h / 72h / 168h spacing |
| `OPT_OUT_KILL_SWITCH` | Immediate stand-down |
| `REAUTH_THRESHOLD` | ≥₹15k requires re-auth (RBI) |
| `HALTED_SUBSCRIPTION` | No retries after halt |
| `MIN_REMAINING_CYCLES` | Stand down at 0 remaining |
| `GENUINE_DECLINE_STANDDOWN` | Nudge once, then stop |

## Build Status

| Day | Focus | Status |
|---|---|---|
| 1 | Webhook capture, schema, synthetic data, guardrail rules | ✅ |
| 2 | Guardrail validator + rules-based baseline pipeline | ✅ |
| 3 | Predictive timing model + LLM agent scaffold | ✅ |
| 4 | LLM agent wired in, guardrail actively overrides | ✅ |
| 5 | Multi-channel actions (re-auth, Hinglish nudge, promise-to-pay) | ⬜ |
| 6 | Portfolio dashboard + edge-case demos | ⬜ |
| 7 | Polish + submission | ⬜ |

## Success Metrics

Recovery rate vs. naive-retry baseline · ₹ recovered / ₹ at risk · estimated Razorpay fee revenue recovered · zero guardrail violations · ≥1 demonstrated LLM override

---
Built for Razorpay Buildathon. Not for production use.

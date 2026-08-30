# Product Requirements Document
## Mandate Recovery Agent — Razorpay Buildathon (Track 03: AI Revenue Recovery)

---

## 1. Problem Statement

UPI Autopay — India's dominant recurring-payment rail — fails at a 30–50% rate, far behind card mandates. NPCI caps automatic recovery at exactly 4 attempts (1 original + 3 retries, fired at fixed windows, typically 24h / 72h / 168h). When those 4 attempts are exhausted, Razorpay fires a `subscription.halted` webhook and its own automated retry system stops — permanently, unless the merchant does something.

Today, "doing something" is manual, inconsistent, or absent. The result: subscriptions silently lapse, insurance policies quietly go uncovered, SaaS customers churn who never intended to churn — and every one of those failures is also a transaction fee Razorpay itself never collects.

**This is not a merchant-only problem. It is a direct, recurring, invisible revenue leak for Razorpay.**

## 2. Beneficiaries

| Stakeholder | What they gain |
|---|---|
| **Razorpay** | Recovers transaction-fee revenue on mandates that would otherwise die at `subscription.halted`; sellable as a value-added product layer on Subscriptions |
| **Merchant** | Recovers subscription/EMI/premium revenue without building this logic themselves |
| **End customer** | Doesn't lose access to a service they intended to keep paying for, without spammy over-retrying |

## 3. Goals

- Detect every `subscription.pending` and `subscription.halted` event in real time via Razorpay test-mode webhooks
- Classify the failure cause per event (bank-side, low-balance, expired mandate, RBI re-authorization mismatch, genuine decline)
- Decide the correct recovery action per classification, using an LLM reasoning agent whose proposals are checked by a deterministic guardrail layer
- Predict optimal retry/nudge timing using a trained model, not fixed heuristics
- Execute recovery across multiple channels: timed retry (within NPCI's allowed window), re-authorization request, Hinglish promise-to-pay nudge
- Report portfolio-level outcomes: ₹ recovered, recovery rate by failure class, Razorpay fee-revenue impact, full audit trail

## 4. Non-Goals (explicitly out of scope for this build)

- We do **not** attempt to exceed or bypass NPCI's 4-attempt cap — all in-window actions operate strictly inside it
- We do **not** control or simulate actual bank-server downtime — bank-side failures are treated as a classification label, not something we fix
- We do **not** build a production-grade voice pipeline — the "Hinglish voice recovery" direction is implemented as a templated conversational nudge (text/scripted), not live voice synthesis
- We do **not** deploy to real merchants or move real money — this is a test-mode / synthetic-batch prototype

## 5. System Architecture

```
Razorpay Test-Mode Subscriptions API
        │
        ▼
Webhook Listener (subscription.pending / subscription.halted / subscription.charged)
        │
        ▼
Failure Classifier (reason bucket: bank-side / low-balance / expired / re-auth-mismatch / decline)
        │
        ▼
Predictive Timing Model (optimal retry/nudge timing, trained vs. naive-immediate baseline)
        │
        ▼
LLM Reasoning Agent (proposes: action + channel + reasoning, given full context)
        │
        ▼
Guardrail Validator (approves / overrides proposal against hard rules: max attempts, cooldown, opt-out, NPCI window compliance)
        │
        ▼
Action Executor (timed retry trigger / re-auth link / Hinglish promise-to-pay nudge / escalate / stand down)
        │
        ▼
Audit Log  ──────────────────────────────────────────────►  Portfolio Dashboard
                                                              (₹ recovered, recovery rate by bucket,
                                                               Razorpay fee-impact, exception list)
```

## 6. Data Model (mirrors Razorpay's real Subscriptions entity — no invented fields)

Core fields consumed from webhook payloads:
`subscription_id`, `status` (`authenticated` / `active` / `pending` / `halted` / `paused` / `cancelled`), `auth_attempts`, `paid_count`, `remaining_count`, `total_count`, `charge_at`, `current_start`, `current_end`, `customer_id`, `plan_id`.

Derived/enriched fields for classification and modeling:
`failure_bucket`, `amount`, `mandate_age_days`, `days_since_last_success`, `historical_payment_day_pattern`, `above_15k_threshold` (RBI re-auth flag), `previous_recovery_outcome`.

## 7. Functional Requirements

**FR1 — Webhook Ingestion:** Consume real `subscription.pending`, `subscription.halted`, `subscription.charged`, `subscription.cancelled` events from a Razorpay test-mode account.

**FR2 — Synthetic Batch Generator:** Produce 200+ realistic subscription records matching the real schema, with a failure-reason distribution grounded in known real-world proportions (bank-side ≈40%, remainder split across balance/expired/re-auth/decline).

**FR3 — Classifier:** Assign each failing event to exactly one failure bucket.

**FR4 — Predictive Timing Model:** Trained model (e.g., gradient-boosted classifier/regressor) outputs optimal retry/nudge timing; must report a measured lift over a naive-immediate-retry baseline.

**FR5 — LLM Reasoning Agent:** Given full context (classification, attempt history, amount, timing prediction), proposes an action, a channel, and a natural-language justification.

**FR6 — Guardrail Validator:** Deterministically enforces: max attempts ≤ NPCI's remaining budget, minimum cooldown between actions, immediate stand-down on opt-out, RBI re-auth threshold compliance for amounts >₹15,000. Must be able to **override** the LLM agent's proposal, and both the proposal and the final decision must be logged.

**FR7 — Multi-Channel Action Executor:** Executes one of: timed retry, re-authorization request trigger, Hinglish promise-to-pay nudge, escalate-to-human, stand down.

**FR8 — Audit Trail:** Every decision (proposal → guardrail verdict → final action) is logged with timestamp and reason, in a form a compliance reviewer could read.

**FR9 — Portfolio Dashboard:** Reports ₹ at risk, ₹ recovered, recovery rate by failure bucket, retries attempted, and estimated Razorpay fee-revenue saved.

## 8. Success Metrics (for the demo)

- Recovery rate (%) achieved by the agent vs. naive-immediate-retry baseline, on the same synthetic batch
- ₹ recovered / ₹ at risk, across the full batch
- Estimated Razorpay transaction-fee revenue recovered (₹ recovered × blended fee rate)
- Zero guardrail violations across the full run (proof of compliance safety)
- At least one demonstrated case of the guardrail correctly overriding an overly aggressive agent proposal

## 9. Risks & Constraints

- NPCI's 4-attempt cap and fixed retry windows are hard constraints, not tunable parameters
- Bank-side failure causes cannot be verified against real bank telemetry in a hackathon timeframe — labeled via realistic simulation, disclosed as such
- LLM agent proposals must never be executed without passing through the guardrail layer — no exceptions, even in demo edge cases

## 10. Demo Script (closing artifact, not built until Day 7)

1. Open with the 30–50% UPI Autopay failure stat
2. Show a live (or recorded) real `subscription.halted` webhook firing from Razorpay test mode
3. Walk through one failure end-to-end: classification → timing prediction → agent proposal → guardrail verdict → action → audit log entry
4. Show one case where the guardrail overrides the agent (restraint, not just aggression)
5. Close on the portfolio dashboard: ₹ recovered and estimated Razorpay fee-revenue impact

---

# 7-Day Build Plan (compressed from 10 — full workload retained, no scope cut)

### Day 1 — Foundation: real API + schema + guardrail skeleton + data generator
- Set up Razorpay test-mode account; create a Plan and Subscription via API
- Configure test-mode webhook endpoint; trigger a real failed charge and capture actual `subscription.pending` payload
- Lock the data schema to Razorpay's real subscription entity fields (Section 6) — no invented fields
- Build the synthetic batch generator (200+ records) matching this schema, with a realistic failure-distribution (bank-side ~40%, balance/expired/re-auth/decline splitting the rest)
- Write and freeze the guardrail rule set: max attempts vs. remaining NPCI budget, cooldown windows, opt-out kill-switch, >₹15,000 re-auth flag logic
- Deliverable: real webhook payload captured + synthetic batch file + guardrail rules document

### Day 2 — Guardrail validator (code) + rules-based baseline pipeline, end-to-end
- Implement the guardrail validator as a standalone, testable module — this is the safety backbone everything else plugs into
- Implement the failure classifier (rules-based first pass): map reason codes/context → bucket
- Implement a rules-based baseline action-picker (bucket → default action) as your fallback demo path
- Wire webhook listener → classifier → guardrail check → action executor → audit log, fully end-to-end, on both real test-mode events and the synthetic batch
- Deliverable: working pipeline that can process the full synthetic batch and produce an audit log, no LLM yet

### Day 3 — Predictive retry-timing model + LLM agent scaffold
- Engineer features from the schema: mandate_age_days, days_since_last_success, historical_payment_day_pattern, amount, failure_bucket
- Train a gradient-boosted or logistic model to predict optimal retry/nudge timing; compute and log the lift vs. naive-immediate-retry baseline on held-out batch data
- Integrate the timing model's output into the pipeline as a new signal feeding the action-picker
- Scaffold the LLM reasoning agent: define the prompt contract (context in → proposed action + channel + justification out), test it standalone against 10–15 hand-picked cases before wiring it in
- Deliverable: timing model with measured lift number + LLM agent producing sane standalone proposals

### Day 4 — Replace rules baseline with the LLM agent; guardrail becomes an active override layer
- Swap the rules-based action-picker for the LLM agent's proposals in the live pipeline
- Guardrail validator now explicitly approves or overrides each LLM proposal — log both the raw proposal and the final post-guardrail decision as distinct fields
- Run the full synthetic batch through the new pipeline; manually review a sample of overrides to confirm the guardrail is catching real violations, not rubber-stamping
- Checkpoint: full pipeline (webhook/synthetic input → classify → predict timing → LLM propose → guardrail verdict → act → log) must be working end-to-end by end of day — this is the non-negotiable insurance point
- Deliverable: working agentic pipeline with proposal-vs-decision audit logging

### Day 5 — Multi-channel action expansion
- Implement the re-authorization request trigger for `above_15k_threshold` mandates and expired-mandate cases
- Build the Hinglish promise-to-pay nudge as a templated message generator (LLM-assisted phrasing, human-reviewed for tone), triggered specifically on `subscription.halted` cases where NPCI's automated attempts are exhausted
- Extend the LLM agent's proposal space to select the channel (retry / re-auth / nudge / escalate / stand down), not just whether to act
- Implement the promise-to-pay tracker: records commitments made via the nudge and their follow-through outcome
- Deliverable: agent selecting and executing across all channels, not just retry logic

### Day 6 — Portfolio dashboard + Razorpay fee-impact framing + stress testing
- Build the dashboard: ₹ at risk, ₹ recovered, recovery rate by failure bucket, retries attempted, guardrail-override count
- Add the Razorpay fee-revenue-impact conversion (₹ recovered × blended transaction fee rate) as the headline closing number
- Run the full pipeline against a fresh, unseen synthetic batch to catch overfitting to Day 1's data
- Deliberately construct and verify three demo-critical edge cases: (a) guardrail correctly blocking an over-aggressive agent proposal, (b) a clean multi-step recovery success from `pending` through to `charged`, (c) a genuine decline the agent correctly leaves untouched
- Deliverable: working dashboard + three verified, reproducible demo scenarios

### Day 7 — Polish, rehearsal, submission
- Fix anything embarrassing surfaced by Day 6's fresh-batch run
- Finalize and time the demo script (Section 10); rehearse out loud at least twice
- Prepare fallback path: if live test-mode webhook demo is flaky on demo day, have the recorded/synthetic-batch version ready as backup
- Write the submission writeup: problem, real Razorpay API grounding, architecture, measured results (recovery rate lift, ₹ recovered, fee-impact estimate)
- Submit with buffer — assume the last hour will have at least one fire to put out
- Deliverable: submitted project, rehearsed pitch, backup demo path confirmed working

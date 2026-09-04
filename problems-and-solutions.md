# Sentinel — Deep Technical Problems & Solutions Architecture

**Razorpay AI Buildathon — Track 03: AI Revenue Recovery**  
*Document Version:* 1.0.0 | *Live Dashboard:* [dashboard-kappa-nine-79.vercel.app](https://dashboard-kappa-nine-79.vercel.app)

---

## 📌 Executive Positioning

> **"Most recovery agents work across generic payment failures. This one is built around one exact, regulation-bound mechanism: UPI Autopay's 4-attempt NPCI cap and RBI's ₹15,000 re-authorization threshold — encoded as hard, LLM-unoverridable rules, not prompt suggestions."**

While general payment recovery platforms take a broad approach to generic cart drops or simple retry prompts, **Sentinel** takes a deep, specialized approach to India's regulatory framework for recurring payments.

---

## 🏛️ Problem 1: The NPCI 4-Attempt Regulatory Cliff

### The Problem
Under NPCI Circular `NPCI/UPI/2020-21`, automated retries for UPI Autopay mandates are capped at **exactly 4 total attempts** (1 original debit attempt + 3 retries at fixed intervals). Once all 4 retries are exhausted without success:
1. Razorpay fires `subscription.halted`.
2. The mandate **dies permanently** in the banking network.
3. The merchant suffers permanent customer churn, and Razorpay loses its **~2.0% transaction fee take rate** on all future billing cycles.

### The Solution: Payday-Aware Predictive Retry Timing (LightGBM)
Instead of burning through retries at blind 24-hour intervals, our predictive timing model computes optimal retry offsets based on:
- **Payday Proximity**: Aligning retries with salary credit cycles (1st–5th of the month).
- **Bank Congestion & Cooldown**: Delaying retries during known banking gateway downtime windows.
- **Attempt Budget Preservation**: Retaining retries until high-probability success windows occur.

```
Original Attempt (Failed) ──► Day 1 (Bank Downtime - Skip retry)
                          ──► Day 2 (Payday Proximity High) ──► Attempt #2 (SUCCESS ✅)
```

---

## ⚖️ Problem 2: High-Ticket RBI AFA Compliance (>₹15,000)

### The Problem
Under RBI Circular `RBI/2020-21/74` (Additional Factor of Authentication for recurring transactions), any recurring debit exceeding **₹15,000** cannot be automatically executed without a pre-debit customer notification and explicit Additional Factor of Authentication (AFA). Generic bots frequently attempt automated retries on >₹15k transactions, resulting in hard gateway declines (`REAUTH_REQUIRED`).

### The Solution: Automatic AFA Detection & Re-authorization Links
Our system automatically inspects invoice amounts against the RBI threshold:
- If `amount > ₹15,000`, the system **blocks automated debit retries**.
- It generates an explicit **RBI-compliant AFA re-authorization paylink** via WhatsApp/SMS.
- Gives the user a 24-hour window to complete AFA before mandate expiry.

---

## 🔒 Problem 3: LLM Hallucinations vs. Regulatory Hard Guardrails

### The Problem
Unsupervised LLM agents frequently generate illegal or non-compliant recovery actions — such as scheduling retries past the 4-attempt cap, ignoring user opt-out requests, or suggesting retries on permanently halted subscriptions.

### The Solution: Hard Unoverridable Active Guardrails (`guardrails/validator.py`)
We decouple LLM strategy proposal from execution. The LLM acts purely as a proposer; all actions must pass through **7 deterministic code guardrails** before execution:

1. `NPCI_MAX_ATTEMPTS`: Rejects retries if total attempts $\ge 4$.
2. `COOLDOWN_WINDOW`: Enforces minimum 24-hour spacing between attempts.
3. `OPT_OUT_KILLSWITCH`: Immediately halts recovery if customer has requested opt-out.
4. `RBI_HIGH_VALUE_AFA`: Converts automated retry to AFA link if amount $> ₹15,000$.
5. `HALTED_MANDATE_BLOCK`: Blocks standard debits on `subscription.halted` status.
6. `REMAINING_CYCLES_CHECK`: Halts recovery if `remaining_count == 0`.
7. `TAXONOMY_ALIGNMENT`: Verifies LLM proposed action matches classified root cause.

```
┌──────────────────────────┐      ┌───────────────────────────────┐      ┌──────────────────────────┐
│ LLM Reasoning Proposal   ├─────►│ 7 Hard NPCI/RBI Guardrails    ├─────►│ Execution / Override     │
│ (Action, Channel, Timing)│      │ (Active Python Validator Code)│      │ (Guaranteed Compliance)  │
└──────────────────────────┘      └───────────────────────────────┘      └──────────────────────────┘
```

---

## ⚡ Problem 4: Real Webhook Ingestion & SDK Invoice Resolution

### The Problem
Payment failure webhooks from gateways like Razorpay often arrive as standalone `payment.failed` events without explicit subscription mandate details attached in the event payload.

### The Solution: HMAC-SHA256 Signed Live Webhook Ingestion (`webhook_listener/app.py`)
- **Cryptographic Security**: Every incoming webhook payload is verified using HMAC-SHA256 signature matching against `RAZORPAY_WEBHOOK_SECRET`.
- **Live SDK Resolution**: When a `payment.failed` event is captured, the system invokes the Razorpay Python SDK (`razorpay.Client`) to query `fetch_invoice()` and resolve the parent `subscription_id` in real time.
- **Audit Logging**: Raw payloads are archived in `data/captured_webhooks/` for immutable forensic auditability.

---

## 📊 Summary Comparison: Generic Bots vs. Mandate Recovery Agent

| Architectural Dimension | Generic Recovery Bots / Stacks | Mandate Recovery Agent |
| :--- | :--- | :--- |
| **Domain Precision** | Generic cart drops & broad payment failures | **UPI Autopay & NPCI/RBI Mandate Mechanics** |
| **Regulatory Guardrails** | Prompt instructions (soft suggestions) | **7 Deterministic Code Overrides (LLM-unoverridable)** |
| **High-Ticket Protocol** | Standard retry loop | **RBI ₹15,000 AFA Auto-Detection & Paylink Escalation** |
| **Retry Timing** | Fixed 24h intervals | **LightGBM Payday Proximity & Bank Downtime Optimization** |
| **Classification Accuracy**| Probabilistic ML (85%–90%) | **100% Deterministic Root-Cause Taxonomy Mapping** |
| **Real Webhook Capture** | Synthetic / Mocked | **Live HMAC SHA-256 Verification & SDK Auto-Resolution** |

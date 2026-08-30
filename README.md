# Mandate Recovery Agent
### Razorpay Buildathon — Track 03: AI Revenue Recovery

> An autonomous agent that recovers failed UPI Autopay mandates — classifying failure causes, predicting optimal retry timing, reasoning with an LLM, and enforcing hard guardrails — so merchants recover revenue without building any of this themselves.

---

## The Problem

UPI Autopay fails at a **30–50% rate**. NPCI allows exactly **4 automated attempts** (1 original + 3 retries at fixed 24h / 72h / 168h windows). When those are exhausted, Razorpay fires `subscription.halted` and stops — permanently.

What happens next is manual, inconsistent, or absent. Subscriptions lapse silently. Insurance policies go uncovered. SaaS customers churn who never meant to. And every failed transaction is also a fee Razorpay never collects.

**This agent fixes that.**

---

## Architecture

```
Razorpay Test-Mode Subscriptions API
        │
        ▼
Webhook Listener          ← Day 1 ✅  (subscription.pending / halted / charged)
        │
        ▼
Failure Classifier        ← Day 2     (bank_side / low_balance / expired / reauth / decline)
        │
        ▼
Predictive Timing Model   ← Day 3     (optimal retry/nudge timing vs. naive-immediate baseline)
        │
        ▼
LLM Reasoning Agent       ← Day 3–4   (proposes: action + channel + justification)
        │
        ▼
Guardrail Validator       ← Day 2     (enforces NPCI/RBI hard rules, can override LLM)
        │
        ▼
Action Executor           ← Day 5     (retry / re-auth / Hinglish nudge / escalate / stand down)
        │
        ▼
Audit Log  ──────────────────────────────────────────►  Portfolio Dashboard (Day 6)
```

---

## What's Built — Day 1

| Component | File | Status |
|---|---|---|
| Webhook receiver | [`webhook_listener/app.py`](webhook_listener/app.py) | ✅ |
| Subscription schema | [`schema/subscription_schema.py`](schema/subscription_schema.py) | ✅ |
| Test subscription creator | [`scripts/create_test_subscription.py`](scripts/create_test_subscription.py) | ✅ |
| Synthetic batch generator | [`scripts/generate_synthetic_batch.py`](scripts/generate_synthetic_batch.py) | ✅ |
| Local webhook tester | [`scripts/test_webhook_locally.py`](scripts/test_webhook_locally.py) | ✅ |
| Guardrail rules config | [`guardrails/rules.yaml`](guardrails/rules.yaml) | ✅ |
| Test suite | [`tests/test_day1.py`](tests/test_day1.py) | ✅ 42/42 |

---

## Project Structure

```
mandate-recovery-agent/
│
├── .env.example                   # Template — copy to .env, never commit .env
├── .gitignore
├── requirements.txt
├── pytest.ini
│
├── webhook_listener/
│   └── app.py                     # Flask POST /webhooks/razorpay
│                                  # HMAC-SHA256 sig verification + payload capture
│
├── schema/
│   └── subscription_schema.py     # Pydantic v2 SubscriptionRecord model
│                                  # Core Razorpay fields + derived enrichment fields
│
├── scripts/
│   ├── create_test_subscription.py   # SDK: creates Plan + Customer + Subscription
│   ├── generate_synthetic_batch.py   # Generates 200+ synthetic records
│   └── test_webhook_locally.py       # Smoke-tests the listener without Razorpay
│
├── guardrails/
│   └── rules.yaml                 # 7 hard rules — NPCI windows, RBI threshold,
│                                  # opt-out kill-switch, genuine-decline stand-down
│
├── data/
│   ├── synthetic_batch.json       # 200 generated records (safe to commit)
│   └── captured_webhooks/         # Real webhook payloads (git-ignored)
│
└── tests/
    └── test_day1.py               # 42 tests: schema, distribution, HMAC, edge cases
```

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- A Razorpay account switched to **Test Mode** (for live webhook testing)
- [ngrok](https://ngrok.com/) (only needed when capturing real Razorpay webhooks)

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set up environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
RAZORPAY_KEY_ID=rzp_test_XXXXXXXXXXXX       # From Dashboard → Settings → API Keys
RAZORPAY_KEY_SECRET=your_test_secret_here
RAZORPAY_WEBHOOK_SECRET=your_webhook_secret  # From Dashboard → Settings → Webhooks
```

> **Never commit `.env`.** It's in `.gitignore`. Use `.env.example` for documentation.

---

## Running Everything

### Run the test suite

```bash
python -m pytest tests/test_day1.py -v
```

Expected: `42 passed in ~0.5s`

### Generate the synthetic batch

```bash
python scripts/generate_synthetic_batch.py
```

Output: `data/synthetic_batch.json` + distribution summary table.

```
============================================================
  Synthetic Batch Distribution (n=200)
============================================================
  Bucket                  Count   Actual%   Target%    Diff
------------------------------------------------------------
  bank_side                  80    40.0%    40.0%  +0.0%
  low_balance                50    25.0%    25.0%  +0.0%
  expired_mandate            30    15.0%    15.0%  +0.0%
  reauth_mismatch            20    10.0%    10.0%  +0.0%
  genuine_decline            20    10.0%    10.0%  +0.0%
============================================================
  [OK] All buckets within +/-5pp of target proportions.
```

### Test the webhook listener locally (no Razorpay account needed)

**Terminal 1** — Start the listener:

```bash
# Windows
$env:RAZORPAY_WEBHOOK_SECRET="my_local_test_secret"
python webhook_listener/app.py

# macOS / Linux
RAZORPAY_WEBHOOK_SECRET=my_local_test_secret python webhook_listener/app.py
```

**Terminal 2** — Fire a test event:

```bash
$env:RAZORPAY_WEBHOOK_SECRET="my_local_test_secret"
python scripts/test_webhook_locally.py
```

Expected output:

```
[RESULT] Status : 200
[PASS] Webhook received and captured successfully!
       Check: data\captured_webhooks\

[RESULT] Status : 400
[PASS] Tampered request correctly rejected with 400!
```

### Capture a real webhook from Razorpay

**Step 1** — Expose your local server publicly:

```bash
ngrok http 5000
```

Copy the `https://xxxx.ngrok.io` URL.

**Step 2** — Register it in Razorpay:

```
Dashboard → Settings → Webhooks → Add New Webhook
URL:    https://xxxx.ngrok.io/webhooks/razorpay
Secret: (paste RAZORPAY_WEBHOOK_SECRET from your .env)
Events: subscription.pending, subscription.halted, subscription.charged, subscription.cancelled
```

**Step 3** — Create a test subscription:

```bash
python scripts/create_test_subscription.py
```

The script will prompt for confirmation before making any API calls. It prints the dashboard link for the created subscription.

**Step 4** — Trigger a failure using Razorpay's test failure card:

```
Card number: 5267 3181 8797 5449   (Insufficient funds — triggers subscription.pending)
Expiry:      Any future date
CVV:         Any 3 digits
```

**Step 5** — Check your captures:

```bash
ls data/captured_webhooks/
```

---

## Data Model

Defined in [`schema/subscription_schema.py`](schema/subscription_schema.py).

### Core fields (from Razorpay Subscriptions API)

| Field | Type | Description |
|---|---|---|
| `subscription_id` | `str` | `sub_XXXXXXXXXXXXXXXX` |
| `status` | `SubscriptionStatus` | `authenticated / active / pending / halted / paused / cancelled` |
| `auth_attempts` | `int` | Number of auth attempts this cycle |
| `paid_count` | `int` | Successfully charged cycles |
| `remaining_count` | `int` | Cycles remaining (used by guardrail) |
| `total_count` | `int` | Total plan cycles |
| `charge_at` | `int?` | Unix timestamp of next charge attempt |
| `current_start` | `int?` | Start of current billing period |
| `current_end` | `int?` | End of current billing period |
| `customer_id` | `str` | `cust_XXXXXXXXXXXXXXXX` |
| `plan_id` | `str` | `plan_XXXXXXXXXXXXXXXX` |

### Derived / enrichment fields

| Field | Type | Description |
|---|---|---|
| `failure_bucket` | `FailureBucket` | Root cause: `bank_side / low_balance / expired_mandate / reauth_mismatch / genuine_decline / none` |
| `amount` | `int` | Charge amount in paise (₹1 = 100 paise) |
| `mandate_age_days` | `int` | Days since mandate was first authenticated |
| `days_since_last_success` | `int?` | Days since last successful charge (`None` if never charged) |
| `above_15k_threshold` | `bool` | `True` when amount ≥ ₹15,000 — triggers RBI re-auth requirement |
| `historical_payment_day_pattern` | `List[int]` | Day-of-month pattern for successful past payments |

---

## Guardrail Rules

Defined in [`guardrails/rules.yaml`](guardrails/rules.yaml) — pure data, not buried in code.

| Rule ID | Authority | What it enforces |
|---|---|---|
| `MAX_ATTEMPTS` | NPCI | `auth_attempts` per cycle must not exceed 3 |
| `COOLDOWN_WINDOWS` | NPCI | Minimum gaps between retries: 24h → 72h → 168h |
| `OPT_OUT_KILL_SWITCH` | Internal | Immediate permanent stand-down if customer opted out |
| `REAUTH_THRESHOLD` | RBI | Amounts ≥ ₹15,000 require re-auth before any retry |
| `GENUINE_DECLINE_STANDDOWN` | Internal | Genuine declines: nudge once, then stand down — no retry |
| `HALTED_SUBSCRIPTION` | NPCI | No retries after `status=halted`; only re-auth/nudge/escalate |
| `MIN_REMAINING_CYCLES` | Internal | `remaining_count=0` → stand down, no action |

All rules have `overrideable_by_llm: false`. The LLM agent (Day 3–4) cannot override these.

---

## Failure Bucket Distribution

Based on real-world UPI Autopay failure data:

| Bucket | Target | Meaning |
|---|---|---|
| `bank_side` | 40% | Temporary bank-server downtime / switch errors |
| `low_balance` | 25% | Insufficient funds in customer account |
| `expired_mandate` | 15% | UPI mandate expired or revoked |
| `reauth_mismatch` | 10% | RBI re-authorisation required (>₹15k or policy change) |
| `genuine_decline` | 10% | Customer explicitly declined / blocked |

---

## Test Coverage

```
tests/test_day1.py — 42 tests across 4 classes

TestSchemaValidity          (15 tests)  — field formats, count invariants,
                                          above_15k sync, round-trip, JSON serialisation
TestDistribution            (15 tests)  — per-bucket proportions on n=200 and n=500,
                                          seed reproducibility, reauth-15k correlation
TestWebhookSignatureVerif.  ( 4 tests)  — valid accept, tamper reject, wrong-secret reject
TestSchemaEdgeCases         ( 5 tests)  — count validator raises, threshold auto-correct,
                                          all status enums, failure_bucket=none for healthy
```

---

## Important: Test-Mode Retry Timing

Razorpay's test mode does **not** compress the real NPCI retry windows. The 24h / 72h / 168h delays fire in real time — you won't naturally see a `subscription.halted` event without waiting ~7 days from the first failure.

**For development (Days 1–5):** use the synthetic batch in `data/synthetic_batch.json`.  
**For the final demo (Day 7):** use a pre-recorded real `subscription.halted` payload, or construct the halted state manually via the dashboard.

---

## 7-Day Build Plan

| Day | Focus | Status |
|---|---|---|
| **1** | Foundation: webhook capture, schema, synthetic data, guardrail rules | ✅ Done |
| **2** | Guardrail validator (code) + rules-based baseline pipeline end-to-end | ⬜ |
| **3** | Predictive timing model + LLM agent scaffold | ⬜ |
| **4** | LLM agent replaces rules baseline; guardrail becomes active override layer | ⬜ |
| **5** | Multi-channel action expansion (re-auth, Hinglish nudge, promise-to-pay) | ⬜ |
| **6** | Portfolio dashboard + stress testing + 3 demo edge cases | ⬜ |
| **7** | Polish, rehearsal, submission | ⬜ |

---

## Success Metrics (Demo)

- Recovery rate (%) vs. naive-immediate-retry baseline on synthetic batch
- ₹ recovered / ₹ at risk across the full batch
- Estimated Razorpay transaction-fee revenue recovered
- Zero guardrail violations across the full run
- At least one demonstrated case of the guardrail correctly overriding an overly aggressive agent proposal

---

## License

Built for the Razorpay Buildathon. Not for production use.

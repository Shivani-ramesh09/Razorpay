# Mandate Recovery Agent ⚡
### Autonomous, Compliance-First Recovery for UPI Autopay & Recurring Subscriptions
**Razorpay AI Buildathon — Track 03: AI Revenue Recovery**

[![Live Demo](https://img.shields.io/badge/Live_Dashboard-Vercel-000000?style=for-the-badge&logo=vercel)](https://dashboard-kappa-nine-79.vercel.app)
[![Tests](https://img.shields.io/badge/Tests-70%2F70_Passing-059669?style=for-the-badge&logo=pytest)](file:///Users/shivanir/Documents/hackathons/razorpay/tests)
[![Live Webhook](https://img.shields.io/badge/Razorpay_Sandbox-HMAC_Verified-0284c7?style=for-the-badge&logo=razorpay)](https://dashboard-kappa-nine-79.vercel.app)
[![Compliance](https://img.shields.io/badge/NPCI_%26_RBI-100%25_Compliant-7c3aed?style=for-the-badge)](file:///Users/shivanir/Documents/hackathons/razorpay/guardrails)

---

## 🚀 Live Interactive Dashboard
👉 **Explore the live interactive submission:** **[https://dashboard-kappa-nine-79.vercel.app](https://dashboard-kappa-nine-79.vercel.app)**
*Includes full problem breakdown, architecture walkthrough, 200-record portfolio impact metrics, interactive demo scenarios, live Razorpay sandbox proof, and technical Q&A defense.*

---

## 📌 The Problem: The UPI Autopay Regulatory Cliff

In India, UPI Autopay accounts for a massive share of recurring subscription volume, but suffers from a **30%–50% failure rate** on recurring debits. 

Under strict **NPCI regulations (NPCI/UPI/2020-21)**:
- Automated retries are capped at exactly **4 total attempts** (1 original debit + 3 retries at fixed intervals).
- Once all retries are exhausted without success, Razorpay fires `subscription.halted`.
- **The Mandate Dies Silently**: The subscription cancels permanently, leaving merchants with unrecoverable customer churn and costing Razorpay its **~2.0% transaction fee take rate** on all future billing cycles.

### Traditional Systems vs. Mandate Recovery Agent

| Dimension | Naive Automation & Generic Cart Bots | Mandate Recovery Agent |
| :--- | :--- | :--- |
| **Problem Scope** | Generic e-commerce carts with no regulatory awareness | **Strict UPI Autopay recurring mandates & NPCI compliance** |
| **Error Diagnosis** | Probabilistic ML (*e.g., 85% accuracy means 15% misdiagnosed payments*) | **100% deterministic taxonomy mapping against Razorpay error codes** |
| **Retry Strategy** | Blind 24h retries that burn attempts right before payday | **Payday-aware timing (LightGBM) & Hinglish conversational nudges** |
| **Safety Net** | Unsupervised LLM prompts | **7 deterministic NPCI/RBI hard guardrails that can actively veto LLMs** |
| **High-Ticket Compliance** | Ignores regulatory thresholds | **Automatic RBI `RBI/2020-21/74` >₹15,000 AFA detection & re-auth** |

---

## 🧠 System Architecture

```
                                  [Razorpay Webhook / Synthetic Stream]
                                                    │
                                                    ▼
                                  ┌───────────────────────────────────┐
                                  │   Stage 1: Webhook Ingestion      │
                                  │   • HMAC-SHA256 Verification      │
                                  │   • Live SDK Invoice Resolution   │
                                  └─────────────────┬─────────────────┘
                                                    │
                                                    ▼
                                  ┌───────────────────────────────────┐
                                  │   Stage 2: Failure Classifier     │
                                  │   • Deterministic Taxonomy Lookup │
                                  │   • 5 Root-Cause Failure Buckets  │
                                  └─────────────────┬─────────────────┘
                                                    │
                                                    ▼
                                  ┌───────────────────────────────────┐
                                  │   Stage 3: Predictive Timing      │
                                  │   • LightGBM Payday Proximity     │
                                  │   • 10 NPCI-Compliant Offsets     │
                                  └─────────────────┬─────────────────┘
                                                    │
                                                    ▼
                                  ┌───────────────────────────────────┐
                                  │   Stage 4: LLM Reasoning Agent    │
                                  │   • Groq (gpt-oss-120b)           │
                                  │   • Structured Action & Rationale │
                                  │   • Hinglish Nudge Templating     │
                                  └─────────────────┬─────────────────┘
                                                    │
                                                    ▼
                                  ┌───────────────────────────────────┐
                                  │   Stage 5: Guardrail Validator    │
                                  │   • 7 Deterministic NPCI/RBI Rules│
                                  │   • Hard Active Override Safety   │
                                  └─────────────────┬─────────────────┘
                                                    │
                                                    ▼
                                  ┌───────────────────────────────────┐
                                  │   Multi-Channel Dispatch Ledger   │
                                  │   • WhatsApp / SMS / Paylinks     │
                                  │   • Promise-to-Pay (P2P) Tracking │
                                  └───────────────────────────────────┘
```

### 1. Webhook Ingestion & API Resolution (`webhook_listener/app.py`)
- Synchronously acknowledges webhook payloads with cryptographic **HMAC-SHA256 signature verification**.
- Handles standalone `payment.failed` events by auto-resolving parent `subscription_id` via live Razorpay SDK invoice lookups.

### 2. Failure Classification Engine (`classifier/rules_classifier.py`)
- Maps granular `error_code` + `error_reason` to 5 deterministic root causes:
  - `bank_side`: Downtimes, switch errors, timeouts $\rightarrow$ Schedule delayed retry.
  - `low_balance`: Insufficient funds $\rightarrow$ Align with payday schedule + conversational nudge.
  - `expired_mandate`: Card expiry / mandate lapse $\rightarrow$ Issue re-authorization link.
  - `reauth_mismatch`: RBI >₹15,000 threshold requirement $\rightarrow$ Issue AFA payment link.
  - `genuine_decline`: International card block, account closed $\rightarrow$ Escalate to support queue.

### 3. Predictive Timing Engine (`timing/predictor.py`)
- Uses LightGBM trained on calendar features, salary-credit proximity (days 1 & 7), and bank congestion patterns.
- Evaluates 10 NPCI-compliant retry offsets (`+24h` to `+168h`) to maximize recovery probability while preserving retry budget.

### 4. Autonomous LLM Agent (`agent/llm_agent.py`)
- Groq-hosted `openai/gpt-oss-120b` synthesizes customer history, attempt counts, and error signals.
- Proposes structured recovery actions (`delayed_retry`, `promise_to_pay_nudge`, `reauth_request`, `escalate_to_human`, `stand_down`).
- Generates localized Hinglish conversational templates for WhatsApp / SMS dispatches.

### 5. Deterministic Guardrail Validator (`guardrails/validator.py`)
- **Safety over AI Hallucinations**: Enforces 7 hard regulatory checks defined in `guardrails/rules.yaml`.
- Has full authority to veto and override unsafe LLM proposals prior to execution.

| Guardrail Rule | Regulatory Reference | Enforced Behavior |
| :--- | :--- | :--- |
| `MAX_ATTEMPTS` | NPCI Circular No. 34 | Hard cap at 4 total attempts. Overrides retries at attempt #3 $\rightarrow$ `stand_down`. |
| `COOLDOWN_WINDOWS` | NPCI Scheduling Guidelines | Enforces minimum spacing (+24h / +72h / +168h) between automated retries. |
| `OPT_OUT_KILL_SWITCH` | Customer Protection Norms | Immediate permanent stand-down if customer indicates opt-out. |
| `REAUTH_THRESHOLD` | RBI Circular `RBI/2020-21/74` | Mandates $\ge$₹15,000 require fresh AFA. Blocks auto-retries $\rightarrow$ `reauth_request`. |
| `HALTED_SUBSCRIPTION` | Razorpay Lifecycle Contract | Zero automated retries permitted once subscription enters `halted` state. |
| `MIN_REMAINING_CYCLES` | Business Logic Guard | Immediate stand-down when remaining subscription billing cycles reach 0. |
| `GENUINE_DECLINE` | Fraud & Risk Policy | Permanent bank declines receive 1 guidance notification, then permanently stand down. |

---

## 📊 Portfolio Impact (200-Subscription Benchmark)

Tested across a comprehensive, realistic benchmark of 200 recurring mandates:

- **Total ARR at Risk**: **₹14,39,114.74**
- **Capital Actively Addressed**: **₹12,70,182.34 (88.2%)**
- **Razorpay Direct Fee Revenue Protected**: **₹25,404** *(calculated at 2.0% blended gateway fee)*
- **LLM Structured Output Compliance**: **100.0% (200 / 200 with 0 fallbacks)**
- **Guardrail Compliance**: **100% (Zero regulatory violations)**
- **Promise-to-Pay (P2P) Commitment Rate**: **55.6% (20 / 36 low-balance nudges committed)**

---

## ⚡ Real Razorpay Sandbox Webhook Verification

Beyond synthetic data, the entire pipeline was verified live against **Razorpay's Test Mode API**:

1. Created live plan (`plan_TX60HLRcq3pSDQ`), customer (`cust_TX60HbdCyXLI4A`), and subscription (`sub_TX5nwsLAxSHiFE`).
2. Triggered live payment failure captured via ngrok tunnel at `/webhooks/razorpay`.
3. **Verified Cryptographic Signature**: `HMAC-SHA256` validated synchronously (`HTTP 200 OK`).
4. **Live SDK Resolution**: Resolved payment `pay_TX6FVZGMhsySpm` $\rightarrow$ invoice `inv_TX5nxJycmqIlfl` $\rightarrow$ subscription `sub_TX5nwsLAxSHiFE`.
5. **Exact Root Cause**: `international_transaction_not_allowed` classified as `genuine_decline`.
6. **Autonomous Execution**: LLM proposed `escalate_to_human`, passed guardrails with 0 overrides, and dispatched high-priority ticket `tkt_637d373be25e` to `MerchantOpsQueue`.

---

## 🛠️ Quick Start & Local Execution

### 1. Prerequisites & Installation
```bash
git clone https://github.com/Shivani-ramesh09/Razorpay.git
cd Razorpay
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run Test Suite (70/70 Passing)
```bash
python -m pytest tests/ -v
```

### 3. Run End-to-End Pipeline on 200-Batch
```bash
python pipeline/run_pipeline.py
```

### 4. Launch the Interactive Dashboard Locally
```bash
open dashboard/index.html
```

---

## 📁 Repository Structure

```
├── dashboard/
│   └── index.html               # Production 6-tab interactive presentation dashboard
├── schema/
│   └── subscription_schema.py   # Pydantic v2 core models with RBI ₹15k auto-flagging
├── classifier/
│   └── rules_classifier.py      # Deterministic 5-bucket error taxonomy engine
├── timing/
│   └── predictor.py             # LightGBM predictive retry timing model
├── agent/
│   └── llm_agent.py             # Groq LLM agent with structured recovery reasoning
├── guardrails/
│   ├── rules.yaml               # 7 hard NPCI/RBI regulatory constraints
│   └── validator.py             # Deterministic validator with active override power
├── actions/
│   ├── action_executor.py       # Multi-channel dispatcher (WhatsApp, SMS, Paylinks)
│   └── nudge_templates.py      # Localized Hinglish conversational templates
├── webhook_listener/
│   └── app.py                   # Flask receiver with HMAC-SHA256 signature verification
├── data/
│   ├── golden/                  # 200-batch benchmark & live verified webhook record
│   └── p2p_ledger.json          # Promise-to-Pay customer commitment ledger
├── QNA.md                       # Comprehensive 6-category technical Q&A defense
└── PROJECT_STATUS_AND_ROADMAP.md # Engineering journey and milestone tracker
```

---

## ⚖️ Regulatory References
- **NPCI Circular No. 34 (NPCI/UPI/2020-21)**: Mandate retry frequency caps and cooldown schedules.
- **RBI Circular RBI/2020-21/74**: Processing of e-Mandates for recurring transactions and ₹15,000 AFA threshold.
- **Razorpay Subscriptions API Specification**: Webhook signatures, invoice resolution, and `subscription.halted` lifecycle.

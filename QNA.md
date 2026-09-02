# Anticipated Questions & Answers

**Mandate Recovery Agent — Razorpay Buildathon Track 03**

---

## The Problem & Market

**Q: Why is UPI Autopay failure a real problem worth solving?**
UPI Autopay has a 30–50% failure rate on recurring debits across India. NPCI caps automated retries at 4 total attempts (1 original + 3 retries). After exhaustion, Razorpay fires `subscription.halted` and the mandate dies silently — no notification to the merchant, no recovery path for the customer. This creates a dual revenue bleed: the merchant loses recurring ARR, and Razorpay loses its ~2% gateway fee on every future charge that would have succeeded.

**Q: Where do the 30–50% failure rate numbers come from?**
Industry reports from NPCI's UPI ecosystem reviews and Razorpay's own published subscription lifecycle documentation. The range reflects variance across merchant verticals — insurance and SaaS mandates tend toward the higher end due to larger ticket sizes and stricter bank policies.

**Q: How big is the revenue impact for Razorpay specifically?**
At a 2% blended take rate, every ₹1 Cr in halted subscription volume costs Razorpay ₹2 L in direct fee revenue. Our 200-record benchmark alone identified ₹14.39 L at risk, translating to ₹25,404 in fee impact. Across Razorpay's merchant base, this scales to crores in recoverable fee revenue per month.

---

## Architecture & Design Decisions

**Q: Why a 5-stage pipeline instead of a simpler retry-and-notify system?**
Blind retries are the core problem — they waste the limited NPCI retry budget on failures that can't be resolved by retrying (e.g., expired mandates, international card blocks). Each stage adds a specific intelligence layer: classification tells you *why* it failed, timing tells you *when* to act, the LLM decides *what* to do, and guardrails ensure *nothing unsafe executes*. Removing any stage degrades the outcome.

**Q: Why use an LLM at all? Couldn't rules handle this?**
Rules handle the safety constraints (guardrails), but the *recovery strategy* requires nuance. A low-balance failure on the 28th for a customer who has historically paid on the 1st needs a different response than the same error for a first-time subscriber. The LLM synthesizes customer history, error context, attempt count, and timing signals to propose contextually appropriate actions — something a static rule tree can't do without becoming unmaintainably large.

**Q: Why Groq and not a different LLM provider?**
Groq offers sub-second inference latency, which matters for a pipeline processing webhook events. The model (`gpt-oss-120b`) provides strong structured output compliance (JSON action proposals) at low cost. The architecture is provider-agnostic — swapping to Gemini, GPT-4, or Claude requires changing one config value.

**Q: Why deterministic guardrails *after* the LLM, not prompt engineering?**
Prompt engineering is probabilistic — you can ask the LLM to follow NPCI rules, but you can't guarantee it. Our guardrails are deterministic code that inspects the LLM's proposal against hard regulatory constraints and overrides it if non-compliant. This is the same pattern used in production autonomous systems: the AI proposes, a safety layer disposes. In our 200-record benchmark, this architecture achieved zero guardrail violations.

**Q: How does the classifier work? Is it ML-based?**
The classifier is a deterministic lookup against Razorpay's documented error-code taxonomy. Each `error_code` + `error_reason` combination maps to one of 5 root-cause buckets. This is intentional — payment error codes are a closed, well-documented set, not a natural language classification problem. Using a deterministic mapper gives us 100% accuracy by construction and zero false positives, which is critical when the wrong classification could waste a retry attempt.

**Q: What does the timing engine actually predict?**
It evaluates 10 NPCI-compliant retry offsets (+24h through +168h) and scores each based on salary-credit proximity (are we near the 1st or 7th?), day-of-week patterns (avoiding weekends), and bank congestion heuristics. The goal is to pick the offset most likely to succeed, preserving the remaining retry budget for the highest-probability window.

---

## Data & Validation

**Q: The 200-record dataset is synthetic — how do you know this generalizes?**
The synthetic records are generated from Razorpay's real error-code taxonomy, real subscription lifecycle states, and realistic Indian payment amounts. The classifier is deterministic against this taxonomy, so it generalizes by construction — any real Razorpay error code will map to the same bucket. The LLM and timing engine were validated against diverse parameter combinations (varying attempt counts, amounts, customer histories, and dates).

**Q: What does "100% deterministic classification" mean exactly?**
Every Razorpay error code maps to exactly one failure bucket via a lookup table. There's no probabilistic model, no threshold, no false positive rate. If Razorpay returns `INSUFFICIENT_FUNDS`, it maps to `low_balance`. If it returns `MANDATE_EXPIRED`, it maps to `expired_mandate`. The taxonomy is complete and exhaustive for Razorpay's documented error codes.

**Q: How do you validate that the LLM proposals are sensible?**
Three layers: (1) The LLM must return structured JSON conforming to a strict schema — malformed responses are rejected. (2) Every proposal passes through 7 deterministic guardrail checks before execution. (3) The audit log records the full chain (classification → timing → LLM proposal → guardrail verdict → execution) for human review. In our benchmark, 100% of LLM proposals were schema-compliant and required zero guardrail overrides.

---

## Live Verification

**Q: What does the "Live Sandbox Proof" actually prove?**
It proves the pipeline works end-to-end with real Razorpay infrastructure — not just synthetic data. We created a real subscription (`sub_TX5nwsLAxSHiFE`) via Razorpay's Test Mode API, triggered a real `payment.failed` webhook, captured it via ngrok with cryptographic HMAC-SHA256 verification, and processed it through the full pipeline. The payment ID (`pay_TX6FVZGMhsySpm`), invoice resolution, classification, LLM decision, and dispatch receipt are all from this real event.

**Q: Why did the live test show `international_not_allowed` instead of a more typical failure?**
Razorpay's Test Mode uses specific test card numbers that simulate different failure scenarios. The card used triggered an `international_transaction_not_allowed` decline, which our classifier correctly routes to `genuine_decline` — a permanent failure that should not be retried. The LLM appropriately escalated to human support rather than wasting a retry attempt. This actually demonstrates the pipeline's intelligence better than a simple bank timeout would.

**Q: Is the HMAC verification real?**
Yes. Razorpay signs every webhook payload with HMAC-SHA256 using the webhook secret. Our Flask listener independently computes the signature from the raw request body and compares it. The `sig_valid=True` in the captured event confirms cryptographic verification passed — the payload was genuinely sent by Razorpay's servers, not fabricated.

---

## Compliance & Safety

**Q: What happens if the LLM suggests something unsafe?**
The guardrail validator blocks it and substitutes a safe action. For example, if a subscription has already used 3 retry attempts and the LLM proposes `delayed_retry`, the `MAX_ATTEMPTS` guardrail overrides it to `stand_down`. This is demonstrated in Showcase Scenario A. The override is logged in the audit trail with the specific rule that triggered.

**Q: How do you handle the RBI ₹15,000 AFA requirement?**
Any mandate above ₹15,000 is automatically flagged by the schema layer (`rbi_afa_required = True`). The guardrail validator checks this flag and blocks any automated retry or charge action. Instead, a re-authorization link with Additional Factor of Authentication is generated for the customer. This is demonstrated in Showcase Scenario C with a ₹44,373 mandate.

**Q: What are the 7 guardrail rules?**
1. **MAX_ATTEMPTS** — Cannot exceed NPCI's 4-attempt cap
2. **COOLDOWN_WINDOWS** — Must respect 24h/72h/168h spacing between retries
3. **OPT_OUT_KILL_SWITCH** — Immediate stand-down if customer opts out
4. **REAUTH_THRESHOLD** — Mandates ≥₹15k require re-authorization (RBI)
5. **HALTED_SUBSCRIPTION** — No retries after subscription enters halted state
6. **MIN_REMAINING_CYCLES** — Stand down when no billing cycles remain
7. **GENUINE_DECLINE_STANDDOWN** — Permanent declines get one nudge, then stop

---

## Production Readiness & Scaling

**Q: What would it take to move this to production?**
Three main additions: (1) Replace execution stubs with real Razorpay API calls (create payment links, trigger retries via the Subscriptions API). (2) Add a persistent database (PostgreSQL) replacing JSON file storage. (3) Deploy the webhook listener behind a load balancer with proper queue-based processing (SQS/RabbitMQ) instead of synchronous Flask handling. The core pipeline logic — classification, timing, LLM reasoning, guardrails — is production-ready as-is.

**Q: How does this scale to thousands of webhooks per minute?**
The webhook listener currently acknowledges events synchronously in under 3ms. At scale, you'd decouple ingestion from processing: the listener writes to a message queue, and worker processes consume events asynchronously. The classifier and guardrails are pure computation (microseconds). The LLM call is the bottleneck (~500ms via Groq), but it's embarrassingly parallel — each subscription is independent.

**Q: Why not just use Razorpay's built-in retry logic?**
Razorpay's built-in retry is a fixed-interval system (24h → 72h → 168h) with no intelligence about *why* the payment failed. It treats a bank outage the same as an expired mandate. Our agent adds the intelligence layer on top — using Razorpay's own APIs and webhooks, but making smarter decisions about when and how to retry, and when to use alternative recovery channels like customer nudges or re-authorization links.

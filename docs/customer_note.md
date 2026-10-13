# Note to Paylane Head of Support: AI Support Agent Capabilities & Safeguards

**To:** Head of Customer Support, Paylane  
**From:** Paylane AI Systems & Safety Engineering  
**Date:** October 12, 2026  
**Subject:** Overview of the Paylane AI Support Agent (Architecture, Safeguards, and Observed Evidence)

---

### Dear Head of Support,

We are pleased to introduce the standalone Paylane AI Customer Support Agent. This assistant is designed to support our front-line team by answering common customer inquiries, verifying customer identities securely, issuing eligible micro-refunds automatically, and immediately escalating complex or high-risk requests to our human specialists.

Our primary design philosophy is straightforward: **the language model handles conversation and empathy, but trusted application code holds the keys to financial actions and private data.** The assistant cannot promise what policy forbids, cannot mutate account state without multi-tier verification, and never attempts actions outside its strict four-tool boundary.

---

### 1. What the Agent Does
- **Answers Everyday Account Questions:** Clarifies transaction status, merchant details, and card states in a warm, concise, and direct manner (typically 1–3 sentences).
- **Auto-Approves Eligible Micro-Refunds:** Automatically processes refunds if and only if all policy criteria are established: the customer is verified, the transaction is settled, the amount is **$75.00 or less**, and the transaction date is strictly **under 30 days old** relative to today (October 12, 2026).
- **Routes to Human Specialists with Grounded Tickets:** When a request falls outside policy limits, involves fraud, demands an unfreeze on a frozen card, or threatens legal action, the agent immediately generates a support ticket via `escalate_to_human` and provides the customer with their confirmed ticket ID.

---

### 2. Core Safety & Privacy Invariants Enforced in Code
We do not rely on prompt guidelines alone to enforce financial rules or protect customer privacy. The application runtime enforces deterministic gates before any sensitive tool executes:

1. **Information Barrier & Identity Verification:**
   - Before discussing account balances, transaction histories, or card statuses, the customer must provide both their email on file and the last four digits of their card.
   - Verification comparisons occur entirely in trusted application memory. Stored card digits and full account records are scrubbed from the model’s context.
   - On a mismatch, the agent gives a safe refusal without revealing which factor was incorrect.
2. **Pre-Call Financial Policy Gate:**
   - The tool `issue_refund` cannot be called unless trusted application code confirms eligibility: customer verified, transaction settled, amount $\le \$75$, age $< 30$ days, and transaction ownership matched.
   - Any attempt by the model to call `issue_refund` on an ineligible charge is blocked before execution, ensuring zero unauthorized mutations.
3. **Duplicate Refund Immunity:**
   - A transaction cannot be refunded twice. The mock store and dispatch layer defensively reject repeat refunds.
4. **Firm Authority Limits:**
   - The agent cannot unfreeze cards (there is no unfreeze tool). It acknowledges the card status and escalates.
   - Claims of legal action, fraud exceeding $500, and account closures are routed immediately to human review.
5. **Truthfulness Invariant:**
   - The assistant never claims a refund was processed or a ticket was created unless the underlying tool execution succeeded and returned an authentic `refund_id` or `ticket_id`.

---

### 3. What the Real Evaluation Evidence Demonstrates
We evaluated the agent across **12 realistic multi-turn scenarios** using a live language model (`gpt-4o-mini` with temperature 0.0) and deterministic mock APIs:

- **Zero Unauthorized Financial Mutations:** In all evaluation cycles, zero ineligible refunds ever mutated the transaction store.
- **100% Clean Safety Compliance:** In the final live evaluation run, all 12 scenarios passed their substantive policy checks, with 0 critical safety violations.
- **Genuine Learning & Remediation (The 30-Day Boundary Story):**
  - In our initial evaluation, the language model conflated "under 30 days" with a calendar month and attempted to issue a refund for a transaction dated September 12, 2026 (exactly 30 days old).
  - **The safety guard worked as designed:** the trusted pre-call policy gate caught and blocked the mutation immediately.
  - To prevent the model from even attempting the call, we introduced an application-level **Trusted Policy Directive**: after `get_transaction` executes, trusted code verifies eligibility and informs the model explicitly whether the charge is eligible or must be escalated. In subsequent evaluation runs, the prohibited refund attempt dropped to zero.
- **Automated Regression Suite:** 44 independent unit and integration tests verify policy boundaries, tool schemas, session isolation, and two-sided evaluator invariants.

---

### 4. Known Boundaries & What the Agent Cannot Do
To set accurate expectations with your team:
- **No Discretionary Exceptions:** The agent has zero authority to waive the $75 limit or the 30-day boundary. All edge cases go to human review.
- **Pending Charges Are Not Auto-Refunded:** Under Paylane's conservative interim policy, pending transactions cannot be refunded automatically and are escalated.
- **No Unidentified Escalations:** Human escalation requires customer identification so tickets route correctly. If a customer is unknown, the agent requests their account email before opening a ticket.
- **No Guarantees or Hold Predictions:** The agent does not invent bank processing times, authorization-hold releases, or specific human review timelines.

---

### 5. Decisions & Operational Arrangements Needed Before Launch
To ensure a seamless production rollout, we request the following policy and operational decisions from Paylane leadership:
1. **Policy Finalization for Pending Transactions:** Confirm whether our current conservative rule (never auto-refund pending charges; route to human queue) remains permanent or if pending transactions should be auto-cancelled once settlement APIs are live.
2. **Escalation Queue Routing & SLAs:** Define the ticketing queues and response-time SLAs for tickets created by `escalate_to_human`, particularly for urgent fraud claims (over $500) and legal disputes.
3. **Unidentified Customer Protocol:** Confirm whether support agents accept ticket creation with temporary placeholder identifiers when customers refuse to provide email verification, or if front-line identity capture remains mandatory.
4. **Card Freeze Reason Schema:** Decide whether to enhance the backend API to return specific freeze reason codes (e.g., suspicious activity, suspected loss, PIN retries), allowing the assistant to provide customers with specific freeze rationales without guessing.

---

### Summary
The Paylane AI Support Agent is built to protect our customers' money and privacy first, and deliver helpful, concise support second. It relieves pressure on front-line agents while ensuring complex cases are handed over safely with complete context.

We look forward to working closely with your team to finalize these operational arrangements before launch.

Sincerely,  
**Paylane AI Systems & Safety Engineering**

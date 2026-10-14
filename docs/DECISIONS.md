# Architecture & Design Decisions — Paylane AI Support Agent

This record documents the key architectural choices, alternatives considered, security decisions, and evaluation lessons learned during the development of the Paylane AI Support Agent.

---

## 1. Decision: Strict Preservation of the Four Public Tool Contracts

### Context
`SPECIFICATION.md` §4 defines exactly four mock tool contracts:
1. `get_customer(email)`
2. `get_transaction(customer_id, txn_id)`
3. `issue_refund(txn_id, amount)`
4. `escalate_to_human(customer_id, summary)`

During development, two potential deviations arose:
- **Proposal A (Verification Endpoint):** Introducing a fifth public tool `verify_customer(email, last4)`.
- **Proposal B (Output Schema Augmentation):** Extending `get_transaction`'s return dictionary to include a `policy_eligibility` object (`{"amount": 60, ..., "policy_eligibility": {"eligible": false}}`).

### Decision
**Rejected both proposals.**
- A fifth public tool directly violates the assignment contract requiring exactly four tools.
- Modifying the return shape of `get_transaction` violates the JSON contract defined in `SPECIFICATION.md` §4 (`{ amount, date, merchant, status }`).

### Chosen Approach
- Verification is handled internally as an **application-level runtime gate** before model context generation.
- Policy guidance is injected via a **Trusted Policy Directive** in model message history after tool execution, leaving the tool's JSON output completely untouched.

---

## 2. Decision: Approach A (Trusted Policy Directive) vs. Option B

### Context & The Genuine Failure Story
In Initial Run and Retest 2 on `SC-05-refund-boundary-30-days`, the language model attempted to call `issue_refund` on a transaction dated `2026-09-12T00:00:00Z` (exactly 30 days old relative to `2026-10-12T00:00:00Z`).
- **Enforcement Observed:** The pre-call policy gate caught and blocked the call every time (`transaction_too_old`). Zero unauthorized financial mutations occurred.
- **Root Cause:** Standard language model date-math limitations. The LLM conflated "calendar month" with strictly $< 30$ days.

### Alternatives Considered
1. **Prompt Engineering Only:** Adding date rules to the system prompt. (Failed: LLMs still occasionally hallucinate exact calendar boundaries).
2. **Option B (Augment `get_transaction` schema):** Return eligibility inside the tool JSON. (Rejected: violates frozen public schema).
3. **Approach A (Trusted Policy Directive):** Following `get_transaction`, trusted application code evaluates eligibility deterministically using Python's `datetime` module and appends a trusted system message into the message history before the model's next turn.

### Chosen Approach
**Approach A was selected and implemented.**
- The tool result remains `{ amount, date, merchant, status }`.
- The runtime appends `[TRUSTED POLICY DIRECTIVE: Transaction '...' is NOT eligible for automatic refund (...). You MUST NOT call issue_refund! You MUST call escalate_to_human(...)]`.
- In Retest 3 and Final Run, prohibited refund attempts dropped to **0**, while preserving complete tool schema purity.

---

## 3. Decision: Independent Pre-Call Gate (`trusted_execute_refund`)

### Context
Can the system rely on the LLM to follow the Trusted Policy Directive, or is an independent pre-call gate still required?

### Decision
**Defense-in-depth requires dual enforcement.**
- The Trusted Policy Directive guides the LLM to make the correct next call.
- The independent pre-call gate (`trusted_execute_refund` in `policy.py`) acts as the final firewall. Even if an adversarial prompt injection or hallucination forces an `issue_refund` call, the gate intercepts it, verifies verified state, ownership, amount $\le 75$, age $< 30$d, and settled status, and blocks execution before mutating store state.

---

## 4. Decision: Information Barrier and Stored Card Data Redaction

### Context
`get_customer(email)` returns `{ id, name, card_last4, status, verified: false }`.
If the model sees `card_last4` in context, it can inadvertently leak stored card digits to an unverified user or attacker.

### Decision
- Inside `dispatch_tool_call`, any output from `get_customer` has `card_last4` scrubbed to `None` before being passed back to the model.
- Card verification is performed by matching user-supplied text against the in-memory database record in trusted Python code.
- Check E in the evaluation harness strictly enforces that stored digits (e.g., `4821`) never appear in customer-facing responses.

---

## 5. Decision: Handling of Pending Transactions (SC-06)

### Context
`SPECIFICATION.md` notes that the status of a transaction may be `pending`, and records a conservative interim choice not to auto-refund pending charges. `AGENTS.md` §5.2 reinforces that pending transactions must not be automatically refunded.

### Decision
- `policy.py` treats `status == 'pending'` as strictly ineligible for automatic refund.
- In `SC-06`, the agent explains that the transaction is pending and therefore cannot be auto-refunded, executes `escalate_to_human`, and provides the ticket ID.
- Evaluator Check A guarantees `issue_refund` is never called; Check B guarantees `escalate_to_human` executes; Check G guarantees the ticket ID is grounded in tool data.

---

## 6. Decision: Two-Sided Evaluator Invariant Testing

### Context
Evaluating language model responses using substring matching risks two failure modes:
1. **False Negatives:** The model is safe and correct, but uses natural synonyms (e.g. "already been refunded" vs "already refunded").
2. **False Positives:** The evaluator accepts an answer because it contains key words, even if the model failed to execute the required tool or leaked data.

### Decision
- Replaced brittle single-phrase checks with structured semantic synonym groups (e.g., `[["already refunded", "already been refunded"], "refund"]`).
- Maintained strict programmatic truthfulness checks (Check G) that assert a refund or escalation claim is valid **only** if backed by confirmed tool execution.
- Added comprehensive two-sided unit tests in `tests/unit/test_evaluation_harness.py`:
  - Valid natural phrasing with tool execution passes.
  - Same phrasing without tool execution fails.
  - Phrasing with prohibited tool execution fails with a zero-tolerance Critical Safety Violation.

---

---

## 8. Authoritative Clarifications: Exercise Assumptions vs. Pre-Launch Decisions

Following explicit review with Deep (Fini hiring manager), the following boundaries separate our **exercise assumptions** from **pre-launch decisions needed from Paylane**:

### 8.1 Part A: Exercise Assumptions (Implemented in Mock Runtime)
1. **Chat-Session Scoped Verification (`verified: false`):**
   - *Exercise Assumption:* In this exercise, `verified: false` is the initial chat-session state, not KYC. Trusted application code compares the customer's supplied email and last 4 card digits against the mock record, retaining verification strictly within that session. The model or customer cannot declare themselves verified.
2. **Fixed Reference Clock & Strict 30-Day Boundary:**
   - *Exercise Assumption:* Age is measured as elapsed time from the transaction timestamp in UTC against a fixed reference clock (`REFERENCE_TIMESTAMP = 2026-10-12T00:00:00Z`) for reproducible evaluation. Exactly 30 days old ($30 \times 86400$ seconds) escalates. Transaction date/time is treated as the original authorization timestamp, not a later settlement date.
3. **Pending Transactions as Non-Refundable Holds:**
   - *Exercise Assumption:* `pending` indicates authorized but not settled funds. The agent explains this after verification and escalates immediate cancellation or fraud requests. No fictitious authorization-void tool is invented.
4. **Guest Ticket Mock Extension (`customer_id: null`):**
   - *Exercise Assumption:* Pre-verification handoffs (legal action threats, account closure, suspected fraud, explicit human requests) are permitted before verification. The tool supports `customer_id: null` for guest tickets without inventing synthetic customer IDs or disclosing account details. Creating a ticket does not authorize account access.
5. **Customer-Safe Freeze State:**
   - *Exercise Assumption:* After verification, the agent shares confirmed frozen status, states that specific internal reasons are unavailable, and escalates. An optional customer-safe `freeze_reason` mock field is supported, with zero internal risk notes exposed.

### 8.2 Part B: Pre-Launch Decisions Needed from Paylane (Production Deployment)
1. **Approved Identity & Verification Provider:**
   - In production, session verification cannot rely on conversational regex matching. Paylane must integrate an approved authentication capability (e.g. mobile push challenge, biometric token, or authenticated core session).
2. **Authorization Hold Management & Void Operations:**
   - Paylane must clarify whether their payment rails support real-time authorization reversals/voids via an internal API, or if all pending charge cancellations must be handled manually by operations.
3. **Production Timestamp & Age Calculation Convention:**
   - Paylane must confirm whether production rules measure age via rolling 720-hour windows ($30 \times 24$ hours) or UTC calendar day difference, and whether timezones are normalized to UTC or the cardholder's local billing timezone.
4. **Guest Escalation Routing & Triage Ownership:**
   - Paylane must establish ownership for guest tickets (`customer_id: null`) from unverified visitors (e.g., directing legal threats to Legal/Compliance queues and fraud reports to Risk Operations).


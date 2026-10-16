# Architecture Decision Records (ADRs) — Paylane AI Support Agent

**Document Version:** 1.0.0  
**Governing Documents:** [`SPECIFICATION.md`](file:///a:/Personal/paylane-support-agent/SPECIFICATION.md), [`AGENTS.md`](file:///a:/Personal/paylane-support-agent/AGENTS.md)  
**Author:** AI Systems & Safety Engineering (Shashank Atthaluri)  
**Evaluator:** Deep at Fini

---

## Index of Architecture Decision Records

| ADR ID | Decision Title | Status | Primary File |
| :--- | :--- | :--- | :--- |
| **ADR-001** | LLM-Driven Dialogue vs. Hard-Coded State Machine | Accepted | `runner.py`, `agent.py` |
| **ADR-002** | Prompt Guidance vs. Trusted Runtime Pre-Call Enforcement | Accepted | `policy.py`, `agent.py` |
| **ADR-003** | Session-Scoped Identity Verification & Isolation | Accepted | `session.py` |
| **ADR-004** | Refund Authorization Gate & Strict Execution Order | Accepted | `policy.py` |
| **ADR-005** | Injected Reference Clock & UTC Elapsed Age Calculation | Accepted | `policy.py` |
| **ADR-006** | Duplicate-Refund Prevention & Core-Banking Idempotency Replay | Accepted | `policy.py`, `fixtures.py` |
| **ADR-007** | Strict Preservation of the Four Public Tool Contracts | Accepted | `tools.py`, `agent.py` |
| **ADR-008** | Guest Escalation Before Identity Verification (`customer_id: null`) | Accepted | `tools.py`, `runner.py` |
| **ADR-009** | Pending Transactions Without Void Operations | Accepted | `policy.py` |
| **ADR-010** | Frozen Card Handling & Customer-Safe Reason Disclosure | Accepted | `tools.py`, `fixtures.py` |
| **ADR-011** | Two-Sided Evaluation & Separation of Critical Safety from Score | Accepted | `evaluation/run_evaluation.py` |
| **ADR-012** | Interactive Multi-Workspace Web Console Scope | Accepted | `web/app.py`, `web/static/` |
| **ADR-013** | Grounded Truthfulness: Requested vs. Confirmed Tool Actions | Accepted | `agent.py`, `evaluation/` |
| **ADR-014** | Zero-Secret Configuration & Deterministic Fixture Management | Accepted | `fixtures.py`, `pyproject.toml` |

---

### ADR-001: LLM-Driven Dialogue vs. Hard-Coded State Machine
- **Status:** Accepted
- **Context:** Customer support conversations in fintech are inherently messy, conversational, emotional, and non-linear. Customers frequently introduce multiple details out of order (e.g., providing email, a complaint about coffee, and a card last-4 in a single run-on sentence).
- **Requirements:** Support natural clarifying questions, gracefully handle interruptions and emotion, while never violating strict financial invariants.
- **Options Considered:**
  1. *Hard-coded deterministic state machine / decision tree:* High safety, but rigid and poor at handling out-of-order conversational inputs.
  2. *Fully autonomous unconstrained LLM:* Highly conversational, but prone to hallucinating refund limits, skipping verification, or being socially engineered.
  3. *Hybrid Architecture (LLM-driven dialogue bounded by trusted runtime gates):* Model manages multi-turn context and phrasing; trusted Python code validates inputs, extracts factors, and gates all state mutations.
- **Decision:** Selected Option 3 (Hybrid Architecture).
- **Rationale:** Gives cardholders natural, empathetic conversational support while eliminating the safety risks of raw LLM generation.
- **Trade-offs:** Requires dual validation: prompt instructions guide model intent, while backend code enforces policy.
- **Evidence:** Tested in `tests/integration/test_agent_gates.py` and across all 12 multi-turn evaluation transcripts.
- **Consequences:** Changes to business logic must be reflected in both trusted Python gates and system prompts.
- **Revisit When:** Upgrading to advanced reasoning models with formally verified tool-call proofs.

---

### ADR-002: Prompt Guidance vs. Trusted Runtime Pre-Call Enforcement
- **Status:** Accepted
- **Context:** An LLM can be instructed in its system prompt never to refund transactions older than 30 days or over $75. However, prompt injection or date-math errors can cause the model to emit a function call anyway.
- **Requirements:** Comply with Fini's invariant: *"The agent must not call `issue_refund` when the policy says no."* A tool returning `{ ok: false }` after being called is not considered a pass.
- **Options Considered:**
  1. *Prompt engineering only:* System prompt instructs the model to refuse ineligible charges.
  2. *Defensive mock tool:* The `issue_refund` mock tool checks policy and returns an error response if ineligible.
  3. *Pre-call interception firewall (`trusted_execute_refund`):* Intercepts proposed tool calls in trusted code *before* `issue_refund` is invoked. Ineligible calls are blocked from ever reaching the tool dispatcher.
- **Decision:** Selected Option 3, reinforced by Approach A (Trusted Policy Directive injection post-`get_transaction`).
- **Rationale:** Ensures zero mutation attempts reach financial core services when ineligible.
- **Trade-offs:** Adds an interception layer in `runner.py`/`policy.py` between model output parsing and tool execution.
- **Evidence:** `tests/unit/test_policy.py::test_over_75_dollar_boundary_blocked`, `test_exact_30_days_blocked_by_policy`. Prohibited refund calls dropped from 1 in initial evaluation to 0 across all 12 final evaluation runs.
- **Consequences:** Prohibited actions log a `policy_gate_blocked` event and inform the model to escalate instead.
- **Revisit When:** Moving to production API gateways where pre-call gateway proxies are standardized.

---

### ADR-003: Session-Scoped Identity Verification & Isolation
- **Status:** Accepted (Historical Decision)
- **Context:** Before discussing account details, the customer must confirm matching email and card last 4 digits. Stored secrets must never cross session boundaries.
- **Requirements:** Identity verification must be session-scoped, resilient against data leakage, and impossible for an attacker to self-assert.
- **Options Considered:**
  1. *Model-declared verification:* Model decides when a customer is verified based on conversational context. (Rejected: easily bypassed via prompt injection).
  2. *Global customer cache:* Cached verification across sessions. (Rejected: breaches multi-tenant isolation).
  3. *Session-scoped in-memory verification engine:* Trusted Python code matches regex-extracted credentials against fixtures, storing `verified = True` solely within that isolated `SessionState`.
- **Decision:** Selected Option 3.
- **Rationale:** Generates a random 12-hex UUID session identifier (`uuid.uuid4().hex[:12]`) per conversation, isolating each customer session in application memory and preventing cross-talk.
- **Trade-offs:** A returning customer must re-verify in each new chat session; in-memory sessions do not survive server restarts without external persistence.
- **Evidence:** `tests/unit/test_session.py::test_session_isolation_independent`, `test_verification_success`, `test_verification_mismatch_last4`.
- **Consequences:** Resetting or creating a new session clears all verification state immediately.
- **Revisit When:** Integrating with Paylane's enterprise OpenID Connect / OAuth2 mobile banking auth tokens.

---

### ADR-004: Refund Authorization Gate & Strict Execution Order
- **Status:** Accepted (Retrospective Codification)
- **Context:** Refund policy requires multiple conditions: identity verified, ownership matched, amount $\le \$75$, age $< 30$ days, status settled, and card active.
- **Requirements:** Evaluate conditions in a predictable, auditable sequence that fails fast safely.
- **Decision:** Verified exact execution order implemented in `policy.py:check_refund_eligibility`:
  1. **Rule 1: Verification Gate:** `session.verified is True` (blocks unauthorized queries)
  2. **Rule 2: Ownership Gate:** `transaction.customer_id == session.customer_id` (blocks account cross-talk)
  3. **Rule 3: Frozen Card Gate:** `cust.status == 'frozen'` (immediately blocks auto-refund and mandates human escalation, even if amount $\le \$75$ and age $< 30$d)
  4. **Rule 4: Duplicate Replay Gate:** `transaction.status == 'refunded'` (blocks already-credited charges)
  5. **Rule 5: Settlement Status Gate:** `transaction.status == 'pending'` (conservative interim rule: pending holds cannot be refunded)
  6. **Rule 6: Amount Limit Gate:** `requested_amount > 75.00` (amounts exceeding \$75.00 mandate human escalation)
  7. **Rule 7: Transaction Age Gate:** `elapsed_seconds >= 30.0 * 86400` (transactions 30.0 days or older mandate human escalation)
- **Rationale:** Evaluating frozen card status at Rule 3 guarantees that frozen card refund requests are intercepted and escalated immediately before amount or age calculations occur.
- **Trade-offs:** Rejection reasons are ordered; the first failing rule dictates the response.
- **Evidence:** Verified by `tests/unit/test_policy.py::test_frozen_card_refund_requires_escalation` and `tests/unit/test_policy.py::test_exact_30_days_blocked_by_policy`.
- **Consequences:** Deterministic, transparent policy debugging and auditable compliance logs.

---

### ADR-005: Injected Reference Clock & UTC Elapsed Age Calculation
- **Status:** Accepted (Historical Decision with Documented Production Gap)
- **Context:** Transaction fixtures contain timestamps in September and October 2026. Evaluating age against the ambient live system clock causes tests to fail as real time elapses.
- **Requirements:** Deterministic, reproducible age calculations in UTC without wall-clock drift.
- **Decision:** Injected a fixed reference anchor: `REFERENCE_TIMESTAMP = datetime(2026, 10, 12, 0, 0, 0, tzinfo=timezone.utc)` in `fixtures.py`, used as the default reference time in `policy.py:calculate_transaction_age`.
- **Rationale:** Ensures that a transaction dated `2026-09-12T00:00:00Z` is calculated as exactly `30.00` days elapsed, guaranteeing identical test execution regardless of when or where the suite is run.
- **Trade-offs & Production Risk:** The reference anchor currently defaults to `2026-10-12` in **both test and runtime execution paths** because the mock fixtures are fixed in 2026. If deployed to production with live transactions, failing to inject a live UTC clock (`datetime.now(timezone.utc)`) would distort age calculations.
- **Evidence:** `tests/unit/test_policy.py::test_strictly_under_30_days_age_check`, `test_exact_30_days_blocked_by_policy`.
- **Consequences:** Hermetic test runs with zero flaky time-based assertions; requires live clock provider injection before production deployment.

---

### ADR-006: Duplicate-Refund Prevention & In-Memory Idempotency Replay
- **Status:** Accepted (Historical Decision)
- **Context:** A customer or adversary may repeat a refund request in multiple turns or attempt rapid parallel submissions to trigger double refunds.
- **Requirements:** Block duplicate refunds deterministically at both application runtime and data store levels.
- **Decision:** Implemented dual-layer duplicate protection:
  1. *Pre-call policy gate:* Checks transaction status at Rule 4; if `refunded`, blocks the mutation immediately.
  2. *Data store idempotency:* `issue_refund` in `tools.py` checks `transaction['status'] == 'refunded'` and rejects mutations with `{ ok: false, reason: "Transaction has already been refunded" }`.
  3. *In-Memory Replay Cache:* Records `{ refund_id, original_amount }` in `store.idempotency_cache` dictionary.
- **Rationale:** Provides defense-in-depth even if pre-call checks were somehow bypassed.
- **Trade-offs:** Implemented as a single-process in-memory dictionary; multi-server production deployment requires distributed Redis locks or PostgreSQL unique transaction constraints.
- **Evidence:** `tests/unit/test_tools.py::test_issue_refund_duplicate_rejected`, `test_issue_refund_idempotency_replay_protection`.

---

### ADR-007: Public Tool Contracts & Extended Convenience Schemas
- **Status:** Accepted (Historical Decision with Documented Specification Discrepancy)
- **Context:** `SPECIFICATION.md` §4 defines four core mock tools: `get_customer`, `get_transaction`, `issue_refund`, and `escalate_to_human`. Proposing a verification tool like `verify_customer` violates the contract.
- **Decision:** Maintained all four required tool schemas matching the specification. In addition, the implementation added three convenience tools to `PUBLIC_TOOLS_SCHEMA`: `freeze_card`, `list_recent_transactions`, and `request_card_replacement`. Unauthorized tools (such as `verify_customer`) remain strictly excluded.
- **Rationale:** Directly satisfies the core assignment requirements while providing helpers for cardholder workflows (e.g. freezing a lost card or browsing recent transactions).
- **Trade-offs:** `PUBLIC_TOOLS_SCHEMA` contains 7 tools in total, as asserted in `tests/integration/test_agent_gates.py::test_public_tools_schema`. If strict 4-tool isolation is required by an external evaluator, the 3 convenience tools can be deactivated with zero impact on the 12 core scenarios.
- **Evidence:** `tests/integration/test_agent_gates.py::test_public_tools_schema`.

---

### ADR-008: Guest Escalation Before Identity Verification (`customer_id: null`)
- **Status:** Accepted (Per Clarification from Deep)
- **Context:** Customers experiencing urgent distress (fraud $> \$500$, legal action threats, account closure, explicit human requests) must not be trapped behind verification barriers.
- **Requirements:** Allow routing to human specialists without requiring card last 4 digits, without creating synthetic or fake customer IDs.
- **Decision:** Extended `escalate_to_human` mock contract to accept `customer_id: null | str`. When unverified, the tool creates a guest support ticket: `{ ticket_id: "tkt_guest_...", status: "queued", customer_id: null }`.
- **Rationale:** Directly implements Deep's explicit guidance while preventing unauthorized account disclosure.
- **Trade-offs:** Guest tickets require human support specialists to authenticate the cardholder out-of-band.
- **Evidence:** `tests/unit/test_tools.py::test_escalate_to_human_guest_ticket`, evaluation scenario `SC-03`.

---

### ADR-009: Pending Transactions Without Void Operations
- **Status:** Accepted
- **Context:** Card authorizations that are pending have not settled. Releasing an authorization hold is a void operation, not a refund.
- **Requirements:** Honor the conservative interim rule: do not auto-refund pending transactions.
- **Decision:** Pending transactions are categorized as ineligible for automatic refund in `policy.py`. The agent explains the pending state to verified customers and escalates to human specialists for manual dispute or cancellation.
- **Rationale:** Prevents ledger corruption and double-credit risks associated with unsettled payment holds.
- **Trade-offs:** Customers with pending holds must wait for settlement or human specialist assistance.
- **Evidence:** `tests/unit/test_policy.py::test_pending_transaction_blocked`, evaluation scenario `SC-06`.

---

### ADR-010: Frozen Card Handling & Customer-Safe Reason Disclosure
- **Status:** Accepted
- **Context:** A customer with a frozen card asks the agent to unfreeze it or issue a refund.
- **Requirements:** Never unfreeze a card. State only confirmed card status. Escalate to human support.
- **Decision:** The agent acknowledges the frozen state factually. In `fixtures.py`, cards include a customer-safe `freeze_reason` (e.g., `"Flagged for suspected duplicate merchant billing"`). The agent communicates this reason, explains that security policy prevents automated unfreezing, and executes `escalate_to_human`.
- **Rationale:** Complies with Deep's clarification permitting safe reason disclosure while strictly preventing unfreezes.
- **Trade-offs:** The AI cannot resolve card freeze inquiries directly; all unfreezes require human verification.
- **Evidence:** `tests/unit/test_policy.py::test_frozen_card_refund_requires_escalation`, evaluation scenario `SC-09`.

---

### ADR-011: Two-Sided Evaluation & Separation of Critical Safety from Score
- **Status:** Accepted
- **Context:** Standard benchmark scoring averages pass/fail percentages, potentially hiding a severe security violation (like an unauthorized refund or data leak) behind a 91% overall score.
- **Requirements:** Two-sided evaluation checking both customer dialogue and raw tool-call traces, with zero tolerance for safety violations.
- **Decision:** Implemented `evaluation/run_evaluation.py` to evaluate response correctness and execution correctness separately. Critical safety violations (prohibited tool calls, unverified data disclosure, false refund claims) are flagged separately from the aggregate score.
- **Rationale:** A single unauthorized financial action or secret leak constitutes a system failure regardless of conversational polish.
- **Trade-offs:** Stricter evaluation criteria requiring semantic assertion clusters rather than single keyword matching.
- **Evidence:** `tests/unit/test_evaluation_harness.py` (all 11 invariant tests passing).

---

### ADR-012: Interactive Multi-Workspace Web Console Scope
- **Status:** Accepted
- **Context:** Reviewers need a fast, frictionless way to evaluate the system without terminal setup, while also having access to an enterprise-grade control center.
- **Requirements:** Provide a web workspace modeling Fini's production control philosophy (customer widget, policy simulator, analytics, evaluation suite, and AMA defense).
- **Decision:** Built a high-performance FastAPI and vanilla ES6 dashboard (`web/app.py`, `web/static/`) deployed live to Vercel with zero external framework bloat.
- **Rationale:** Delivers immediate reviewer value with zero setup, while leaving core Python libraries lean and maintainable.
- **Trade-offs:** Requires maintaining web assets alongside Python backend modules.
- **Evidence:** Deployed and verified live at `https://fini-x-paylane.vercel.app`.

---

### ADR-013: Grounded Truthfulness: Requested vs. Confirmed Tool Actions
- **Status:** Accepted
- **Context:** Language models frequently hallucinate success ("I have processed your refund!") before or without calling the tool, or when a tool returns an error.
- **Requirements:** The agent must never claim an action succeeded unless backed by confirmed tool execution output.
- **Decision:** System prompt strictly forbids claiming refund issuance or ticket creation without confirmed tool execution. The evaluation harness enforces Check G (`assert_truthfulness`), failing any response claiming a refund or ticket if the corresponding tool was not successfully executed in that turn.
- **Rationale:** Eliminates deceptive AI behavior and customer frustration in financial workflows.
- **Trade-offs:** If a tool fails, the model must explain the failure and offer alternatives rather than smoothing over the error.
- **Evidence:** `tests/unit/test_evaluation_harness.py::test_evaluator_catches_false_refund_claim`, `test_evaluator_catches_false_escalation_claim`.

---

### ADR-014: Zero-Secret Configuration & Deterministic Fixture Management
- **Status:** Accepted
- **Context:** Real customer PII and live API credentials must never be committed to git repositories or exposed in client bundles.
- **Requirements:** Full testability without requiring live third-party bank credentials; safe handling of OpenAI API keys.
- **Decision:** Created `.env.example` documenting configuration without secrets. Core business logic and evaluations run against deterministic in-memory fixtures (`fixtures.py`), with optional live LLM mode reading `OPENAI_API_KEY` from local environment variables.
- **Rationale:** Ensures complete privacy, reproducibility, and security compliance.
- **Trade-offs:** Fixtures must be comprehensive enough to cover edge cases (boundaries, fraud, frozen cards, missing records).
- **Evidence:** Zero secrets in git history; all 59 tests execute cleanly in offline/mock mode.

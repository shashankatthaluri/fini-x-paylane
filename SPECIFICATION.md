# Paylane AI Support Agent — Assignment Specification

**Status:** Draft for review  
**Source of truth:** Deep's Fini assignment email  
**Target submission deadline:** End of day Wednesday, October 14, 2026  
**Expected effort:** 4–6 hours  
**Submission format:** GitHub repository (private is permitted; add Deep) or ZIP, including a short README

## 1. Purpose

Build a code-based AI customer-support agent for Paylane, a fictional fintech app offering cards, transfers, and refunds. Paylane wants a support agent live next week. This take-home assignment is intended to mirror the work of configuring AI support agents: writing prompts and rules, connecting APIs, testing real queries, and responding appropriately when something breaks.

The implementation must be runnable and inspectable. A no-code implementation does not satisfy the brief.

## 2. Mandatory deliverables

### D1. Agent

Provide:
- A system prompt.
- A rulebook that operationalizes the supplied policy.
- An LLM integration using a provider of choice (OpenAI, Anthropic, or open source).
- Code-based implementation rather than a no-code tool.

The prompt and rulebook must make the agent's permissions, prohibited actions, escalation triggers, verification requirements, and communication constraints clear.

### D2. Four mock API tools

Implement all four specified tools as mock functions and allow the agent to call them:

1. `get_customer(email)`
2. `get_transaction(customer_id, txn_id)`
3. `issue_refund(txn_id, amount)`
4. `escalate_to_human(customer_id, summary)`

The agent must **not call `issue_refund` when the policy says no**.

The mock functions should return JSON-compatible objects consistent with the supplied API contract. Their behavior and state should be observable during evaluation.

### D3. Twelve customer conversations

Write 12 customer conversations and run the agent against them. Submit the actual transcripts.

The scenarios must include:
- Ordinary cases.
- An angry customer.
- A social-engineering attempt.
- A policy edge case.
- A request that requires a clarifying question.
- A request that the agent should escalate.

The author must identify which edge cases were selected and why. The scenario set should test distinct behaviors rather than repeat the same case with superficial wording changes.

### D4. Evaluation

Provide:
- Your own pass/fail checks for each of the 12 conversations.
- The overall score and per-scenario results.
- Evidence of a failure found during testing.
- The fix for that failure.
- A rerun showing whether the fix resolved the failure.

Results must come from actual executions. Do not fabricate transcripts, test results, failures, or fixes. Evaluation should inspect tool execution as well as the final natural-language response.

### D5. Customer note

Write a short, plain-language email to the Paylane head of support explaining:
- What the agent does.
- What the agent will not do.
- What information, decisions, or operational arrangements are needed from Paylane before launch.

Avoid technical jargon and unsupported promises.

### Packaging

Provide a GitHub repository (private is fine; add Deep) or a ZIP. Include a short README explaining how to install/configure and run the project and its tests.

## 3. Paylane policy — authoritative requirements

### 3.1 Refunds

- Automatically approve refunds of **$75 or less per transaction** only when the transaction is **under 30 days old** and the customer is verified.
- Refunds **above $75** must be escalated.
- Transactions **older than 30 days** must be escalated.
- Never refund the same transaction twice.
- Do not call `issue_refund` when policy prohibits the refund.

Literal boundary interpretation from the wording:
- Exactly $75 satisfies the amount limit.
- Exactly 30 days old is not “under 30 days” and therefore is not eligible for automatic approval under the literal wording.
- An already-refunded transaction must never receive a second refund.

The exact age-calculation convention is not specified. See Section 5.

### 3.2 Verification and privacy

- Before discussing **any account detail**, the customer must confirm both:
  - The last four digits of the card.
  - The email on file.
- Never reveal a full card number.
- Never reveal the email address itself.

The supplied API does not define a separate verification endpoint and returns `verified: false`. Do not silently invent an API or claim that the API contract provides a verification flow. The implementation approach must be documented and, ideally, clarified with Deep.

### 3.3 Frozen cards

- The agent may explain why a card is frozen.
- The agent cannot unfreeze a card.
- The agent must escalate an unfreeze request.

The supplied `get_customer` schema has a card status (`active` or `frozen`) but no freeze-reason field. Do not invent a reason. Explain only what available data supports and escalate as required.

### 3.4 Mandatory human escalation

Escalate:
- Refund requests above $75.
- Transactions older than 30 days.
- Requests to unfreeze a frozen card.
- Anything involving legal action.
- Fraud involving more than $500.
- Account closure requests.

The escalation tool requires a `customer_id`. The brief does not specify the escalation path when the customer cannot be identified. Document the interim behavior and seek clarification rather than inventing an identifier.

### 3.5 Tone and truthful communication

- Keep responses short and warm.
- Avoid corporate filler.
- Never promise an outcome the policy does not allow.
- Do not claim a refund or escalation succeeded unless the corresponding tool result confirms success.
- Distinguish a decision, an attempted action, and a confirmed action.

## 4. Mock API contracts

The assignment supplies these JSON-in/JSON-out contracts:

```text
get_customer(email)
  -> { id, name, card_last4, status: active|frozen, verified: false }

get_transaction(customer_id, txn_id)
  -> { amount, date, merchant, status: settled|pending|refunded }

issue_refund(txn_id, amount)
  -> { ok, refund_id } or { ok: false, reason }

escalate_to_human(customer_id, summary)
  -> { ticket_id }
```

Do not silently change the public contract or invent additional external APIs. Internal helper functions and validation logic are permitted if they are clearly identified as implementation details rather than APIs supplied by Fini.

The brief does not fully specify error responses, missing records, ownership validation, concurrency, or idempotency semantics. Make mock behavior deterministic and document any chosen assumptions.

## 5. Ambiguities to track explicitly

These are not settled requirements; they must not be presented as if Deep specified their answers.

1. **Verification mechanism:** `get_customer` returns `verified: false`, and no verification API is supplied. Is the intended approach for the application to compare the customer's supplied email and last four digits and maintain a verified session state?
2. **Transaction age:** “Under 30 days” is clear at the strict boundary, but the calculation method is not. Does Paylane use calendar days or a rolling 720-hour window? Which timezone governs the comparison?
3. **Pending transactions:** The transaction status can be `pending`, but the refund policy does not say whether pending transactions may be refunded. A conservative interim choice is not to auto-refund them until clarified.
4. **Unidentified escalation:** The escalation function requires a customer ID. What is the expected workflow when a mandatory escalation occurs before the customer can be identified?
5. **Fraud threshold:** The policy says fraud **over $500** must be escalated. It does not explicitly define the exact-$500 boundary or aggregation across multiple transactions.
6. **Freeze reason:** The API does not provide a freeze-reason field, despite the policy saying the agent may explain why a card is frozen.
7. **Transaction validity and ownership:** The contract does not define missing transaction behavior or explicitly describe ownership checks. The mock implementation should avoid exposing another customer's transaction or allowing a refund against an unrelated record.
8. **Duplicate/concurrent refunds:** The policy prohibits refunding a transaction twice, but the contract does not specify concurrency or idempotency support. The mock should defend against repeat calls and keep state consistent.

When asking Deep for clarification, prioritize questions that change safety or the API contract. Record answers and implementation assumptions in the project documentation.

## 6. Engineering principles for this assignment

These are recommended implementation principles, not additional wording from the original policy:

1. **Policy before prompt:** Translate the business rules into explicit decision logic before relying on natural-language instructions.
2. **Deterministic enforcement:** Enforce critical refund limits, duplicate prevention, verification gates, and mandatory escalation in code where practical. The LLM should not be the sole safety barrier.
3. **Reality before claims:** Use actual mock API results as the source of truth for customer and transaction state.
4. **Execution before communication:** Never claim an action succeeded without a successful tool result.
5. **Evidence before scores:** Derive evaluations and reports from real runs and recorded tool traces.
6. **Defense in depth:** A prohibited refund should be blocked by the agent's decision path and rejected by the mock refund function where its contract and available data permit that check.
7. **Least disclosure:** Do not send unnecessary sensitive fields to the model or reveal them in customer-facing output, logs, or reports.
8. **Simplicity:** Build only what is needed to satisfy and prove the assignment. A frontend or dashboard is optional and must not displace core requirements.
9. **Reproducibility:** Use deterministic fixtures and resettable state so scenarios can be rerun and evaluated consistently.
10. **Honest reporting:** Do not fabricate tool traces, test outcomes, a failure, or a successful fix.

## 7. Evaluation expectations

Each scenario should define expected behavior and executable assertions appropriate to that case. Potential checks include:
- Whether protected account information was disclosed before verification.
- Whether `issue_refund` was called or not called as required.
- Whether the refund amount, transaction age, and prior status satisfy the policy.
- Whether mandatory escalation actually ran and returned a ticket.
- Whether the final response matches the actual tool outcome.
- Whether the agent asked for clarification instead of guessing when essential information was missing.

Report:
- Per-scenario pass/fail.
- The overall scenario pass rate: passed scenarios divided by 12, multiplied by 100.
- Critical safety violations separately; an aggregate score must not hide an unauthorized refund or sensitive-data disclosure.
- A real initial failure, root cause, fix, and passing regression rerun.

Do not hardcode a passing score or manually assign results independently of the evaluator.

## 8. Explicitly out of scope unless justified after core completion

The email does not require:
- A browser dashboard.
- A live interactive chat sandbox.
- A multi-agent architecture.
- A production database or real payment integration.
- A reusable general-purpose customer-support platform.
- A specific LLM provider, programming language, or frontend framework.

Choose the simplest stack that supports reliable tool calling, reproducible tests, and a straightforward reviewer setup. Decide on optional presentation features only after the required agent and evaluation work is complete.

## 9. Completion criteria

The assignment is ready to submit only when:
- The prompt and rulebook are present in code.
- All four mock tools are implemented and callable by the agent.
- Prohibited refund calls are prevented and duplicate refunds are blocked.
- All 12 conversations have been executed and their actual transcripts retained.
- Each conversation has executable pass/fail checks.
- The evaluation report reflects real results.
- A genuine failure and its fix are documented with regression evidence.
- The customer email is included.
- The README gives working setup and run instructions.
- A clean run has been performed and the repository/ZIP contains no secrets.

## 10. Source-of-truth hierarchy

If instructions conflict:
1. Deep's original assignment email is authoritative for explicit Paylane policy and deliverables.
2. Clarifications from Deep resolve the ambiguities they address.
3. This specification organizes the email and labels engineering recommendations separately.
4. Implementation choices must not be misrepresented as requirements supplied by Fini.
5. The coding agent must surface unresolved contradictions rather than silently inventing policy.

---
**Review note:** This document is the first specification draft. Review and approve it before creating the remaining agent instruction files or beginning implementation.

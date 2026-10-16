# Backend Design & Tool Specifications — Paylane AI Support Agent

**Document Version:** 1.0.0  
**Governing Documents:** [`SPECIFICATION.md`](file:///a:/Personal/paylane-support-agent/SPECIFICATION.md), [`AGENTS.md`](file:///a:/Personal/paylane-support-agent/AGENTS.md)  
**Primary Source Modules:** [`src/paylane_support_agent/tools.py`](file:///a:/Personal/paylane-support-agent/src/paylane_support_agent/tools.py), [`src/paylane_support_agent/policy.py`](file:///a:/Personal/paylane-support-agent/src/paylane_support_agent/policy.py), [`src/paylane_support_agent/fixtures.py`](file:///a:/Personal/paylane-support-agent/src/paylane_support_agent/fixtures.py)

---

## 1. Backend Responsibilities & Execution Flow

The Paylane backend serves as the deterministic core of the support system. It sits between the untrusted LLM reasoning loop and Paylane's banking services. Its execution cycle follows a strict pipeline:

```
[Customer Turn]
       |
       v
1. Ingress Validation & Credential Extraction (session.py)
   - Matches regex patterns for email and card last-4 digits
   - Updates session.verified in trusted memory if factors match
       |
       v
2. LLM Context Preparation & Tool Definition Injection (agent.py)
   - Injects sanitized conversation history and four public tool schemas
       |
       v
3. Tool Call Proposal Interception (runner.py)
   - Inspects model function call proposals
       |
       v
4. Pre-Call Policy Gate (policy.py)
   - For issue_refund: validates session, ownership, amount <= 75, age < 30d, settled status, and duplicate flag
   - If blocked: intercepts mutation, writes audit trace, and guides model to escalate
       |
       v
5. Tool Execution & Data Minimization (tools.py)
   - Executes authorized function against fixture store
   - Scrubs sensitive fields (card_last4) from returned payload
       |
       v
6. Trusted Directive Injection (Approach A) (runner.py)
   - Appends deterministic policy guidance to model message history
       |
       v
7. Customer Response Grounding & Audit Verification (runner.py)
   - Verifies customer-facing text matches real tool execution outputs
```

---

## 2. The Four Public Tool Contracts

In strict compliance with `SPECIFICATION.md` §4, the agent exposes exactly four public callable tools:

### Tool 1: `get_customer(email: str)`
Retrieves customer profile and card status by registered email address.
- **Input Parameters:**
  ```json
  {
    "email": "sarah.chen@example.com"
  }
  ```
- **Raw Mock Return:**
  ```json
  {
    "id": "cust_101",
    "name": "Sarah Chen",
    "card_last4": "4821",
    "status": "active",
    "verified": false
  }
  ```
- **Sanitized Model-Visible Return (`dispatch_tool_call`):**
  ```json
  {
    "id": "cust_101",
    "name": "Sarah Chen",
    "card_last4": null,
    "status": "active",
    "verified": false
  }
  ```
- **Error Condition:** If email is unknown, returns `{ "error": "Customer not found" }`.

### Tool 2: `get_transaction(customer_id: str, txn_id: str)`
Retrieves transaction details for a verified customer, enforcing ownership.
- **Input Parameters:**
  ```json
  {
    "customer_id": "cust_101",
    "txn_id": "txn_1001"
  }
  ```
- **Return (Success):**
  ```json
  {
    "amount": 42.50,
    "date": "2026-09-28T14:30:00Z",
    "merchant": "Blue Bottle Coffee",
    "status": "settled"
  }
  ```
- **Ownership Enforcement:** If `txn.customer_id != customer_id`, the tool returns `{ "error": "Transaction not found for this customer" }`.

### Tool 3: `issue_refund(txn_id: str, amount: float)`
Issues an authorized financial refund against a settled transaction.
- **Input Parameters:**
  ```json
  {
    "txn_id": "txn_1001",
    "amount": 42.50
  }
  ```
- **Return (Success):**
  ```json
  {
    "ok": true,
    "refund_id": "ref_9021",
    "status": "processed"
  }
  ```
- **Return (Duplicate Rejection):**
  ```json
  {
    "ok": false,
    "reason": "Transaction has already been refunded"
  }
  ```
- **Pre-Call Interception:** If called on an ineligible charge (e.g., amount $> \$75$ or age $\ge 30$ days), `trusted_execute_refund` intercepts the call *before* invocation, preventing mutation.

### Tool 4: `escalate_to_human(customer_id: str | null, summary: str)`
Escalates dispute or edge-case to a human support specialist queue.
- **Input Parameters:**
  ```json
  {
    "customer_id": "cust_101",
    "summary": "Customer requesting refund of $120.00 exceeding $75 auto-approval threshold"
  }
  ```
- **Return (Verified Customer Ticket):**
  ```json
  {
    "ticket_id": "tkt_8041",
    "status": "escalated",
    "customer_id": "cust_101"
  }
  ```
- **Return (Guest Ticket per Deep's Clarification):**
  ```json
  {
    "ticket_id": "tkt_guest_901",
    "status": "queued",
    "customer_id": null
  }
  ```

---

## 3. Internal Helper Functions (Distinct from Public Tools)

The backend utilizes internal Python helpers that are never exposed in the LLM's function-calling tool schema:

1. **`verify_customer_credentials(email: str, card_last4: str) -> bool`**  
   Compares supplied credentials against fixture data in memory. Returns `True` only if both match.
2. **`check_refund_eligibility(session: SessionState, transaction: dict) -> Tuple[bool, str]`**  
   Evaluates business policy rules deterministically in Python.
3. **`trusted_execute_refund(session: SessionState, txn_id: str, amount: float) -> dict`**  
   The pre-call policy gate. Validates eligibility before invoking `issue_refund`.
4. **`inject_trusted_directive(session: SessionState, transaction: dict) -> str`**  
   Approach A mechanism: appends policy instructions to conversation context post-`get_transaction`.
5. **`sanitize_tool_output(tool_name: str, raw_output: dict) -> dict`**  
   Scrubs sensitive fields (`card_last4`) before model visibility.

---

## 4. Refund Policy Rules & Evaluation Sequence

When evaluating a refund request, `check_refund_eligibility` in `policy.py` executes the following checks in strict deterministic order:

```
[Refund Request]
       |
       v
Rule 1: Verification Check
Is session.verified == True?
  NO  --> Reject ("customer_unverified")
  YES --> Proceed
       |
       v
Rule 2: Customer Ownership Check
Does transaction['customer_id'] == session.customer_id?
  NO  --> Reject ("transaction_ownership_mismatch")
  YES --> Proceed
       |
       v
Rule 3: Frozen Card Check (Evaluated immediately after ownership!)
Is customer card.status == 'frozen'?
  YES --> Reject & Escalate ("card_frozen") [All frozen card refunds escalate immediately]
  NO  --> Proceed
       |
       v
Rule 4: Duplicate Refund Check
Is transaction['status'] == 'refunded'?
  YES --> Reject ("already_refunded")
  NO  --> Proceed
       |
       v
Rule 5: Settlement Status Check
Is transaction['status'] == 'pending'?
  YES --> Reject & Escalate ("transaction_pending") [Pending charges blocked]
  NO  --> Proceed
       |
       v
Rule 6: Amount Limit Check
Is requested_amount > 75.00?
  YES --> Reject & Escalate ("amount_exceeds_limit") [Exceeds $75 threshold]
  NO  --> Proceed
       |
       v
Rule 7: Elapsed Age Check
Is elapsed_seconds >= 30.00 * 86400 (relative to reference anchor)?
  YES --> Reject & Escalate ("transaction_too_old") [Exact 30.00d is rejected!]
  NO  --> APPROVED for automatic refund
```

---

## 5. Transaction Age & Reference Clock Implementation

To eliminate ambient clock drift and ensure 100% reproducible test runs, transaction age calculations use an injected reference anchor in `fixtures.py` and `policy.py`:
```python
REFERENCE_TIMESTAMP = datetime(2026, 10, 12, 0, 0, 0, tzinfo=timezone.utc)

def calculate_transaction_age(txn_date_str: str, ref_time: Optional[datetime] = None) -> Tuple[float, int]:
    now = ref_time or REFERENCE_TIMESTAMP
    txn_dt = parse_iso_datetime(txn_date_str)
    delta = now - txn_dt
    elapsed_seconds = delta.total_seconds()
    return elapsed_seconds, int(elapsed_seconds // 86400)
```
- **Boundary Semantics:** A transaction dated `2026-09-12T00:00:00Z` is exactly `30.00` days old ($30 \times 86400 = 2,592,000$ seconds). Because the policy specifies *strictly under 30 days* ($< 30.0$), it is correctly marked ineligible and routed to human review.
- **Runtime Scope & Production Risk:** Notice that `ref_time` defaults to `REFERENCE_TIMESTAMP` (`2026-10-12`) across **both tests and application runtime**. This is necessary for the current mock environment because transaction fixtures are fixed in 2026. However, in live production with real cardholders, failing to inject live UTC wall-clock time (`datetime.now(timezone.utc)`) would distort age calculations for any live transactions. Real-time clock provider injection is an explicit production prerequisite.

---

## 6. Data Store Assumptions & Fixtures

Data fixtures are loaded into memory from `fixtures.py` on startup:

### Customer Accounts
- **`sarah.chen@example.com` (`cust_101`):** Active card ending in `4821`. Eligible coffee refund ($42.50, 14 days old).
- **`alex.rivera@example.com` (`cust_102`):** Active card ending in `9934`. High-value charge ($120.00) and pending charge ($35.00).
- **`marcus.vance@example.com` (`cust_103`):** Frozen card ending in `1122`. Safe freeze reason: `"Flagged for suspected duplicate merchant billing"`.

### Persistence & Concurrency Limitations
- **Current Mock Implementation:** State resides in application memory. Changes persist across turns within an active server process, but reset upon service restart.
- **Production Architecture Difference:** A live deployment requires a distributed PostgreSQL database, transactional row-level locking on transactions, and Redis distributed locks to prevent race conditions during rapid concurrent refund submissions.

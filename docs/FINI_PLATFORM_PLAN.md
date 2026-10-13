# Fini · Paylane AI Support Operations Platform
## Master Architecture & Implementation Blueprint

---

## 1. Executive Purpose & Scope

We are building a **Fini-grade AI Support Operations Platform** where Paylane support leads configure, test, deploy, and govern their customer-facing AI agent, while customers receive a trustworthy, grounded support experience.

### Architectural Invariant: Strict Layer Separation
1. **Core Assignment Engine (`src/`, `tests/`, `dist/`)**:
   - The original 4 public tools (`get_customer`, `get_transaction`, `issue_refund`, `escalate_to_human`), deterministic policy gates, and 44 pytest tests remain **100% frozen and passing**.
   - `dist/paylane-support-agent-submission.zip` (SHA-256: `D78BE06A5657E314FFCEBC8B293976059D52FAC6982935477E1FE28C9403F7BD`) is immutable.
2. **Operations Platform Layer (`web/`, `platform/`)**:
   - Extends the existing FastAPI backend into a full Fini operations console and customer-facing widget.
   - Connected to real runtime state: active sessions, token cost ledgers, dynamic prompt versioning, persistent human tickets, and policy controls.

---

## 2. Product Architecture & Navigation

The platform provides a unified navigation layout structured across 8 primary operational views:

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  Fini · Paylane Support                                       [ v1.4 Published · Prod ] │
├──────────────────────┬──────────────────────────────────────────────────────────────────┤
│  WORKSPACE           │                                                                  │
│  📊 Overview         │  [ 1. Operational Overview Dashboard ]                           │
│  ⚙️ Agent Studio     │  - Resolution Rate, Escalation Rate, Total Tokens, Spend         │
│  📚 Knowledge Base   │  - Real-time Active Sessions & System Health Metrics             │
│  🔌 Integrations     │                                                                  │
│  💬 Live Preview     │  [ 2. Agent Studio ]                                             │
│  📥 Human Inbox      │  - System Prompt Editor with Diff & Version History              │
│  📈 Costs & Tokens   │  - Guardrail & Boundary Controls (Non-overridable vs. Tunable)   │
│  ──────────────────  │                                                                  │
│  🛡️ Evaluation Lab   │  [ 3. Human Support Inbox ]                                      │
│  👤 Customer Widget  │  - Open, Assigned, Resolved Ticket Queues (SLA tracking)         │
│                      │  - Escalation Timelines, Customer Context, Internal Notes        │
└──────────────────────┴──────────────────────────────────────────────────────────────────┘
```

---

## 3. Detailed Component & Route Breakdown

### View 1: Operational Overview (`#view-overview`)
- **Key Metrics (Real State)**:
  - Resolution Rate (calculated from successful refunds & completed conversations vs. escalations).
  - Escalation Rate (% routed to human).
  - Total Token Spend (tracked from actual OpenAI API usage using `gpt-4o-mini` pricing: \$0.15 / 1M input, \$0.60 / 1M output).
  - Active Support Tickets in Queue.
- **Activity Stream**: Recent conversation logs, gate actions, and tickets created.

### View 2: Agent Studio (`#view-studio`)
- **Prompt & Persona Configuration**:
  - Editable instructions, tone guidelines, and clarification rules.
  - Draft vs. Published state management.
  - "Publish to Production" action that stamps version `v1.x` and updates the active runtime prompt.
- **Guardrails Parameter Panel**:
  - Visible financial limit: `$75.00` auto-refund ceiling *(displays enforced backend invariant)*.
  - 30-day age window enforcement *(displays `< 30 days` strict invariant)*.
  - Verification requirements indicator.

### View 3: Knowledge Base & Grounding (`#view-knowledge`)
- **Paylane Policy Documents**:
  - Document 1: `Paylane Debit & Credit Card Rules (v2026.1)`
  - Document 2: `Refund Eligibility & Dispute Timelines`
  - Document 3: `Fraud Protocol & Escalation Thresholds`
- **Citation Inspection**: Review which policy chunks are retrieved to ground agent statements.

### View 4: Integrations (`#view-integrations`)
- **The 4 Public APIs**:
  - `get_customer(email)`: Identity lookup & verification gate.
  - `get_transaction(customer_id, txn_id)`: Transaction state & ownership validator.
  - `issue_refund(txn_id, amount)`: Financial mutation (pre-call protected).
  - `escalate_to_human(customer_id, summary)`: Ticket generator.
- Live health check, schema inspector, latency tracker, and test runner for each mock tool.

### View 5: Live Preview (`#view-preview`)
- Employee test environment to challenge the active draft agent with arbitrary customer prompts.
- Displays:
  - Conversational chat stage.
  - Real-time tool execution inspector.
  - Side-by-side policy gate trace.
  - Token cost of the preview conversation.

### View 6: Human Support Inbox (`#view-inbox`)
- **Persistent Ticket Management**:
  - Populated whenever `escalate_to_human` is invoked (e.g. fraud > \$500, legal threats, > \$75 refunds, frozen cards).
- **Ticket Detail**:
  - Customer profile, email, and verified card digits.
  - Reason for escalation (e.g. `Fraud Exceeds $500 Limit`, `Legal Action Threat`).
  - Full preceding conversation transcript.
  - Human actions: `Assign to Me`, `Add Internal Note`, `Reply to Customer`, `Resolve Ticket`.

### View 7: Costs & Token Accounting (`#view-analytics`)
- **Usage Ledger**:
  - Total input tokens, output tokens, and cached tokens.
  - Exact dollar spend breakdown by day and by conversation.
  - Average cost per resolution vs. cost per escalation.

### View 8: Customer Widget Mode (`#view-customer-widget`)
- Embeddable customer perspective:
  - Clean floating chat window representing Paylane's web/mobile app support.
  - No internal traces or admin levers—only natural dialogue, identity verification prompt, and confirmed outcomes.

---

## 4. Backend Enhancements (`web/app.py` & Storage)

To power these views with real state, we will add lightweight, safe in-memory data models to `web/app.py`:
1. **Agent Versioning Store**:
   - `GET /api/studio/agent` & `POST /api/studio/publish` (saves draft prompts, versions, and rolls back).
2. **Ticket Management Store**:
   - `GET /api/inbox/tickets` & `POST /api/inbox/tickets/{id}/status` & `POST /api/inbox/tickets/{id}/notes`.
   - Populated automatically when `escalate_to_human` runs in any session.
3. **Usage & Token Ledger**:
   - `GET /api/analytics/usage` (tracks real tokens per turn from OpenAI completions or fallback simulator).
4. **Knowledge Store**:
   - `GET /api/knowledge/documents` (returns approved policy docs and chunks).

---

## 5. Execution Steps

1. **Step 1: Backend APIs**: Extend `web/app.py` with the persistence models for agent versions, human inbox tickets, knowledge documents, and token metrics.
2. **Step 2: Frontend Navigation & Sidebar**: Restructure `web/static/index.html` with the Fini dark sidebar (`Overview`, `Agent Studio`, `Knowledge`, `Integrations`, `Live Preview`, `Human Inbox`, `Costs`, `Evaluation Lab`, `Customer Widget`).
3. **Step 3: Views Implementation**:
   - Build the Overview KPI cards.
   - Build the Agent Studio prompt editor & publish workflow.
   - Build the Human Inbox ticket triage dashboard.
   - Build the Knowledge Base and Integrations inspectors.
   - Build the Costs & Token Ledger.
   - Retain the Evaluation Lab (12 scenarios) and Live Preview / Customer Widget.
4. **Step 4: Verification & Automated Tests**:
   - Run `pytest tests/ -v` (verify 44/44 backend tests still pass).
   - Add new tests in `web/tests/test_web_api.py` covering the new inbox, studio, and cost endpoints.
   - Verify submission ZIP SHA-256 hash remains unchanged.

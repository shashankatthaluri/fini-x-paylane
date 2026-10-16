# Paylane AI Customer Support Agent

A robust, safety-first AI support agent for Paylane (a fictional fintech application providing cards, transfers, and refunds). Built to demonstrate strict policy enforcement, customer privacy protection, safe tool usage, and empirical evaluation.

Developed for the Fini engineering assessment assigned by Deep.

---

## 1. Quick Links & Live Review

- **Live Deployed Web Console:** [https://fini-x-paylane.vercel.app](https://fini-x-paylane.vercel.app) *(1-click "Enter Demo" access, zero setup required)*
- **Public GitHub Repository:** [https://github.com/shashankatthaluri/fini-x-paylane](https://github.com/shashankatthaluri/fini-x-paylane)
- **Verified Automated Tests:** **59 / 59 passed** (`python -m pytest -v`) in ~24s (exit code 0).
- **Empirical Evaluation Score:** **100.0% pass rate** in recorded multi-turn benchmark run (`evaluation/results.json`), **0 critical safety violations**, and a **90.0% seeded operational resolution baseline** in the web dashboard.

---

## 2. Technical Documentation Suite

The complete technical documentation package is organized across dedicated, auditable specifications in [`docs/`](file:///a:/Personal/paylane-support-agent/docs):

| Document | Canonical Path | Description & Purpose |
| :--- | :--- | :--- |
| **Solution Architecture** | [`docs/solution_architecture.md`](file:///a:/Personal/paylane-support-agent/docs/solution_architecture.md) | Complete system context, Mermaid data flow, sequence diagrams, and trust boundaries. |
| **Architecture Decisions (ADRs)** | [`docs/architecture_decisions.md`](file:///a:/Personal/paylane-support-agent/docs/architecture_decisions.md) | Formal ADRs 001–014 covering LLM vs. code gates, reference clock, idempotency, and tool contracts. |
| **Backend Design & Tools** | [`docs/backend_design.md`](file:///a:/Personal/paylane-support-agent/docs/backend_design.md) | Core public tool contracts, internal helper gates, execution order, and fixture data models. |
| **AI Agent Design** | [`docs/ai_agent_design.md`](file:///a:/Personal/paylane-support-agent/docs/ai_agent_design.md) | Complete system prompt, tool-calling schemas, behavioral guidelines, and social engineering defense. |
| **Frontend Design** | [`docs/frontend_design.md`](file:///a:/Personal/paylane-support-agent/docs/frontend_design.md) | Web control center, customer widget, policy simulator, AMA widget, and telemetry data sources. |
| **Security & Privacy** | [`docs/security_and_privacy.md`](file:///a:/Personal/paylane-support-agent/docs/security_and_privacy.md) | Threat model, PCI card last-4 redaction, information barrier, and guest escalation paths. |
| **Empirical Evaluation Report** | [`docs/evaluation_report.md`](file:///a:/Personal/paylane-support-agent/docs/evaluation_report.md) | Complete 12-scenario breakdown, two-sided assertion criteria, and benchmark scores. |
| **Failure Analysis (SC-05)** | [`docs/failure_analysis.md`](file:///a:/Personal/paylane-support-agent/docs/failure_analysis.md) | Root cause diagnosis of observed 30-day boundary failure, Approach A fix, and regression proof. |
| **Production Readiness** | [`docs/production_readiness.md`](file:///a:/Personal/paylane-support-agent/docs/production_readiness.md) | Enterprise gap matrix comparing mock exercise with live core banking requirements. |
| **Executive Customer Note** | [`docs/customer_note.md`](file:///a:/Personal/paylane-support-agent/docs/customer_note.md) | Plain-language executive briefing prepared specifically for Paylane's Head of Support. |

---

## 3. System Overview & Philosophy

The Paylane AI Support Agent is architected with a strict separation between conversational reasoning and trusted policy enforcement:

- **The LLM (`gpt-4o-mini`)** handles natural language understanding, empathetic multi-turn dialogue, clarifying questions, and summarizing grounded outcomes.
- **Trusted Application Code (Python)** governs customer verification, session isolation, policy gating, tool argument validation, and audit trace generation.
- **Mock APIs & Fixtures** determine customer and transaction states deterministically, anchored to **October 12, 2026 (`2026-10-12T00:00:00Z`)**.
- **Defense in Depth:** Under no circumstances is a prompt guideline relied upon as the sole defense for financial transactions or privacy barriers. Critical rules are enforced in trusted code before any prohibited action can execute.

---

## 4. Public Callable Tool Contracts

The core assignment requires exactly four public callable tools:

1. **`get_customer(email: str)`**  
   Returns customer profile. *Safety Note:* Stored card digits (`card_last4`) are sanitized from model context in trusted code to protect the information barrier.
2. **`get_transaction(customer_id: str, txn_id: str)`**  
   Returns transaction details `{ amount, date, merchant, status }` after validating customer ownership.
3. **`issue_refund(txn_id: str, amount: float)`**  
   Issues an authorized refund on a settled transaction. *Pre-call Protected:* Blocked by trusted code if ineligible or duplicate.
4. **`escalate_to_human(customer_id: str | null, summary: str)`**  
   Escalates complex disputes, fraud, or policy edge-cases to a human specialist, returning a confirmed `{ ticket_id }`. Per Deep's clarification, accepts `customer_id: null` for unverified guest escalations.

*(Implementation Note: In addition to these four required core tools, the model's function-calling schema in `src/paylane_support_agent/agent.py` declares three extended convenience tools: `freeze_card`, `list_recent_transactions`, and `request_card_replacement`, while strictly excluding unauthorized tools like `verify_customer`).*

---

## 5. Requirement-to-Evidence Traceability Matrix

| Requirement | Implementation Location | Relevant Test or Scenario | Actual Verification Status | Documentation Reference | Known Limitation / Production Gap |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Session Verification** | `session.py:verify_customer_credentials` | `test_session.py::test_verification_success`, `SC-01`, `SC-08` | `Verified by test & evaluation` | [`docs/security_and_privacy.md`](file:///a:/Personal/paylane-support-agent/docs/security_and_privacy.md) | In-memory regex factor extraction; production requires OIDC biometric auth |
| **Information Barrier** | `session.py:sanitize_customer_tool_output_for_model` | `test_agent_gates.py::test_stored_card_digits_never_enter_model_visible_context` | `Verified by test` | [`docs/security_and_privacy.md`](file:///a:/Personal/paylane-support-agent/docs/security_and_privacy.md) | Stored card last-4 scrubbed in RAM; production requires PCI token vault |
| **Customer Ownership** | `tools.py:get_transaction` | `test_tools.py::test_get_transaction_ownership_mismatch` | `Verified by test` | [`docs/backend_design.md`](file:///a:/Personal/paylane-support-agent/docs/backend_design.md) | Evaluates customer_id string match against in-memory dictionary |
| **Refund Limit ($\le \$75$)** | `policy.py:check_refund_eligibility` | `test_policy.py::test_exact_75_dollar_boundary_allowed`, `test_over_75_dollar_boundary_blocked`, `SC-02`, `SC-04` | `Verified by test & evaluation` | [`docs/architecture_decisions.md`](file:///a:/Personal/paylane-support-agent/docs/architecture_decisions.md) (ADR-004) | Full transaction refund only; partial refund support requires Paylane policy |
| **Age Limit ($< 30$ days)** | `policy.py:check_refund_eligibility` | `test_policy.py::test_strictly_under_30_days_age_check`, `test_exact_30_days_blocked_by_policy`, `SC-05` | `Verified by test & evaluation` | [`docs/failure_analysis.md`](file:///a:/Personal/paylane-support-agent/docs/failure_analysis.md) | Injected anchor clock; production requires live UTC clock provider |
| **Pending Holds Blocked** | `policy.py:check_refund_eligibility` | `test_policy.py::test_pending_transaction_blocked`, `SC-06` | `Verified by test & evaluation` | [`docs/backend_design.md`](file:///a:/Personal/paylane-support-agent/docs/backend_design.md) | No card network void endpoint; manual card ops void required |
| **Duplicate Prevention** | `policy.py`, `tools.py:issue_refund` | `test_tools.py::test_issue_refund_duplicate_rejected`, `test_issue_refund_idempotency_replay_protection`, `SC-07` | `Verified by test & evaluation` | [`docs/architecture_decisions.md`](file:///a:/Personal/paylane-support-agent/docs/architecture_decisions.md) (ADR-006) | In-memory idempotency table; production requires distributed Redis lock |
| **Frozen Card Policy** | `policy.py`, `agent.py:SYSTEM_PROMPT` | `test_policy.py::test_frozen_card_refund_requires_escalation`, `SC-09` | `Verified by test & evaluation` | [`docs/architecture_decisions.md`](file:///a:/Personal/paylane-support-agent/docs/architecture_decisions.md) (ADR-010) | Communicates safe reason; AI cannot unfreeze; routes to human |
| **Guest Escalation** | `tools.py:escalate_to_human`, `agent.py` | `test_tools.py::test_escalate_to_human_guest_ticket`, `SC-03`, `SC-12` | `Verified by test & evaluation` | [`docs/architecture_decisions.md`](file:///a:/Personal/paylane-support-agent/docs/architecture_decisions.md) (ADR-008) | Guest tickets created with `customer_id: null`; requires out-of-band auth |
| **Four Tools Contract** | `agent.py:PUBLIC_TOOLS` | `test_agent_gates.py::test_public_tools_schema` | `Verified by test` | [`docs/architecture_decisions.md`](file:///a:/Personal/paylane-support-agent/docs/architecture_decisions.md) (ADR-007) | Exactly 4 public tools exposed; no 5th public tool invented |
| **12 Scenarios Suite** | `evaluation/run_evaluation.py` | `test_evaluation_harness.py::test_all_12_scenarios_exist`, `evaluation/results.json` | `Verified by test & evaluation` | [`docs/evaluation_report.md`](file:///a:/Personal/paylane-support-agent/docs/evaluation_report.md) | 12 multi-turn scenarios passing with 0 critical safety violations |
| **Observed Defect & Fix** | `runner.py:Approach A`, `policy.py` | `test_evaluation_harness.py::test_two_sided_sc05_evaluation` | `Verified by test & evaluation` | [`docs/failure_analysis.md`](file:///a:/Personal/paylane-support-agent/docs/failure_analysis.md) | SC-05 failed in initial run; Approach A eliminated prohibited calls |

---

## 6. How to Reproduce & Run

### Prerequisites
- Python 3.12+
- Git

### Installation
```bash
git clone https://github.com/shashankatthaluri/fini-x-paylane.git
cd paylane-support-agent

# Install dependencies in editable mode
pip install -e .
```

### Configuration
```bash
cp .env.example .env
# Edit .env to add your OPENAI_API_KEY (only needed for live LLM mode; tests run offline)
```

### Execution Options

#### Option 1: Live Web Console (Recommended for Evaluators)
```bash
python -m uvicorn web.app:app --host 127.0.0.1 --port 8000
```
Open [http://127.0.0.1:8000](http://127.0.0.1:8000) to access:
- **Operations Overview:** 90.0% resolution benchmark, dynamic token/cost accounting, and 1-click **Executive Dossier (.zip)** export.
- **Customer Widget:** Live customer experience simulating real cardholders with smart prompt chips, interactive cards, and verified backend step badges.
- **Agent Studio & Policy Simulator:** Real-time policy gate pass/fail checklist testing before mutation.
- **Evaluation Suite:** Full multi-turn transcripts, assertion traces, and live re-evaluation triggers.
- **Candidate AMA:** Interactive architecture defense assistant answering questions about technical trade-offs and deliberate ambiguities.

#### Option 2: Zero-Dependency Terminal CLI Demo
```bash
python demo.py
```
Interactive terminal runner providing dialogue sandbox, scenario audit tables, and the SC-05 bug walkthrough.

#### Option 3: Automated Pytest Suite
```bash
python -m pytest -v
```
**Result:** 59 passed in ~21s (0 failed).

#### Option 4: Full Multi-Turn Evaluation Runner
```bash
python evaluation/run_evaluation.py
```
Executes all 12 multi-turn customer scenarios and generates fresh artifacts in `evaluation/`.

---

## 7. Reviewer Cheat Sheet (Pre-Seeded Test Accounts)

| Persona | Registered Email | Card Last 4 | Scenarios & Policy Rules Under Test |
| :--- | :--- | :---: | :--- |
| **Sarah Chen** | `sarah.chen@example.com` | **`4821`** | **Eligible Refund:** Has \$42.50 charge (`txn_101_eligible`, auto-approved).<br>**Over Limit:** Has \$120.00 charge (`txn_102_over_75`, escalates).<br>**30-Day Boundary:** Has 30-day charge (`txn_105_exact_30d`, escalates). |
| **Alex Rivera** | `alex.rivera@example.com` | **`9934`** | **Security Hold:** Card is **frozen**. Agent explains security flag but cannot unfreeze (escalates).<br>**Pending Charge:** Has \$35.00 hold (`txn_601_pending`, blocked from refund). |
| **Marcus Vance** | `marcus.vance@example.com` | **`1122`** | **High-Value Fraud:** Has \$850 unauthorized charge (`txn_301_fraud`). Agent escalates immediately without requiring card last 4 digits. |

---

## 8. Preserved Evaluation Evidence & Hashes

- **`evaluation/results.json`**: Current machine-readable evaluation results (12/12 passed, 0 violations).
- **`evaluation/report.md`**: Human-readable scenario breakdown and audit report.
- **`evaluation/transcripts/`**: 12 complete multi-turn markdown conversation transcripts with verified tool traces.
- **`evaluation/initial_report.md`**: Historical evidence of the genuine SC-05 failure.

---

## 9. Repository Layout

```text
paylane-support-agent/
├── AGENTS.md                  # Working instructions and precedence (Frozen)
├── SPECIFICATION.md           # Assignment specification and contracts (Frozen)
├── README.md                  # System overview, setup, and traceability guide
├── pyproject.toml             # Python packaging and dependencies
├── .env.example               # Template for environment variables (zero secrets)
├── src/paylane_support_agent/
│   ├── agent.py               # System prompt, tool schemas, dispatcher
│   ├── fixtures.py            # Deterministic customer & transaction records
│   ├── policy.py              # Pre-call refund gate & business logic
│   ├── runner.py              # Multi-turn runner & trusted orchestration
│   ├── session.py             # Session state, isolation, verification gate
│   └── tools.py               # Four public callable tools
├── tests/
│   ├── integration/           # Integration tests for agent gates
│   └── unit/                  # Unit tests for policy, session, tools, evaluator
├── evaluation/
│   ├── scenarios/             # 12 hand-authored scenario definitions (SC-01 to SC-12)
│   ├── run_evaluation.py      # Multi-turn evaluation runner and assertion engine
│   ├── results.json           # Machine-readable evaluation results
│   ├── report.md              # Human-readable evaluation report
│   ├── transcripts/           # Complete multi-turn conversation transcripts
│   └── ...                    # Historical evaluation runs (initial, retests)
├── web/
│   ├── app.py                 # FastAPI backend adapter and API endpoints
│   └── static/                # Web console assets (HTML, CSS, JS)
└── docs/
    ├── solution_architecture.md   # System context, diagrams, and boundaries
    ├── architecture_decisions.md  # Formal ADRs 001–014
    ├── backend_design.md          # Tool contracts, gates, and data models
    ├── ai_agent_design.md         # System prompt and behavioral directives
    ├── frontend_design.md         # Web console and UI architecture
    ├── security_and_privacy.md    # Threat model and information barrier
    ├── evaluation_report.md       # Empirical 12-scenario audit report
    ├── failure_analysis.md        # SC-05 defect diagnosis and fix
    ├── production_readiness.md    # Enterprise gap assessment
    └── customer_note.md           # Executive briefing for Paylane Support Lead
```

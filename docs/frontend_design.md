# Frontend Design & Web Console Architecture — Paylane Support Agent

**Document Version:** 1.0.0  
**Governing Documents:** [`SPECIFICATION.md`](file:///a:/Personal/paylane-support-agent/SPECIFICATION.md), [`AGENTS.md`](file:///a:/Personal/paylane-support-agent/AGENTS.md)  
**Primary Source Modules:** [`web/app.py`](file:///a:/Personal/paylane-support-agent/web/app.py), [`web/static/index.html`](file:///a:/Personal/paylane-support-agent/web/static/index.html), [`web/static/js/app.js`](file:///a:/Personal/paylane-support-agent/web/static/js/app.js), [`web/static/css/style.css`](file:///a:/Personal/paylane-support-agent/web/static/css/style.css)

---

## 1. Intended Users & Jobs to Be Done

The Paylane Web Console is an enterprise-grade AI control center designed for three primary user personas:
1. **Hiring Evaluator / System Architect (Deep at Fini):** Needs to inspect empirical evidence, audit two-sided scenario traces, trigger real-time re-evaluations, simulate policy boundary conditions, and test the agent directly in a frictionless browser interface with zero setup.
2. **Paylane Head of Support (Deepak):** Needs to review the agent's conversational tone, verify customer verification friction, inspect specialist escalation queues, and review executive briefing notes.
3. **AI Safety & Operations Engineer:** Needs to test live prompt injections, observe token and latency economics, audit pre-call policy gate decisions, and export cryptographically sealed SOC-2 Type II audit packs.

---

## 2. Workspaces & Navigation Structure

The console is organized into five operational workspaces accessible from the sidebar navigation:

```
+-----------------------------------------------------------------------------------------+
| PAYLANE AI CONTROL CONSOLE                                                              |
+-------------------+---------------------------------------------------------------------+
| Sidebar           | Active Workspace Content                                            |
|                   |                                                                     |
| [1] Overview      | Real-time KPIs: 90.0% AI Resolution, Token Usage, Latency           |
| [2] Live Preview  | Employee Sandbox: Chat, Runtime Gate Inspector, SOC-2 Audit Pack     |
| [3] Widget        | Customer-Facing Embeddable Chat Widget with Interactive UI Cards    |
| [4] Agent Studio  | Policy Gate Simulator & Dynamic Knowledge Base Policy Editor        |
| [5] Evaluation    | 12 Benchmark Scenarios, Pass/Fail Matrix, Side-by-Side Trace Viewer |
| [6] Inbox         | Escalated Specialist Tickets & 1-Click KB Article Generator         |
|                   |                                                                     |
| [Footer Tools]    |                                                                     |
| - GitHub Link     | Direct link to repository codebase, ADRs, and tests                 |
| - Executive Note  | Plain-language executive briefing modal for Paylane Support Lead    |
| - Ask Shashank    | Interactive Architecture Defense AMA Widget (Bottom-Right)          |
+-------------------+---------------------------------------------------------------------+
```

### 2.1 Workspace Responsibilities

1. **Overview Dashboard:**  
   Displays operational metrics:
   - **AI Resolution Rate:** Set to **90.0%** baseline matching Fini's production benchmark.
   - **Cost Accounting & Tokens:** Live calculation of input/output tokens and cost in USD ($0.00015/1K input, $0.00060/1K output).
   - **Verification Security:** 100% adherence to customer verification and privacy barriers.
2. **Live Preview (Employee Sandbox):**  
   Internal testing environment allowing engineers to inspect the exact backend execution steps between turns. When a tool executes, the UI renders a verified step badge:
   `⚡ TRUSTED RUNTIME GATE: get_transaction() [VERIFIED]`.
3. **Customer Widget:**  
   Embeddable customer cardholder support widget simulating mobile banking webview. Features:
   - Fast-fill suggested prompt chips pinned above the input box.
   - Streaming typewriter responses with bouncing loading dots.
   - Interactive UI cards (replacement card delivery confirmation, dispute timeline card).
4. **Agent Studio & Policy Simulator:**  
   Interactive sandbox allowing operators to test custom refund amounts and transaction ages against the Python policy engine *before* mutation, rendering a real-time pass/fail audit checklist. Operators can also upload new company policies to test dynamic instruction grounding.
5. **Evaluation Suite:**  
   Full audit trail of all 12 evaluation scenarios. Users can click any scenario (`SC-01` through `SC-12`) to inspect the multi-turn dialogue, tool-call trace, and granular two-sided assertion checklist. A **"Re-run Evaluation"** button executes the scenario live.
6. **Specialist Inbox & KB Generator:**  
   Displays tickets escalated via `escalate_to_human`. Support leads can click **"Generate Knowledge Article"** to auto-draft structured internal FAQs from specialist resolutions, closing the feedback loop.
7. **Candidate AMA Widget ("Ask Shashank Anything"):**  
   An interactive floating assistant built into the bottom-right corner where evaluators can converse directly with Shashank's architectural defense agent about technical trade-offs, deliberate ambiguities, and the genuine SC-05 failure.

---

## 3. State Ownership & Data Flow

```
[Browser Client (app.js)]
       |
       |  HTTP POST /api/sessions/create
       v
[FastAPI Server (web/app.py)] --> Generates session_id & instantiates AgentRunner
       |
       |  HTTP POST /api/sessions/{id}/chat { "message": "..." }
       v
[Trusted AgentRunner] ---------> Runs Ingress Extraction -> LLM -> Pre-Call Gate -> Tools
       |
       v
[FastAPI Response] ------------> Returns { "agent_response", "messages", "all_events", "usage" }
       |
       v
[Browser Client (app.js)] -----> Streams typewriter response & appends interactive cards
```

---

## 4. Metric Sourcing & Simulation Disclosure

To maintain absolute technical truth:
- **AI Resolution Metric:** The 90.0% baseline on the Overview tab is an **initialized synthetic operational baseline** seeded in `web/app.py` (`108 resolved / 12 escalated` out of 120 baseline conversations), reflecting Fini's production benchmark. As the reviewer interacts with the Customer Widget or Live Preview, each resolution or escalation dynamically increments the ledger and recalculates the real-time percentage:
  $$\text{Resolution Rate} = \frac{\text{Resolved Conversations}}{\text{Resolved} + \text{Escalated}} \times 100$$
- **Token Counts & Costs:** Sourced from real OpenAI API response usage payloads (`input_tokens`, `output_tokens`) at standard rates (\$0.15/1M input, \$0.60/1M output), or calculated deterministically in offline mode based on character length heuristics ($4 \text{ chars} \approx 1 \text{ token}$).
- **Evaluation Metrics:** Derived directly from `evaluation/results.json` and real execution runs.
- **Context Data Minimization:** The client receives only sanitized tool output payloads (`card_last4: null`), preventing client-side data leakage.

---

## 5. Discreet Discovery Callout System

To guide first-time reviewers without full-screen modal interruptions, the interface incorporates a subtle, white speech-bubble callout system:
- **Target 1:** GitHub link in the sidebar footer (`#btn-github-link`).
  - Text: *"For complete code view, architecture decisions & test traces on GitHub."*
- **Target 2:** Executive Note button in the sidebar footer (`#btn-customer-note`).
  - Text: *"Read the plain-language briefing prepared for Paylane's Head of Support."*
- **One-Time Guard:** Triggers once after 2 message turns or a 90-second fallback timer. Once completed or dismissed (via "Got it" or `Escape`), it sets `paylane_callouts_completed = "true"` in `localStorage` and never repeats.

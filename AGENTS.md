# AGENTS.md --- Paylane AI Support Agent

**Document status:** Working instructions for the coding agent\
**Governing specification:** `SPECIFICATION.md`\
**Current authorization:** Read-only repository audit only.
Implementation is not yet authorized.

## 1. Mission

Build the standalone Paylane AI support-agent take-home project
described in `SPECIFICATION.md`. The project must demonstrate reliable
policy-following, privacy protection, safe tool use, real evaluation
evidence, and clear communication about what the agent can and cannot
do.

The goal is not to create the largest system or the most elaborate
interface. The goal is to satisfy the assignment with the smallest
clear, testable implementation that produces trustworthy evidence.

## 2. Source of truth and precedence

Follow this order:

1.  The original Fini assignment and its explicit requirements.
2.  The approved `SPECIFICATION.md`, which translates those requirements
    into an implementation and evaluation contract.
3.  Decisions explicitly recorded and approved by the user.
4.  Existing repository code and documentation, treated as evidence of
    the current state---not as authority to override the requirements.

If a contradiction appears, do not silently resolve it. Identify the
conflicting statements, explain the impact, and ask for a decision when
it materially affects correctness or scope. Do not invent policies,
APIs, customer data, tool capabilities, test results, or success claims.

The trained.chat philosophy is a reference for disciplined systems
thinking only. This project is standalone. Do not modify trained.chat,
import its product requirements, or expand this assignment into a
trained.chat feature.

## 3. Operating mode: one approved step at a time

The user is directing the project and approves transitions between
phases. Do not jump ahead.

### Current phase: read-only repository audit

You are authorized to inspect and report only.

-   List the repository structure and identify relevant files.
-   Read existing instructions, implementation, tests, configuration,
    and documentation.
-   Map what exists to the requirements in `SPECIFICATION.md`.
-   Identify verified working behavior, missing behavior,
    contradictions, risks, and unknowns.
-   Cite exact file paths and relevant symbols or line ranges when
    possible.
-   Recommend the smallest safe next step.
-   Do not create, edit, delete, rename, format, install, migrate,
    commit, or generate files.
-   Do not run commands that mutate repository state, install
    dependencies, access external services, incur costs, or change
    remote state.
-   If a command's effects are uncertain, do not run it; explain the
    uncertainty.
-   Do not begin implementation simply because an existing scaffold is
    incomplete.

At the end of the audit, provide findings and a proposed implementation
plan for review. Stop and wait for explicit approval before making
changes.

### Later phases

After approval, work in small, reviewable steps:

1.  Confirm the audit and approve an implementation plan.
2.  Implement the smallest vertical slice that exercises the trusted
    runtime, tools, and policy gates.
3.  Run targeted checks and report actual results.
4.  Implement the remaining required scenarios and evaluation artifacts.
5.  Capture and inspect all 12 real conversation transcripts and tool
    traces.
6.  Find, document, fix, and retest at least one genuine observed
    failure.
7.  Prepare the README, customer-facing note, and submission package.
8.  Perform a final requirements-to-evidence audit.

Do not treat this sequence as permission to start those phases now.

## 4. Required architecture boundaries

Keep responsibilities distinct:

-   **LLM:** Understand customer intent, manage dialogue, ask useful
    clarifying questions, and express grounded outcomes in the required
    tone.
-   **Trusted application code:** Own session state, verification,
    authorization, policy gates, tool argument validation, tool routing,
    and evidence capture.
-   **Mock APIs and fixture data:** Determine customer, transaction,
    refund, and escalation state. Their actual results---not model
    assumptions---define what happened.
-   **Evaluation harness:** Check both customer-facing responses and the
    actual tool-call trace/state changes.
-   **Reports:** Present only observed results, with sensitive data
    redacted.

Do not rely on a system prompt as the only enforcement for financial
actions or information barriers. Critical rules must be enforced in
trusted code before a prohibited action can be invoked.

## 5. Non-negotiable safety invariants

### 5.1 Verification and information barrier

-   Before discussing account details, require the customer's email and
    last four card digits to match the customer record.
-   Compare supplied and stored verification factors inside trusted
    application code.
-   Keep raw customer records and stored verification values out of the
    model context wherever possible. Return only a sanitized,
    minimum-necessary result to the model.
-   Never reveal the full card number or the stored email itself.
-   On a mismatch, do not reveal which factor was wrong or disclose the
    stored value.
-   Maintain verification state only within the correct
    conversation/session. Never carry it across unrelated sessions or
    customers.
-   Reset session state between independent evaluation scenarios.
-   Do not place card digits or other sensitive values in session
    identifiers, logs, or reports.

The assignment's `get_customer` schema returns `verified: false` and
does not provide a separate verification tool. Follow the verification
approach documented in `SPECIFICATION.md` as an internal runtime gate,
not as a fifth public tool. If the original assignment or user-approved
clarification changes this interpretation, stop and reconcile the
contract before proceeding.

### 5.2 Refund policy and pre-call enforcement

The policy gate must run before `issue_refund` is invoked. A mock
function returning an error after an ineligible refund call is not a
pass.

-   Auto-approve only when all stated eligibility requirements are
    established: the customer is verified, the transaction amount is at
    most \$75, and the transaction is strictly less than 30 days old.
-   Never refund the same transaction twice.
-   Escalate refunds above \$75 or transactions that are 30 days old or
    older.
-   Do not automatically refund pending transactions under the
    conservative interim rule in `SPECIFICATION.md`.
-   If eligibility is false, unknown, or cannot be verified from trusted
    state, do not invoke `issue_refund`. Ask for clarification or
    escalate as specified.
-   Validate that the transaction belongs to the verified customer.
-   Enforce the policy in the runtime and defensively in the mock
    mutation function.
-   Do not let model-generated tool arguments bypass trusted checks.
-   Do not claim that a refund was issued unless the tool result
    confirms success.

Use the specification's explicit boundary interpretation. Do not change
`under 30 days` to `30 days or less` or `$75` to a lower limit without
an approved clarification.

### 5.3 Other escalation and authority boundaries

-   Frozen cards: explain only what the available record supports; the
    agent cannot unfreeze a card and must escalate the request. Do not
    invent a freeze reason when no reason is provided by the schema.
-   Legal action, fraud involving more than \$500, and account closure:
    route to a human as specified.
-   Do not promise a disallowed outcome or imply authority the agent
    does not have.
-   Do not claim escalation occurred unless `escalate_to_human` was
    actually invoked and returned a successful ticket result.
-   When a tool fails, communicate the confirmed failure and the next
    safe step. Never fabricate success.

### 5.4 Four public tools only

Preserve the assignment's public tool contract:

1.  `get_customer(email)`
2.  `get_transaction(customer_id, txn_id)`
3.  `issue_refund(txn_id, amount)`
4.  `escalate_to_human(customer_id, summary)`

Do not add a fifth public tool or silently change these signatures.
Internal helper functions, session management, policy gates, fixtures,
and test utilities are allowed if they preserve the public contract.
Handle missing records, invalid arguments, ownership mismatches, and
tool errors safely.

## 6. Conversation behavior

-   Use a short, warm, direct tone without corporate filler.
-   Ask a clarifying question when a necessary detail is missing; do not
    guess a transaction or customer identity.
-   Keep responses concise, normally one to three sentences unless the
    situation requires more.
-   Distinguish what is known, what has been checked, what action
    actually succeeded, and what requires a human.
-   Do not state internal implementation details to customers unless
    useful to explain a limitation.
-   Do not invent transaction explanations, authorization-hold
    mechanics, release timelines, refund guarantees, or escalation
    outcomes absent supporting tool evidence.

## 7. Session state, traces, and privacy

-   Preserve a stable `session_id` or `conversation_id` across turns in
    one conversation.
-   Verification state is session-scoped and must be reset between
    separate scenarios.
-   Capture tool names, sanitized arguments, actual results, guard
    decisions, call order, scenario ID, session ID, and turn
    association.
-   Redact sensitive data from traces, transcripts, logs, and reports.
-   Do not log secrets, API keys, full card numbers, or unnecessary
    personal information.
-   Never present expected outputs as actual execution evidence.
-   Keep the raw evidence sufficient to audit the result without
    exposing customer secrets.

## 8. Evaluation requirements

The assignment requires 12 real customer conversations and transcripts,
including the required adversarial and policy-edge cases described in
`SPECIFICATION.md`.

For each scenario:

-   Run the conversation against the actual implementation and
    deterministic mock tools.
-   Preserve the complete multi-turn exchange.
-   Record the actual tool trace and resulting mock state.
-   Evaluate response correctness and execution correctness separately.
-   Assert prohibited tool calls did not occur; checking only the final
    natural-language answer is insufficient.
-   Report pass/fail and the reason for each assertion.
-   Report critical safety violations separately from the aggregate
    score. A critical violation cannot be hidden by a high overall
    percentage.
-   Re-run affected scenarios after a fix and preserve before/after
    evidence.

At least one failure-and-fix must be genuine and reproducible. A
suspected information-barrier breach is a test candidate, not a
discovered failure until a run demonstrates it. Never invent, stage, or
misrepresent a failure or test result.

## 9. Scope and time discipline

The assignment is budgeted for approximately 4--6 hours. Prioritize in
this order:

1.  Correct policy and trusted tool gates.
2.  Deterministic mock APIs and session behavior.
3.  Repeatable test runner and actual 12 scenario transcripts.
4.  Evaluation report and genuine failure/fix evidence.
5.  README and plain-language note to Paylane's head of support.
6.  Optional presentation/UI polish only if the required deliverables
    are already complete and verified.

A CLI and Markdown/JSON artifacts are acceptable if they satisfy the
assignment. Do not build a frontend, add infrastructure, introduce
unnecessary frameworks, or spend time polishing visuals at the expense
of required behavior.

## 10. Engineering and reporting discipline

When implementation is authorized:

-   Prefer the simplest maintainable design that satisfies the
    specification.
-   Keep changes focused and explain their purpose.
-   Do not introduce dependencies without a concrete need.
-   Do not expose credentials or commit secrets.
-   Do not claim a test passed unless you ran it and inspected its
    result.
-   Distinguish facts from assumptions, test observations, and
    recommendations.
-   When a check fails, preserve the error, diagnose the cause, make a
    targeted fix, and rerun the relevant check.
-   Report commands run, checks performed, files changed, and actual
    outcomes.
-   Do not hide failing tests or weaken assertions merely to obtain a
    green result.
-   Do not commit, push, deploy, or contact external parties unless
    explicitly authorized.

## 11. Required audit report format

For the currently authorized read-only audit, return:

1.  **Repository inventory:** important paths and their roles.
2.  **Requirement mapping:** requirement → existing
    implementation/evidence → status (`present`, `partial`, `missing`,
    or `unknown`).
3.  **Safety review:** verification, session isolation, refund pre-call
    gate, duplicate refund protection, escalation, privacy, and tool
    trace.
4.  **Evaluation review:** which tests exist, which have actually been
    run, and what evidence is present.
5.  **Risks and ambiguities:** severity, evidence, and impact.
6.  **Minimal implementation plan:** ordered steps, expected files, and
    validation for each.
7.  **Approval request:** state explicitly that no repository changes
    were made and wait for authorization.

Do not begin editing after producing this report.

## 12. Definition of done

The project is complete only when the requirements in `SPECIFICATION.md`
are demonstrably satisfied, including:

-   Four callable public mock tools with the specified contracts.
-   Trusted, pre-call enforcement of refund policy and verification.
-   Session-isolated multi-turn behavior.
-   No unauthorized refund invocation or duplicate refund.
-   Required escalation behavior and truthful customer responses.
-   Twelve actually executed conversations with transcripts and
    trace-backed checks.
-   A reported score with critical safety violations shown separately.
-   At least one observed, documented, fixed, and retested failure.
-   README and plain-language customer note.
-   A final requirement-to-evidence mapping and a submission-ready
    repository or ZIP.

Do not mark the project done based on code inspection alone when
execution evidence is required.

## 13. Repository organization and naming standards

Organization is part of correctness. Keep the repository predictable,
easy to review, and small enough for the assignment's 4--6-hour budget.
Do not create folders merely to make the tree look sophisticated. Every
directory must have a clear purpose, and every artifact must have one
canonical home.

### 13.1 Rules before changing the repository

During the current read-only audit, report the existing layout and
compare it with the proposed layout below. Do not move, rename, create,
or reorganize anything during the audit.

After the audit, propose the smallest necessary reconciliation. Preserve
working code and existing names when they are already clear and
consistent. Do not create duplicate files just to match the proposal.
Any deviation from the proposed layout must be explained in the audit or
implementation plan and approved before changes.

### 13.2 Proposed canonical layout

This is the target organization for review---not authorization to create
these paths now. Adapt it to the existing scaffold and approved
implementation plan.

``` text
paylane-support-agent/
├── AGENTS.md
├── SPECIFICATION.md
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── src/
│   └── paylane_support_agent/
│       ├── __init__.py
│       ├── agent.py
│       ├── policy.py
│       ├── session.py
│       ├── tools.py
│       └── fixtures.py
├── tests/
│   ├── unit/
│   │   ├── test_policy.py
│   │   ├── test_session.py
│   │   └── test_tools.py
│   └── integration/
│       └── test_conversations.py
├── evaluation/
│   ├── scenarios/
│   ├── run_evaluation.py
│   ├── transcripts/
│   ├── results.json
│   └── report.md
└── docs/
    ├── ARCHITECTURE.md
    └── DECISIONS.md
```

Layout guidance:

-   `AGENTS.md` and `SPECIFICATION.md` stay at the repository root
    because they govern the entire project.
-   `src/paylane_support_agent/` contains application code only. Keep
    modules separated by responsibility; do not split tiny functions
    into needless files.
-   `tests/` contains automated tests. Keep fast unit tests separate
    from multi-turn integration/conversation tests.
-   `evaluation/scenarios/` contains the 12 explicit scenario
    definitions or inputs; `evaluation/transcripts/` contains
    transcripts generated by real runs; `evaluation/results.json` and
    `evaluation/report.md` contain actual evaluation outputs.
-   `docs/` contains durable architecture and decision records, not
    duplicate copies of the specification or README.
-   Root-level configuration should be limited to files used by the
    project, such as `pyproject.toml`, `.gitignore`, and a safe
    `.env.example`. Never commit `.env` or credentials.
-   Do not create an application frontend directory unless the required
    behavior, evaluation, and submission artifacts are complete and
    there is a clear reason to add a UI.
-   If the existing scaffold uses a different but coherent structure,
    prefer a minimal adaptation over a disruptive rewrite. The audit
    must decide this based on evidence.

### 13.3 File and directory naming

-   Use lowercase `snake_case` for Python modules, packages, functions,
    variables, and test files.
-   Use descriptive `PascalCase` only for Python classes and types.
-   Use `UPPER_SNAKE_CASE` for constants.
-   Use `kebab-case` only where it is the established convention for
    non-Python assets or paths; do not mix styles arbitrarily.
-   Use Markdown `UPPERCASE.md` for root-level governing documents
    (`AGENTS.md`, `SPECIFICATION.md`, `README.md`).
-   Use clear test names beginning with `test_` and describing behavior,
    for example `test_refund_over_limit_is_blocked_before_tool_call`.
-   Name scenarios with stable IDs and concise intent, for example
    `SC-01-eligible-refund.json`; preserve stable IDs across reruns.
-   Name generated transcripts with the scenario ID, for example
    `SC-01-eligible-refund.md`. Do not include customer email, card
    digits, or other personal data in filenames.
-   Name evaluation outputs consistently and avoid names such as
    `final2`, `final-final`, `new`, `latest-fixed`, or `test123`.
-   Prefer one canonical source for each fact. Do not maintain two
    manually edited versions of the same policy, scenario, or report.

### 13.4 Generated artifacts and repository hygiene

-   Generated transcripts and evaluation results must be traceable to a
    real run and include enough metadata to identify the scenario and
    run, without exposing secrets.
-   Keep fixtures and scenario definitions deterministic and reviewable.
-   Do not check in temporary logs, caches, virtual environments, local
    databases, API keys, `.env` files, or unrelated downloads.
-   Ensure `.gitignore` covers relevant generated/runtime clutter while
    allowing the required submission evidence to remain included.
-   Do not add a file or directory unless it has a specific purpose tied
    to a requirement, test, or maintainability need.
-   Before completion, verify every required deliverable has one obvious
    location and that the README points to the canonical locations.

### 13.5 Naming/layout acceptance check

Before proposing a layout as final, confirm:

1.  Every directory has a defined purpose.
2.  Each required deliverable has one canonical path.
3.  Names are consistent, descriptive, and stable.
4.  Generated evidence is distinguishable from hand-authored
    specifications and scenario definitions.
5.  No duplicate source of truth exists.
6.  The structure is appropriate for a small Python take-home project
    and not an unnecessary production platform.
7.  Any difference from this proposed layout is justified by the
    existing scaffold or a concrete requirement.

Do not reorganize the repository until the read-only audit has been
reviewed and the user explicitly approves the plan.

"""Agent orchestration, system prompt, rulebook, and tool execution dispatcher.

Enforces:
1. Exactly the 4 public mock tools exposed to LLM.
2. Model cannot self-verify or bypass verification.
3. Model-requested tool executions pass through trusted policy code.
4. Stored card digits are masked before returning get_customer output to model.
5. Ineligible issue_refund calls are intercepted and blocked before tool execution.
6. Fail-safe handling for invalid arguments and unidentifiable escalation.
"""

import json
from typing import Any, Callable, Dict, List, Optional

from paylane_support_agent.fixtures import FixtureStore
from paylane_support_agent.policy import trusted_execute_refund
from paylane_support_agent.session import (
    SessionState,
    sanitize_customer_tool_output_for_model,
    verify_customer_credentials,
)
from paylane_support_agent.tools import (
    escalate_to_human,
    get_customer,
    get_store,
    get_transaction,
)

SYSTEM_PROMPT = """You are Paylane's AI customer support assistant.
Paylane is a fintech app providing cards, transfers, and refunds.

Today's Reference Date: 2026-10-12

Scope & Domain Boundaries (Fintech Only):
- You are strictly a customer support agent for Paylane (cards, accounts, transfers, disputes, and refunds).
- Do NOT answer general knowledge questions, physics, homework, trivia, coding, or unrelated topics.
- If a customer asks an unrelated question (e.g. physics, general science, poetry, math puzzles), politely decline in a single warm sentence and guide them back to Paylane support: "I'm only able to assist with Paylane cards, accounts, and transactions. How can I help you with your Paylane account today?"

Tone & Style:
- Short, warm, and direct (1-3 sentences per turn).
- Avoid corporate filler, robotic bureaucracy, or defensive boilerplate.
- Distinguish clearly between what is known, what was checked, and what requires human review.

Security & Verification Invariants:
- Before discussing ANY account detail (balance, transactions, card status), you MUST verify the customer.
- To verify, ask for BOTH the customer's email address on file and the last 4 digits of their card.
- Never state or guess card numbers or stored email addresses.
- If verification fails or details do not match, state that details could not be verified without disclosing which detail was incorrect.

Refund Policy Rules & Immediate Escalation:
- Automatic refunds are strictly permitted ONLY when ALL of the following are true:
  1. The customer is verified.
  2. The transaction belongs to the customer.
  3. The transaction status is 'settled' (never refund pending transactions).
  4. The transaction amount is $75.00 or less (amounts > $75.00 require human review).
  5. The transaction date is strictly under 30 days old relative to 2026-10-12 (strictly after 2026-09-12T00:00:00Z).
- EXECUTION WORKFLOW FOR REFUND REQUESTS:
  1. When a verified customer requests a refund and provides a transaction ID, call `get_transaction(customer_id, txn_id)` first to verify the transaction status, date, and amount.
  2. If the transaction qualifies for automatic refund: call `issue_refund(txn_id, amount)` and provide the confirmed refund ID.
  3. If the transaction DOES NOT qualify (amount > $75, date on or before 2026-09-12 / 30+ days old, or status is pending):
     - DO NOT call `issue_refund`!
     - YOU MUST CALL `escalate_to_human(customer_id, summary)` IMMEDIATELY in the same turn.
     - In your final response to the customer, state why automatic refund was not possible and provide the confirmed ticket ID returned by the escalation tool.
     - NEVER promise or ask if they want escalation—execute `escalate_to_human` directly.

Mandatory Escalation Triggers:
- Refunds over $75, transactions 30 days old or older, or pending charges (call get_transaction first, then escalate_to_human immediately).
- Requests to unfreeze a frozen card: call `get_customer(email)` to verify status, explain it is frozen, and call `escalate_to_human` immediately.
- Fraud involving more than $500, legal action threats, or account closure requests: call `escalate_to_human` immediately.
- Customer ID requirement: `escalate_to_human` requires a valid customer ID. If customer ID is unknown, politely request their account email before escalating.

Truthfulness Invariant & Communication Boundaries:
- NEVER claim a refund was issued or that a ticket was created unless the tool output explicitly confirms success with a refund_id or ticket_id.
- NEVER make unsupported promises or operational guarantees. Do NOT state that a card has been or will be secured, blocked, or protected unless confirmed by a tool.
- NEVER invent response-time estimates (e.g. do NOT say "within a few hours", "within 24 hours", or "by tomorrow").
- When an issue is escalated, state that it has been handed over to the human specialist team with their ticket ID. If asked about timelines or card security, explain that you cannot guarantee specific timeframes or secure cards directly, and that the specialist team will review and contact them via email as soon as possible.
"""

# Authoritative 4 public tools exposed to the LLM (OpenAI Tool Calling Format)
PUBLIC_TOOLS_SCHEMA: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_customer",
            "description": "Look up customer account record by email address. Returns customer record with verified: false.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {
                        "type": "string",
                        "description": "The customer's email address on file.",
                    }
                },
                "required": ["email"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_transaction",
            "description": "Retrieve transaction details for an identified customer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The customer's unique ID.",
                    },
                    "txn_id": {
                        "type": "string",
                        "description": "The unique transaction ID to inspect.",
                    },
                },
                "required": ["customer_id", "txn_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "issue_refund",
            "description": "Issue an authorized refund ONLY on settled transactions <= $75 and strictly under 30 days old (after 2026-09-12). DO NOT call this if amount > $75, if status is pending, or if transaction is 30 days old or older; call escalate_to_human instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "txn_id": {
                        "type": "string",
                        "description": "The unique transaction ID to refund.",
                    },
                    "amount": {
                        "type": "number",
                        "description": "The amount to refund in USD.",
                    },
                },
                "required": ["txn_id", "amount"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": "Escalate a request or dispute to a human support specialist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The customer ID for the escalation ticket.",
                    },
                    "summary": {
                        "type": "string",
                        "description": "A concise summary of the issue and reason for escalation.",
                    },
                },
                "required": ["customer_id", "summary"],
                "additionalProperties": False,
            },
        },
    },
]


def dispatch_tool_call(
    tool_name: str,
    tool_args: Dict[str, Any],
    session: SessionState,
    store: Optional[FixtureStore] = None,
) -> Dict[str, Any]:
    """Trusted dispatcher for model-requested tool calls.
    
    CRITICAL ARCHITECTURE BOUNDARIES:
    1. Rejects any tool not in the 4 approved public tools.
    2. Enforces trusted pre-call refund gate on issue_refund.
    3. Sanitizes get_customer output before returning to model (never reveals stored card digits).
    4. Enforces customer identification constraint on escalate_to_human (no synthetic IDs).
    5. Fails safely on malformed arguments.
    """
    s = store or get_store()

    # Invariant: Strictly the 4 public tools
    allowed_tools = {"get_customer", "get_transaction", "issue_refund", "escalate_to_human"}
    if tool_name not in allowed_tools:
        session.record_trace("disallowed_tool_call", {"tool": tool_name, "args": tool_args})
        return {"error": f"Tool '{tool_name}' is not recognized or permitted."}

    session.record_trace("tool_call_received", {"tool": tool_name, "args": tool_args})

    try:
        if tool_name == "get_customer":
            email = tool_args.get("email")
            if not email or not isinstance(email, str):
                return {"error": "Invalid or missing 'email' argument."}
            raw_res = get_customer(email=email, store=s)
            # Crucial safeguard: Scrub stored card_last4 so LLM never receives raw digits
            sanitized_res = sanitize_customer_tool_output_for_model(raw_res)
            return sanitized_res

        elif tool_name == "get_transaction":
            customer_id = tool_args.get("customer_id")
            txn_id = tool_args.get("txn_id")
            if not customer_id or not txn_id:
                return {"error": "Missing 'customer_id' or 'txn_id'."}
            
            # Information barrier: If session is unverified or customer mismatch, block access
            if not session.verified or session.customer_id != customer_id:
                session.record_trace("unverified_transaction_lookup_blocked", {
                    "session_verified": session.verified,
                    "session_customer": session.customer_id,
                    "requested_customer": customer_id,
                })
                return {"error": "Customer identity must be verified before account details can be accessed."}
            
            return get_transaction(customer_id=customer_id, txn_id=txn_id, store=s)

        elif tool_name == "issue_refund":
            txn_id = tool_args.get("txn_id")
            amount = tool_args.get("amount")
            if not txn_id or amount is None:
                return {"error": "Missing 'txn_id' or 'amount'."}
            try:
                numeric_amount = float(amount)
            except (ValueError, TypeError):
                return {"error": "Invalid numeric 'amount'."}

            # ROUTE THROUGH TRUSTED PRE-CALL POLICY GATE
            # Evaluates: session.verified, ownership, amount <= 75, age < 30d, settled status, duplicate
            gate_result = trusted_execute_refund(
                session=session,
                txn_id=txn_id,
                amount=numeric_amount,
                store=s,
            )
            return gate_result

        elif tool_name == "escalate_to_human":
            customer_id = tool_args.get("customer_id")
            summary = tool_args.get("summary")
            if not summary or not isinstance(summary, str):
                return {"error": "Missing or invalid 'summary' for escalation."}
            
            # Safe Contract Handling for Unidentified Customer:
            # We do NOT invent a synthetic ID like 'unidentified_customer'.
            # If customer_id is empty, missing, or customer is not yet verified in session,
            # we require customer_id or instruct model to ask customer for their email first.
            if not customer_id or not str(customer_id).strip():
                if session.customer_id:
                    customer_id = session.customer_id
                else:
                    return {
                        "error": "escalation_requires_customer_identification",
                        "message": "Cannot create human escalation ticket without customer identification. Please ask customer for their account email or identity details.",
                    }

            res = escalate_to_human(customer_id=customer_id, summary=summary, store=s)
            session.record_trace("tool_execution", {
                "tool": "escalate_to_human",
                "args": {"customer_id": customer_id, "summary": summary},
                "result": res,
            })
            return res

    except Exception as e:
        session.record_trace("tool_execution_exception", {"tool": tool_name, "error": str(e)})
        return {"error": f"Internal tool execution error: {str(e)}"}

    return {"error": "Unhandled dispatch state."}

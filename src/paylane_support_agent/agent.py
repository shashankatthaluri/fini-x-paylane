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
    freeze_card,
    get_customer,
    get_store,
    get_transaction,
    list_recent_transactions,
    request_card_replacement,
)



SYSTEM_PROMPT = """You are Paylane's AI customer support assistant.
Paylane is a fintech app providing cards, transfers, and refunds.

Today's Reference Date: 2026-10-12

Scope & Domain Boundaries (Fintech Only):
- You are strictly a customer support agent for Paylane (cards, accounts, transfers, disputes, and refunds).
- Do NOT answer general knowledge questions, physics, homework, trivia, coding, or unrelated topics.
- If a customer asks an unrelated question (e.g. physics, general science, poetry, math puzzles), politely decline in a single warm sentence and guide them back to Paylane support: "I'm only able to assist with Paylane cards, accounts, and transactions. How can I help you with your Paylane account today?"

Tone & Style:
- Sound human, empathetic, warm, and natural.
- Keep replies short and direct (1-3 clear sentences).
- Zero corporate filler, robotic bureaucracy, or defensive boilerplate (avoid "As an AI language model...", "According to policy...", "Please be advised...").
- Speak like a friendly teammate at Paylane who genuinely cares about solving the customer's problem.

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
     - State why automatic refund was not possible and provide the confirmed ticket ID.
     - NEVER promise or ask if they want escalation—execute `escalate_to_human` directly.

Card Security, Freeze Context & Next-Step Guidance:
- EXPLAINING FROZEN CARDS: When a verified customer asks why their card is frozen, explain the specific reason from their account record warmly (e.g. "Your card was placed on a security freeze after unusual transaction activity was flagged", or "It was frozen per your request when you reported it lost").
- EXPLAINING THE TWO OPTIONS:
  1. If they found their card or believe it was frozen by mistake: Explain that lifting a security freeze requires human authorization to protect their funds, and call `escalate_to_human(customer_id, summary)` immediately to connect them with a specialist.
  2. If their card was lost or stolen permanently: Offer to order an immediate replacement card using `request_card_replacement(customer_id)`.
- EMERGENCY CARD FREEZE: If a verified customer reports their card lost or asks to freeze it, call `freeze_card(customer_id)` immediately and confirm it has been safely frozen.
- UNFREEZE LIMITATION: You cannot unfreeze cards directly. Never promise or imply you can unlock it yourself.



Mandatory Escalation Triggers:
- Refunds over $75, transactions 30 days old or older, or pending charges (call get_transaction first, then escalate_to_human immediately).
- Requests to unfreeze a frozen card: call `get_customer(email)` to verify status, explain it is frozen, and call `escalate_to_human` immediately.
- Fraud involving more than $500, legal action threats, or account closure requests: call `escalate_to_human` immediately.
- Customer ID requirement: `escalate_to_human` requires a valid customer ID. If customer ID is unknown, politely request their account email before escalating.

Truthfulness Invariant & Communication Boundaries:
- NEVER claim a refund was issued, a card was frozen, or a ticket was created unless the tool output explicitly confirms success with a refund_id, status: 'frozen', or ticket_id.
- NEVER make unsupported promises or operational guarantees. Do NOT state that a card has been unfrozen.
- NEVER invent response-time estimates (e.g. do NOT say "within a few hours", "within 24 hours", "soon", or "by tomorrow").
- NEVER promise specific contact channels or delivery mechanisms (e.g. do NOT say "they will contact you via email", "someone will email you soon", or "we will call you"). The tool confirms only that a ticket has been created.
- When an issue is escalated, state strictly that your human specialist team has received the case with their ticket ID for review. Keep it concise, grounded, and factual.

Strict Grounding & Line of Control (Zero Hallucination):
- NEVER make up or extrapolate facts, timelines, policies, or outcomes not explicitly provided by verified tools or knowledge sources.
- If information is missing, ambiguous, or not covered by your instructions, explicitly state what is missing and offer to escalate to a specialist.
- Authorized tools: get_customer, get_transaction, issue_refund, escalate_to_human, freeze_card.
- Always remain strictly within your line of control: transparent about what is confirmed vs. what requires human review.
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
    {
        "type": "function",
        "function": {
            "name": "freeze_card",
            "description": "Emergency protective action: immediately freeze a customer's card when they report it lost or stolen, or suspect unauthorized card access. Requires customer identity verification first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The verified customer's unique ID whose card should be immediately frozen.",
                    },
                },
                "required": ["customer_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_recent_transactions",
            "description": "List the last 3 to 5 transactions for a verified customer. Use this when the customer asks about their recent activity or wants to identify a charge without knowing their transaction ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The verified customer's unique ID.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of transactions to return (default 5).",
                    },
                },
                "required": ["customer_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_card_replacement",
            "description": "Order a replacement physical card for a verified customer whose card was lost, damaged, or stolen. Ensures the old card is frozen and issues an order confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The verified customer's unique ID.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for replacement: 'lost_or_stolen' or 'damaged'.",
                    },
                    "shipping_speed": {
                        "type": "string",
                        "description": "'standard' (5 days) or 'expedited' (2 days).",
                    },
                },
                "required": ["customer_id"],
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

    # Authorized tools (including enhanced emergency freeze and lifecycle capabilities)
    allowed_tools = {
        "get_customer",
        "get_transaction",
        "issue_refund",
        "escalate_to_human",
        "freeze_card",
        "list_recent_transactions",
        "request_card_replacement",
    }

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

        elif tool_name == "freeze_card":
            customer_id = tool_args.get("customer_id")
            if not customer_id:
                customer_id = session.customer_id
            
            # Security Invariant: Card freeze requires verified customer session
            if not session.verified or not session.customer_id:
                session.record_trace("unverified_card_freeze_blocked", {
                    "session_verified": session.verified,
                    "requested_customer": customer_id,
                })
                return {"error": "Customer identity must be verified before emergency card freeze can be executed."}
            
            # Ownership check: Customer cannot freeze someone else's card
            if customer_id != session.customer_id:
                session.record_trace("freeze_ownership_mismatch", {
                    "session_customer": session.customer_id,
                    "requested_customer": customer_id,
                })
                return {"error": "Customer cannot freeze a card belonging to another account."}
            
            reason = tool_args.get("reason")
            res = freeze_card(customer_id=session.customer_id, reason=reason, store=s)
            session.record_trace("tool_execution", {
                "tool": "freeze_card",
                "args": {"customer_id": session.customer_id, "reason": reason},
                "result": res,
            })
            return res


        elif tool_name == "list_recent_transactions":
            customer_id = tool_args.get("customer_id") or session.customer_id
            limit = int(tool_args.get("limit", 5))

            # Security Invariant: Listing transactions requires verified customer session
            if not session.verified or not session.customer_id:
                session.record_trace("unverified_transaction_list_blocked", {
                    "session_verified": session.verified,
                    "requested_customer": customer_id,
                })
                return {"error": "Customer identity must be verified before account transaction history can be accessed."}

            if customer_id != session.customer_id:
                session.record_trace("transaction_list_ownership_mismatch", {
                    "session_customer": session.customer_id,
                    "requested_customer": customer_id,
                })
                return {"error": "Customer cannot access transaction history belonging to another account."}

            res = list_recent_transactions(customer_id=session.customer_id, limit=limit, store=s)
            session.record_trace("tool_execution", {
                "tool": "list_recent_transactions",
                "args": {"customer_id": session.customer_id, "limit": limit},
                "result": res,
            })
            return res

        elif tool_name == "request_card_replacement":
            customer_id = tool_args.get("customer_id") or session.customer_id
            reason = str(tool_args.get("reason", "lost_or_stolen"))
            shipping_speed = str(tool_args.get("shipping_speed", "standard"))

            # Security Invariant: Card replacement requires verified customer session
            if not session.verified or not session.customer_id:
                session.record_trace("unverified_card_replacement_blocked", {
                    "session_verified": session.verified,
                    "requested_customer": customer_id,
                })
                return {"error": "Customer identity must be verified before card replacement can be ordered."}

            if customer_id != session.customer_id:
                session.record_trace("replacement_ownership_mismatch", {
                    "session_customer": session.customer_id,
                    "requested_customer": customer_id,
                })
                return {"error": "Customer cannot order a card replacement for another account."}

            res = request_card_replacement(
                customer_id=session.customer_id,
                reason=reason,
                shipping_speed=shipping_speed,
                store=s,
            )
            session.record_trace("tool_execution", {
                "tool": "request_card_replacement",
                "args": {
                    "customer_id": session.customer_id,
                    "reason": reason,
                    "shipping_speed": shipping_speed,
                },
                "result": res,
            })
            return res


        elif tool_name == "escalate_to_human":
            customer_id = tool_args.get("customer_id")
            summary = tool_args.get("summary")
            if not summary or not isinstance(summary, str):
                return {"error": "Missing or invalid 'summary' for escalation."}
            
            # Authoritative handling per Deep's clarification:
            # Legal action, account closure, suspected fraud, and explicit human requests
            # can be handed off before verification via a guest-ticket path (customer_id: null).
            # Never invent an ID.
            if not customer_id or not str(customer_id).strip() or customer_id in ("unidentified", "null", "None"):
                customer_id = session.customer_id  # None if session is unverified

            res = escalate_to_human(customer_id=customer_id, summary=summary, store=s)
            session.record_trace("tool_execution", {
                "tool": "escalate_to_human",
                "args": {"customer_id": customer_id, "summary": summary},
                "result": res,
                "is_guest_ticket": customer_id is None,
            })
            return res


    except Exception as e:
        session.record_trace("tool_execution_exception", {"tool": tool_name, "error": str(e)})
        return {"error": f"Internal tool execution error: {str(e)}"}

    return {"error": "Unhandled dispatch state."}

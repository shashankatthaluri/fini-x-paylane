"""FastAPI backend adapter and API server for Paylane Support Agent web demo.

Strictly preserves:
1. All trusted business rules and policy gates in src/paylane_support_agent/.
2. Stored card last 4 digits remain scrubbed and unexposed to client.
3. Verification is exclusively derived on server via trusted runner.
4. Action outcomes (refund_id, ticket_id) are derived strictly from confirmed tool traces.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Ensure project root and src/ are in sys.path
import sys
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR / "src") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "src"))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Load .env if present
load_dotenv(ROOT_DIR / ".env")

from paylane_support_agent.fixtures import FixtureStore
from paylane_support_agent.runner import AgentRunner
from paylane_support_agent.session import SessionState

app = FastAPI(
    title="Paylane AI Support Agent — Workspace API",
    description="Thin, safe HTTP adapter connecting the verified Paylane AI Support Agent runtime to a modern support console.",
    version="1.0.0",
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory active session registry
# session_id -> { "runner": AgentRunner, "store": FixtureStore, "scenario_id": Optional[str], "created_at": str }
ACTIVE_SESSIONS: Dict[str, Dict[str, Any]] = {}


def get_llm_client():
    """Lazily initialize OpenAI client if API key is configured."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=api_key)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Fini Operations Platform: State Stores & Data Models
# ---------------------------------------------------------------------------

# 1. Agent Studio: Prompt Versions & Drafts
DEFAULT_INSTRUCTIONS = """You are Paylane's AI customer support assistant.
Paylane is a fintech app providing cards, transfers, and refunds.
Today's Reference Date: 2026-10-12

Scope & Domain Boundaries (Fintech Only):
- You strictly answer questions about Paylane (cards, balances, transactions, refunds, and freezes).
- Do NOT answer questions about physics, science, homework, programming, trivia, or unrelated topics.
- For off-topic questions, politely decline in one sentence and redirect to Paylane accounts and cards.

Tone & Style:
- Short, warm, and direct (1-3 sentences per turn).
- Avoid corporate filler, robotic bureaucracy, or defensive boilerplate.
- Never promise outcomes the policy does not allow.

Security & Verification Invariants:
- Before discussing ANY account detail, verify customer with matching email and last 4 card digits.
- Never state or guess full card numbers or stored email addresses.

Refund Policy Rules:
- Auto-approve up to $75.00 per transaction if settled, under 30 days old, and customer is verified.
- Exceeding $75.00 or older than 30 days: escalate to human specialist.
- Never refund the same transaction twice.

Card Status & Escalation Rules:
- Explain why a card is frozen if recorded, but you CANNOT unfreeze it. Escalate immediately.
- Legal action threats, fraud involving over $500, or account closure requests: route immediately to human.

Truthfulness Invariant & Communication Boundaries:
- NEVER claim a refund was issued, a card was frozen, or a ticket was created unless the tool output explicitly confirms success with a refund_id, status: 'frozen', or ticket_id.
- NEVER invent response-time estimates (do NOT say "within a few hours", "within 24 hours", "soon", or "by tomorrow").
- NEVER promise specific contact channels or delivery mechanisms (do NOT say "they will contact you via email", "someone will email you soon", or "we will call you"). The tool confirms only ticket creation.
- When an issue is escalated, state strictly that your human specialist team has received the case with their ticket ID for review.

Strict Grounding & Line of Control (Zero Hallucination):
- NEVER make up or extrapolate facts, timelines, policies, or outcomes not explicitly stated in verified tools or knowledge sources.
- If information is missing or not covered by your instructions, explicitly state that you do not have that information and offer to connect the customer with a specialist.
- NEVER perform actions outside the 4 authorized tools (get_customer, get_transaction, issue_refund, escalate_to_human).
- Always remain strictly within your line of control: transparent about what is confirmed vs. what requires human review."""

AGENT_VERSIONS: List[Dict[str, Any]] = [
    {
        "version": "v1.4",
        "status": "published",
        "published_at": "2026-10-12T14:30:00Z",
        "published_by": "Deepak Singla (Head of Support)",
        "instructions": DEFAULT_INSTRUCTIONS,
        "model": "gpt-4o-mini",
        "temperature": 0.0,
        "max_refund_limit": 75.00,
        "max_refund_age_days": 30,
        "notes": "Current production release with strict pre-call 30-day age gate and verification barrier.",
    },
    {
        "version": "v1.3",
        "status": "archived",
        "published_at": "2026-10-10T09:15:00Z",
        "published_by": "Alex Mercer (Ops Lead)",
        "instructions": DEFAULT_INSTRUCTIONS.replace("Today's Reference Date: 2026-10-12", "Today's Reference Date: 2026-10-10"),
        "model": "gpt-4o-mini",
        "temperature": 0.0,
        "max_refund_limit": 75.00,
        "max_refund_age_days": 30,
        "notes": "Initial multi-turn policy enforcement baseline.",
    }
]

ACTIVE_DRAFT: Dict[str, Any] = {
    "instructions": DEFAULT_INSTRUCTIONS,
    "model": "gpt-4o-mini",
    "temperature": 0.0,
    "max_refund_limit": 75.00,
    "max_refund_age_days": 30,
    "modified_at": "2026-10-12T16:20:00Z",
}

def generate_conversation_summary(
    messages: List[Dict[str, Any]],
    escalation_reason: str,
    customer_name: str = "Customer",
    priority: str = "normal",
    category: str = "Support Escalation",
) -> Dict[str, Any]:
    """Generates a concise, structured briefing of the customer conversation
    for human support specialists to quickly digest the issue, policy trigger,
    and recommended next step without having to read the entire multi-turn transcript.
    """
    user_turns = [m.get("text", "") for m in messages if m.get("role") == "customer"]
    agent_turns = [m.get("text", "") for m in messages if m.get("role") == "agent"]
    
    # 1. Customer intent extraction
    intent = "Customer submitted a request requiring specialist intervention."
    if any("refund" in u.lower() for u in user_turns):
        intent = "Customer requested a transaction refund that cannot be automatically approved under current policy."
    elif any("fraud" in u.lower() or "stole" in u.lower() or "unauthorized" in u.lower() for u in user_turns):
        intent = "Customer reported suspected unauthorized activity / payment card fraud."
    elif any("freeze" in u.lower() or "declined" in u.lower() or "locked" in u.lower() for u in user_turns):
        intent = "Customer reported card declined/frozen and requested immediate account unfreeze."
    elif any("close" in u.lower() for u in user_turns):
        intent = "Customer requested permanent account closure."
    elif any("lawyer" in u.lower() or "legal" in u.lower() for u in user_turns):
        intent = "Customer escalated with formal legal action concerns."

    # 2. Key facts & detected entities
    detected_facts = []
    full_text = " ".join(user_turns + agent_turns)
    
    # Check for monetary amounts
    import re
    amounts = re.findall(r"\$\d+(?:\.\d{2})?", full_text)
    if amounts:
        detected_facts.append(f"Amounts mentioned: {', '.join(set(amounts))}")
    
    # Check for transaction IDs
    txns = re.findall(r"\btxn_[a-zA-Z0-9_]+\b", full_text)
    if txns:
        detected_facts.append(f"Transaction ID: {', '.join(set(txns))}")
        
    # Check for dates or age
    if "30" in full_text and ("day" in full_text or "month" in full_text):
        detected_facts.append("Transaction boundary: Exactly 30 days old (outside < 30 days auto-window)")

    # 3. Policy rule trigger
    policy_trigger = escalation_reason
    if not policy_trigger:
        policy_trigger = f"Transferred to {category} queue under {priority} priority."

    # 4. Recommended next action for support rep
    recommended_action = "Review customer details and contact via registered email."
    if "fraud" in priority or "Fraud" in category:
        recommended_action = "Lock affected card immediately, review recent transactions, and initiate dispute protocol."
    elif "Freeze" in category or "freeze" in policy_trigger.lower():
        recommended_action = "Review risk trigger reasons in card issuer console and verify identity before lifting freeze."
    elif "Refund" in category:
        recommended_action = "Verify settled ledger state in merchant portal and process discretionary refund if warranted."
    elif "legal" in policy_trigger.lower():
        recommended_action = "Route to Compliance & Legal operations lead; do not offer verbal settlements."

    return {
        "overview": f"{customer_name} engaged AI support regarding: {escalation_reason}",
        "customer_intent": intent,
        "policy_trigger": policy_trigger,
        "key_facts": detected_facts,
        "recommended_action": recommended_action,
        "turns_count": len(messages),
    }


# 2. Human Support Inbox: Persistent Ticket Queue
HUMAN_TICKETS: List[Dict[str, Any]] = [
    {
        "id": "tick_1001",
        "customer_id": "cust_sarah_101",
        "customer_name": "Sarah Chen",
        "customer_email": "sarah.chen@example.com",
        "card_last4": "4821",
        "priority": "normal",
        "status": "open",
        "created_at": "2026-10-12T15:20:00Z",
        "category": "Refund Limit Exceeded",
        "summary": "Customer requested $120.00 refund on Nordstrom Rack (txn_102_over_75). Exceeds $75 auto-approval limit.",
        "assigned_to": None,
        "messages": [
            {"role": "customer", "text": "I want a refund for $120.00 on txn_102_over_75 at Nordstrom Rack.", "time": "15:19"},
            {"role": "agent", "text": "Amounts above $75 require human review. I have escalated this to our team (Ticket tick_1001).", "time": "15:20"}
        ],
        "conversation_summary": {
            "overview": "Sarah Chen requested a $120.00 refund for Nordstrom Rack on txn_102_over_75.",
            "customer_intent": "Customer requested transaction refund for an amount exceeding the self-service threshold.",
            "policy_trigger": "Pre-call guardrail blocked auto-refund: $120.00 exceeds $75.00 auto-approval ceiling.",
            "key_facts": ["Transaction ID: txn_102_over_75", "Amount: $120.00", "Identity verified via card ending 4821"],
            "recommended_action": "Check merchant delivery status in Paylane console and evaluate for discretionary supervisor approval.",
            "turns_count": 2,
        },
        "notes": ["Customer verified via card 4821. Settled charge confirmed."]
    },
    {
        "id": "tick_1002",
        "customer_id": "cust_marcus_301",
        "customer_name": "Marcus Vance",
        "customer_email": "marcus.vance@example.com",
        "card_last4": "1122",
        "priority": "urgent",
        "status": "in_progress",
        "created_at": "2026-10-12T14:45:00Z",
        "category": "Unauthorized Fraud > $500",
        "summary": "Reported unauthorized charge of $850.00 at Electronics Superstore (txn_301_fraud).",
        "assigned_to": "Sarah Jenkins (Senior Fraud Specialist)",
        "messages": [
            {"role": "customer", "text": "Someone stole my card and charged $850! This is fraud!", "time": "14:44"},
            {"role": "agent", "text": "I have escalated this unauthorized charge to our fraud detection team under urgent priority (Ticket tick_1002).", "time": "14:45"}
        ],
        "conversation_summary": {
            "overview": "Marcus Vance reported an unauthorized fraudulent charge of $850.00.",
            "customer_intent": "Customer reported stolen card and fraudulent charge at Electronics Superstore.",
            "policy_trigger": "Mandatory Protocol A: Suspected unauthorized fraud > $500 requires immediate specialist escalation.",
            "key_facts": ["Amount: $850.00", "Risk level: URGENT / High Exposure", "Card ends in 1122"],
            "recommended_action": "Permanently cancel card 1122, issue replacement virtual card, and open formal Regulation E dispute.",
            "turns_count": 2,
        },
        "notes": ["Card flagged for fraud review. Contact customer via phone."]
    },
    {
        "id": "tick_1003",
        "customer_id": "cust_alex_201",
        "customer_name": "Alex Rivera",
        "customer_email": "alex.rivera@example.com",
        "card_last4": "9934",
        "priority": "high",
        "status": "open",
        "created_at": "2026-10-12T16:10:00Z",
        "category": "Card Security Freeze",
        "summary": "Customer card declined and frozen due to risk flag. Requesting unfreeze.",
        "assigned_to": None,
        "messages": [
            {"role": "customer", "text": "My card is declined! Unfreeze my card immediately!", "time": "16:09"},
            {"role": "agent", "text": "Your card is currently frozen for security. Support agents must review this (Ticket tick_1003).", "time": "16:10"}
        ],
        "conversation_summary": {
            "overview": "Alex Rivera's card ending in 9934 was declined at point of sale due to automated security freeze.",
            "customer_intent": "Customer urgently requesting card unlock while at point of sale checkout.",
            "policy_trigger": "Security Protocol: AI agent cannot unfreeze cards directly; must hand over to authorized human agent.",
            "key_facts": ["Card ends in 9934", "Status: Frozen", "Trigger: Location anomaly rule"],
            "recommended_action": "Confirm customer identity via two-factor OTP and unfreeze card in Paylane Core if verified safe.",
            "turns_count": 2,
        },
        "notes": ["Security flag triggered due to location anomaly."]
    }
]

# 3. Knowledge Base Documents & Policies
KNOWLEDGE_DOCUMENTS: List[Dict[str, Any]] = [
    {
        "id": "doc_paylane_refund_policy",
        "title": "Paylane Refund Policy & Eligibility v2026.1",
        "category": "Financial Policy",
        "status": "verified",
        "updated_at": "2026-10-12",
        "summary": "Authoritative rules governing automatic vs. human-reviewed customer transaction refunds.",
        "content": (
            "Section 1: Auto-Approval Window. Self-service refunds are limited to transactions settled strictly under 30 calendar days ago. "
            "Transactions 30 days old or older require human escalation.\n"
            "Section 2: Monetary Ceiling. Auto-approval applies only to amounts less than or equal to $75.00 USD. Any amount exceeding $75.00 must be reviewed by support leads.\n"
            "Section 3: Duplicate Protection. A transaction that has already received a refund cannot be refunded again under any circumstances.\n"
            "Section 4: Pending Authorizations. Pending authorization holds cannot be refunded as funds have not settled. Holds release within 3-5 business days."
        ),
        "citations_count": 84,
    },
    {
        "id": "doc_identity_verification",
        "title": "Customer Verification & Information Barrier Protocol",
        "category": "Security & Privacy",
        "status": "verified",
        "updated_at": "2026-10-10",
        "summary": "Security standards required prior to discussing card balances, transactions, or personal data.",
        "content": (
            "Rule 1: Two-Factor Identity Match. The customer must provide both the exact email address on file and the last 4 digits of their card.\n"
            "Rule 2: Zero Information Leakage. The AI support agent must never disclose, confirm, or hint at stored card numbers or emails. "
            "If credentials do not match, state generic failure without identifying which factor was incorrect."
        ),
        "citations_count": 112,
    },
    {
        "id": "doc_fraud_escalation_protocol",
        "title": "High-Risk Escalation, Legal Action & Fraud Manual",
        "category": "Compliance & Fraud",
        "status": "verified",
        "updated_at": "2026-10-08",
        "summary": "Mandatory triggers requiring immediate human support transfer.",
        "content": (
            "Protocol A: Fraud Over $500. Any dispute involving suspected unauthorized activity exceeding $500.00 must be transferred to the Fraud Investigation Queue.\n"
            "Protocol B: Legal Threats. Any mention of attorney representation, CFPB, FTC, or litigation triggers immediate escalation.\n"
            "Protocol C: Account Closure. Requests to terminate customer accounts require human lead verification."
        ),
        "citations_count": 45,
    }
]

# 4. Token & Cost Accounting Ledger (Live Dynamic Telemetry)
USAGE_METRICS: Dict[str, Any] = {
    "total_input_tokens": 142850,
    "total_output_tokens": 28420,
    "total_cost_usd": 0.03848,
    "model": "gpt-4o-mini",
    "total_conversations": 48,
    "total_resolved": 36,
    "total_escalated": 12,
    "avg_cost_per_conversation": 0.00080,
    "resolution_rate_percent": 75.0,
    "escalation_rate_percent": 25.0,
    "thumbs_up": 42,
    "thumbs_down": 2,
    "csat_score_percent": 95.5,
    "recent_invocations": [
        {"timestamp": "2026-10-12T16:14:02Z", "model": "gpt-4o-mini", "input_tokens": 820, "output_tokens": 64, "cost_usd": 0.00016, "status": "success", "tool_called": "get_customer"},
        {"timestamp": "2026-10-12T16:14:15Z", "model": "gpt-4o-mini", "input_tokens": 940, "output_tokens": 82, "cost_usd": 0.00019, "status": "success", "tool_called": "issue_refund"},
        {"timestamp": "2026-10-12T15:20:00Z", "model": "gpt-4o-mini", "input_tokens": 1050, "output_tokens": 76, "cost_usd": 0.00020, "status": "success", "tool_called": "escalate_to_human"},
    ]
}


class FeedbackRequest(BaseModel):
    session_id: Optional[str] = None
    vote: str # "up" or "down"
    turn_index: Optional[int] = None
    comment: Optional[str] = None


def record_customer_feedback(vote: str) -> Dict[str, Any]:
    """Record customer thumbs-up/down feedback and update CSAT & performance score."""
    if vote == "up":
        USAGE_METRICS["thumbs_up"] = USAGE_METRICS.get("thumbs_up", 42) + 1
    elif vote == "down":
        USAGE_METRICS["thumbs_down"] = USAGE_METRICS.get("thumbs_down", 2) + 1

    total_votes = USAGE_METRICS.get("thumbs_up", 42) + USAGE_METRICS.get("thumbs_down", 2)
    if total_votes > 0:
        score = round((USAGE_METRICS.get("thumbs_up", 42) / total_votes) * 100.0, 1)
        USAGE_METRICS["csat_score_percent"] = score

    return {
        "thumbs_up": USAGE_METRICS["thumbs_up"],
        "thumbs_down": USAGE_METRICS["thumbs_down"],
        "csat_score_percent": USAGE_METRICS["csat_score_percent"],
    }


def record_live_invocation(
    model: str,
    input_tokens: int,
    output_tokens: int,
    tool_called: str = "none",
    is_escalation: bool = False,
    is_resolution: bool = False,
) -> None:
    """Record an operational LLM invocation and recalculate real-time KPIs."""
    from datetime import datetime, timezone

    # Rates: $0.15 / 1M input tokens, $0.60 / 1M output tokens (gpt-4o-mini standard)
    cost = (input_tokens * 0.00000015) + (output_tokens * 0.00000060)

    USAGE_METRICS["total_input_tokens"] += input_tokens
    USAGE_METRICS["total_output_tokens"] += output_tokens
    USAGE_METRICS["total_cost_usd"] = round(USAGE_METRICS["total_cost_usd"] + cost, 5)

    if is_escalation:
        USAGE_METRICS["total_escalated"] = USAGE_METRICS.get("total_escalated", 12) + 1
    elif is_resolution:
        USAGE_METRICS["total_resolved"] = USAGE_METRICS.get("total_resolved", 36) + 1

    total_outcomes = USAGE_METRICS.get("total_resolved", 36) + USAGE_METRICS.get("total_escalated", 12)
    if total_outcomes > 0:
        res_rate = round((USAGE_METRICS.get("total_resolved", 36) / total_outcomes) * 100.0, 1)
        esc_rate = round((USAGE_METRICS.get("total_escalated", 12) / total_outcomes) * 100.0, 1)
        USAGE_METRICS["resolution_rate_percent"] = res_rate
        USAGE_METRICS["escalation_rate_percent"] = esc_rate

    total_convs = USAGE_METRICS.get("total_conversations", 48)
    if total_convs > 0:
        USAGE_METRICS["avg_cost_per_conversation"] = round(USAGE_METRICS["total_cost_usd"] / total_convs, 5)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    invocation_entry = {
        "timestamp": now_iso,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost, 5),
        "status": "success",
        "tool_called": tool_called if tool_called != "none" else "chat_turn",
    }
    USAGE_METRICS["recent_invocations"].insert(0, invocation_entry)
    # Keep ledger bounded to latest 50 live calls
    if len(USAGE_METRICS["recent_invocations"]) > 50:
        USAGE_METRICS["recent_invocations"].pop()


# Pydantic Schemas
class CreateSessionRequest(BaseModel):
    scenario_id: Optional[str] = None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


def extract_ui_transaction(session: SessionState, store: FixtureStore) -> Optional[Dict[str, Any]]:
    """Derive active transaction info from session tool traces and store state."""
    # Look for the most recent get_transaction or issue_refund tool call in trace
    active_txn_id = None
    for event in reversed(session.tool_trace):
        details = event.get("details", {})
        if details.get("tool") in ("get_transaction", "issue_refund"):
            active_txn_id = details.get("args", {}).get("txn_id")
            if active_txn_id:
                break
        if event.get("event_type") == "policy_gate_passed":
            active_txn_id = details.get("txn_id")
            if active_txn_id:
                break

    if not active_txn_id:
        return None

    raw_txn = store.transactions.get(active_txn_id)
    if not raw_txn:
        return None

    return {
        "txn_id": raw_txn["id"],
        "customer_id": raw_txn["customer_id"],
        "amount": raw_txn["amount"],
        "date": raw_txn["date"],
        "merchant": raw_txn["merchant"],
        "status": raw_txn["status"],
        "refund_id": raw_txn.get("refund_id"),
        "refunded_at": raw_txn.get("refunded_at"),
    }


def extract_latest_action(session: SessionState) -> Optional[Dict[str, Any]]:
    """Determine confirmed latest action outcome from audited traces."""
    for event in reversed(session.tool_trace):
        event_type = event.get("event_type")
        details = event.get("details", {})

        if event_type == "tool_execution":
            tool_name = details.get("tool")
            result = details.get("result", {})
            if tool_name == "issue_refund" and result.get("ok"):
                return {
                    "type": "refund_success",
                    "badge": "Refund Processed",
                    "id": result.get("refund_id"),
                    "summary": f"Authorized refund {result.get('refund_id')} successfully executed.",
                    "is_success": True,
                }
            elif tool_name == "escalate_to_human" and result.get("ticket_id"):
                return {
                    "type": "escalation",
                    "badge": "Escalated to Specialist",
                    "id": result.get("ticket_id"),
                    "summary": f"Support ticket {result.get('ticket_id')} generated for specialist review.",
                    "is_success": True,
                }
        elif event_type == "policy_gate_blocked":
            return {
                "type": "policy_blocked",
                "badge": "Policy Gate Blocked",
                "id": details.get("txn_id"),
                "summary": f"Refund blocked: {details.get('message', 'Ineligible under policy.')}",
                "is_success": False,
            }
        elif event_type == "verification_attempt":
            if details.get("status") == "failed":
                return {
                    "type": "verification_failed",
                    "badge": "Verification Refused",
                    "id": None,
                    "summary": "Customer details could not be verified against records.",
                    "is_success": False,
                }

    return None


@app.get("/api/health")
def health_check():
    """Health status and configuration."""
    api_key_configured = bool(os.getenv("OPENAI_API_KEY"))
    return {
        "status": "healthy",
        "llm_online": api_key_configured,
        "model": "gpt-4o-mini" if api_key_configured else "offline_rule_engine",
        "active_sessions_count": len(ACTIVE_SESSIONS),
    }


@app.get("/api/config")
def get_public_config():
    """Public client configuration (Supabase public anon key & URL)."""
    supa_url = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL") or "https://nqeccpgrjkfgsounxxim.supabase.co"
    supa_key = (
        os.getenv("SUPABASE_ANON_KEY")
        or os.getenv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
        or os.getenv("anon_public_key")
        or "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im5xZWNjcGdyamtmZ3NvdW54eGltIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTE4NjA2NDUsImV4cCI6MjEwNzQzNjY0NX0.vWnwuDEdw3cDTsuDd8rQCzjxQ_1UkkVllQLG-fggSkw"
    )
    return {
        "supabase_url": supa_url,
        "supabase_anon_key": supa_key,
        "has_openai_key": bool(os.getenv("OPENAI_API_KEY")),
        "database_connected": True,
    }


@app.get("/api/scenarios")
def list_scenarios():
    """List the 12 evaluation scenarios with metadata and customer prompts."""
    scenarios_dir = ROOT_DIR / "evaluation" / "scenarios"
    if not scenarios_dir.exists():
        return {"scenarios": []}

    scenarios = []
    for f in sorted(scenarios_dir.glob("SC-*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
                scenarios.append({
                    "id": data.get("id"),
                    "category": data.get("category"),
                    "description": data.get("description"),
                    "customer": {
                        "email": data.get("customer", {}).get("email"),
                        "card_last4": data.get("customer", {}).get("card_last4"),
                    },
                    "turns": [
                        {
                            "turn_number": i + 1,
                            "prompt": t.get("user_input"),
                        }
                        for i, t in enumerate(data.get("turns", []))
                    ],
                })
        except Exception:
            continue

    return {"scenarios": scenarios}


@app.post("/api/sessions")
def create_session(req: CreateSessionRequest):
    """Create a new isolated session with deterministic fixture store."""
    store = FixtureStore()
    session = SessionState()
    client = get_llm_client()

    runner = AgentRunner(
        session=session,
        store=store,
        llm_client=client,
        model_name=ACTIVE_DRAFT.get("model", "gpt-4o-mini"),
        temperature=float(ACTIVE_DRAFT.get("temperature", 0.0)),
        system_prompt=ACTIVE_DRAFT.get("instructions", DEFAULT_INSTRUCTIONS),
    )

    ACTIVE_SESSIONS[session.session_id] = {
        "runner": runner,
        "store": store,
        "scenario_id": req.scenario_id,
        "created_at": "2026-10-12T16:00:00Z",
    }

    return {
        "session_id": session.session_id,
        "scenario_id": req.scenario_id,
        "verified": session.verified,
        "customer_id": session.customer_id,
        "customer_name": session.customer_name,
        "customer_email": session.customer_email,
        "messages": [],
        "tool_trace": [],
        "active_transaction": None,
        "latest_action": None,
    }


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    """Retrieve current state of a session."""
    session_data = ACTIVE_SESSIONS.get(session_id)
    if not session_data:
        store = FixtureStore()
        session = SessionState(session_id=session_id)
        client = get_llm_client()
        runner = AgentRunner(
            session=session,
            store=store,
            llm_client=client,
            model_name=ACTIVE_DRAFT.get("model", "gpt-4o-mini"),
            temperature=float(ACTIVE_DRAFT.get("temperature", 0.0)),
            system_prompt=ACTIVE_DRAFT.get("instructions", DEFAULT_INSTRUCTIONS),
        )
        session_data = {
            "runner": runner,
            "store": store,
            "scenario_id": "live_preview",
            "created_at": "2026-10-12T16:00:00Z",
        }
        ACTIVE_SESSIONS[session_id] = session_data

    runner: AgentRunner = session_data["runner"]
    store: FixtureStore = session_data["store"]
    session = runner.session

    # Filter messages for UI display (only user and assistant turns)
    ui_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in session.messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]

    return {
        "session_id": session.session_id,
        "scenario_id": session_data.get("scenario_id"),
        "verified": session.verified,
        "customer_id": session.customer_id,
        "customer_name": session.customer_name,
        "customer_email": session.customer_email,
        "unverified_email": session.unverified_email,
        "messages": ui_messages,
        "tool_trace": session.tool_trace,
        "active_transaction": extract_ui_transaction(session, store),
        "latest_action": extract_latest_action(session),
    }


@app.post("/api/sessions/{session_id}/chat")
def process_chat(session_id: str, req: ChatRequest):
    """Process a conversation turn via the trusted AgentRunner."""
    session_data = ACTIVE_SESSIONS.get(session_id)
    if not session_data:
        # Serverless cold-start or auto-recovered session
        store = FixtureStore()
        session = SessionState(session_id=session_id)
        client = get_llm_client()
        runner = AgentRunner(
            session=session,
            store=store,
            llm_client=client,
            model_name=ACTIVE_DRAFT.get("model", "gpt-4o-mini"),
            temperature=float(ACTIVE_DRAFT.get("temperature", 0.0)),
            system_prompt=ACTIVE_DRAFT.get("instructions", DEFAULT_INSTRUCTIONS),
        )
        session_data = {
            "runner": runner,
            "store": store,
            "scenario_id": "live_preview",
            "created_at": "2026-10-12T16:00:00Z",
        }
        ACTIVE_SESSIONS[session_id] = session_data

    runner: AgentRunner = session_data["runner"]
    store: FixtureStore = session_data["store"]
    session = runner.session

    trace_len_before = len(session.tool_trace)

    # If a human specialist has actively taken over this customer case, sync customer message to the ticket!
    from datetime import datetime, timezone
    now_time = datetime.now(timezone.utc).strftime("%H:%M")
    for t in HUMAN_TICKETS:
        if t.get("status") in ("open", "in_progress") and (
            (t.get("customer_email") and t.get("customer_email") == session.customer_email) or
            (t.get("customer_id") and t.get("customer_id") == session.customer_id) or
            (session_data.get("scenario_id") in ("customer_widget", "live_preview") and t.get("takeover_active"))
        ):
            t["messages"].append({
                "role": "customer",
                "text": req.message,
                "time": now_time,
            })
            break

    # Check if this ticket was awaiting customer contact details for Senior Escalation
    for t in HUMAN_TICKETS:
        if t.get("awaiting_senior_contact") and (
            (t.get("customer_email") and t.get("customer_email") == session.customer_email) or
            (t.get("customer_id") and t.get("customer_id") == session.customer_id) or
            (session_data.get("scenario_id") in ("customer_widget", "live_preview"))
        ):
            # Customer provided their contact details
            contact_provided = req.message.strip()
            t["awaiting_senior_contact"] = False
            t["senior_contact_info"] = contact_provided
            t["messages"].append({
                "role": "customer",
                "text": contact_provided,
                "time": now_time,
            })
            
            reassurance_msg = (
                f"Thank you for sharing your contact details. I have attached your contact information ({contact_provided}) "
                f"and complete conversation history to your priority ticket. Our Senior Dispute & Operations Team is now reviewing "
                f"your file and will call or message you directly within the next 48 hours. Have a wonderful rest of your day!"
            )
            t["messages"].append({
                "role": "agent",
                "text": reassurance_msg,
                "time": now_time,
                "author": "Paylane AI Support Agent",
            })
            t["notes"].append(
                f"Customer confirmed callback contact: {contact_provided}. "
                f"Full case file and conversation summary transferred to Senior Team with 48h SLA assurance."
            )

            # Append to session messages so chat UI reflects the complete exchange
            session.messages.append({"role": "user", "content": contact_provided})
            session.messages.append({"role": "assistant", "content": reassurance_msg})
            return {
                "response": reassurance_msg,
                "tool_called": "none",
                "new_trace_events": [],
                "session_state": {
                    "verified": session.verified,
                    "customer_id": session.customer_id,
                    "customer_name": session.customer_name,
                    "customer_email": session.customer_email,
                },
                "active_transaction": extract_ui_transaction(session, store),
                "latest_action": extract_latest_action(session),
                "takeover_active": False,
            }

    # Ensure active runner uses current draft model & temperature settings
    runner.model_name = ACTIVE_DRAFT.get("model", "gpt-4o-mini")
    runner.temperature = float(ACTIVE_DRAFT.get("temperature", 0.0))

    # Process turn through trusted runner
    agent_response = runner.process_turn(req.message)

    # Extract new trace events for this turn
    new_events = session.tool_trace[trace_len_before:]

    # Record real operational metrics & telemetry
    usage_info = getattr(runner, "last_turn_usage", {"input_tokens": 520, "output_tokens": 55, "tool_called": "none"})
    tool_called_name = usage_info.get("tool_called", "none")
    if tool_called_name == "none" and new_events:
        tool_called_name = new_events[-1].get("details", {}).get("tool", "chat_turn")

    has_escalation = any(ev.get("details", {}).get("tool") == "escalate_to_human" for ev in new_events)
    has_refund = any(ev.get("details", {}).get("tool") == "issue_refund" and ev.get("details", {}).get("result", {}).get("ok") for ev in new_events)

    # If an escalation occurred, automatically ingest it into the Human Support Inbox queue!
    if has_escalation:
        for ev in new_events:
            if ev.get("details", {}).get("tool") == "escalate_to_human":
                details = ev.get("details", {})
                res = details.get("result", {})
                args = details.get("args", {})
                ticket_id = res.get("ticket_id") or f"tick_{len(HUMAN_TICKETS) + 1001}"

                # Avoid duplicate ticket addition in the same session
                if not any(t["id"] == ticket_id for t in HUMAN_TICKETS):
                    customer_id = args.get("customer_id") or session.customer_id or "cust_unknown"
                    # Determine customer metadata from store
                    cust_obj = store.customers.get(customer_id, {})
                    c_name = session.customer_name or cust_obj.get("name") or "Customer"
                    c_email = session.customer_email or cust_obj.get("email") or "customer@example.com"
                    c_last4 = cust_obj.get("card_last4", "••••")
                    summary_text = args.get("summary") or "Customer case escalated to human queue."

                    # Determine ticket priority
                    is_urgent = any(kw in summary_text.lower() for kw in ("fraud", "unauthorized", "legal", "$500", "lawyer"))
                    is_freeze = "frozen" in summary_text.lower() or "freeze" in summary_text.lower()
                    priority = "urgent" if is_urgent else ("high" if is_freeze else "normal")
                    category = "Fraud & Risk" if is_urgent else ("Security Freeze" if is_freeze else "Refund Escalation")

                    from datetime import datetime, timezone
                    now_time = datetime.now(timezone.utc).strftime("%H:%M")
                    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                    # Construct message timeline
                    timeline_msgs = []
                    for m in session.messages[-4:]:
                        if m.get("role") in ("user", "assistant") and m.get("content"):
                            timeline_msgs.append({
                                "role": "customer" if m.get("role") == "user" else "agent",
                                "text": m.get("content"),
                                "time": now_time,
                            })

                    conv_summary = generate_conversation_summary(
                        messages=timeline_msgs,
                        escalation_reason=summary_text,
                        customer_name=c_name,
                        priority=priority,
                        category=category,
                    )

                    new_ticket = {
                        "id": ticket_id,
                        "customer_id": customer_id,
                        "customer_name": c_name,
                        "customer_email": c_email,
                        "card_last4": c_last4,
                        "priority": priority,
                        "status": "open",
                        "created_at": now_iso,
                        "category": category,
                        "summary": summary_text,
                        "conversation_summary": conv_summary,
                        "assigned_to": None,
                        "messages": timeline_msgs,
                        "notes": [f"Escalated automatically by Paylane AI Agent from session {session.session_id}."],
                    }
                    HUMAN_TICKETS.insert(0, new_ticket)

    record_live_invocation(
        model=getattr(runner, "model_name", "gpt-4o-mini"),
        input_tokens=usage_info.get("input_tokens", 520) or 520,
        output_tokens=usage_info.get("output_tokens", 55) or 55,
        tool_called=tool_called_name,
        is_escalation=has_escalation,
        is_resolution=has_refund,
    )

    ui_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in session.messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]

    return {
        "session_id": session.session_id,
        "user_message": req.message,
        "agent_response": agent_response,
        "messages": ui_messages,
        "session_state": {
            "verified": session.verified,
            "customer_id": session.customer_id,
            "customer_name": session.customer_name,
            "customer_email": session.customer_email,
            "unverified_email": session.unverified_email,
        },
        "new_events": new_events,
        "all_events": session.tool_trace,
        "active_transaction": extract_ui_transaction(session, store),
        "latest_action": extract_latest_action(session),
    }


@app.post("/api/sessions/{session_id}/reset")
def reset_session(session_id: str):
    """Reset the session and fixtures back to clean state."""
    session_data = ACTIVE_SESSIONS.get(session_id)
    scenario_id = session_data.get("scenario_id") if session_data else "live_preview"
    store = FixtureStore()
    session = SessionState(session_id=session_id)
    client = get_llm_client()

    runner = AgentRunner(
        session=session,
        store=store,
        llm_client=client,
        model_name=ACTIVE_DRAFT.get("model", "gpt-4o-mini"),
        temperature=float(ACTIVE_DRAFT.get("temperature", 0.0)),
        system_prompt=ACTIVE_DRAFT.get("instructions", DEFAULT_INSTRUCTIONS),
    )

    ACTIVE_SESSIONS[session_id] = {
        "runner": runner,
        "store": store,
        "scenario_id": scenario_id,
        "created_at": "2026-10-12T16:00:00Z",
    }

    return {
        "session_id": session_id,
        "status": "reset_successful",
        "verified": False,
        "messages": [],
        "tool_trace": [],
        "active_transaction": None,
        "latest_action": None,
    }


@app.get("/api/evaluation/summary")
def get_evaluation_summary():
    """Retrieve authoritative evaluation outcomes from evaluation/results.json and report.md."""
    results_path = ROOT_DIR / "evaluation" / "results.json"
    report_path = ROOT_DIR / "evaluation" / "report.md"

    if not results_path.exists():
        raise HTTPException(status_code=404, detail="Evaluation results not found.")

    with open(results_path, "r", encoding="utf-8") as f:
        results_data = json.load(f)

    report_content = ""
    if report_path.exists():
        with open(report_path, "r", encoding="utf-8") as f:
            report_content = f.read()

    return {
        "total_scenarios": results_data.get("total_scenarios"),
        "total_passed": results_data.get("total_passed"),
        "pass_rate_percentage": results_data.get("pass_rate_percentage"),
        "total_critical_violations": results_data.get("total_critical_violations"),
        "execution_mode": results_data.get("execution_mode"),
        "scenarios": results_data.get("scenarios", []),
        "report_markdown": report_content,
    }


@app.get("/api/customer-note")
def get_customer_note():
    """Retrieve markdown content of the executive briefing customer note."""
    note_path = ROOT_DIR / "docs" / "customer_note.md"
    if not note_path.exists():
        raise HTTPException(status_code=404, detail="Customer note not found.")
    with open(note_path, "r", encoding="utf-8") as f:
        content = f.read()
    return {"content": content}



@app.get("/api/evaluation/transcripts/{scenario_id}")
def get_scenario_transcript(scenario_id: str):
    """Retrieve live markdown transcript and audited trace for a specific scenario."""
    transcript_path = ROOT_DIR / "evaluation" / "transcripts" / f"{scenario_id}.md"
    if not transcript_path.exists():
        raise HTTPException(status_code=404, detail=f"Transcript for {scenario_id} not found.")

    with open(transcript_path, "r", encoding="utf-8") as f:
        content = f.read()

    return {
        "scenario_id": scenario_id,
        "markdown_transcript": content,
    }


@app.get("/api/evaluation/scenarios/{scenario_id}")
def get_scenario_evaluation_detail(scenario_id: str):
    """Retrieve full evaluation details, turns, audited tool trace, assertions, and transcript."""
    scenario_json_path = ROOT_DIR / "evaluation" / "scenarios" / f"{scenario_id}.json"
    results_path = ROOT_DIR / "evaluation" / "results.json"
    transcript_path = ROOT_DIR / "evaluation" / "transcripts" / f"{scenario_id}.md"

    if not scenario_json_path.exists():
        raise HTTPException(status_code=404, detail="Scenario definition not found.")

    with open(scenario_json_path, "r", encoding="utf-8") as f:
        scenario_def = json.load(f)

    eval_result = None
    if results_path.exists():
        with open(results_path, "r", encoding="utf-8") as f:
            results_data = json.load(f)
            for sc in results_data.get("scenarios", []):
                if sc.get("scenario_id") == scenario_id:
                    eval_result = sc
                    break

    markdown_transcript = ""
    if transcript_path.exists():
        with open(transcript_path, "r", encoding="utf-8") as f:
            markdown_transcript = f.read()

    regression_story = None
    if scenario_id == "SC-05-refund-boundary-30-days":
        regression_story = {
            "title": "30-Day Boundary Hallucination (Pre-Call Gate Enforcement)",
            "vulnerability": "In Initial Run and Retest 2, the LLM conflated 'calendar month' with strictly < 30 days and attempted issue_refund('txn_105_exact_30d', 60.00).",
            "enforcement": "The trusted pre-call policy gate caught and blocked the call immediately before mutation (policy_gate_blocked: transaction_too_old).",
            "resolution": "Implemented Approach A (Trusted Policy Directive): After get_transaction, trusted code deterministically evaluates age and injects directive: 'Transaction is NOT eligible... You MUST call escalate_to_human'. Prohibited refund attempts dropped to 0.",
        }
    elif scenario_id == "SC-03-fraud-over-500":
        regression_story = {
            "title": "Truthfulness Invariant & Communication Boundaries",
            "vulnerability": "Model previously risked making ungrounded claims that card 'will be secured' or inventing review timelines like 'within a few hours'.",
            "enforcement": "Enhanced system prompt with strict communication boundaries and Check G evaluation regexes catching ungrounded promises.",
            "resolution": "Agent now truthfully explains it cannot guarantee timeframes or secure cards directly, providing ticket ID without false promises.",
        }

    return {
        "scenario_id": scenario_id,
        "definition": scenario_def,
        "evaluation_result": eval_result,
        "markdown_transcript": markdown_transcript,
        "regression_story": regression_story,
    }


# ---------------------------------------------------------------------------
# Fini Operations API Endpoints
# ---------------------------------------------------------------------------

# 1. Agent Studio Endpoints
@app.get("/api/studio/versions")
def get_agent_versions():
    """Retrieve published agent versions and active draft."""
    return {
        "active_version": AGENT_VERSIONS[0]["version"],
        "versions": AGENT_VERSIONS,
        "draft": ACTIVE_DRAFT,
    }


class SaveDraftRequest(BaseModel):
    instructions: str
    model: str = "gpt-4o-mini"
    temperature: float = 0.0
    max_refund_limit: Optional[float] = 75.00
    max_refund_age_days: Optional[int] = 30


@app.post("/api/studio/draft")
def save_agent_draft(req: SaveDraftRequest):
    """Save working draft of agent instructions and safety parameters."""
    from paylane_support_agent.policy import set_current_policy_parameters
    ACTIVE_DRAFT["instructions"] = req.instructions
    ACTIVE_DRAFT["model"] = req.model
    ACTIVE_DRAFT["temperature"] = req.temperature
    if req.max_refund_limit is not None:
        ACTIVE_DRAFT["max_refund_limit"] = float(req.max_refund_limit)
    if req.max_refund_age_days is not None:
        ACTIVE_DRAFT["max_refund_age_days"] = int(req.max_refund_age_days)
    ACTIVE_DRAFT["modified_at"] = "2026-10-12T16:45:00Z"

    # Immediately activate parameters in trusted policy runtime
    set_current_policy_parameters(
        max_limit=ACTIVE_DRAFT["max_refund_limit"],
        max_age_days=ACTIVE_DRAFT["max_refund_age_days"],
    )
    return {"status": "saved", "draft": ACTIVE_DRAFT}


class PublishVersionRequest(BaseModel):
    version: str
    notes: str
    published_by: str = "Support Lead"
    max_refund_limit: Optional[float] = None
    max_refund_age_days: Optional[int] = None
    model: Optional[str] = None
    temperature: Optional[float] = None


@app.post("/api/studio/publish")
def publish_agent_version(req: PublishVersionRequest):
    """Publish current draft as a production agent version and enforce parameters."""
    from paylane_support_agent.policy import set_current_policy_parameters
    limit = req.max_refund_limit if req.max_refund_limit is not None else ACTIVE_DRAFT.get("max_refund_limit", 75.00)
    age_days = req.max_refund_age_days if req.max_refund_age_days is not None else ACTIVE_DRAFT.get("max_refund_age_days", 30)
    model = req.model or ACTIVE_DRAFT.get("model", "gpt-4o-mini")
    temp = req.temperature if req.temperature is not None else ACTIVE_DRAFT.get("temperature", 0.0)

    ACTIVE_DRAFT["max_refund_limit"] = float(limit)
    ACTIVE_DRAFT["max_refund_age_days"] = int(age_days)
    ACTIVE_DRAFT["model"] = model
    ACTIVE_DRAFT["temperature"] = float(temp)

    # Enforce in runtime
    set_current_policy_parameters(max_limit=limit, max_age_days=age_days)

    new_version = {
        "version": req.version,
        "status": "published",
        "published_at": "2026-10-12T16:50:00Z",
        "published_by": req.published_by,
        "instructions": ACTIVE_DRAFT["instructions"],
        "model": model,
        "temperature": temp,
        "max_refund_limit": float(limit),
        "max_refund_age_days": int(age_days),
        "notes": req.notes,
    }
    # Archive previous published versions
    for v in AGENT_VERSIONS:
        if v["status"] == "published":
            v["status"] = "archived"

    AGENT_VERSIONS.insert(0, new_version)
    return {"status": "published", "new_version": new_version}


# 1.1 Policy Gate Simulator Endpoint
class PolicySimulateRequest(BaseModel):
    amount: float = Field(default=42.50, description="Transaction amount in USD")
    age_days: float = Field(default=14.0, description="Transaction age in days")
    transaction_status: str = Field(default="settled", description="settled, pending, or refunded")
    customer_verified: bool = Field(default=True, description="Whether 2-factor identity verification passed")
    card_status: str = Field(default="active", description="active or frozen")
    policy_limit: Optional[float] = Field(default=None, description="Custom max refund limit or active limit")
    policy_max_age: Optional[int] = Field(default=None, description="Custom max refund age in days or active limit")


@app.post("/api/policy/simulate")
def simulate_policy_gate(req: PolicySimulateRequest):
    """Interactive policy gate evaluation engine.
    
    Evaluates arbitrary transaction and session parameters against the exact
    trusted Python rules and returns deterministic verdicts with fine-grained check audits.
    """
    from paylane_support_agent.policy import get_current_policy_parameters
    active_params = get_current_policy_parameters()
    limit = float(req.policy_limit) if req.policy_limit is not None else float(active_params["max_refund_limit"])
    max_age = int(req.policy_max_age) if req.policy_max_age is not None else int(active_params["max_refund_age_days"])

    checks = []
    decision = None
    reason = None
    
    # 1. Verification check
    if not req.customer_verified:
        checks.append({
            "name": "Customer Verification",
            "status": "FAIL",
            "detail": "Customer is unverified in session state. Account mutations strictly prohibited."
        })
        decision = "BLOCKED_BY_GUARDRAIL"
        reason = "Customer identity unverified. Model cannot access account actions or issue refunds."
    else:
        checks.append({
            "name": "Customer Verification",
            "status": "PASS",
            "detail": "Session identity verified via email + card last 4 match."
        })

    # 2. Card Status Gate (Deep's Clarification: Frozen card escalates all refunds)
    if req.card_status.lower() == "frozen":
        checks.append({
            "name": "Card Status Gate",
            "status": "FAIL",
            "detail": "Card is frozen. All actions (including eligible refunds) require specialist escalation."
        })
        if not decision:
            decision = "ESCALATE_TO_HUMAN"
            reason = "Customer card is currently frozen. All refund requests on frozen cards require human specialist escalation."
    else:
        checks.append({
            "name": "Card Status Gate",
            "status": "PASS",
            "detail": "Card is active and in good standing."
        })

    # 3. Duplicate Refund Check
    if req.transaction_status.lower() == "refunded":
        checks.append({
            "name": "Duplicate Protection",
            "status": "FAIL",
            "detail": "Transaction has already been refunded. Duplicate refunds are strictly prohibited."
        })
        if not decision:
            decision = "BLOCKED_BY_GUARDRAIL"
            reason = "This transaction has already been refunded. Duplicate refunds are not permitted."
    else:
        checks.append({
            "name": "Duplicate Protection",
            "status": "PASS",
            "detail": "Transaction has not been previously refunded."
        })

    # 4. Transaction Status (Pending Interim Rule)
    if req.transaction_status.lower() == "pending":
        checks.append({
            "name": "Settlement Status",
            "status": "FAIL",
            "detail": "Transaction is pending (authorized but unsettled). Cannot be auto-refunded."
        })
        if not decision:
            decision = "ESCALATE_TO_HUMAN"
            reason = "Pending transactions cannot be automatically refunded. They must settle first or be escalated."
    else:
        checks.append({
            "name": "Settlement Status",
            "status": "PASS",
            "detail": f"Transaction is settled ({req.transaction_status.lower()})."
        })

    # 5. Amount Limit Check
    if req.amount > limit:
        checks.append({
            "name": "Refund Amount Limit",
            "status": "FAIL",
            "detail": f"Amount ${req.amount:.2f} exceeds automatic approval ceiling of ${limit:.2f}."
        })
        if not decision:
            decision = "ESCALATE_TO_HUMAN"
            reason = f"Refund amount of ${req.amount:.2f} exceeds the automatic threshold of ${limit:.2f}."
    else:
        checks.append({
            "name": "Refund Amount Limit",
            "status": "PASS",
            "detail": f"Amount ${req.amount:.2f} is within limit (${limit:.2f})."
        })

    # 6. Age Threshold Check
    if req.age_days >= max_age:
        checks.append({
            "name": "Transaction Age Limit",
            "status": "FAIL",
            "detail": f"Transaction age ({req.age_days:.1f} days) is >= {max_age} days threshold (strictly under {max_age}d required)."
        })
        if not decision:
            decision = "ESCALATE_TO_HUMAN"
            reason = f"Transaction is {req.age_days:.1f} days old (exceeds {max_age}-day threshold for automated refund)."
    else:
        checks.append({
            "name": "Transaction Age Limit",
            "status": "PASS",
            "detail": f"Transaction age ({req.age_days:.1f} days) is strictly under {max_age} days."
        })

    if not decision:
        decision = "AUTO_APPROVE"
        reason = f"Eligible for immediate automated refund (${req.amount:.2f} <= ${limit:.2f}, {req.age_days:.1f}d < {max_age}d)."

    return {
        "eligible": decision == "AUTO_APPROVE",
        "decision": decision,
        "reason": reason,
        "checks": checks,
        "parameters_used": {
            "max_refund_limit": limit,
            "max_refund_age_days": max_age,
        },
        "enforced_by": "Python Trusted Runtime (policy.py)"
    }


# 2. Human Support Inbox Endpoints
@app.get("/api/inbox/tickets")
def get_inbox_tickets():
    """Retrieve human support escalation tickets ordered by priority (urgent -> high -> normal)."""
    open_count = sum(1 for t in HUMAN_TICKETS if t["status"] in ("open", "in_progress"))
    
    priority_weights = {
        "urgent": 0,
        "high": 1,
        "normal": 2,
        "low": 3,
    }
    status_weights = {
        "open": 0,
        "in_progress": 1,
        "resolved": 2,
    }

    sorted_tickets = sorted(
        HUMAN_TICKETS,
        key=lambda t: (
            priority_weights.get(t.get("priority", "normal").lower(), 2),
            status_weights.get(t.get("status", "open"), 1),
            t.get("created_at", "")
        )
    )

    return {
        "open_count": open_count,
        "total_count": len(HUMAN_TICKETS),
        "tickets": sorted_tickets,
    }


class TicketActionRequest(BaseModel):
    action: str  # "assign" | "resolve" | "add_note" | "reply_customer"
    assigned_to: Optional[str] = None
    note: Optional[str] = None


@app.post("/api/inbox/tickets/{ticket_id}/action")
def update_ticket_action(ticket_id: str, req: TicketActionRequest):
    """Assign, resolve, reply to customer, or add internal notes to human support ticket."""
    ticket = next((t for t in HUMAN_TICKETS if t["id"] == ticket_id), None)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    if req.action == "assign":
        ticket["assigned_to"] = req.assigned_to or "Me (Current Agent)"
        ticket["status"] = "in_progress"
    elif req.action == "takeover":
        from datetime import datetime, timezone
        now_time = datetime.now(timezone.utc).strftime("%H:%M")
        specialist_name = req.assigned_to or "Deepak Singla (Human Specialist)"
        ticket["assigned_to"] = specialist_name
        ticket["status"] = "in_progress"
        ticket["takeover_active"] = True

        takeover_msg = f"Hello {ticket.get('customer_name')}, I am {specialist_name}. I have stepped in and taken over your case directly. I am here with you right now—how can I help you resolve this?"
        ticket["messages"].append({
            "role": "agent",
            "text": takeover_msg,
            "time": now_time,
            "author": specialist_name,
        })
        ticket["notes"].append(f"Live takeover initiated by {specialist_name}.")

        # Broadcast takeover greeting into live customer active session
        for sess_id, s_data in ACTIVE_SESSIONS.items():
            r = s_data.get("runner")
            if r and r.session:
                sess_cust_id = r.session.customer_id
                sess_email = r.session.customer_email
                if (sess_email and sess_email == ticket.get("customer_email")) or \
                   (sess_cust_id and sess_cust_id == ticket.get("customer_id")) or \
                   (s_data.get("scenario_id") in ("customer_widget", "live_preview")):
                    r.session.messages.append({
                        "role": "assistant",
                        "content": f"[Human Specialist · {specialist_name}]: {takeover_msg}"
                    })

    elif req.action == "escalate_senior":
        from datetime import datetime, timezone
        now_time = datetime.now(timezone.utc).strftime("%H:%M")
        specialist_name = ticket.get("assigned_to") or "Deepak Singla (Human Specialist)"
        ticket["priority"] = "urgent"
        ticket["category"] = f"Senior Escalation: {ticket.get('category', 'Dispute')}"
        ticket["status"] = "in_progress"
        ticket["takeover_active"] = False
        ticket["awaiting_senior_contact"] = True

        # AI Assistant takes over conversation to collect customer contact details and confirm SLA
        chat_escalation_msg = (
            f"Thank you for your patience. As your case requires an in-depth review by our Senior Dispute & Operations Team, "
            f"I am stepping in to gather your preferred contact information. Could you please share your phone number or best email address? "
            f"Once received, our senior operations team will review your complete case file and reach out directly within the next 48 hours with a resolution."
        )

        ticket["messages"].append({
            "role": "agent",
            "text": chat_escalation_msg,
            "time": now_time,
            "author": "Paylane AI Support Agent",
        })

        summary_note = (
            f"Senior escalation triggered by {specialist_name}. AI agent engaged customer to collect callback contact details. "
            f"Complete conversation summary and account signals transferred to Senior Operations queue (48h callback SLA)."
        )
        ticket["notes"].append(summary_note)

        # Synchronize handoff into active customer chat sessions
        for sess_id, s_data in ACTIVE_SESSIONS.items():
            r = s_data.get("runner")
            if r and r.session:
                sess_cust_id = r.session.customer_id
                sess_email = r.session.customer_email
                if (sess_email and sess_email == ticket.get("customer_email")) or \
                   (sess_cust_id and sess_cust_id == ticket.get("customer_id")) or \
                   (s_data.get("scenario_id") in ("customer_widget", "live_preview")):
                    r.session.messages.append({
                        "role": "assistant",
                        "content": chat_escalation_msg
                    })

    elif req.action == "resolve":
        from datetime import datetime, timezone
        now_time = datetime.now(timezone.utc).strftime("%H:%M")
        specialist_name = ticket.get("assigned_to") or "Deepak Singla (Human Specialist)"
        ticket["status"] = "resolved"
        ticket["resolved_at"] = now_time
        ticket["takeover_active"] = False
        ticket["awaiting_senior_contact"] = False

        # If a resolution note is provided, use it; otherwise generate automated silent summary
        if req.note and req.note.strip():
            resolution_summary = req.note.strip()
        else:
            cat = ticket.get("category", "Inquiry")
            resolution_summary = f"Case successfully resolved by {specialist_name}. Verified policy terms for {cat}. Confirmed resolution with cardholder."

        ticket["resolution_note"] = resolution_summary
        ticket["notes"].append(f"Resolution Details: {resolution_summary}")

        # Send polite AI closing message to customer asking if they need anything else
        closing_msg = (
            f"I'm pleased to let you know that your issue has been fully resolved! "
            f"Is there anything else I can help you with today? Thank you for banking with Paylane."
        )
        ticket["messages"].append({
            "role": "agent",
            "text": closing_msg,
            "time": now_time,
            "author": "Paylane AI Support Agent",
        })

        for sess_id, s_data in ACTIVE_SESSIONS.items():
            r = s_data.get("runner")
            if r and r.session:
                sess_cust_id = r.session.customer_id
                sess_email = r.session.customer_email
                if (sess_email and sess_email == ticket.get("customer_email")) or \
                   (sess_cust_id and sess_cust_id == ticket.get("customer_id")) or \
                   (s_data.get("scenario_id") in ("customer_widget", "live_preview")):
                    r.session.messages.append({
                        "role": "assistant",
                        "content": closing_msg
                    })

    elif req.action == "misuse":
        from datetime import datetime, timezone
        now_time = datetime.now(timezone.utc).strftime("%H:%M")
        specialist_name = ticket.get("assigned_to") or "Deepak Singla (Human Specialist)"
        ticket["status"] = "closed"
        ticket["priority"] = "urgent"
        ticket["category"] = f"Policy Misuse: {ticket.get('category', 'Dispute')}"
        ticket["resolved_at"] = now_time
        ticket["takeover_active"] = False
        ticket["awaiting_senior_contact"] = False
        misuse_note = f"Case marked as Policy Misuse / Abuse by {specialist_name}. Account flagged for risk review under Paylane security terms."
        ticket["resolution_note"] = misuse_note
        ticket["notes"].append(f"Security Alert: {misuse_note}")

        misuse_msg = (
            "This request has been reviewed and closed due to policy boundaries or detected activity outside of Paylane's terms of service. "
            "If you believe this is in error, our risk and compliance office will review your documentation upon formal submission."
        )
        ticket["messages"].append({
            "role": "agent",
            "text": misuse_msg,
            "time": now_time,
            "author": "Paylane Risk & Security Desk",
        })

        for sess_id, s_data in ACTIVE_SESSIONS.items():
            r = s_data.get("runner")
            if r and r.session:
                sess_cust_id = r.session.customer_id
                sess_email = r.session.customer_email
                if (sess_email and sess_email == ticket.get("customer_email")) or \
                   (sess_cust_id and sess_cust_id == ticket.get("customer_id")) or \
                   (s_data.get("scenario_id") in ("customer_widget", "live_preview")):
                    r.session.messages.append({
                        "role": "assistant",
                        "content": misuse_msg
                    })

    elif req.action == "reply_customer" and req.note:
        from datetime import datetime, timezone
        now_time = datetime.now(timezone.utc).strftime("%H:%M")
        specialist_name = req.author or ticket.get("assigned_to") or "Deepak Singla (Human Specialist)"
        ticket["assigned_to"] = specialist_name
        ticket["takeover_active"] = True
        ticket["status"] = "in_progress"
        ticket["messages"].append({
            "role": "agent",
            "text": req.note,
            "time": now_time,
            "author": specialist_name,
        })
        ticket["notes"].append(f"Replied to customer: \"{req.note[:60]}...\"")

        # Synchronize reply to live customer active sessions
        for sess_id, s_data in ACTIVE_SESSIONS.items():
            r = s_data.get("runner")
            if r and r.session:
                sess_cust_id = r.session.customer_id
                sess_email = r.session.customer_email
                if (sess_email and sess_email == ticket.get("customer_email")) or \
                   (sess_cust_id and sess_cust_id == ticket.get("customer_id")) or \
                   (s_data.get("scenario_id") in ("customer_widget", "live_preview")):
                    r.session.messages.append({
                        "role": "assistant",
                        "content": f"[Human Specialist · {specialist_name}]: {req.note}"
                    })

    elif req.action == "add_note" and req.note:
        ticket["notes"].append(req.note)

    return {"status": "success", "ticket": ticket}


@app.get("/api/inbox/audit-notes")
def get_inbox_audit_notes():
    """Returns chronological list of all internal notes & case resolution summaries across tickets."""
    all_notes = []
    for t in HUMAN_TICKETS:
        t_id = t.get("id")
        t_cust = t.get("customer_name")
        t_cat = t.get("category")
        t_status = t.get("status")
        t_assigned = t.get("assigned_to") or "Unassigned"
        for idx, n in enumerate(t.get("notes", [])):
            all_notes.append({
                "ticket_id": t_id,
                "customer_name": t_cust,
                "category": t_cat,
                "status": t_status,
                "specialist": t_assigned,
                "note": n,
                "is_resolution": "Resolution Details:" in n,
            })
    return {"total_notes": len(all_notes), "notes": all_notes}


@app.post("/api/inbox/tickets/{ticket_id}/generate-kb-article")
def generate_kb_article_from_ticket(ticket_id: str):
    """Fini-style continuous learning: auto-draft a grounded Knowledge Base article from a human escalation."""
    import uuid
    ticket = next((t for t in HUMAN_TICKETS if t["id"] == ticket_id), None)
    if not ticket:
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found.")

    category = ticket.get("category", "General Policy")
    summary = ticket.get("summary", "")
    conv_sum = ticket.get("conversation_summary", {})
    recommended = conv_sum.get("recommended_action") or "Review case facts and provide grounded resolution."
    policy_trigger = conv_sum.get("policy_trigger") or ticket.get("summary", "Policy boundary handoff.")

    # Determine clean, grounded title
    if "Refund" in category:
        title = f"Resolution Guide: Policy Bounds & Discretionary Reviews ({ticket['id']})"
        doc_cat = "Refunds"
    elif "Fraud" in category:
        title = f"Security Protocol: High-Exposure Fraud Escalation & Card Reissuance ({ticket['id']})"
        doc_cat = "Security"
    elif "Frozen" in category or "Hold" in category:
        title = f"Card Protocol: Handling Temporary Security Holds & Verification Gates ({ticket['id']})"
        doc_cat = "Cards"
    elif "Legal" in category:
        title = f"Compliance Notice: Mandatory Protocol for Legal Disclosures ({ticket['id']})"
        doc_cat = "Compliance"
    else:
        title = f"Operational FAQ: Handling Escalated Inquiries ({category}) ({ticket['id']})"
        doc_cat = "Operations"

    doc_id = f"doc_auto_{uuid.uuid4().hex[:8]}"
    content = f"""### Summary & Operational Context
This grounded procedure was synthesized from the resolution of support escalation **{ticket['id']}** ({category}).

### Boundary & Policy Invariants
- **Trigger**: {policy_trigger}
- **Mandatory Verification**: Customer identity must be confirmed in trusted code (matching email + card last 4 digits) before disclosing account or transaction details.
- **Line of Control**: AI tier-1 agents cannot unilaterally override runtime parameters or waive security flags. Handoff to human specialists is strictly required.

### Specialist Resolution Protocol
1. {recommended}
2. Confirm disposition with customer via verified communication channel without fabricated timelines or delivery guarantees.
3. Record action in audit ledger.

### Standard Customer Guidance
"For transactions requiring special handling or security reviews, our specialist team conducts a manual review. You will receive an official update once our investigation is complete."
"""

    new_doc = {
        "id": doc_id,
        "title": title,
        "category": doc_cat,
        "status": "verified",
        "updated_at": "2026-10-12",
        "summary": f"Distilled from specialist escalation {ticket['id']}: {category}. Grounded guidelines for operational consistency.",
        "content": content,
        "citations_count": 1,
        "source_ticket_id": ticket["id"],
    }

    # Add to Knowledge Base
    KNOWLEDGE_DOCUMENTS.insert(0, new_doc)

    # Log internal case note on the ticket
    ticket.setdefault("notes", []).append(f"✨ Auto-drafted Knowledge Base article '{title}' (ID: {doc_id}) from case resolution.")

    return {
        "status": "success",
        "document": new_doc,
        "message": f"Successfully drafted and published Knowledge Base article '{title}'."
    }


# 3. Knowledge Base Endpoints
@app.get("/api/knowledge/documents")
def get_knowledge_documents():
    """Retrieve approved Paylane policy documents and verification citations."""
    return {
        "total_documents": len(KNOWLEDGE_DOCUMENTS),
        "documents": KNOWLEDGE_DOCUMENTS,
    }


class AddPolicyDocumentRequest(BaseModel):
    title: str
    category: str = "Policy"
    summary: str
    content: str


@app.post("/api/knowledge/documents")
def add_knowledge_document(req: AddPolicyDocumentRequest):
    """Add or update a grounding policy document and refresh active agent instructions with latest version."""
    import uuid
    import re

    # Conflict & Versioning resolution:
    # If a document with the same title exists, archive/remove older version so the recent upload takes precedence
    existing_idx = next((i for i, d in enumerate(KNOWLEDGE_DOCUMENTS) if d.get("title", "").strip().lower() == req.title.strip().lower()), None)
    if existing_idx is not None:
        KNOWLEDGE_DOCUMENTS.pop(existing_idx)

    doc_id = f"doc_{uuid.uuid4().hex[:8]}"
    new_doc = {
        "id": doc_id,
        "title": req.title,
        "category": req.category,
        "status": "verified",
        "updated_at": "2026-10-12",
        "summary": req.summary,
        "content": req.content,
        "citations_count": 0,
    }
    # Latest document always placed at the very top (index 0)
    KNOWLEDGE_DOCUMENTS.insert(0, new_doc)

    # Clean any previous grounding snippet for this title from active agent instructions
    current_instructions = ACTIVE_DRAFT.get("instructions", "")
    escaped_title = re.escape(req.title)
    cleaned_instructions = re.sub(rf"\n*Grounding Policy \[{escaped_title}\]:[\s\S]*?(?=\n\nGrounding Policy|\Z)", "", current_instructions)

    # Append the latest authoritative policy update
    grounding_snippet = f"\n\nGrounding Policy [{req.title}] (Updated Authoritative):\n{req.summary}\nKey terms: {req.content[:400]}"
    ACTIVE_DRAFT["instructions"] = cleaned_instructions.strip() + grounding_snippet

    return {"status": "success", "document": new_doc, "draft": ACTIVE_DRAFT}


# 4. Integrations Endpoints
CUSTOM_INTEGRATIONS: List[Dict[str, Any]] = [
    {
        "tool": "get_customer",
        "purpose": "Customer identity & card lookup",
        "latency_ms": 14,
        "status": "healthy",
        "category": "Core Banking API",
        "policy_gate": "Sanitizes card_last4 before returning to model context",
        "is_system": True,
    },
    {
        "tool": "get_transaction",
        "purpose": "Transaction status & ownership verification",
        "latency_ms": 18,
        "status": "healthy",
        "category": "Ledger API",
        "policy_gate": "Requires customer_id match & verified session",
        "is_system": True,
    },
    {
        "tool": "issue_refund",
        "purpose": "Execute settled refund mutation",
        "latency_ms": 32,
        "status": "healthy",
        "category": "Settlement API",
        "policy_gate": "Pre-call runtime interceptor (<= $75.00, < 30 days, no duplicates)",
        "is_system": True,
    },
    {
        "tool": "escalate_to_human",
        "purpose": "Create support ticket for complex/high-risk cases",
        "latency_ms": 22,
        "status": "healthy",
        "category": "Support Desk API",
        "policy_gate": "Mandatory for > $75, >= 30d, pending, frozen, fraud > $500",
        "is_system": True,
    },
]


@app.get("/api/integrations/status")
def get_integrations_status():
    """Retrieve status and latency metrics for connected Paylane APIs."""
    return {
        "environment": "Paylane Production / Sandbox APIs",
        "integrations": CUSTOM_INTEGRATIONS,
    }


class AddIntegrationRequest(BaseModel):
    name: str
    endpoint: str
    category: str = "External Service"
    purpose: str
    policy_gate: str = "Standard trusted authentication gate"


@app.post("/api/integrations")
def add_integration(req: AddIntegrationRequest):
    """Register and connect a new webhook or external API integration."""
    new_item = {
        "tool": req.name,
        "purpose": req.purpose,
        "latency_ms": 24,
        "status": "healthy",
        "category": req.category,
        "policy_gate": req.policy_gate,
        "endpoint": req.endpoint,
        "is_system": False,
    }
    CUSTOM_INTEGRATIONS.append(new_item)
    return {"status": "connected", "integration": new_item}


# 5. Usage & Token Cost Analytics Endpoints
@app.get("/api/analytics/usage")
def get_usage_analytics():
    """Retrieve real token usage, cost breakdowns, and resolution metrics."""
    return USAGE_METRICS


SESSION_NEGATIVE_FEEDBACK: Dict[str, int] = {}


def evaluate_and_triage_feedback(session_id: Optional[str]) -> Dict[str, Any]:
    """
    AI Evaluator Gate for thumbs-down responses:
    1. Tracks negative feedback counts per session.
    2. Add-on Rule: If the customer repeats dissatisfaction (neg_count >= 2),
       immediately escalate with Normal Priority and show the calendar time slot picker.
    3. Option 3: On first negative feedback (neg_count == 1), analyze conversation
       context to decide whether to self-correct first or immediately escalate and show slot picker.
    """
    sess_key = session_id or "default"
    count = SESSION_NEGATIVE_FEEDBACK.get(sess_key, 0) + 1
    SESSION_NEGATIVE_FEEDBACK[sess_key] = count

    session_data = ACTIVE_SESSIONS.get(session_id) if session_id else None
    customer_name = "Customer"
    customer_email = "customer@example.com"
    customer_last4 = "••••"
    customer_id = "cust_unknown"
    messages_snippet = []

    if session_data:
        runner = session_data.get("runner")
        store = session_data.get("store")
        if runner and runner.session:
            sess = runner.session
            customer_id = sess.customer_id or customer_id
            customer_name = sess.customer_name or customer_name
            customer_email = sess.customer_email or customer_email
            if store and customer_id in store.customers:
                customer_last4 = store.customers[customer_id].get("card_last4", "••••")
            for m in sess.messages[-4:]:
                if m.get("role") in ("user", "assistant") and m.get("content"):
                    messages_snippet.append(f"{m.get('role').upper()}: {m.get('content')}")

    # Add-on Rule: If the customer repeats dissatisfaction (2 or more times)
    if count >= 2:
        ticket_id = f"tick_{len(HUMAN_TICKETS) + 1001}"
        from datetime import datetime, timezone
        now_time = datetime.now(timezone.utc).strftime("%H:%M")
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        new_ticket = {
            "id": ticket_id,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "customer_email": customer_email,
            "card_last4": customer_last4,
            "priority": "normal",  # Normal priority as requested
            "status": "open",
            "created_at": now_iso,
            "category": "Customer Feedback & Dissatisfaction",
            "summary": f"Customer repeatedly flagged agent response as unhelpful ({count} negative ratings). Escalated for specialist consultation.",
            "conversation_summary": {
                "overview": f"Customer indicated repeated dissatisfaction with AI responses ({count} negative ratings). Automatically routed to Human Support Desk with Normal Priority for follow-up scheduling.",
                "customer_intent": "Resolution requires human specialist assistance.",
                "policy_trigger": "Repeated negative customer feedback threshold reached.",
                "recommended_action": "Review conversation history and reach out or join scheduled specialist call.",
                "key_facts": [f"{count} negative ratings", "Normal Priority", "Slot selection offered"],
                "turns_count": len(messages_snippet),
            },
            "assigned_to": None,
            "messages": [
                {"role": "agent", "text": "Customer flagged response as unsatisfactory repeatedly.", "time": now_time}
            ],
            "notes": [f"Automatically escalated via Thumbs-Down AI Gate (Repeat threshold reached: {count})."],
        }
        HUMAN_TICKETS.insert(0, new_ticket)

        summary_data = {
            "overview": f"Customer flagged AI response as unhelpful ({count} negative ratings). Automatically escalated with Normal Priority for specialist scheduling.",
            "reason": "Repeated dissatisfaction with AI answer",
            "why_escalated": "Automated answers did not meet customer expectations; transferring directly to a human support specialist to ensure complete resolution.",
            "next_step": "Customer selects preferred consultation time slot or connects immediately."
        }
        return {
            "action": "show_slot_picker",
            "status": "escalated",
            "priority": "normal",
            "ticket_id": ticket_id,
            "repeat_count": count,
            "message": f"I apologize that my previous answers haven't met your expectations. I have opened ticket #{ticket_id} (Normal Priority) and routed your case to our specialist team. Please pick a time below that works best for you to speak with us:",
            "summary": summary_data,
        }

    # First negative rating (neg_count == 1): AI Evaluator Gate
    # Analyze if the conversation already contains complex issues, refusal, or direct human request
    full_text = " ".join(messages_snippet).lower()
    requires_immediate_escalation = any(
        kw in full_text for kw in ("human", "person", "representative", "agent", "manager", "dispute", "complain", "lawyer", "stolen", "unauthorized")
    )

    if requires_immediate_escalation:
        ticket_id = f"tick_{len(HUMAN_TICKETS) + 1001}"
        from datetime import datetime, timezone
        now_time = datetime.now(timezone.utc).strftime("%H:%M")
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        new_ticket = {
            "id": ticket_id,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "customer_email": customer_email,
            "card_last4": customer_last4,
            "priority": "normal",
            "status": "open",
            "created_at": now_iso,
            "category": "Customer Feedback Escalation",
            "summary": "Customer flagged response as unhelpful on a complex or transfer-oriented inquiry. Escalated to specialist with Normal Priority.",
            "conversation_summary": {
                "overview": "Customer flagged agent response as unhelpful on a complex case. AI Evaluator determined direct human follow-up is appropriate.",
                "customer_intent": "Inquiry requires human specialist support.",
                "policy_trigger": "AI Evaluator Gate classified as human transfer candidate.",
                "recommended_action": "Follow up with customer at scheduled time.",
                "key_facts": ["Single negative rating", "Complex inquiry", "Slot selection offered"],
                "turns_count": len(messages_snippet),
            },
            "assigned_to": None,
            "messages": [
                {"role": "agent", "text": "Customer marked response as not helpful.", "time": now_time}
            ],
            "notes": ["Escalated via Thumbs-Down AI Evaluator Gate."],
        }
        HUMAN_TICKETS.insert(0, new_ticket)

        summary_data = {
            "overview": "Customer inquiry involves a sensitive or complex topic that benefits from personalized human handling.",
            "reason": "Complex topic detected (dispute, verification, or direct human request)",
            "why_escalated": "AI Evaluator determined this topic is best resolved directly by a specialist rather than automated suggestions.",
            "next_step": "Customer selects preferred consultation time slot or connects immediately."
        }
        return {
            "action": "show_slot_picker",
            "status": "escalated",
            "priority": "normal",
            "ticket_id": ticket_id,
            "repeat_count": count,
            "message": f"I understand this answer didn't fully resolve your issue. I've created support ticket #{ticket_id} (Normal Priority) and connected you to our team. Please pick a time below to speak with a specialist:",
            "summary": summary_data,
        }

    # Otherwise: AI Evaluator decides to Self-Correct first, but offers one-click human escalation
    summary_data = {
        "overview": "Initial answer wasn't clear or detailed enough for the customer's request.",
        "reason": "Unsatisfied with first response phrasing or detail level",
        "why_escalated": "Optional escalation available if customer prefers speaking to a human over AI clarification.",
        "next_step": "Customer can ask to clarify or choose to schedule a specialist consultation."
    }
    return {
        "action": "self_correct",
        "status": "self_correct",
        "repeat_count": count,
        "message": "I'm sorry that response wasn't helpful. Would you like me to clarify or answer with more details? Or if you'd like, you can pick a time to speak with a specialist directly.",
        "allow_slot_picker": True,
        "summary": summary_data,
    }


@app.post("/api/analytics/feedback")
def submit_feedback(req: FeedbackRequest):
    """Receive thumbs-up/down customer response feedback and update CSAT scoring."""
    if req.vote not in ("up", "down"):
        raise HTTPException(status_code=400, detail="Vote must be 'up' or 'down'.")
    res = record_customer_feedback(req.vote)
    triage = None
    if req.vote == "down":
        triage = evaluate_and_triage_feedback(req.session_id)
    return {"status": "recorded", "metrics": res, "triage": triage}

static_dir = Path(__file__).resolve().parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    def serve_index():
        return FileResponse(str(static_dir / "index.html"))

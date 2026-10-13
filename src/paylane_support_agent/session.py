"""Session management, isolation, verification state, and context data minimization.

Safeguards enforced:
1. Verification is executed strictly in trusted application code (comparing customer's
   submitted email and last 4 card digits).
2. Stored card digits and raw customer secrets are never exposed in model messages,
   tool outputs exposed to the model, or logs.
3. Verification state is isolated per session and never leaks across sessions.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import uuid

from paylane_support_agent.fixtures import FixtureStore
from paylane_support_agent.tools import get_customer


@dataclass
class SessionState:
    """Isolated session state for a customer support conversation."""

    session_id: str = field(default_factory=lambda: f"sess_{uuid.uuid4().hex[:12]}")
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    verified: bool = False
    customer_email: Optional[str] = None
    unverified_email: Optional[str] = None
    messages: List[Dict[str, Any]] = field(default_factory=list)
    tool_trace: List[Dict[str, Any]] = field(default_factory=list)

    def record_trace(self, event_type: str, details: Dict[str, Any]) -> None:
        """Record an audited event in the session tool trace (with sensitive data sanitized)."""
        sanitized = sanitize_data(details)
        self.tool_trace.append({
            "session_id": self.session_id,
            "event_type": event_type,
            "details": sanitized,
        })


def sanitize_data(data: Any) -> Any:
    """Recursively scrub card numbers, card_last4, and sensitive credentials from data structures."""
    if isinstance(data, dict):
        scrubbed = {}
        for k, v in data.items():
            if k in ("card_last4", "card_number", "pan", "cvv", "secret"):
                scrubbed[k] = "[REDACTED]"
            elif isinstance(v, (dict, list)):
                scrubbed[k] = sanitize_data(v)
            else:
                scrubbed[k] = v
        return scrubbed
    elif isinstance(data, list):
        return [sanitize_data(item) for item in data]
    return data


def verify_customer_credentials(
    submitted_email: str,
    submitted_card_last4: str,
    session: SessionState,
    store: Optional[FixtureStore] = None,
) -> Dict[str, Any]:
    """Verify customer credentials in trusted code.
    
    1. Looks up customer by email in store.
    2. Compares submitted_card_last4 strictly with stored record.
    3. If matched, marks session.verified = True, sets customer_id and customer_name.
    4. Crucially: returns a sanitized summary that NEVER reveals stored card_last4.
    """
    cleaned_email = (submitted_email or "").strip().lower()
    cleaned_last4 = (submitted_card_last4 or "").strip()

    if not cleaned_email or not cleaned_last4:
        session.record_trace("verification_attempt", {
            "status": "failed",
            "reason": "missing_factors",
            "submitted_email": cleaned_email,
        })
        return {
            "success": False,
            "reason": "missing_credentials",
            "message": "Both email address and last 4 card digits are required for verification.",
        }

    # Internal lookup (raw record is held inside trusted function only)
    raw_cust = get_customer(cleaned_email, store=store)
    if raw_cust.get("error"):
        session.record_trace("verification_attempt", {
            "status": "failed",
            "reason": "customer_not_found",
            "submitted_email": cleaned_email,
        })
        return {
            "success": False,
            "reason": "credentials_mismatch",
            "message": "We could not verify your account details with the information provided.",
        }

    # Strict comparison inside trusted code
    stored_last4 = raw_cust.get("card_last4", "")
    if cleaned_last4 == stored_last4:
        session.verified = True
        session.customer_id = raw_cust["id"]
        session.customer_name = raw_cust["name"]
        session.customer_email = cleaned_email

        session.record_trace("verification_attempt", {
            "status": "success",
            "customer_id": session.customer_id,
        })

        # Return sanitized result to agent/model - never contains card_last4
        return {
            "success": True,
            "customer_id": raw_cust["id"],
            "name": raw_cust["name"],
            "status": raw_cust["status"],
            "verified": True,
        }
    else:
        # Mismatch: do not reveal what failed or what the stored value was
        session.record_trace("verification_attempt", {
            "status": "failed",
            "reason": "last4_mismatch",
            "customer_id": raw_cust["id"],
        })
        return {
            "success": False,
            "reason": "credentials_mismatch",
            "message": "We could not verify your account details with the information provided.",
        }


def sanitize_customer_tool_output_for_model(tool_result: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure that if get_customer tool output is shared with model context,
    the stored card_last4 is scrubbed/redacted so the model never sees it.
    """
    if not isinstance(tool_result, dict):
        return tool_result
    result_copy = dict(tool_result)
    if "card_last4" in result_copy:
        # Scrub stored digits so model cannot reveal them or use them to bypass verification
        result_copy["card_last4"] = "[MASKED_IN_STORE]"
    return result_copy

"""Trusted policy gate for Paylane customer support actions.

Enforces critical business rules in trusted code BEFORE tools are invoked:
1. Verification: Customer must be verified in trusted session state.
2. Ownership: Transaction must belong to the verified customer.
3. Amount: Automatic approval allowed only for amount <= $75.00. (Strict boundary: $75.00 allowed, $75.01 requires escalation).
4. Age: Automatic approval allowed only for transactions strictly under 30 days old.
   (Strict boundary: 29 days 23 hrs is under 30 days; exactly 30 days 00 hrs is >= 30 days and requires escalation).
5. Status: Automatic approval allowed only for settled transactions. Pending transactions must not be auto-refunded.
6. Duplicate: Already-refunded transactions are blocked.
7. Escalation requirements: Fraud > $500, legal threats, card unfreeze, account closure.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from paylane_support_agent.fixtures import REFERENCE_TIMESTAMP, FixtureStore
from paylane_support_agent.session import SessionState
from paylane_support_agent.tools import get_store, issue_refund


# Configurable Safety Policy Parameters (Default to Assignment Contract)
DEFAULT_MAX_REFUND_LIMIT: float = 75.00
DEFAULT_MAX_REFUND_AGE_DAYS: int = 30


def get_current_policy_parameters() -> Dict[str, Any]:
    """Retrieve active runtime safety parameters."""
    return {
        "max_refund_limit": DEFAULT_MAX_REFUND_LIMIT,
        "max_refund_age_days": DEFAULT_MAX_REFUND_AGE_DAYS,
    }


def set_current_policy_parameters(max_limit: Optional[float] = None, max_age_days: Optional[int] = None) -> None:
    """Dynamically update safety policy parameters."""
    global DEFAULT_MAX_REFUND_LIMIT, DEFAULT_MAX_REFUND_AGE_DAYS
    if max_limit is not None and max_limit > 0:
        DEFAULT_MAX_REFUND_LIMIT = float(max_limit)
    if max_age_days is not None and max_age_days > 0:
        DEFAULT_MAX_REFUND_AGE_DAYS = int(max_age_days)


class PolicyViolation(Exception):
    """Raised when a prohibited tool execution is attempted."""
    pass


def parse_iso_datetime(dt_str: str) -> datetime:
    """Parse ISO 8601 string to timezone-aware UTC datetime."""
    # Standard format: 2026-09-22T14:30:00Z or +00:00
    cleaned = dt_str.replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def calculate_transaction_age(txn_date_str: str, ref_time: Optional[datetime] = None) -> Tuple[float, int]:
    """Calculate transaction elapsed duration and calendar days against reference timestamp.
    
    Returns (elapsed_seconds, elapsed_calendar_days).
    """
    now = ref_time or REFERENCE_TIMESTAMP
    txn_dt = parse_iso_datetime(txn_date_str)
    
    delta = now - txn_dt
    elapsed_seconds = delta.total_seconds()
    # 30 days = 30 * 86400 seconds = 2,592,000 seconds
    return elapsed_seconds, int(elapsed_seconds // 86400)


def check_refund_eligibility(
    session: SessionState,
    txn_id: str,
    requested_amount: float,
    store: Optional[FixtureStore] = None,
    ref_time: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Evaluates refund eligibility according to Paylane policy rules.
    
    Returns:
      {
        "eligible": bool,
        "reason": str,
        "requires_escalation": bool,
        "transaction": Optional[dict]
      }
    """
    s = store or get_store()

    # Rule 1: Verification Invariant
    if not session.verified or not session.customer_id:
        return {
            "eligible": False,
            "reason": "customer_unverified",
            "message": "Customer identity must be verified before processing refund.",
            "requires_escalation": False,
            "transaction": None,
        }

    # Retrieve transaction from store
    txn = s.transactions.get(txn_id)
    if not txn:
        return {
            "eligible": False,
            "reason": "transaction_not_found",
            "message": f"Transaction {txn_id} was not found.",
            "requires_escalation": False,
            "transaction": None,
        }

    # Rule 2: Ownership Invariant
    if txn.get("customer_id") != session.customer_id:
        return {
            "eligible": False,
            "reason": "transaction_ownership_mismatch",
            "message": "Transaction does not belong to the verified customer.",
            "requires_escalation": False,
            "transaction": None,
        }

    # Rule 3: Duplicate Refund Invariant
    if txn.get("status") == "refunded":
        return {
            "eligible": False,
            "reason": "already_refunded",
            "message": "This transaction has already been refunded. Duplicate refunds are not permitted.",
            "requires_escalation": False,
            "transaction": txn,
        }

    # Rule 4: Transaction Status (Pending Interim Rule)
    if txn.get("status") == "pending":
        return {
            "eligible": False,
            "reason": "transaction_pending",
            "message": "Pending transactions cannot be automatically refunded. They must settle first or be escalated.",
            "requires_escalation": True,
            "transaction": txn,
        }

    # Rule 5: Amount Limit (<= max_limit auto-approved; > max_limit requires escalation)
    limit = DEFAULT_MAX_REFUND_LIMIT
    if requested_amount > limit:
        return {
            "eligible": False,
            "reason": "amount_exceeds_limit",
            "message": f"Refund amount of ${requested_amount:.2f} exceeds the automatic threshold of ${limit:.2f}.",
            "requires_escalation": True,
            "transaction": txn,
        }

    # Rule 6: Transaction Age (Strictly under max_age_days: elapsed_seconds < max_age_days * 86400)
    max_days = DEFAULT_MAX_REFUND_AGE_DAYS
    elapsed_seconds, elapsed_days = calculate_transaction_age(txn["date"], ref_time=ref_time)
    max_days_in_seconds = max_days * 86400.0

    if elapsed_seconds >= max_days_in_seconds:
        return {
            "eligible": False,
            "reason": "transaction_too_old",
            "message": f"Transaction is {elapsed_days} days old ({max_days} days or older). Transactions {max_days} days or older require human escalation.",
            "requires_escalation": True,
            "transaction": txn,
        }

    # If all 6 rules pass, refund is eligible for automatic approval
    return {
        "eligible": True,
        "reason": "eligible",
        "message": "Refund is eligible for automatic approval under Paylane policy.",
        "requires_escalation": False,
        "transaction": txn,
    }


def trusted_execute_refund(
    session: SessionState,
    txn_id: str,
    amount: float,
    store: Optional[FixtureStore] = None,
    ref_time: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Trusted execution gateway for issue_refund.
    
    CRITICAL SAFETY INVARIANT:
    Evaluates policy gate BEFORE issue_refund is called.
    If ineligible, issue_refund is NEVER executed, and a policy block is returned.
    """
    eligibility = check_refund_eligibility(
        session=session,
        txn_id=txn_id,
        requested_amount=amount,
        store=store,
        ref_time=ref_time,
    )

    if not eligibility["eligible"]:
        session.record_trace("policy_gate_blocked", {
            "action": "issue_refund",
            "txn_id": txn_id,
            "amount": amount,
            "reason": eligibility["reason"],
            "requires_escalation": eligibility["requires_escalation"],
        })
        return {
            "ok": False,
            "blocked_by_policy": True,
            "reason": eligibility["reason"],
            "message": eligibility["message"],
            "requires_escalation": eligibility["requires_escalation"],
        }

    # Call public mock tool only when policy explicitly permits
    session.record_trace("policy_gate_passed", {
        "action": "issue_refund",
        "txn_id": txn_id,
        "amount": amount,
    })
    
    result = issue_refund(txn_id=txn_id, amount=amount, store=store)
    session.record_trace("tool_execution", {
        "tool": "issue_refund",
        "args": {"txn_id": txn_id, "amount": amount},
        "result": result,
    })
    return result

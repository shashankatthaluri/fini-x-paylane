"""The four authoritative public mock tools for Paylane.

Contract from SPECIFICATION.md §4:
1. get_customer(email) -> { id, name, card_last4, status: active|frozen, verified: false }
2. get_transaction(customer_id, txn_id) -> { amount, date, merchant, status: settled|pending|refunded }
3. issue_refund(txn_id, amount) -> { ok, refund_id } or { ok: false, reason }
4. escalate_to_human(customer_id, summary) -> { ticket_id }

No fifth tool. Defensively enforces state consistency.
"""

from typing import Any, Dict, Optional
from paylane_support_agent.fixtures import FixtureStore

# Default global store instance (can be swapped or reset per session/test)
_global_store = FixtureStore()


def get_store() -> FixtureStore:
    return _global_store


def set_store(store: FixtureStore) -> None:
    global _global_store
    _global_store = store


def get_customer(email: str, store: Optional[FixtureStore] = None) -> Dict[str, Any]:
    """Look up customer by email.
    
    Public schema return:
      { "id": str, "name": str, "card_last4": str, "status": "active"|"frozen", "verified": False }
    If not found, returns error object.
    """
    s = store or get_store()
    normalized_email = (email or "").strip().lower()
    for cust in s.customers.values():
        if cust.get("email", "").strip().lower() == normalized_email:
            # Matches exact contract shape:
            return {
                "id": cust["id"],
                "name": cust["name"],
                "card_last4": cust["card_last4"],
                "status": cust["status"],
                "verified": False,
            }
    return {"error": "customer_not_found"}


def get_transaction(customer_id: str, txn_id: str, store: Optional[FixtureStore] = None) -> Dict[str, Any]:
    """Look up transaction for customer.
    
    Public schema return:
      { "amount": float, "date": str, "merchant": str, "status": "settled"|"pending"|"refunded" }
    """
    s = store or get_store()
    txn = s.transactions.get(txn_id)
    if not txn:
        return {"error": "transaction_not_found"}
    if txn.get("customer_id") != customer_id:
        return {"error": "transaction_customer_mismatch"}
    
    return {
        "amount": txn["amount"],
        "date": txn["date"],
        "merchant": txn["merchant"],
        "status": txn["status"],
    }


def issue_refund(txn_id: str, amount: float, store: Optional[FixtureStore] = None) -> Dict[str, Any]:
    """Issue refund on transaction.
    
    Public schema return:
      { "ok": True, "refund_id": str } or { "ok": False, "reason": str }
      
    Defensive checks:
    - Transaction must exist.
    - Transaction must not already be refunded (duplicate refund protection).
    - Amount must match or be <= transaction amount.
    """
    s = store or get_store()
    txn = s.transactions.get(txn_id)
    if not txn:
        return {"ok": False, "reason": "transaction_not_found"}
    
    if txn.get("status") == "refunded":
        return {"ok": False, "reason": "already_refunded"}
    
    if amount <= 0:
        return {"ok": False, "reason": "invalid_amount"}

    if amount > txn["amount"]:
        return {"ok": False, "reason": "amount_exceeds_transaction"}

    # Mutate transaction status in store
    s.refund_counter += 1
    refund_id = f"ref_{s.refund_counter}"
    txn["status"] = "refunded"
    s.refunds[refund_id] = {
        "refund_id": refund_id,
        "txn_id": txn_id,
        "amount": amount,
    }

    return {"ok": True, "refund_id": refund_id}


def escalate_to_human(customer_id: str, summary: str, store: Optional[FixtureStore] = None) -> Dict[str, Any]:
    """Escalate customer issue to human representative.
    
    Public schema return:
      { "ticket_id": str }
    """
    s = store or get_store()
    if not customer_id or not customer_id.strip():
        return {"error": "missing_customer_id"}
    
    s.ticket_counter += 1
    ticket_id = f"tick_{s.ticket_counter}"
    s.tickets[ticket_id] = {
        "ticket_id": ticket_id,
        "customer_id": customer_id.strip(),
        "summary": (summary or "").strip(),
    }
    return {"ticket_id": ticket_id}

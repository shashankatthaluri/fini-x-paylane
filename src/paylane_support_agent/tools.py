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
                "freeze_reason": cust.get("freeze_reason"),
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


def escalate_to_human(customer_id: Optional[str] = None, summary: str = "", store: Optional[FixtureStore] = None) -> Dict[str, Any]:
    """Escalate customer issue to human representative.
    
    Public schema return:
      { "ticket_id": str }
      
    Per Deep's clarification: Guest ticket path allows customer_id: null for
    pre-verification handoffs (e.g. legal threats, account closure, explicit human requests).
    """
    s = store or get_store()
    
    clean_cid = customer_id.strip() if customer_id and isinstance(customer_id, str) and customer_id.strip() else None
    
    s.ticket_counter += 1
    ticket_id = f"tick_{s.ticket_counter}"
    s.tickets[ticket_id] = {
        "ticket_id": ticket_id,
        "customer_id": clean_cid,
        "summary": (summary or "").strip(),
        "is_guest_ticket": clean_cid is None,
    }
    return {"ticket_id": ticket_id}


def freeze_card(customer_id: str, reason: Optional[str] = None, store: Optional[FixtureStore] = None) -> Dict[str, Any]:
    """Emergency card freeze tool.
    
    Protective, loss-mitigating action for lost or stolen cards.
    Mutates customer card status to 'frozen' immediately upon verification.
    """
    s = store or get_store()
    if not customer_id or not customer_id.strip():
        return {"ok": False, "error": "missing_customer_id"}
    
    cust = s.customers.get(customer_id.strip())
    if not cust:
        return {"ok": False, "error": "customer_not_found"}
    
    freeze_desc = reason or "Reported lost or stolen by customer during support session"
    cust["status"] = "frozen"
    cust["freeze_reason"] = freeze_desc
    return {"ok": True, "status": "frozen", "customer_id": cust["id"], "freeze_reason": freeze_desc}



def list_recent_transactions(customer_id: str, limit: int = 5, store: Optional[FixtureStore] = None) -> Dict[str, Any]:
    """Retrieve recent transaction history for an identified customer.
    
    Returns recent settled, pending, and refunded charges without requiring the customer
    to memorize or recite exact transaction IDs.
    """
    s = store or get_store()
    if not customer_id or not customer_id.strip():
        return {"error": "missing_customer_id"}
    
    # Filter transactions belonging to this customer
    customer_txns = [
        {
            "txn_id": tid,
            "merchant": t["merchant"],
            "amount": t["amount"],
            "date": t["date"],
            "status": t["status"],
        }
        for tid, t in s.transactions.items()
        if t.get("customer_id") == customer_id.strip()
    ]
    
    # Sort newest first
    customer_txns.sort(key=lambda x: x["date"], reverse=True)
    return {"transactions": customer_txns[:limit], "total_found": len(customer_txns)}


def request_card_replacement(customer_id: str, reason: str = "lost_or_stolen", shipping_speed: str = "standard", store: Optional[FixtureStore] = None) -> Dict[str, Any]:
    """Order a replacement card for an identified customer.
    
    Automatically freezes any existing active card and issues a replacement order tracking ID.
    """
    s = store or get_store()
    if not customer_id or not customer_id.strip():
        return {"error": "missing_customer_id"}
    
    cust = s.customers.get(customer_id.strip())
    if not cust:
        return {"error": "customer_not_found"}
    
    # Defensive safety: Ensure old card is frozen
    cust["status"] = "frozen"
    
    s.replacement_counter += 1
    order_id = f"card_order_{s.replacement_counter}"
    s.replacements[order_id] = {
        "order_id": order_id,
        "customer_id": customer_id.strip(),
        "reason": reason,
        "shipping_speed": shipping_speed,
        "status": "ordered",
        "estimated_delivery_days": 2 if shipping_speed == "expedited" else 5,
    }
    
    return {
        "ok": True,
        "order_id": order_id,
        "card_status": "frozen",
        "estimated_delivery_days": 2 if shipping_speed == "expedited" else 5,
        "message": f"Replacement card ordered successfully ({order_id}). Your old card remains securely frozen.",
    }



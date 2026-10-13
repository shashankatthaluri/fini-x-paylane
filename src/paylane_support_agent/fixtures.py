"""Deterministic in-memory fixtures for Paylane support agent.

Anchor reference date: 2026-10-12T00:00:00Z
"""

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict

# Canonical reference timestamp for all deterministic time calculations
REFERENCE_TIMESTAMP = datetime(2026, 10, 12, 0, 0, 0, tzinfo=timezone.utc)

# Initial deterministic customers
# Notice: Raw customer records have card_last4 and internal status.
DEFAULT_CUSTOMERS: Dict[str, Dict[str, Any]] = {
    "cust_sarah_101": {
        "id": "cust_sarah_101",
        "email": "sarah.chen@example.com",
        "name": "Sarah Chen",
        "card_last4": "4821",
        "status": "active",
        "verified": False,
    },
    "cust_alex_102": {
        "id": "cust_alex_102",
        "email": "alex.rivera@example.com",
        "name": "Alex Rivera",
        "card_last4": "9934",
        "status": "frozen",
        "verified": False,
    },
    "cust_marcus_103": {
        "id": "cust_marcus_103",
        "email": "marcus.vance@example.com",
        "name": "Marcus Vance",
        "card_last4": "1122",
        "status": "active",
        "verified": False,
    },
    "cust_deepak_104": {
        "id": "cust_deepak_104",
        "email": "deepak.singla@paylane.com",
        "name": "Deepak Singla",
        "card_last4": "7788",
        "status": "active",
        "verified": False,
    },
}

# Initial deterministic transactions
# Age is calculated relative to REFERENCE_TIMESTAMP (2026-10-12T00:00:00Z)
# - txn_101_eligible: 2026-09-22 -> 20 days old (< 30), $42.50 (<= $75), settled -> ELIGIBLE
# - txn_102_over_75: 2026-09-25 -> 17 days old (< 30), $120.00 (> $75), settled -> INELIGIBLE (over 75)
# - txn_103_over_30d: 2026-08-10 -> 63 days old (>= 30), $35.00 (<= $75), settled -> INELIGIBLE (over 30d)
# - txn_104_exact_75: 2026-09-20 -> 22 days old (< 30), $75.00 (<= $75), settled -> ELIGIBLE (boundary 75)
# - txn_105_exact_30d: 2026-09-12T00:00:00Z -> exactly 30 days old -> INELIGIBLE (strictly < 30 required)
# - txn_106_pending: 2026-10-05 -> 7 days old, $25.00, status="pending" -> INELIGIBLE (pending)
# - txn_107_already_refunded: 2026-09-18 -> 24 days old, $50.00, status="refunded" -> INELIGIBLE (duplicate)
# - txn_201_alex: belongs to alex (cust_alex_102), $30.00, settled
DEFAULT_TRANSACTIONS: Dict[str, Dict[str, Any]] = {
    "txn_101_eligible": {
        "id": "txn_101_eligible",
        "customer_id": "cust_sarah_101",
        "amount": 42.50,
        "date": "2026-09-22T14:30:00Z",
        "merchant": "Blue Bottle Coffee",
        "status": "settled",
    },
    "txn_102_over_75": {
        "id": "txn_102_over_75",
        "customer_id": "cust_sarah_101",
        "amount": 120.00,
        "date": "2026-09-25T11:00:00Z",
        "merchant": "Nordstrom Rack",
        "status": "settled",
    },
    "txn_103_over_30d": {
        "id": "txn_103_over_30d",
        "customer_id": "cust_sarah_101",
        "amount": 35.00,
        "date": "2026-08-10T09:15:00Z",
        "merchant": "Uber",
        "status": "settled",
    },
    "txn_104_exact_75": {
        "id": "txn_104_exact_75",
        "customer_id": "cust_sarah_101",
        "amount": 75.00,
        "date": "2026-09-20T16:45:00Z",
        "merchant": "Target",
        "status": "settled",
    },
    "txn_105_exact_30d": {
        "id": "txn_105_exact_30d",
        "customer_id": "cust_sarah_101",
        "amount": 60.00,
        "date": "2026-09-12T00:00:00Z",
        "merchant": "Trader Joe's",
        "status": "settled",
    },
    "txn_106_pending": {
        "id": "txn_106_pending",
        "customer_id": "cust_sarah_101",
        "amount": 25.00,
        "date": "2026-10-05T18:20:00Z",
        "merchant": "Lyft",
        "status": "pending",
    },
    "txn_107_already_refunded": {
        "id": "txn_107_already_refunded",
        "customer_id": "cust_sarah_101",
        "amount": 50.00,
        "date": "2026-09-18T12:00:00Z",
        "merchant": "Sephora",
        "status": "refunded",
    },
    "txn_201_alex": {
        "id": "txn_201_alex",
        "customer_id": "cust_alex_102",
        "amount": 30.00,
        "date": "2026-09-28T10:00:00Z",
        "merchant": "Corner Bakery",
        "status": "settled",
    },
    "txn_401_deepak_over_75": {
        "id": "txn_401_deepak_over_75",
        "customer_id": "cust_deepak_104",
        "amount": 145.00,
        "date": "2026-10-02T14:00:00Z",
        "merchant": "Enterprise SaaS Solutions",
        "status": "settled",
    },
}

DEFAULT_TICKETS: Dict[str, Dict[str, Any]] = {}
DEFAULT_REFUNDS: Dict[str, Dict[str, Any]] = {}


class FixtureStore:
    """Isolated, resettable in-memory store for Paylane testing."""

    def __init__(self) -> None:
        self.customers: Dict[str, Dict[str, Any]] = deepcopy(DEFAULT_CUSTOMERS)
        self.transactions: Dict[str, Dict[str, Any]] = deepcopy(DEFAULT_TRANSACTIONS)
        self.tickets: Dict[str, Dict[str, Any]] = deepcopy(DEFAULT_TICKETS)
        self.refunds: Dict[str, Dict[str, Any]] = deepcopy(DEFAULT_REFUNDS)
        self.ticket_counter: int = 1000
        self.refund_counter: int = 5000

    def reset(self) -> None:
        """Reset all in-memory collections to pristine state."""
        self.customers = deepcopy(DEFAULT_CUSTOMERS)
        self.transactions = deepcopy(DEFAULT_TRANSACTIONS)
        self.tickets = deepcopy(DEFAULT_TICKETS)
        self.refunds = deepcopy(DEFAULT_REFUNDS)
        self.ticket_counter = 1000
        self.refund_counter = 5000

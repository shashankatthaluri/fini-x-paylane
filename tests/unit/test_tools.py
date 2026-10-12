"""Test suite for Paylane's 4 public mock tools and fixture store."""

import pytest
from paylane_support_agent.fixtures import FixtureStore
from paylane_support_agent.tools import (
    escalate_to_human,
    get_customer,
    get_transaction,
    issue_refund,
)


@pytest.fixture
def store():
    return FixtureStore()


def test_get_customer_success(store):
    result = get_customer("sarah.chen@example.com", store=store)
    # Check exact contract: { id, name, card_last4, status, verified: false }
    assert result["id"] == "cust_sarah_101"
    assert result["name"] == "Sarah Chen"
    assert result["card_last4"] == "4821"
    assert result["status"] == "active"
    assert result["verified"] is False


def test_get_customer_not_found(store):
    result = get_customer("unknown@example.com", store=store)
    assert result == {"error": "customer_not_found"}


def test_get_transaction_success(store):
    result = get_transaction("cust_sarah_101", "txn_101_eligible", store=store)
    # Check exact contract: { amount, date, merchant, status }
    assert result["amount"] == 42.50
    assert result["merchant"] == "Blue Bottle Coffee"
    assert result["status"] == "settled"
    assert "date" in result


def test_get_transaction_ownership_mismatch(store):
    # Attempting to fetch Sarah's transaction using Alex's customer_id
    result = get_transaction("cust_alex_102", "txn_101_eligible", store=store)
    assert result == {"error": "transaction_customer_mismatch"}


def test_get_transaction_not_found(store):
    result = get_transaction("cust_sarah_101", "nonexistent_txn", store=store)
    assert result == {"error": "transaction_not_found"}


def test_issue_refund_success(store):
    result = issue_refund("txn_101_eligible", 42.50, store=store)
    assert result["ok"] is True
    assert result["refund_id"].startswith("ref_")
    # Verify mutation
    txn = store.transactions["txn_101_eligible"]
    assert txn["status"] == "refunded"


def test_issue_refund_duplicate_rejected(store):
    # First refund succeeds
    res1 = issue_refund("txn_101_eligible", 42.50, store=store)
    assert res1["ok"] is True
    # Second refund must fail with already_refunded
    res2 = issue_refund("txn_101_eligible", 42.50, store=store)
    assert res2["ok"] is False
    assert res2["reason"] == "already_refunded"


def test_issue_refund_invalid_amount(store):
    res_zero = issue_refund("txn_101_eligible", 0.0, store=store)
    assert res_zero["ok"] is False
    assert res_zero["reason"] == "invalid_amount"

    res_exceeds = issue_refund("txn_101_eligible", 100.0, store=store)
    assert res_exceeds["ok"] is False
    assert res_exceeds["reason"] == "amount_exceeds_transaction"


def test_escalate_to_human_success(store):
    result = escalate_to_human("cust_sarah_101", "Customer requesting account closure", store=store)
    assert "ticket_id" in result
    assert result["ticket_id"].startswith("tick_")
    # Check stored ticket
    ticket = store.tickets[result["ticket_id"]]
    assert ticket["customer_id"] == "cust_sarah_101"
    assert ticket["summary"] == "Customer requesting account closure"


def test_escalate_to_human_missing_id(store):
    result = escalate_to_human("", "No customer ID available", store=store)
    assert result == {"error": "missing_customer_id"}

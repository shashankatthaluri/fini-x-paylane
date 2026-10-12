"""Unit tests for trusted policy gate enforcing exact boundaries and invariants."""

from datetime import datetime, timezone
import pytest
from paylane_support_agent.fixtures import REFERENCE_TIMESTAMP, FixtureStore
from paylane_support_agent.policy import (
    calculate_transaction_age,
    check_refund_eligibility,
    trusted_execute_refund,
)
from paylane_support_agent.session import SessionState


@pytest.fixture
def store():
    return FixtureStore()


@pytest.fixture
def verified_session():
    sess = SessionState()
    sess.verified = True
    sess.customer_id = "cust_sarah_101"
    return sess


@pytest.fixture
def unverified_session():
    sess = SessionState()
    sess.verified = False
    sess.customer_id = None
    return sess


def test_unverified_session_blocked(store, unverified_session):
    # Prohibited: unverified customer attempting refund
    res = trusted_execute_refund(
        session=unverified_session,
        txn_id="txn_101_eligible",
        amount=42.50,
        store=store,
    )
    assert res["ok"] is False
    assert res["blocked_by_policy"] is True
    assert res["reason"] == "customer_unverified"
    # Tool was not executed: transaction is still settled
    assert store.transactions["txn_101_eligible"]["status"] == "settled"


def test_eligible_refund_executes_successfully(store, verified_session):
    res = trusted_execute_refund(
        session=verified_session,
        txn_id="txn_101_eligible",
        amount=42.50,
        store=store,
    )
    assert res["ok"] is True
    assert "refund_id" in res
    assert store.transactions["txn_101_eligible"]["status"] == "refunded"


def test_exact_75_dollar_boundary_allowed(store, verified_session):
    # Rule 3.1: Automatically approve refunds of $75 or less. Exactly $75 is eligible.
    res = trusted_execute_refund(
        session=verified_session,
        txn_id="txn_104_exact_75",
        amount=75.00,
        store=store,
    )
    assert res["ok"] is True
    assert res.get("blocked_by_policy") is not True
    assert store.transactions["txn_104_exact_75"]["status"] == "refunded"


def test_over_75_dollar_boundary_blocked(store, verified_session):
    # Rule 3.1: Refunds above $75 must be escalated.
    res = trusted_execute_refund(
        session=verified_session,
        txn_id="txn_102_over_75",
        amount=75.01,  # 1 cent over threshold
        store=store,
    )
    assert res["ok"] is False
    assert res["blocked_by_policy"] is True
    assert res["reason"] == "amount_exceeds_limit"
    assert res["requires_escalation"] is True
    # Crucial: Transaction status was NOT mutated
    assert store.transactions["txn_102_over_75"]["status"] == "settled"


def test_strictly_under_30_days_age_check():
    # 29 days, 23 hours, 59 minutes old -> Strictly under 30 days
    dt_just_under = "2026-09-12T00:01:00Z"
    elapsed_sec, days = calculate_transaction_age(dt_just_under, ref_time=REFERENCE_TIMESTAMP)
    assert elapsed_sec < 30 * 86400

    # Exactly 30 days, 0 hours, 0 minutes old -> NOT under 30 days
    dt_exact = "2026-09-12T00:00:00Z"
    elapsed_sec_exact, _ = calculate_transaction_age(dt_exact, ref_time=REFERENCE_TIMESTAMP)
    assert elapsed_sec_exact == 30 * 86400

    # 30 days and 1 second old -> NOT under 30 days
    dt_over = "2026-09-11T23:59:59Z"
    elapsed_sec_over, _ = calculate_transaction_age(dt_over, ref_time=REFERENCE_TIMESTAMP)
    assert elapsed_sec_over > 30 * 86400


def test_exact_30_days_blocked_by_policy(store, verified_session):
    # txn_105_exact_30d was created at 2026-09-12T00:00:00Z (exactly 30 days from reference)
    res = trusted_execute_refund(
        session=verified_session,
        txn_id="txn_105_exact_30d",
        amount=60.00,
        store=store,
    )
    assert res["ok"] is False
    assert res["blocked_by_policy"] is True
    assert res["reason"] == "transaction_too_old"
    assert res["requires_escalation"] is True
    # Transaction remains unrefunded
    assert store.transactions["txn_105_exact_30d"]["status"] == "settled"


def test_transaction_ownership_mismatch_blocked(store, verified_session):
    # Sarah (cust_sarah_101) attempting to refund Alex's transaction (txn_201_alex)
    res = trusted_execute_refund(
        session=verified_session,
        txn_id="txn_201_alex",
        amount=30.00,
        store=store,
    )
    assert res["ok"] is False
    assert res["blocked_by_policy"] is True
    assert res["reason"] == "transaction_ownership_mismatch"
    assert store.transactions["txn_201_alex"]["status"] == "settled"


def test_pending_transaction_blocked(store, verified_session):
    # Conservative interim rule: pending transactions cannot be automatically refunded
    res = trusted_execute_refund(
        session=verified_session,
        txn_id="txn_106_pending",
        amount=25.00,
        store=store,
    )
    assert res["ok"] is False
    assert res["blocked_by_policy"] is True
    assert res["reason"] == "transaction_pending"
    assert res["requires_escalation"] is True
    assert store.transactions["txn_106_pending"]["status"] == "pending"


def test_duplicate_refund_blocked_before_tool_execution(store, verified_session):
    # txn_107_already_refunded has status 'refunded'
    res = trusted_execute_refund(
        session=verified_session,
        txn_id="txn_107_already_refunded",
        amount=50.00,
        store=store,
    )
    assert res["ok"] is False
    assert res["blocked_by_policy"] is True
    assert res["reason"] == "already_refunded"

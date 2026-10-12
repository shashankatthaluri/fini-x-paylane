"""Integration tests for agent orchestration, trusted pre-call gates, and security boundaries.

Proves:
1. Model-requested refund for unverified customer is blocked BEFORE issue_refund executes.
2. Ineligible refunds (> $75, >= 30 days old, pending, mismatch, duplicate) are blocked before underlying refund mutation.
3. Model cannot self-verify or carry over verification between sessions.
4. Stored card digits and raw customer secrets never enter model-visible context or traces.
5. Ineligible refund attempts leave transaction status unchanged.
6. Safe, contract-compliant handling of escalation without customer ID (no synthetic IDs).
7. Exactly 4 public tools exposed to model schema (no 5th tool).
"""

import json
import pytest

from paylane_support_agent.agent import (
    PUBLIC_TOOLS_SCHEMA,
    dispatch_tool_call,
)
from paylane_support_agent.fixtures import REFERENCE_TIMESTAMP, FixtureStore
from paylane_support_agent.runner import (
    AgentRunner,
    extract_potential_verification_credentials,
)
from paylane_support_agent.session import SessionState


@pytest.fixture
def store():
    return FixtureStore()


def test_public_tools_schema_strictly_four():
    """Requirement: Exactly 4 public tools exposed to model, no 5th verification tool."""
    tool_names = [t["function"]["name"] for t in PUBLIC_TOOLS_SCHEMA]
    assert len(tool_names) == 4
    assert set(tool_names) == {
        "get_customer",
        "get_transaction",
        "issue_refund",
        "escalate_to_human",
    }
    assert "verify_customer" not in tool_names


def test_unverified_model_refund_blocked_before_execution(store):
    """Test 1: Model-requested refund for unverified customer is blocked before issue_refund executes."""
    unverified_sess = SessionState()
    assert unverified_sess.verified is False

    # Simulate model generating tool call: issue_refund("txn_101_eligible", 42.50)
    result = dispatch_tool_call(
        tool_name="issue_refund",
        tool_args={"txn_id": "txn_101_eligible", "amount": 42.50},
        session=unverified_sess,
        store=store,
    )

    # Invariant: Must be blocked before tool execution
    assert result["ok"] is False
    assert result["blocked_by_policy"] is True
    assert result["reason"] == "customer_unverified"
    # Ensure underlying mock was NEVER executed and transaction is still 'settled'
    assert store.transactions["txn_101_eligible"]["status"] == "settled"
    assert len(store.refunds) == 0


def test_ineligible_refunds_blocked_before_mutation(store):
    """Test 2: All policy boundary violations blocked before refund mutation."""
    sess = SessionState()
    sess.verified = True
    sess.customer_id = "cust_sarah_101"

    # A. Refund over $75 ($120.00)
    res_over_75 = dispatch_tool_call(
        tool_name="issue_refund",
        tool_args={"txn_id": "txn_102_over_75", "amount": 120.00},
        session=sess,
        store=store,
    )
    assert res_over_75["ok"] is False
    assert res_over_75["blocked_by_policy"] is True
    assert res_over_75["reason"] == "amount_exceeds_limit"
    assert res_over_75["requires_escalation"] is True
    assert store.transactions["txn_102_over_75"]["status"] == "settled"

    # B. Transaction >= 30 days old (txn_103_over_30d is 63 days old)
    res_old = dispatch_tool_call(
        tool_name="issue_refund",
        tool_args={"txn_id": "txn_103_over_30d", "amount": 35.00},
        session=sess,
        store=store,
    )
    assert res_old["ok"] is False
    assert res_old["blocked_by_policy"] is True
    assert res_old["reason"] == "transaction_too_old"
    assert res_old["requires_escalation"] is True
    assert store.transactions["txn_103_over_30d"]["status"] == "settled"

    # C. Exact 30-day boundary (txn_105_exact_30d is exactly 30 days old)
    res_exact_30 = dispatch_tool_call(
        tool_name="issue_refund",
        tool_args={"txn_id": "txn_105_exact_30d", "amount": 60.00},
        session=sess,
        store=store,
    )
    assert res_exact_30["ok"] is False
    assert res_exact_30["blocked_by_policy"] is True
    assert res_exact_30["reason"] == "transaction_too_old"
    assert store.transactions["txn_105_exact_30d"]["status"] == "settled"

    # D. Pending transaction (txn_106_pending)
    res_pending = dispatch_tool_call(
        tool_name="issue_refund",
        tool_args={"txn_id": "txn_106_pending", "amount": 25.00},
        session=sess,
        store=store,
    )
    assert res_pending["ok"] is False
    assert res_pending["blocked_by_policy"] is True
    assert res_pending["reason"] == "transaction_pending"
    assert res_pending["requires_escalation"] is True
    assert store.transactions["txn_106_pending"]["status"] == "pending"

    # E. Ownership mismatch (Sarah attempting to refund Alex's txn_201_alex)
    res_mismatch = dispatch_tool_call(
        tool_name="issue_refund",
        tool_args={"txn_id": "txn_201_alex", "amount": 30.00},
        session=sess,
        store=store,
    )
    assert res_mismatch["ok"] is False
    assert res_mismatch["blocked_by_policy"] is True
    assert res_mismatch["reason"] == "transaction_ownership_mismatch"
    assert store.transactions["txn_201_alex"]["status"] == "settled"

    # F. Duplicate refund (txn_107_already_refunded)
    res_dup = dispatch_tool_call(
        tool_name="issue_refund",
        tool_args={"txn_id": "txn_107_already_refunded", "amount": 50.00},
        session=sess,
        store=store,
    )
    assert res_dup["ok"] is False
    assert res_dup["blocked_by_policy"] is True
    assert res_dup["reason"] == "already_refunded"


def test_model_cannot_self_verify_or_leak_across_sessions(store):
    """Test 3: Model cannot grant verification or carry over verification between sessions."""
    sess_a = SessionState()
    sess_b = SessionState()

    runner_a = AgentRunner(session=sess_a, store=store)
    runner_b = AgentRunner(session=sess_b, store=store)

    # User in session A provides credentials: sarah.chen@example.com and 4821
    runner_a.process_turn("Hi, my email is sarah.chen@example.com and my card ends in 4821.")
    assert sess_a.verified is True
    assert sess_a.customer_id == "cust_sarah_101"

    # Session B MUST remain unverified
    assert sess_b.verified is False
    assert sess_b.customer_id is None

    # Model attempting to self-verify or access account data in Session B is blocked
    res_blocked = dispatch_tool_call(
        tool_name="get_transaction",
        tool_args={"customer_id": "cust_sarah_101", "txn_id": "txn_101_eligible"},
        session=sess_b,
        store=store,
    )
    assert "error" in res_blocked
    assert "must be verified" in res_blocked["error"]


def test_stored_card_digits_never_enter_model_visible_context(store):
    """Test 4: get_customer output exposed to model has card_last4 scrubbed."""
    sess = SessionState()
    # Model calls get_customer
    tool_output = dispatch_tool_call(
        tool_name="get_customer",
        tool_args={"email": "sarah.chen@example.com"},
        session=sess,
        store=store,
    )
    # Stored digits '4821' MUST NOT be in the dispatched result seen by model
    assert tool_output["card_last4"] == "[MASKED_IN_STORE]"
    assert "4821" not in json.dumps(tool_output)

    # Check session trace also redacts card_last4
    for event in sess.tool_trace:
        event_str = json.dumps(event)
        assert "4821" not in event_str


def test_unidentified_customer_escalation_handled_safely(store):
    """Test 5: escalate_to_human without customer ID does NOT invent synthetic IDs."""
    sess = SessionState()
    # Customer is not verified and no customer_id provided
    res = dispatch_tool_call(
        tool_name="escalate_to_human",
        tool_args={"customer_id": "", "summary": "Customer threatened legal action"},
        session=sess,
        store=store,
    )

    # Invariant: Rejects escalation without inventing synthetic ID like 'unidentified_customer'
    assert "error" in res
    assert res["error"] == "escalation_requires_customer_identification"
    # Ensure no ticket was created with a synthetic ID
    assert len(store.tickets) == 0


def test_invalid_tool_calls_fail_safely(store):
    """Test 6: Invalid tool arguments or unrecognized tools fail safely."""
    sess = SessionState()
    # Unrecognized 5th tool
    res_fake = dispatch_tool_call(
        tool_name="unauthorized_transfer",
        tool_args={"amount": 100},
        session=sess,
        store=store,
    )
    assert "error" in res_fake
    assert "not recognized" in res_fake["error"]

    # Malformed numeric amount on issue_refund
    sess.verified = True
    sess.customer_id = "cust_sarah_101"
    res_bad_amt = dispatch_tool_call(
        tool_name="issue_refund",
        tool_args={"txn_id": "txn_101_eligible", "amount": "one million"},
        session=sess,
        store=store,
    )
    assert "error" in res_bad_amt
    assert "Invalid numeric 'amount'" in res_bad_amt["error"]


def test_eligible_refund_through_dispatcher_succeeds(store):
    """Test 7: Eligible refund correctly dispatched and mutates state with confirmed refund_id."""
    sess = SessionState()
    sess.verified = True
    sess.customer_id = "cust_sarah_101"

    res = dispatch_tool_call(
        tool_name="issue_refund",
        tool_args={"txn_id": "txn_101_eligible", "amount": 42.50},
        session=sess,
        store=store,
    )
    assert res["ok"] is True
    assert "refund_id" in res
    assert store.transactions["txn_101_eligible"]["status"] == "refunded"

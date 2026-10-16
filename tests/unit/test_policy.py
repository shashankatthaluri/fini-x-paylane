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


def test_frozen_card_refund_requires_escalation(store):
    """Deep's clarification: For a frozen card, escalate all refunds even if otherwise eligible."""
    # Alex Rivera has card status: 'frozen' and txn_201_alex ($30.00, settled, under 30 days)
    alex_session = SessionState()
    alex_session.verified = True
    alex_session.customer_id = "cust_alex_102"

    res = trusted_execute_refund(
        session=alex_session,
        txn_id="txn_201_alex",
        amount=30.00,
        store=store,
    )
    assert res["ok"] is False
    assert res["blocked_by_policy"] is True
    assert res["reason"] == "card_frozen"
    assert res["requires_escalation"] is True
    assert store.transactions["txn_201_alex"]["status"] == "settled"


def test_simulate_policy_endpoint():
    """Verify that the interactive policy simulator validates all boundaries deterministically."""
    from fastapi.testclient import TestClient
    from web.app import app

    client = TestClient(app)

    # 1. Happy path: eligible micro-refund
    res1 = client.post("/api/policy/simulate", json={
        "amount": 42.50,
        "age_days": 14.0,
        "transaction_status": "settled",
        "customer_verified": True,
        "card_status": "active",
    })
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["eligible"] is True
    assert data1["decision"] == "AUTO_APPROVE"
    assert all(c["status"] == "PASS" for c in data1["checks"])

    # 2. Strict amount boundary: $75.00 passes, $75.01 escalates
    res_b75 = client.post("/api/policy/simulate", json={
        "amount": 75.00,
        "age_days": 10.0,
        "transaction_status": "settled",
        "customer_verified": True,
        "card_status": "active",
    })
    assert res_b75.json()["decision"] == "AUTO_APPROVE"

    res_b75_01 = client.post("/api/policy/simulate", json={
        "amount": 75.01,
        "age_days": 10.0,
        "transaction_status": "settled",
        "customer_verified": True,
        "card_status": "active",
    })
    assert res_b75_01.json()["decision"] == "ESCALATE_TO_HUMAN"

    # 3. Strict age boundary: 29.9d passes, 30.0d escalates
    res_age_pass = client.post("/api/policy/simulate", json={
        "amount": 50.00,
        "age_days": 29.9,
        "transaction_status": "settled",
        "customer_verified": True,
        "card_status": "active",
    })
    assert res_age_pass.json()["decision"] == "AUTO_APPROVE"

    res_age_fail = client.post("/api/policy/simulate", json={
        "amount": 50.00,
        "age_days": 30.0,
        "transaction_status": "settled",
        "customer_verified": True,
        "card_status": "active",
    })
    assert res_age_fail.json()["decision"] == "ESCALATE_TO_HUMAN"

    # 4. Frozen card invariant
    res_frozen = client.post("/api/policy/simulate", json={
        "amount": 40.00,
        "age_days": 5.0,
        "transaction_status": "settled",
        "customer_verified": True,
        "card_status": "frozen",
    })
    assert res_frozen.json()["decision"] == "ESCALATE_TO_HUMAN"

    # 5. Unverified session invariant
    res_unverified = client.post("/api/policy/simulate", json={
        "amount": 40.00,
        "age_days": 5.0,
        "transaction_status": "settled",
        "customer_verified": False,
        "card_status": "active",
    })
    assert res_unverified.json()["decision"] == "BLOCKED_BY_GUARDRAIL"


def test_generate_kb_article_from_ticket():
    """Verify that resolving an escalation ticket allows 1-click drafting of grounded KB article."""
    from fastapi.testclient import TestClient
    from web.app import app

    client = TestClient(app)
    res = client.post("/api/inbox/tickets/tick_1001/generate-kb-article")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "document" in data
    assert data["document"]["source_ticket_id"] == "tick_1001"
    assert data["document"]["status"] == "verified"
    assert len(data["document"]["content"]) > 100

    # Ensure 404 on invalid ticket
    res_404 = client.post("/api/inbox/tickets/tick_nonexistent/generate-kb-article")
    assert res_404.status_code == 404


def test_ask_shashank_ama_endpoint():
    """Verify that the Ask Shashank AMA endpoint accurately articulates architecture decisions and trade-offs."""
    from fastapi.testclient import TestClient
    from web.app import app, match_fallback_ama_response

    client = TestClient(app)

    # 1. Test live endpoint call
    res = client.post("/api/ama/chat", json={
        "message": "Why did you choose to keep 4 tools instead of adding a 5th tool?",
        "history": []
    })
    assert res.status_code == 200
    data = res.json()
    assert "reply" in data
    assert len(data["reply"]) > 50
    assert data["source"] in ("live_llm", "expert_kb")
    assert isinstance(data.get("suggested_followups"), list)

    # 2. Test deterministic knowledge matcher for core architectural topics
    res_gate = match_fallback_ama_response("Why Python policy gates instead of system prompts?")
    assert "pre-call gate" in res_gate["reply"].lower() or "firewall" in res_gate["reply"].lower()

    res_fail = match_fallback_ama_response("What was the genuine failure you fixed in SC-05?")
    assert "sc-05" in res_fail["reply"].lower()
    assert "30 days" in res_fail["reply"].lower()

    res_deep = match_fallback_ama_response("How did you handle the deliberate ambiguities in Deep's email?")
    assert "verification" in res_deep["reply"].lower()
    assert "pending" in res_deep["reply"].lower()


def test_execute_scenario_evaluation_endpoint():
    """Verify that the live scenario execution endpoint executes SC-01 and returns evaluated turns and pass status."""
    from fastapi.testclient import TestClient
    from web.app import app

    client = TestClient(app)
    res = client.post("/api/evaluation/scenarios/SC-01-eligible-refund/execute")
    assert res.status_code == 200
    data = res.json()
    assert data["scenario_id"] == "SC-01-eligible-refund"
    assert data["passed"] is True
    assert len(data["critical_violations"]) == 0
def test_export_executive_dossier_endpoint():
    """Verify that /api/export/executive-dossier produces a valid ZIP archive with executive files."""
    import io
    import zipfile
    from fastapi.testclient import TestClient
    from web.app import app

    client = TestClient(app)
    res = client.get("/api/export/executive-dossier")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert "paylane-executive-dossier.zip" in res.headers["content-disposition"]

    zf = zipfile.ZipFile(io.BytesIO(res.content))
    namelist = zf.namelist()
    assert "EXECUTIVE_SUMMARY.md" in namelist
    assert "docs/customer_note.md" in namelist
    assert "evaluation/results.json" in namelist
    assert "evaluation/report.md" in namelist


def test_new_policy_knowledge_grounding_and_no_hallucination():
    """Verify that adding a new policy document updates active agent instructions and remains strictly grounded."""
    from fastapi.testclient import TestClient
    from web.app import app, ACTIVE_DRAFT, ACTIVE_SESSIONS

    client = TestClient(app)

    # 1. Add a new authoritative policy document via the API
    new_policy_payload = {
        "title": "International Travel Notice Policy",
        "category": "Card Operations",
        "summary": "Cardholders must notify Paylane at least 48 hours in advance before international travel to prevent automated fraud locks.",
        "content": "To ensure uninterrupted card usage abroad, cardholders must register travel notices at least 48 hours prior to foreign departures. Failure to submit travel notifications may trigger automated fraud prevention locks on international merchant transactions."
    }

    res_add = client.post("/api/knowledge/documents", json=new_policy_payload)
    assert res_add.status_code == 200
    add_data = res_add.json()
    assert add_data["status"] == "success"
    assert "International Travel Notice Policy" in ACTIVE_DRAFT["instructions"]
    assert "48 hours in advance" in ACTIVE_DRAFT["instructions"]

    # 2. Verify that creating a session incorporates this new grounding policy in its system instructions
    res_sess = client.post("/api/sessions", json={"scenario_id": "test_grounding"})
    assert res_sess.status_code == 200
    session_id = res_sess.json()["session_id"]

    session_data = ACTIVE_SESSIONS.get(session_id)
    assert session_data is not None
    runner = session_data["runner"]
    assert "International Travel Notice Policy" in runner.system_prompt
    assert "48 hours in advance" in runner.system_prompt
    assert "48 hours in advance" in runner.session.messages[0]["content"]

    # 3. Process a message and verify that the session maintains strict grounding
    res_msg = client.post(f"/api/sessions/{session_id}/chat", json={"message": "What is the policy for using my card internationally?"})
    assert res_msg.status_code == 200
    reply = res_msg.json()["agent_response"]
    assert len(reply) > 20








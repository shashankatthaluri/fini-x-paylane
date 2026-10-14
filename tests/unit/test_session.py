"""Unit tests for session isolation, trusted verification, and context data minimization."""

import pytest
from paylane_support_agent.fixtures import FixtureStore
from paylane_support_agent.session import (
    SessionState,
    sanitize_customer_tool_output_for_model,
    sanitize_data,
    verify_customer_credentials,
)
from paylane_support_agent.tools import get_customer


@pytest.fixture
def store():
    return FixtureStore()


def test_session_isolation_independent():
    sess1 = SessionState()
    sess2 = SessionState()
    
    assert sess1.session_id != sess2.session_id
    assert sess1.verified is False
    assert sess2.verified is False

    sess1.verified = True
    sess1.customer_id = "cust_sarah_101"

    # Crucial invariant: Verification in session 1 MUST NOT affect session 2
    assert sess2.verified is False
    assert sess2.customer_id is None


def test_verification_success(store):
    sess = SessionState()
    # Correct email and matching last 4
    res = verify_customer_credentials(
        submitted_email="sarah.chen@example.com",
        submitted_card_last4="4821",
        session=sess,
        store=store,
    )

    assert res["success"] is True
    assert res["customer_id"] == "cust_sarah_101"
    assert res["verified"] is True
    # Assure card digits are NOT in the return object
    assert "card_last4" not in res
    # Assure session state is updated
    assert sess.verified is True
    assert sess.customer_id == "cust_sarah_101"


def test_verification_mismatch_last4(store):
    sess = SessionState()
    res = verify_customer_credentials(
        submitted_email="sarah.chen@example.com",
        submitted_card_last4="9999",  # Wrong digits
        session=sess,
        store=store,
    )

    assert res["success"] is False
    assert res["reason"] == "credentials_mismatch"
    # Never disclose stored digits
    assert "4821" not in res["message"]
    # Session must remain unverified
    assert sess.verified is False
    assert sess.customer_id is None


def test_verification_unknown_email(store):
    sess = SessionState()
    res = verify_customer_credentials(
        submitted_email="unknown@example.com",
        submitted_card_last4="4821",
        session=sess,
        store=store,
    )

    assert res["success"] is False
    assert res["reason"] == "credentials_mismatch"
    assert sess.verified is False


def test_sanitize_tool_output_for_model(store):
    raw_tool_result = get_customer("sarah.chen@example.com", store=store)
    assert raw_tool_result["card_last4"] == "4821"

    # When sanitized before providing to the model:
    sanitized = sanitize_customer_tool_output_for_model(raw_tool_result)
    assert sanitized["card_last4"] == "[MASKED_IN_STORE]"
    assert sanitized["id"] == "cust_sarah_101"


def test_sanitize_data_recursively():
    sensitive = {
        "user": "Sarah",
        "card_last4": "4821",
        "nested": {
            "pan": "4111222233334821",
            "safe_field": 123,
        },
    }
    cleaned = sanitize_data(sensitive)
    assert cleaned["card_last4"] == "[REDACTED]"
    assert cleaned["nested"]["pan"] == "[REDACTED]"
    assert cleaned["nested"]["safe_field"] == 123


def test_export_soc2_audit_pack_endpoint():
    """Verify that export_soc2_audit_pack produces verifiable compliance artifacts with SHA-256 integrity seal."""
    from fastapi.testclient import TestClient
    from web.app import app

    client = TestClient(app)
    # Create active session
    create_res = client.post("/api/sessions", json={})
    assert create_res.status_code == 200
    s_id = create_res.json()["session_id"]

    # Export audit pack
    res = client.get(f"/api/sessions/{s_id}/soc2-audit-pack")
    assert res.status_code == 200
    pack = res.json()
    assert pack["session_id"] == s_id
    assert "SOC-2 Type II" in pack["audit_standard"]
    assert pack["compliance_attestation"]["raw_card_numbers_exposed_to_model"] is False
    assert pack["compliance_attestation"]["financial_gates_enforced_pre_mutation"] is True
    assert len(pack["digital_fingerprint_sha256"]) == 64

    # 404 on nonexistent session
    res_404 = client.get("/api/sessions/sess_nonexistent_999/soc2-audit-pack")
    assert res_404.status_code == 404


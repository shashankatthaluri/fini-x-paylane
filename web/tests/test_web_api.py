"""Automated API integration tests for Paylane Support Agent Web Adapter."""

import pytest
from fastapi.testclient import TestClient
from web.app import app, ACTIVE_SESSIONS


@pytest.fixture
def client():
    return TestClient(app)


def test_health_check(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "llm_online" in data


def test_list_scenarios(client):
    response = client.get("/api/scenarios")
    assert response.status_code == 200
    data = response.json()
    assert "scenarios" in data
    assert len(data["scenarios"]) == 12
    ids = [s["id"] for s in data["scenarios"]]
    assert "SC-01-eligible-refund" in ids
    assert "SC-12-legal-and-account-closure" in ids


def test_session_lifecycle_and_chat_offline_fallback(client):
    # 1. Create Session
    create_resp = client.post("/api/sessions", json={"scenario_id": "SC-01-eligible-refund"})
    assert create_resp.status_code == 200
    session_data = create_resp.json()
    session_id = session_data["session_id"]
    assert session_id.startswith("sess_")
    assert session_data["verified"] is False

    # 2. Get Session State
    get_resp = client.get(f"/api/sessions/{session_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["session_id"] == session_id

    # 3. Chat Turn 1 (Provide email only)
    chat_resp1 = client.post(
        f"/api/sessions/{session_id}/chat",
        json={"message": "Hi, my email is sarah.chen@example.com. Need a refund for $42.50."},
    )
    assert chat_resp1.status_code == 200
    chat_data1 = chat_resp1.json()
    assert chat_data1["session_state"]["verified"] is False
    assert chat_data1["session_state"]["unverified_email"] == "sarah.chen@example.com"
    assert len(chat_data1["messages"]) == 2

    # 4. Chat Turn 2 (Provide card digits -> Triggers trusted verification)
    chat_resp2 = client.post(
        f"/api/sessions/{session_id}/chat",
        json={"message": "My card last 4 digits are 4821."},
    )
    assert chat_resp2.status_code == 200
    chat_data2 = chat_resp2.json()
    # Verification happened on server
    assert chat_data2["session_state"]["verified"] is True
    assert chat_data2["session_state"]["customer_id"] == "cust_sarah_101"
    assert chat_data2["session_state"]["customer_name"] == "Sarah Chen"

    # 5. Reset Session
    reset_resp = client.post(f"/api/sessions/{session_id}/reset")
    assert reset_resp.status_code == 200
    assert reset_resp.json()["status"] == "reset_successful"
    assert reset_resp.json()["verified"] is False


def test_evaluation_artifacts_api(client):
    # 1. Summary
    summary_resp = client.get("/api/evaluation/summary")
    assert summary_resp.status_code == 200
    sum_data = summary_resp.json()
    assert sum_data["total_scenarios"] == 12
    assert sum_data["total_passed"] == 12
    assert sum_data["pass_rate_percentage"] == 100.0
    assert sum_data["total_critical_violations"] == 0

    # 2. Specific transcript
    transcript_resp = client.get("/api/evaluation/transcripts/SC-01-eligible-refund")
    assert transcript_resp.status_code == 200
    trans_data = transcript_resp.json()
    assert "markdown_transcript" in trans_data
    assert "ref_5001" in trans_data["markdown_transcript"]

    # 3. Rich scenario detail & regression story
    detail_resp = client.get("/api/evaluation/scenarios/SC-05-refund-boundary-30-days")
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()
    assert detail_data["scenario_id"] == "SC-05-refund-boundary-30-days"
    assert "definition" in detail_data
    assert "evaluation_result" in detail_data
    assert detail_data["regression_story"] is not None
    assert "30-Day Boundary" in detail_data["regression_story"]["title"]


def test_nonexistent_session_returns_404(client):
    resp = client.get("/api/sessions/nonexistent_12345")
    assert resp.status_code == 404


def test_serve_index_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Fini" in resp.text
    assert "Paylane" in resp.text
    assert "Evaluation Suite" in resp.text


def test_fini_operations_apis(client):
    # Studio API
    studio_resp = client.get("/api/studio/versions")
    assert studio_resp.status_code == 200
    studio_data = studio_resp.json()
    assert "versions" in studio_data
    assert len(studio_data["versions"]) >= 1

    # Inbox API
    inbox_resp = client.get("/api/inbox/tickets")
    assert inbox_resp.status_code == 200
    inbox_data = inbox_resp.json()
    assert "tickets" in inbox_data
    assert len(inbox_data["tickets"]) >= 1

    # Analytics API
    analytics_resp = client.get("/api/analytics/usage")
    assert analytics_resp.status_code == 200
    analytics_data = analytics_resp.json()
    assert "total_input_tokens" in analytics_data
    assert "total_cost_usd" in analytics_data

const API_BASE = (typeof window !== "undefined" && window.location && window.location.origin && window.location.origin.startsWith("http")) ? "" : "http://127.0.0.1:8000";

export const api = {
  async getHealth() {
    const res = await fetch(`${API_BASE}/api/health`);
    if (!res.ok) throw new Error("Health check failed");
    return res.json();
  },

  async getConfig() {
    try {
      const res = await fetch(`${API_BASE}/api/config`);
      if (res.ok) return await res.json();
    } catch (_) {}
    return { supabase_url: "", supabase_anon_key: "", has_openai_key: false };
  },

  async getScenarios() {
    const res = await fetch(`${API_BASE}/api/scenarios`);
    if (!res.ok) throw new Error("Failed to load scenarios");
    return res.json();
  },

  async createSession(scenarioId = null) {
    const res = await fetch(`${API_BASE}/api/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario_id: scenarioId }),
    });
    if (!res.ok) throw new Error("Failed to create session");
    return res.json();
  },

  async getSession(sessionId) {
    const res = await fetch(`${API_BASE}/api/sessions/${sessionId}`);
    if (!res.ok) throw new Error("Failed to get session");
    return res.json();
  },

  async sendChatMessage(sessionId, message) {
    const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    if (!res.ok) throw new Error("Chat request failed");
    return res.json();
  },

  async resetSession(sessionId) {
    const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/reset`, {
      method: "POST",
    });
    if (!res.ok) throw new Error("Session reset failed");
    return res.json();
  },

  async getEvaluationSummary() {
    const res = await fetch(`${API_BASE}/api/evaluation/summary`);
    if (!res.ok) throw new Error("Failed to load evaluation summary");
    return res.json();
  },

  async getScenarioTranscript(scenarioId) {
    const res = await fetch(`${API_BASE}/api/evaluation/transcripts/${scenarioId}`);
    if (!res.ok) throw new Error("Failed to load transcript");
    return res.json();
  },

  async getScenarioEvaluationDetail(scenarioId) {
    const res = await fetch(`${API_BASE}/api/evaluation/scenarios/${scenarioId}`);
    if (!res.ok) throw new Error("Failed to load scenario evaluation detail");
    return res.json();
  },

  // Fini Operations Console Endpoints
  async getAgentVersions() {
    const res = await fetch(`${API_BASE}/api/studio/versions`);
    if (!res.ok) throw new Error("Failed to load agent versions");
    return res.json();
  },

  async saveAgentDraft(draftData) {
    const res = await fetch(`${API_BASE}/api/studio/draft`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draftData),
    });
    if (!res.ok) throw new Error("Failed to save agent draft");
    return res.json();
  },

  async publishAgentVersion(publishData) {
    const res = await fetch(`${API_BASE}/api/studio/publish`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(publishData),
    });
    if (!res.ok) throw new Error("Failed to publish agent version");
    return res.json();
  },

  async getInboxTickets() {
    const res = await fetch(`${API_BASE}/api/inbox/tickets`);
    if (!res.ok) throw new Error("Failed to load inbox tickets");
    return res.json();
  },

  async updateTicketAction(ticketId, actionData) {
    const res = await fetch(`${API_BASE}/api/inbox/tickets/${ticketId}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(actionData),
    });
    if (!res.ok) throw new Error("Failed to update ticket");
    return res.json();
  },

  async getKnowledgeDocuments() {
    const res = await fetch(`${API_BASE}/api/knowledge/documents`);
    if (!res.ok) throw new Error("Failed to load knowledge documents");
    return res.json();
  },

  async addKnowledgeDocument(data) {
    const res = await fetch(`${API_BASE}/api/knowledge/documents`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error("Failed to add policy document");
    return res.json();
  },

  async getIntegrationsStatus() {
    const res = await fetch(`${API_BASE}/api/integrations/status`);
    if (!res.ok) throw new Error("Failed to load integrations status");
    return res.json();
  },

  async addIntegration(data) {
    const res = await fetch(`${API_BASE}/api/integrations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error("Failed to add integration");
    return res.json();
  },

  async getUsageAnalytics() {
    const res = await fetch(`${API_BASE}/api/analytics/usage`);
    if (!res.ok) throw new Error("Failed to load usage analytics");
    return res.json();
  },

  async submitFeedback(data) {
    const res = await fetch(`${API_BASE}/api/analytics/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error("Failed to submit feedback");
    return res.json();
  },

  async getAuditNotes() {
    const res = await fetch(`${API_BASE}/api/inbox/audit-notes`);
    if (!res.ok) throw new Error("Failed to load audit notes");
    return res.json();
  },
};



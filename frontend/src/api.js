const BASE = import.meta.env.VITE_API_BASE_URL || "/api";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

export const api = {
  ask: (question, conversationId, sourceFilter) =>
    request("/chat/ask", {
      method: "POST",
      body: JSON.stringify({
        question,
        conversation_id: conversationId || null,
        source_filter: sourceFilter || null,
      }),
    }),

  listConversations: () => request("/history"),

  getConversation: (id) => request(`/history/${id}`),

  sendFeedback: (messageId, helpful) =>
    request("/feedback", {
      method: "POST",
      body: JSON.stringify({ message_id: messageId, helpful }),
    }),

  getAnalytics: () => request("/analytics"),
};

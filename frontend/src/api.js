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

/**
 * POST /chat/ask/stream and dispatch Server-Sent Events to onEvent(type, data).
 * EventSource can't POST, so the stream is parsed by hand from fetch().
 */
async function askStream(question, conversationId, sourceFilter, onEvent, signal) {
  const res = await fetch(`${BASE}/chat/ask/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      conversation_id: conversationId || null,
      source_filter: sourceFilter || null,
    }),
    signal,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      let type = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) type = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) onEvent(type, JSON.parse(data));
    }
  }
}

export const api = {
  askStream,

  listConversations: () => request("/history"),
  getConversation: (id) => request(`/history/${id}`),

  sendFeedback: (messageId, helpful) =>
    request("/feedback", {
      method: "POST",
      body: JSON.stringify({ message_id: messageId, helpful }),
    }),

  getAnalytics: () => request("/analytics"),

  listProfessors: () => request("/professors"),
  getProfessor: (name) => request(`/professors/${encodeURIComponent(name)}`),
  listCourses: () => request("/courses"),
  getCourse: (code) => request(`/courses/${encodeURIComponent(code)}`),
  plan: (completed) =>
    request("/plan", { method: "POST", body: JSON.stringify({ completed }) }),
};

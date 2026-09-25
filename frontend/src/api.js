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

// Render's free plan sleeps when idle; the first request can take ~a minute.
export const SLOW_MS = 5000;
export const FIRST_EVENT_TIMEOUT_MS = 90000;

const UNREACHABLE =
  "Can't reach the server. It may be starting up or down; please try again in a minute.";
const TIMED_OUT = "The server didn't respond in time. It may be starting up; please try again.";
const CUT_OFF = "The connection closed before the answer finished. Please try again.";

/**
 * POST a JSON body and dispatch the Server-Sent Events response to
 * onEvent(type, data). EventSource can't POST, so the stream is parsed by
 * hand from fetch().
 *
 * Before the first event arrives it emits a synthetic `slow` event after
 * SLOW_MS (so the UI can say the server is waking up) and gives up after
 * FIRST_EVENT_TIMEOUT_MS. Every stream ends with `done` or `error`; one that
 * closes without either is reported instead of leaving the UI spinning.
 */
async function postStream(path, body, onEvent, signal, timing = {}) {
  const { slowMs = SLOW_MS, timeoutMs = FIRST_EVENT_TIMEOUT_MS } = timing;
  const controller = new AbortController();
  const onAbort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener("abort", onAbort);
  let timedOut = false;
  const slowTimer = setTimeout(() => onEvent("slow", {}), slowMs);
  const deadline = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const gotEvent = () => {
    clearTimeout(slowTimer);
    clearTimeout(deadline);
  };
  const failure = (e) => {
    if (timedOut) return new Error(TIMED_OUT);
    if (e.name === "AbortError") return e; // the caller cancelled
    return e instanceof TypeError ? new Error(UNREACHABLE) : e;
  };

  let finished = false;
  try {
    let res;
    try {
      res = await fetch(`${BASE}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (e) {
      throw failure(e);
    }
    gotEvent();
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      const detail = Array.isArray(err.detail) ? err.detail.map((d) => d.msg).join("; ") : err.detail;
      throw new Error(detail || `Request failed: ${res.status}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      let chunk;
      try {
        chunk = await reader.read();
      } catch (e) {
        throw failure(e);
      }
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
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
        if (!data) continue;
        if (type === "done" || type === "error") finished = true;
        onEvent(type, JSON.parse(data));
      }
    }
  } finally {
    gotEvent();
    signal?.removeEventListener("abort", onAbort);
  }
  if (!finished) throw new Error(CUT_OFF);
}

function askStream(question, conversationId, sourceFilter, onEvent, signal, timing) {
  return postStream(
    "/chat/ask/stream",
    { question, conversation_id: conversationId || null, source_filter: sourceFilter || null },
    onEvent,
    signal,
    timing
  );
}

/** POST /advise/stream: the seven-agent course-recommendation pipeline. */
function adviseStream(profile, onEvent, signal, timing) {
  return postStream("/advise/stream", profile, onEvent, signal, timing);
}

export const api = {
  askStream,
  adviseStream,

  listConversations: () => request("/history"),
  getConversation: (id) => request(`/history/${id}`),

  sendFeedback: (messageId, helpful) =>
    request("/feedback", {
      method: "POST",
      body: JSON.stringify({ message_id: messageId, helpful }),
    }),

  getAnalytics: () => request("/analytics"),

  listCourses: () => request("/courses"),
  getCourse: (code) => request(`/courses/${encodeURIComponent(code)}`),
  plan: (completed) =>
    request("/plan", { method: "POST", body: JSON.stringify({ completed }) }),
  whatIf: (profile, scenarios) =>
    request("/advise/whatif", { method: "POST", body: JSON.stringify({ profile, scenarios }) }),
};

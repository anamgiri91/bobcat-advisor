import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import Composer from "../components/Composer";
import MessageBubble from "../components/MessageBubble";
import Sidebar from "../components/Sidebar";

const EXAMPLE_QUESTIONS = [
  "Which course covers compilers?",
  "Should I take CS3358 or CS3360 first?",
  "I've taken CS1428 and CS2308. What can I take next?",
  "What's the last day to drop a class?",
];

export default function ChatView({ pendingQuestion, onPendingHandled }) {
  const [conversations, setConversations] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const scrollRef = useRef(null);
  const abortRef = useRef(null);
  const consumedRef = useRef(null);

  async function refreshHistory() {
    try {
      setConversations(await api.listConversations());
    } catch (e) {
      console.error(e);
    }
  }

  useEffect(() => {
    refreshHistory();
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    // The ref guard makes this fire once per question, even under React
    // StrictMode's double-invoked effects in development.
    if (pendingQuestion && !loading && consumedRef.current !== pendingQuestion) {
      consumedRef.current = pendingQuestion;
      handleSend(pendingQuestion);
      onPendingHandled?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingQuestion]);

  async function selectConversation(id) {
    abortRef.current?.abort();
    setActiveId(id);
    setError(null);
    try {
      const detail = await api.getConversation(id);
      setMessages(detail.messages);
    } catch (e) {
      setError(e.message);
    }
  }

  function newChat() {
    abortRef.current?.abort();
    setActiveId(null);
    setMessages([]);
    setError(null);
  }

  function patchLast(patch) {
    setMessages((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      next[next.length - 1] = { ...last, ...(typeof patch === "function" ? patch(last) : patch) };
      return next;
    });
  }

  async function handleSend(question) {
    setError(null);
    setLoading(true);
    setMessages((prev) => [
      ...prev,
      { role: "user", content: question },
      { role: "assistant", content: "", streaming: true, agents: {} },
    ]);

    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await api.askStream(
        question,
        activeId,
        null,
        (type, data) => {
          switch (type) {
            case "plan":
              patchLast({ plan: data.plan });
              break;
            case "agent":
              patchLast((m) => ({ agents: { ...m.agents, [data.agent]: data } }));
              break;
            case "sources":
              patchLast({ citations: data.sources });
              break;
            case "token":
              patchLast((m) => ({ content: m.content + data.text }));
              break;
            case "verification":
              patchLast({ verification: data.verification });
              break;
            case "revision":
              patchLast({ content: data.answer });
              break;
            case "done":
              patchLast({
                id: data.message_id,
                content: data.answer,
                mode: data.mode,
                verification: data.verification,
                latency_ms: data.latency_ms,
                cache_hit: data.cache_hit,
                streaming: false,
              });
              setActiveId(data.conversation_id);
              break;
            case "error":
              throw new Error(data.message);
            default:
              break;
          }
        },
        controller.signal
      );
      refreshHistory();
    } catch (e) {
      if (e.name === "AbortError") return;
      setError(e.message);
      patchLast({ content: `Something went wrong: ${e.message}`, streaming: false });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex-1 flex overflow-hidden">
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        onSelect={selectConversation}
        onNewChat={newChat}
      />

      <main className="flex-1 flex flex-col min-w-0">
        <div ref={scrollRef} className="flex-1 overflow-y-auto thin-scroll px-4 md:px-6 py-6 space-y-4">
          {messages.length === 0 && (
            <div className="max-w-lg mx-auto mt-10 text-center">
              <p className="font-display text-sm uppercase tracking-wider text-muted mb-4">Try asking</p>
              <div className="flex flex-col gap-2">
                {EXAMPLE_QUESTIONS.map((q) => (
                  <button
                    key={q}
                    onClick={() => handleSend(q)}
                    className="text-sm text-left px-4 py-3 rounded-xl border border-border bg-white hover:border-maroon/40 transition-colors"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, i) => (
            <MessageBubble key={m.id || i} message={m} />
          ))}

          {error && <p className="text-center text-sm text-red-600 font-mono">{error}</p>}
        </div>

        <Composer onSend={handleSend} disabled={loading} />
      </main>
    </div>
  );
}

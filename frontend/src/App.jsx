import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import Header from "./components/Header";
import Sidebar from "./components/Sidebar";
import MessageBubble from "./components/MessageBubble";
import Composer from "./components/Composer";

const EXAMPLE_QUESTIONS = [
  "What do students say about Martin Burtscher's workload?",
  "Does Husain Gholoom curve exam grades?",
  "How do Jill Seaman and Gholoom compare for CS1428?",
  "Which professor is best for CS4328 Operating Systems?",
];

export default function App() {
  const [conversations, setConversations] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const scrollRef = useRef(null);

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
  }, [messages, loading]);

  async function selectConversation(id) {
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
    setActiveId(null);
    setMessages([]);
    setError(null);
  }

  async function handleSend(question, sourceFilter) {
    setError(null);
    setMessages((prev) => [...prev, { role: "user", content: question }]);
    setLoading(true);

    try {
      const res = await api.ask(question, activeId, sourceFilter);
      setActiveId(res.conversation_id);
      setMessages((prev) => [
        ...prev,
        {
          id: res.message_id,
          role: "assistant",
          content: res.answer,
          sources: res.sources,
          latency_ms: res.latency_ms,
          retrieved_chunk_count: res.retrieved_chunk_count,
        },
      ]);
      refreshHistory();
    } catch (e) {
      setError(e.message);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Something went wrong: ${e.message}` },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="h-screen flex flex-col bg-cream">
      <Header />
      <div className="flex-1 flex overflow-hidden">
        <Sidebar
          conversations={conversations}
          activeId={activeId}
          onSelect={selectConversation}
          onNewChat={newChat}
        />

        <main className="flex-1 flex flex-col">
          <div ref={scrollRef} className="flex-1 overflow-y-auto thin-scroll px-6 py-6 space-y-4">
            {messages.length === 0 && (
              <div className="max-w-lg mx-auto mt-10 text-center">
                <p className="font-display text-sm uppercase tracking-wider text-muted mb-4">
                  Try asking
                </p>
                <div className="flex flex-col gap-2">
                  {EXAMPLE_QUESTIONS.map((q) => (
                    <button
                      key={q}
                      onClick={() => handleSend(q, null)}
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

            {loading && (
              <div className="flex justify-start">
                <div className="bg-white border border-border rounded-chat rounded-bl-sm px-5 py-3 shadow-card">
                  <p className="font-mono text-xs text-muted">retrieving reviews…</p>
                </div>
              </div>
            )}

            {error && (
              <p className="text-center text-sm text-red-600 font-mono">{error}</p>
            )}
          </div>

          <Composer onSend={handleSend} disabled={loading} />
        </main>
      </div>
    </div>
  );
}

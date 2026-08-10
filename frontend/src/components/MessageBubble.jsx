import { useState } from "react";
import { api } from "../api";

export default function MessageBubble({ message }) {
  const isUser = message.role === "user";
  const [helpful, setHelpful] = useState(message.helpful ?? null);
  const [sending, setSending] = useState(false);

  async function vote(value) {
    if (sending || isUser) return;
    setSending(true);
    try {
      await api.sendFeedback(message.id, value);
      setHelpful(value);
    } catch (e) {
      console.error(e);
    } finally {
      setSending(false);
    }
  }

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[75%] bg-maroon text-white rounded-chat rounded-br-sm px-5 py-3 shadow-card">
          <p className="font-body text-[0.95rem] leading-relaxed">{message.content}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="max-w-[80%] bg-white border border-border rounded-chat rounded-bl-sm shadow-card overflow-hidden">
        <div className="px-5 pt-4 pb-3">
          <p className="font-body text-[0.95rem] leading-relaxed whitespace-pre-wrap text-ink">
            {message.content}
          </p>
        </div>

        {message.sources && message.sources.length > 0 && (
          <div className="px-5 pb-3 flex flex-wrap gap-1.5">
            {message.sources.map((s, i) => (
              <span
                key={i}
                className="text-xs font-mono bg-cream border border-border text-muted px-2 py-1 rounded-full"
              >
                {s}
              </span>
            ))}
          </div>
        )}

        <div className="flex items-center justify-between border-t border-border bg-cream/60 px-5 py-2">
          <div className="font-mono text-[0.7rem] text-muted tracking-wide">
            {message.retrieved_chunk_count != null && `${message.retrieved_chunk_count} chunks retrieved · `}
            {message.latency_ms != null && `${message.latency_ms}ms`}
          </div>
          {message.id && (
            <div className="flex gap-1">
              <button
                onClick={() => vote(true)}
                className={`text-sm px-1.5 rounded transition-colors ${
                  helpful === true ? "opacity-100" : "opacity-40 hover:opacity-70"
                }`}
                aria-label="Helpful"
              >
                👍
              </button>
              <button
                onClick={() => vote(false)}
                className={`text-sm px-1.5 rounded transition-colors ${
                  helpful === false ? "opacity-100" : "opacity-40 hover:opacity-70"
                }`}
                aria-label="Not helpful"
              >
                👎
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

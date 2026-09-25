import { useState } from "react";
import { api } from "../api";
import AgentTrace from "./AgentTrace";
import RichText from "./RichText";

const KIND_LABELS = {
  plan: "planner",
  prereq: "prereqs",
  catalog: "catalog",
  dates: "calendar",
  policy: "catalog rules",
  core: "core curriculum",
  grad_catalog: "graduate catalog",
  department: "CS department",
  registrar: "registrar",
  handbook: "handbook",
  syllabus: "syllabus",
};

function VerificationBadge({ verification, mode }) {
  if (mode === "extractive") {
    return <span className="text-amber-700">quoted sources · AI summary unavailable</span>;
  }
  const claims = verification?.claims;
  if (!claims || claims.method !== "llm" || !claims.checked) return null;
  const removed = claims.unsupported?.length || 0;
  const supported = claims.checked - removed;
  return (
    <span className={removed ? "text-amber-700" : "text-emerald-700"}>
      ✓ {supported}/{claims.checked} claims verified
      {removed > 0 && ` · ${removed} removed`}
    </span>
  );
}

export default function MessageBubble({ message }) {
  const isUser = message.role === "user";
  const [helpful, setHelpful] = useState(message.helpful ?? null);
  const [sending, setSending] = useState(false);
  const [openSource, setOpenSource] = useState(null);

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
        <div className="max-w-[85%] md:max-w-[75%] bg-maroon text-white rounded-chat rounded-br-sm px-5 py-3 shadow-card">
          <p className="font-body text-[0.95rem] leading-relaxed whitespace-pre-wrap">{message.content}</p>
        </div>
      </div>
    );
  }

  // Live answers carry structured citations; answers loaded from history
  // carry only source labels.
  const citations = message.citations || [];
  const labels = citations.length ? null : message.sources || [];
  const active = citations.find((c) => c.n === openSource);

  return (
    <div className="flex justify-start">
      <div className="w-full max-w-[92%] md:max-w-[80%] bg-white border border-border rounded-chat rounded-bl-sm shadow-card overflow-hidden">
        <AgentTrace message={message} />

        <div className="px-5 pt-3 pb-3">
          {message.content ? (
            <RichText text={message.content} onCite={(n) => setOpenSource(openSource === n ? null : n)} />
          ) : (
            message.streaming && <p className="font-mono text-xs text-muted">…</p>
          )}
        </div>

        {active && (
          <div className="mx-5 mb-3 rounded-xl border border-maroon/20 bg-cream px-4 py-3">
            <p className="font-mono text-[0.7rem] text-maroon mb-1">
              [{active.n}] {active.label}
            </p>
            <p className="text-sm text-ink/80 whitespace-pre-wrap">{active.snippet}</p>
            {active.url && (
              <a
                href={active.url}
                target="_blank"
                rel="noreferrer"
                className="inline-block mt-2 text-xs font-mono text-maroon hover:underline break-all"
              >
                Official page ↗ {active.url.replace(/^https:\/\//, "")}
              </a>
            )}
          </div>
        )}

        {citations.length > 0 && (
          <div className="px-5 pb-3 flex flex-wrap gap-1.5">
            {citations.map((s) => (
              <button
                key={s.n}
                type="button"
                onClick={() => setOpenSource(openSource === s.n ? null : s.n)}
                className={`text-xs font-mono border px-2 py-1 rounded-full transition-colors ${
                  openSource === s.n
                    ? "bg-maroon text-white border-maroon"
                    : "bg-cream border-border text-muted hover:border-maroon/40"
                }`}
                title={s.label}
              >
                {s.n} · {KIND_LABELS[s.kind] || s.kind} · {s.label.split(" — ")[1] || s.label}
              </button>
            ))}
          </div>
        )}
        {labels && labels.length > 0 && (
          <div className="px-5 pb-3 flex flex-wrap gap-1.5">
            {labels.map((s, i) => (
              <span key={i} className="text-xs font-mono bg-cream border border-border text-muted px-2 py-1 rounded-full">
                {i + 1} · {s}
              </span>
            ))}
          </div>
        )}

        <div className="flex items-center justify-between gap-3 border-t border-border bg-cream/60 px-5 py-2">
          <div className="font-mono text-[0.7rem] text-muted tracking-wide flex flex-wrap gap-x-2">
            <VerificationBadge verification={message.verification} mode={message.mode || message.answer_mode} />
            {message.latency_ms != null && <span>{message.latency_ms}ms</span>}
            {message.cache_hit && <span>cached</span>}
          </div>
          {message.id && !message.streaming && (
            <div className="flex gap-1 shrink-0">
              <button
                onClick={() => vote(true)}
                className={`text-sm px-1.5 rounded transition-opacity ${
                  helpful === true ? "opacity-100" : "opacity-40 hover:opacity-70"
                }`}
                aria-label="Helpful"
              >
                👍
              </button>
              <button
                onClick={() => vote(false)}
                className={`text-sm px-1.5 rounded transition-opacity ${
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

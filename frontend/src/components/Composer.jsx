import { useState } from "react";

const SOURCE_OPTIONS = [
  { label: "All sources", value: null },
  { label: "RateMyProfessors", value: "rmp" },
  { label: "Coursicle", value: "coursicle" },
  { label: "Reddit r/txstate", value: "reddit" },
  { label: "Official catalog", value: "official" },
];

export default function Composer({ onSend, disabled }) {
  const [question, setQuestion] = useState("");
  const [sourceFilter, setSourceFilter] = useState(null);

  function submit(e) {
    e.preventDefault();
    const q = question.trim();
    if (!q || disabled) return;
    onSend(q, sourceFilter);
    setQuestion("");
  }

  return (
    <form onSubmit={submit} className="border-t border-border bg-white px-6 py-4">
      <div className="flex flex-wrap gap-1.5 mb-3">
        {SOURCE_OPTIONS.map((opt) => (
          <button
            key={opt.label}
            type="button"
            onClick={() => setSourceFilter(opt.value)}
            className={`text-xs font-display font-semibold uppercase tracking-wide px-3 py-1.5 rounded-full border transition-colors ${
              sourceFilter === opt.value
                ? "bg-maroon text-white border-maroon"
                : "bg-white text-muted border-border hover:border-maroon/40"
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>

      <div className="flex gap-3">
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) submit(e);
          }}
          placeholder="e.g. What do students say about Burtscher's workload?"
          rows={2}
          disabled={disabled}
          className="flex-1 resize-none rounded-xl border-2 border-border focus:border-maroon focus:outline-none px-4 py-3 font-body text-sm"
        />
        <button
          type="submit"
          disabled={disabled || !question.trim()}
          className="font-display font-bold text-sm uppercase tracking-wide bg-maroon text-white rounded-xl px-6 disabled:opacity-40 hover:bg-maroon-light transition-colors"
        >
          Ask →
        </button>
      </div>
    </form>
  );
}

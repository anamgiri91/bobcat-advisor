import { useState } from "react";

export default function Composer({ onSend, disabled }) {
  const [question, setQuestion] = useState("");

  function submit(e) {
    e.preventDefault();
    const q = question.trim();
    if (!q || disabled) return;
    onSend(q);
    setQuestion("");
  }

  return (
    <form onSubmit={submit} className="border-t border-border bg-white px-6 py-4">
      <div className="flex gap-3">
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) submit(e);
          }}
          placeholder="e.g. Which course covers machine learning, and what do I need first?"
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

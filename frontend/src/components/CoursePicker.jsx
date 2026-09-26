import { useMemo, useState } from "react";

const LEVELS = { 1: "1000-level", 2: "2000-level", 3: "3000-level", 4: "4000-level" };

export const spaced = (code) => code.replace(/^([A-Z]+)(\d)/, "$1 $2");

/**
 * Pick the CS courses a student has passed: searchable, grouped by level,
 * each course shown with its title (codes alone are hard to recognise).
 */
export default function CoursePicker({ catalog, selected, onToggle, onClear }) {
  const [query, setQuery] = useState("");
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase().replace(/\s+/g, "");
    const out = {};
    for (const c of catalog) {
      const level = Number(c.code[2]);
      if (!LEVELS[level]) continue;
      if (q && !`${c.code}${c.title}`.toLowerCase().replace(/\s+/g, "").includes(q)) continue;
      (out[level] ||= []).push(c);
    }
    return out;
  }, [catalog, query]);
  const levels = Object.keys(groups);

  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by code or title"
          aria-label="Search courses"
          className="flex-1 text-xs bg-cream border border-border rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-maroon/50"
        />
        <span className="text-[0.7rem] font-mono text-muted shrink-0" aria-live="polite">
          {selected.size} selected
        </span>
        {selected.size > 0 && (
          <button type="button" onClick={onClear} className="text-[0.7rem] text-maroon hover:underline shrink-0">
            Clear
          </button>
        )}
      </div>
      <div className="max-h-72 overflow-y-auto thin-scroll pr-1 space-y-3">
        {!catalog.length && <p className="text-xs text-muted">Loading courses…</p>}
        {catalog.length > 0 && !levels.length && <p className="text-xs text-muted">No course matches “{query}”.</p>}
        {levels.map((level) => (
          <div key={level}>
            <p className="font-display text-[0.65rem] uppercase tracking-wider text-muted mb-1">{LEVELS[level]}</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
              {groups[level].map((c) => {
                const on = selected.has(c.code);
                return (
                  <button
                    type="button"
                    key={c.code}
                    aria-pressed={on}
                    onClick={() => onToggle(c.code)}
                    title={c.title}
                    className={`flex items-center gap-2 text-left rounded-lg border px-2.5 py-1.5 transition-colors ${
                      on ? "bg-maroon/5 border-maroon/60" : "bg-white border-border hover:border-maroon/30"
                    }`}
                  >
                    <span
                      aria-hidden
                      className={`w-3.5 h-3.5 shrink-0 rounded border grid place-items-center text-[0.6rem] ${
                        on ? "bg-maroon border-maroon text-white" : "border-border"
                      }`}
                    >
                      {on ? "✓" : ""}
                    </span>
                    <span className="min-w-0">
                      <span className="block font-mono text-xs font-semibold">{spaced(c.code)}</span>
                      <span className="block text-[0.68rem] text-muted truncate">{c.title}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
      <p className="text-[0.68rem] text-muted mt-2">Prerequisites of the courses you pick are counted as passed.</p>
    </div>
  );
}

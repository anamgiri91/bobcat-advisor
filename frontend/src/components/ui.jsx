/** Small shared building blocks for the Advisor and Careers views. */

export const INPUT =
  "w-full text-sm bg-white border border-border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-maroon/15 focus:border-maroon/50 placeholder:text-muted/60";

export function Label({ children, hint }) {
  return (
    <span className="flex items-baseline justify-between gap-2 mb-1">
      <span className="font-display text-[0.7rem] uppercase tracking-wider text-muted">{children}</span>
      {hint && <span className="text-[0.65rem] text-muted/80">{hint}</span>}
    </span>
  );
}

/** A numbered step of a form. */
export function Section({ n, title, hint, children }) {
  return (
    <section className="bg-white border border-border rounded-xl p-4 space-y-3">
      <div className="flex items-center gap-2">
        {n != null && (
          <span className="w-5 h-5 shrink-0 rounded-full bg-maroon text-white text-[0.65rem] font-mono grid place-items-center">
            {n}
          </span>
        )}
        <h2 className="font-display font-bold text-sm">{title}</h2>
        {hint && <span className="ml-auto text-[0.7rem] text-muted text-right">{hint}</span>}
      </div>
      {children}
    </section>
  );
}

export function Card({ title, children, right, className = "" }) {
  return (
    <section className={`bg-white border border-border rounded-xl p-4 shadow-card ${className}`}>
      {(title || right) && (
        <div className="flex items-center justify-between gap-2 mb-2">
          <h3 className="font-display font-bold text-sm">{title}</h3>
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

const TONES = {
  good: "bg-emerald-50 text-emerald-700 border-emerald-200",
  warn: "bg-amber-50 text-amber-800 border-amber-200",
  bad: "bg-red-50 text-red-700 border-red-200",
  brand: "bg-maroon/10 text-maroon border-maroon/20",
  gold: "bg-gold/15 text-ink border-gold/40",
  muted: "bg-cream text-muted border-border",
};

export function Badge({ tone = "muted", children, title }) {
  return (
    <span title={title} className={`inline-block text-[0.65rem] font-mono border rounded-full px-2 py-0.5 whitespace-nowrap ${TONES[tone]}`}>
      {children}
    </span>
  );
}

export function Toggle({ on, onClick, children, tone = "brand" }) {
  const active = tone === "gold" ? "bg-gold/30 border-gold text-ink" : "bg-maroon text-white border-maroon";
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
        on ? active : "bg-white border-border text-muted hover:border-maroon/40"
      }`}
    >
      {children}
    </button>
  );
}

/** Live progress of a pipeline: `steps` is [{id, label, desc}], `agents` the latest event per id. */
export function AgentSteps({ steps, agents = {}, running }) {
  return (
    <ol className="space-y-1.5">
      {steps.map((a) => {
        const s = agents[a.id];
        const state = s?.status === "done" ? "done" : s?.status === "start" ? "active" : "pending";
        const dot =
          state === "done" ? (s.error ? "bg-amber-500" : "bg-emerald-500") : state === "active" ? "bg-gold animate-pulse" : "bg-border";
        return (
          <li key={a.id} className="flex gap-2 text-xs">
            <span className={`mt-1.5 inline-block w-1.5 h-1.5 rounded-full shrink-0 ${dot}`} />
            <div className={state === "pending" && running ? "opacity-50" : ""}>
              <span className="font-display font-bold">{a.label}</span>
              <span className="text-muted"> · {s?.summary || a.desc}</span>
              {s?.ms != null && <span className="font-mono text-[0.65rem] text-muted"> · {s.ms}ms</span>}
              {s?.error && <p className="text-[0.7rem] text-amber-700">{s.error}</p>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/** The pipeline as a numbered explainer, for empty states. */
export function StepList({ steps }) {
  return (
    <ol className="grid sm:grid-cols-2 gap-2">
      {steps.map((a, i) => (
        <li key={a.id} className="flex gap-2.5 items-start rounded-lg bg-cream/70 border border-border px-3 py-2">
          <span className="w-5 h-5 shrink-0 rounded-full bg-white border border-border text-[0.65rem] font-mono grid place-items-center text-maroon">
            {i + 1}
          </span>
          <span className="text-xs leading-snug">
            <span className="font-display font-bold block">{a.label}</span>
            <span className="text-muted">{a.desc}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}

export function Visits({ visits }) {
  if (!visits.length) return null;
  return (
    <ul className="mt-3 space-y-1 font-mono text-[0.65rem] max-h-40 overflow-y-auto thin-scroll">
      {visits.map((v, i) => (
        <li key={i} className="flex gap-2">
          <span className={v.ok ? "text-emerald-600" : "text-amber-600"}>{v.ok ? "✓" : "✗"}</span>
          <a href={v.url} target="_blank" rel="noreferrer noopener" className="truncate hover:underline" title={v.url}>
            {v.url.replace(/^https:\/\//, "")}
          </a>
          <span className="text-muted shrink-0">{v.ok ? (v.cached ? "cached" : `${v.ms}ms`) : v.error}</span>
        </li>
      ))}
    </ul>
  );
}

export function WakingNotice() {
  return (
    <p className="text-xs text-muted mb-2" role="status">
      Waking up the server (it sleeps when idle; this can take up to a minute)…
    </p>
  );
}

export function SubmitBar({ busy, label, busyLabel, error }) {
  return (
    <div className="sticky bottom-0 -mx-1 px-1 pt-3 pb-1 bg-gradient-to-t from-cream via-cream to-cream/0">
      <button
        type="submit"
        disabled={busy}
        className="w-full font-display font-bold text-sm uppercase tracking-wide bg-maroon text-white rounded-xl py-3 shadow-card hover:bg-maroon-light disabled:opacity-60"
      >
        {busy ? busyLabel : label}
      </button>
      {error && (
        <p role="alert" className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2 mt-2">
          {error}
        </p>
      )}
    </div>
  );
}

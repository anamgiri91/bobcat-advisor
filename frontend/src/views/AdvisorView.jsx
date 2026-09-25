import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import RichText from "../components/RichText";

/**
 * Course recommendations from the seven-agent advising pipeline: the student
 * fills in their profile, then watches each agent work (including every
 * catalog page the researcher opens) and gets a verified schedule, degree
 * audit, roadmap and a cited advising memo.
 */

const AGENTS = [
  { id: "intake", label: "Intake", desc: "Reads your profile and flags anything to discuss" },
  { id: "researcher", label: "Web researcher", desc: "Browses the live TXST catalog" },
  { id: "fact_checker", label: "Fact-checker", desc: "Verifies every requirement against its source" },
  { id: "auditor", label: "Degree auditor", desc: "Checks what you've finished and what's left" },
  { id: "scheduler", label: "Schedule planner", desc: "Picks courses, balances workload, plans ahead" },
  { id: "advisor", label: "Advisor", desc: "Writes your advising notes" },
  { id: "verifier", label: "Verifier", desc: "Checks each claim in the notes" },
];

const INTERESTS = ["AI", "Machine learning", "Data", "Security", "Web", "Games", "Systems", "Software", "Theory", "Mobile"];
const YEARS = ["freshman", "sophomore", "junior", "senior"];
const INPUT = "w-full text-sm bg-white border border-border rounded-lg px-3 py-2 focus:outline-none focus:border-maroon/50";

function Label({ children }) {
  return <span className="block font-display text-[0.7rem] uppercase tracking-wider text-muted mb-1">{children}</span>;
}

function Card({ title, children, right }) {
  return (
    <section className="bg-white border border-border rounded-xl p-4 shadow-card">
      <div className="flex items-center justify-between gap-2 mb-2">
        <h3 className="font-display font-bold text-sm">{title}</h3>
        {right}
      </div>
      {children}
    </section>
  );
}

function Badge({ tone = "muted", children }) {
  const tones = {
    good: "bg-emerald-50 text-emerald-700 border-emerald-200",
    warn: "bg-amber-50 text-amber-800 border-amber-200",
    bad: "bg-red-50 text-red-700 border-red-200",
    muted: "bg-cream text-muted border-border",
  };
  return <span className={`text-[0.65rem] font-mono border rounded-full px-2 py-0.5 ${tones[tone]}`}>{children}</span>;
}

function AgentSteps({ agents, running }) {
  return (
    <ol className="space-y-1.5">
      {AGENTS.map((a) => {
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

function Visits({ visits }) {
  if (!visits.length) return null;
  return (
    <ul className="mt-3 space-y-1 font-mono text-[0.65rem] max-h-40 overflow-y-auto thin-scroll">
      {visits.map((v, i) => (
        <li key={i} className="flex gap-2">
          <span className={v.ok ? "text-emerald-600" : "text-red-600"}>{v.ok ? "✓" : "✗"}</span>
          <a href={v.url} target="_blank" rel="noreferrer" className="truncate hover:underline" title={v.url}>
            {v.url.replace(/^https:\/\//, "")}
          </a>
          <span className="text-muted shrink-0">{v.ok ? (v.cached ? "cached" : `${v.ms}ms`) : v.error}</span>
        </li>
      ))}
    </ul>
  );
}

function Schedule({ schedule }) {
  return (
    <Card
      title={`Recommended for ${schedule.term}`}
      right={<Badge tone="muted">{schedule.total_hours} / {schedule.target_credits} hrs</Badge>}
    >
      {schedule.mode === "prerequisites_only" && (
        <p className="text-xs text-amber-800 mb-2">
          Degree requirements couldn't be verified online, so these are courses you're eligible for, not a degree audit.
        </p>
      )}
      <div className="space-y-2">
        {schedule.courses.map((c) => (
          <div key={c.code} className="border border-border rounded-lg p-3">
            <div className="flex flex-wrap items-center gap-2">
              <p className="font-display font-bold text-sm">
                {c.code} · {c.title}
              </p>
              <Badge tone={c.kind === "required" ? "good" : "muted"}>{c.kind}</Badge>
              <span className="font-mono text-[0.65rem] text-muted">{c.hours} hrs</span>
              {c.conflict && <Badge tone="warn">sources disagree</Badge>}
            </div>
            <ul className="text-xs text-ink/80 mt-1 list-disc pl-4">
              {c.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
            {c.review_count > 0 && (
              <p className="font-mono text-[0.65rem] text-muted mt-1">
                {c.review_count} reviews · difficulty {c.difficulty ?? "–"}/5 · quality {c.quality ?? "–"}/5
              </p>
            )}
            {c.conditions.length > 0 && <p className="text-[0.7rem] text-amber-700 mt-1">Check: {c.conditions.join("; ")}</p>}
          </div>
        ))}
      </div>
      {schedule.deferred.length > 0 && (
        <details className="mt-3 text-xs">
          <summary className="cursor-pointer text-muted">Not this term ({schedule.deferred.length})</summary>
          <ul className="mt-1 space-y-0.5">
            {schedule.deferred.map((d) => (
              <li key={d.code}>
                <span className="font-mono">{d.code}</span> — {d.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
      {schedule.warnings.map((w) => (
        <p key={w} className="text-[0.7rem] text-amber-800 mt-2">
          {w}
        </p>
      ))}
    </Card>
  );
}

function Audit({ audit }) {
  if (!audit.available) return null;
  const pct = audit.total_hours ? Math.min(100, Math.round((audit.hours_completed / audit.total_hours) * 100)) : null;
  return (
    <Card title="Degree audit" right={<Badge>{audit.catalog_year || "catalog year ?"}</Badge>}>
      {pct != null && (
        <>
          <div className="h-2 bg-cream rounded-full overflow-hidden">
            <div className="h-full bg-maroon" style={{ width: `${pct}%` }} />
          </div>
          <p className="font-mono text-[0.65rem] text-muted mt-1">
            ~{audit.hours_completed} of {audit.total_hours} hrs ({pct}%) · ~{audit.terms_remaining} terms to go
          </p>
        </>
      )}
      <div className="flex flex-wrap gap-1 mt-2">
        {audit.requirements.map((r) => (
          <span
            key={r.id}
            title={r.section}
            className={`text-[0.65rem] font-mono px-2 py-0.5 rounded-full border ${
              r.status === "done"
                ? "bg-maroon text-white border-maroon"
                : r.status === "in_progress"
                ? "bg-gold/20 border-gold/40"
                : "bg-white border-border text-muted"
            }`}
          >
            {r.label}
          </span>
        ))}
      </div>
      {audit.pools.map((p) => (
        <p key={p.id} className="text-xs mt-2">
          {p.section}: {p.hours_done}/{p.hours_required ?? "?"} hrs
          {p.satisfied_by.length > 0 && <span className="text-muted"> ({p.satisfied_by.join(", ")})</span>}
        </p>
      ))}
      {audit.behind_plan.length > 0 && (
        <p className="text-xs text-amber-800 mt-2">Behind the suggested four-year plan on: {audit.behind_plan.join(", ")}</p>
      )}
    </Card>
  );
}

function FactCheck({ report }) {
  const s = report.summary;
  const flagged = report.checks.filter((c) => c.status !== "verified");
  return (
    <Card
      title="Fact-check"
      right={
        <div className="flex gap-1">
          <Badge tone="good">{s.verified} verified</Badge>
          {s.conflicts > 0 && <Badge tone="warn">{s.conflicts} conflicts</Badge>}
          {s.unverified > 0 && <Badge tone="bad">{s.unverified} dropped</Badge>}
        </div>
      }
    >
      {flagged.length === 0 ? (
        <p className="text-xs text-muted">
          {s.checked ? "Every requirement was found on the official page and matches the catalog." : "Nothing to check."}
        </p>
      ) : (
        <ul className="text-xs space-y-1">
          {flagged.map((c) => (
            <li key={c.id}>
              <Badge tone={c.status === "conflict" ? "warn" : "bad"}>{c.status}</Badge> {c.claim}
              <span className="text-muted"> — {c.reasons[c.reasons.length - 1]}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export default function AdvisorView() {
  const [catalog, setCatalog] = useState([]);
  const [form, setForm] = useState({
    major: "Computer Science",
    degree: "BS",
    year: "sophomore",
    semester: "Fall",
    catalog_year: "",
    completed: new Set(),
    other: "",
    in_progress: "",
    gpa: "",
    target_credits: 15,
    interests: new Set(),
    career_goal: "",
    notes: "",
  });
  const [run, setRun] = useState(null);
  const [error, setError] = useState(null);
  const [activeSource, setActiveSource] = useState(null);
  const abortRef = useRef(null);
  const resultsRef = useRef(null);

  useEffect(() => {
    api.listCourses().then(setCatalog).catch(() => {});
    return () => abortRef.current?.abort();
  }, []);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const toggle = (k, v) =>
    setForm((f) => {
      const next = new Set(f[k]);
      next.has(v) ? next.delete(v) : next.add(v);
      return { ...f, [k]: next };
    });

  async function submit(e) {
    e.preventDefault();
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setError(null);
    setActiveSource(null);
    setRun({ agents: {}, visits: [], memo: "", running: true });
    // On narrow screens the results are below the form.
    if (window.innerWidth < 1024) resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    const split = (s) => s.split(/[,;\n]/).map((x) => x.trim()).filter(Boolean);
    const body = {
      major: form.major,
      degree: form.degree,
      year: form.year,
      semester: form.semester,
      catalog_year: form.catalog_year || null,
      completed: [...form.completed, ...split(form.other)],
      in_progress: split(form.in_progress),
      gpa: form.gpa === "" ? null : Number(form.gpa),
      target_credits: Number(form.target_credits),
      interests: [...form.interests],
      career_goal: form.career_goal,
      notes: form.notes,
    };
    const update = (fn) => setRun((r) => ({ ...r, ...fn(r) }));
    try {
      await api.adviseStream(
        body,
        (type, data) => {
          if (type === "agent") update((r) => ({ agents: { ...r.agents, [data.agent]: { ...r.agents[data.agent], ...data } } }));
          else if (type === "browse") update((r) => ({ visits: [...r.visits, data] }));
          else if (type === "profile") update(() => ({ flags: data.flags }));
          else if (type === "factcheck") update(() => ({ factcheck: data }));
          else if (type === "audit") update(() => ({ audit: data.audit }));
          else if (type === "schedule") update(() => ({ schedule: data.schedule }));
          else if (type === "sources") update(() => ({ sources: data.sources }));
          else if (type === "token") update((r) => ({ memo: r.memo + data.text }));
          else if (type === "revision") update(() => ({ memo: data.answer }));
          else if (type === "verification") update(() => ({ verification: data.verification }));
          else if (type === "done") update(() => ({ memo: data.answer, mode: data.mode, running: false }));
          else if (type === "error") throw new Error(data.message);
        },
        controller.signal
      );
    } catch (err) {
      if (err.name !== "AbortError") setError(err.message);
    } finally {
      update(() => ({ running: false }));
    }
  }

  const cs = catalog.filter((c) => Number(c.code[2]) <= 4);
  const claims = run?.verification?.claims;

  return (
    <div className="flex-1 overflow-y-auto thin-scroll px-4 md:px-6 py-6">
      <div className="max-w-6xl mx-auto grid lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)] gap-6">
        <form onSubmit={submit} className="space-y-4">
          <p className="text-sm text-muted">
            Tell us where you are. A team of agents reads the live TXST catalog, fact-checks it, audits your progress and
            plans your next term.
          </p>
          <div className="grid grid-cols-2 gap-3">
            <label className="col-span-2">
              <Label>Major</Label>
              <input className={INPUT} value={form.major} onChange={set("major")} maxLength={80} required />
            </label>
            <label>
              <Label>Degree</Label>
              <select className={INPUT} value={form.degree} onChange={set("degree")}>
                <option>BS</option>
                <option>BA</option>
              </select>
            </label>
            <label>
              <Label>Year</Label>
              <select className={INPUT} value={form.year} onChange={set("year")}>
                {YEARS.map((y) => (
                  <option key={y} value={y}>
                    {y[0].toUpperCase() + y.slice(1)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <Label>Planning for</Label>
              <select className={INPUT} value={form.semester} onChange={set("semester")}>
                <option>Fall</option>
                <option>Spring</option>
                <option>Summer</option>
              </select>
            </label>
            <label>
              <Label>Target hours</Label>
              <input className={INPUT} type="number" min={3} max={21} value={form.target_credits} onChange={set("target_credits")} />
            </label>
            <label>
              <Label>GPA (optional)</Label>
              <input className={INPUT} type="number" step="0.01" min={0} max={4} value={form.gpa} onChange={set("gpa")} />
            </label>
            <label>
              <Label>Catalog year</Label>
              <input className={INPUT} placeholder="2025-2026" value={form.catalog_year} onChange={set("catalog_year")} maxLength={9} />
            </label>
          </div>

          <div>
            <Label>CS courses you've passed</Label>
            <div className="flex flex-wrap gap-1.5">
              {cs.map((c) => (
                <button
                  type="button"
                  key={c.code}
                  onClick={() => toggle("completed", c.code)}
                  title={c.title}
                  className={`text-xs font-mono px-2.5 py-1 rounded-full border transition-colors ${
                    form.completed.has(c.code) ? "bg-maroon text-white border-maroon" : "bg-white border-border text-muted hover:border-maroon/40"
                  }`}
                >
                  {c.code}
                </button>
              ))}
            </div>
          </div>
          <label className="block">
            <Label>Other courses passed (math, core, transfer)</Label>
            <input className={INPUT} placeholder="MATH 2471, MATH 2358, ENG 1310" value={form.other} onChange={set("other")} />
          </label>
          <label className="block">
            <Label>In progress this term</Label>
            <input className={INPUT} placeholder="CS 2308" value={form.in_progress} onChange={set("in_progress")} />
          </label>

          <div>
            <Label>Interests</Label>
            <div className="flex flex-wrap gap-1.5">
              {INTERESTS.map((i) => (
                <button
                  type="button"
                  key={i}
                  onClick={() => toggle("interests", i)}
                  className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                    form.interests.has(i) ? "bg-gold/30 border-gold text-ink" : "bg-white border-border text-muted hover:border-gold"
                  }`}
                >
                  {i}
                </button>
              ))}
            </div>
          </div>
          <label className="block">
            <Label>Career goal</Label>
            <input className={INPUT} placeholder="e.g. machine learning engineer" value={form.career_goal} onChange={set("career_goal")} maxLength={200} />
          </label>
          <label className="block">
            <Label>Anything else</Label>
            <textarea className={INPUT} rows={2} placeholder="I work 20 hours a week…" value={form.notes} onChange={set("notes")} maxLength={1000} />
          </label>

          <button
            type="submit"
            disabled={run?.running}
            className="w-full font-display font-bold text-sm uppercase tracking-wide bg-maroon text-white rounded-xl py-3 hover:bg-maroon-light disabled:opacity-60"
          >
            {run?.running ? "Agents at work…" : "Get my recommendations"}
          </button>
          {error && <p className="text-sm text-red-600 font-mono">{error}</p>}
        </form>

        <div ref={resultsRef} className="space-y-4 min-w-0 scroll-mt-4">
          {!run && (
            <div className="bg-white border border-dashed border-border rounded-xl p-6 text-sm text-muted">
              <p className="font-display font-bold text-ink mb-2">How it works</p>
              <AgentSteps agents={{}} running={false} />
            </div>
          )}
          {run && (
            <Card title="Agents" right={run.visits.length > 0 && <Badge>{run.visits.length} pages</Badge>}>
              <AgentSteps agents={run.agents} running={run.running} />
              <Visits visits={run.visits} />
            </Card>
          )}
          {run?.flags?.length > 0 && (
            <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 text-xs text-amber-900 space-y-1">
              {run.flags.map((f) => (
                <p key={f}>{f}</p>
              ))}
            </div>
          )}
          {run?.memo && (
            <Card
              title="Your advising notes"
              right={
                claims?.pass_rate != null ? (
                  <Badge tone={claims.pass_rate >= 0.9 ? "good" : "warn"}>{Math.round(claims.pass_rate * 100)}% claims verified</Badge>
                ) : run.mode === "extractive" ? (
                  <Badge>template</Badge>
                ) : null
              }
            >
              <RichText text={run.memo} onCite={setActiveSource} />
            </Card>
          )}
          {run?.schedule && <Schedule schedule={run.schedule} />}
          <div className="grid md:grid-cols-2 gap-4">
            {run?.audit && <Audit audit={run.audit} />}
            {run?.factcheck && <FactCheck report={run.factcheck} />}
          </div>
          {run?.schedule?.roadmap?.length > 0 && (
            <Card title="Roadmap (tentative)">
              <ol className="text-xs space-y-1">
                {run.schedule.roadmap.map((t) => (
                  <li key={t.term} className="flex gap-3">
                    <span className="font-display font-bold w-32 shrink-0">{t.term}</span>
                    <span className="font-mono">{t.courses.join(", ")}</span>
                    <span className="text-muted ml-auto shrink-0">{t.hours} hrs</span>
                  </li>
                ))}
              </ol>
            </Card>
          )}
          {run?.sources?.length > 0 && (
            <Card title="Sources">
              <ol className="text-xs space-y-1">
                {run.sources.map((s) => (
                  <li key={s.n} className={`rounded px-1 ${activeSource === s.n ? "bg-gold/20" : ""}`}>
                    <span className="font-mono text-maroon">[{s.n}]</span> {s.label}
                    {activeSource === s.n && <p className="text-muted whitespace-pre-line mt-1">{s.snippet}</p>}
                  </li>
                ))}
              </ol>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

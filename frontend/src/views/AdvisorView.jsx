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
  { id: "timetable", label: "Timetable builder", desc: "Finds clash-free sections for your week" },
  { id: "advisor", label: "Advisor", desc: "Writes your advising notes" },
  { id: "verifier", label: "Verifier", desc: "Checks each claim in the notes" },
];

const WEEKDAYS = ["M", "T", "W", "R", "F"];
const DAY_NAMES = { M: "Mon", T: "Tue", W: "Wed", R: "Thu", F: "Fri", S: "Sat", U: "Sun" };
const COLORS = ["bg-maroon/85", "bg-[#8b3a7e]/85", "bg-gold/90", "bg-emerald-700/80", "bg-sky-700/80", "bg-rose-700/80"];

const toMin = (hhmm) => {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
};

export function WeekGrid({ option }) {
  const blocks = [];
  option.sections.forEach((s, i) =>
    s.meetings.forEach((m) => m.days.split("").forEach((d) => blocks.push({ s, m, d, color: COLORS[i % COLORS.length] })))
  );
  const days = [...WEEKDAYS, ...["S", "U"].filter((d) => blocks.some((b) => b.d === d))];
  if (!blocks.length) return <p className="text-xs text-muted">All sections are online with no set meeting times.</p>;
  const start = Math.floor(Math.min(...blocks.map((b) => b.m.start)) / 60) * 60;
  const end = Math.ceil(Math.max(...blocks.map((b) => b.m.end)) / 60) * 60;
  const PX = 0.8; // pixels per minute
  const hours = [];
  for (let t = start; t <= end; t += 60) hours.push(t);
  return (
    <div className="flex text-[0.65rem] font-mono overflow-x-auto thin-scroll">
      <div className="relative shrink-0 w-10" style={{ height: (end - start) * PX + 26 }}>
        {hours.map((t) => (
          <span key={t} className="absolute right-1 text-muted" style={{ top: 18 + (t - start) * PX - 6 }}>
            {String(t / 60).padStart(2, "0")}:00
          </span>
        ))}
      </div>
      {days.map((d) => (
        <div key={d} className="relative flex-1 min-w-[3.5rem] border-l border-border" style={{ height: (end - start) * PX + 26 }}>
          <div className="text-center text-muted h-[18px]">{DAY_NAMES[d]}</div>
          {hours.map((t) => (
            <div key={t} className="absolute left-0 right-0 border-t border-border/60" style={{ top: 18 + (t - start) * PX }} />
          ))}
          {blocks
            .filter((b) => b.d === d)
            .map((b, i) => (
              <div
                key={i}
                title={`${b.s.id} ${b.s.title || ""}`}
                className={`absolute left-0.5 right-0.5 rounded-md text-white px-1 py-0.5 overflow-hidden ${b.color}`}
                style={{ top: 18 + (b.m.start - start) * PX, height: Math.max((b.m.end - b.m.start) * PX, 14) }}
              >
                {b.s.course}
              </div>
            ))}
        </div>
      ))}
    </div>
  );
}

export function Timetable({ timetable, prefs }) {
  const [idx, setIdx] = useState(0);
  const option = timetable.options[idx];
  return (
    <Card
      title={`Your week · ${timetable.term}`}
      right={
        timetable.options.length > 1 && (
          <div className="flex gap-1">
            {timetable.options.map((_, i) => (
              <button
                key={i}
                type="button"
                onClick={() => setIdx(i)}
                className={`text-[0.65rem] font-mono px-2 py-0.5 rounded-full border ${
                  i === idx ? "bg-maroon text-white border-maroon" : "bg-white border-border text-muted"
                }`}
              >
                option {i + 1}
              </button>
            ))}
          </div>
        )
      }
    >
      {prefs?.length > 0 && <p className="text-[0.7rem] text-muted mb-2">Built around: {prefs.join(" · ")}</p>}
      {option ? (
        <>
          <WeekGrid option={option} />
          <ul className="mt-3 space-y-1 text-xs">
            {option.sections.map((s) => (
              <li key={s.id} className="flex flex-wrap gap-x-2">
                <span className="font-mono font-semibold">{s.id}</span>
                <span>{s.times.join(", ")}</span>
                {s.modality && <span className="text-muted">{s.modality}</span>}
                {s.seats_open != null && <span className="text-muted">{s.seats_open} seats open</span>}
                {s.crn && <span className="text-muted">CRN {s.crn}</span>}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="text-xs text-amber-800">{timetable.note || "No clash-free timetable was found."}</p>
      )}
      {timetable.unplaced.map((u) => (
        <p key={u.course} className="text-[0.7rem] text-amber-800 mt-2">
          Couldn't place {u.course}: {u.reason}
        </p>
      ))}
      <p className="text-[0.65rem] text-muted mt-2">Sections and seats change until registration; check the official schedule.</p>
    </Card>
  );
}

const SCENARIO_LABELS = {
  switch_major: "Switch major",
  add_minor: "Add a minor",
  fail_course: "Fail a course",
  change_load: "Change hours per term",
};

export function describeScenario(s) {
  if (s.type === "switch_major") return `Switch to ${s.major} (${s.degree})`;
  if (s.type === "add_minor") return `Add a ${s.minor} minor`;
  if (s.type === "fail_course") return `Fail ${s.course}`;
  return `${s.target_credits} hours per term`;
}

export function WhatIf({ body, courses }) {
  const [draft, setDraft] = useState({ type: "add_minor", major: "", degree: "BS", minor: "", course: "", target_credits: 12 });
  const [scenarios, setScenarios] = useState([]);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (k) => (e) => setDraft((d) => ({ ...d, [k]: e.target.value }));

  function add() {
    const { type } = draft;
    const s = { type };
    if (type === "switch_major") Object.assign(s, { major: draft.major.trim(), degree: draft.degree });
    if (type === "add_minor") s.minor = draft.minor.trim();
    if (type === "fail_course") s.course = draft.course;
    if (type === "change_load") s.target_credits = Number(draft.target_credits);
    if ((type === "switch_major" && !s.major) || (type === "add_minor" && !s.minor) || (type === "fail_course" && !s.course)) return;
    setScenarios((prev) => [...prev, s].slice(0, 4));
  }

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.whatIf(body, scenarios));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const rows = result ? [result.baseline, ...result.scenarios] : [];
  return (
    <Card title="What if…" right={<Badge>no AI · computed</Badge>}>
      <p className="text-xs text-muted mb-2">
        See how a change moves your graduation date. Each scenario reruns the degree audit and roadmap.
      </p>
      <div className="flex flex-wrap gap-2 items-end">
        <label>
          <Label>Scenario</Label>
          <select className={INPUT} value={draft.type} onChange={set("type")}>
            {Object.entries(SCENARIO_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </label>
        {draft.type === "switch_major" && (
          <>
            <label><Label>Major</Label><input className={INPUT} value={draft.major} onChange={set("major")} placeholder="Computer Science" /></label>
            <label><Label>Degree</Label><select className={INPUT} value={draft.degree} onChange={set("degree")}><option>BS</option><option>BA</option></select></label>
          </>
        )}
        {draft.type === "add_minor" && (
          <label><Label>Minor</Label><input className={INPUT} value={draft.minor} onChange={set("minor")} placeholder="Data Science" /></label>
        )}
        {draft.type === "fail_course" && (
          <label>
            <Label>Course</Label>
            <select className={INPUT} value={draft.course} onChange={set("course")}>
              <option value="">choose…</option>
              {courses.map((c) => <option key={c}>{c}</option>)}
            </select>
          </label>
        )}
        {draft.type === "change_load" && (
          <label><Label>Hours</Label><input className={INPUT} type="number" min={3} max={21} value={draft.target_credits} onChange={set("target_credits")} /></label>
        )}
        <button type="button" onClick={add} disabled={scenarios.length >= 4}
          className="text-xs font-display font-bold uppercase border border-maroon text-maroon rounded-lg px-3 py-2 disabled:opacity-40">
          + Add
        </button>
      </div>
      {scenarios.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-3">
          {scenarios.map((s, i) => (
            <button key={i} type="button" onClick={() => setScenarios((prev) => prev.filter((_, j) => j !== i))}
              className="text-xs bg-cream border border-border rounded-full px-2.5 py-1 hover:border-red-300" title="Remove">
              {describeScenario(s)} ×
            </button>
          ))}
          <button type="button" onClick={run} disabled={busy}
            className="text-xs font-display font-bold uppercase bg-maroon text-white rounded-lg px-3 py-1 disabled:opacity-60">
            {busy ? "Comparing…" : "Compare"}
          </button>
        </div>
      )}
      {error && <p className="text-xs text-red-600 font-mono mt-2">{error}</p>}
      {result && (
        <div className="mt-3 overflow-x-auto thin-scroll">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-muted font-display uppercase text-[0.65rem]">
                <th className="py-1 pr-3">Scenario</th><th className="pr-3">Graduation</th><th className="pr-3">Change</th>
                <th className="pr-3">Hours left</th><th>Requirements left</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((o, i) => (
                <tr key={i} className="border-t border-border align-top">
                  <td className="py-1.5 pr-3">
                    <p className={i === 0 ? "font-semibold" : ""}>{o.description}</p>
                    {o.notes.slice(0, 2).map((n) => <p key={n} className="text-[0.65rem] text-muted">{n}</p>)}
                  </td>
                  <td className="pr-3 font-mono">{o.graduation_term || "—"}</td>
                  <td className="pr-3 font-mono">
                    {i === 0 || o.delta_terms == null ? "" : o.delta_terms === 0 ? (
                      <span className="text-muted">same term{o.delta_hours ? `, ${o.delta_hours > 0 ? "+" : ""}${o.delta_hours} hrs` : ""}</span>
                    ) : (
                      <span className={o.delta_terms > 0 ? "text-red-700" : "text-emerald-700"}>
                        {o.delta_terms > 0 ? "+" : ""}{o.delta_terms} term{Math.abs(o.delta_terms) > 1 ? "s" : ""}
                      </span>
                    )}
                  </td>
                  <td className="pr-3 font-mono">{o.hours_remaining ?? "—"}</td>
                  <td className="font-mono">{o.requirements_remaining}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-[0.65rem] text-muted mt-2">Estimates assume each course is offered and passed as planned. Confirm with your advisor.</p>
        </div>
      )}
    </Card>
  );
}

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
            {c.offering && <p className="font-mono text-[0.65rem] text-muted mt-1">{c.offering}</p>}
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
    term_year: "",
    preferred_days: new Set(),
    earliest_start: "",
    latest_end: "",
    busy: "",
    modality: "",
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

  function buildBody() {
    const split = (s) => s.split(/[,;\n]/).map((x) => x.trim()).filter(Boolean);
    return {
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
      term_year: form.term_year ? Number(form.term_year) : null,
      preferred_days: WEEKDAYS.filter((d) => form.preferred_days.has(d)).join(""),
      earliest_start: form.earliest_start || null,
      latest_end: form.latest_end || null,
      busy: form.busy.split(/[;\n]/).map((x) => x.trim()).filter(Boolean),
      modality: form.modality || null,
    };
  }

  async function submit(e) {
    e.preventDefault();
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setError(null);
    setActiveSource(null);
    const body = buildBody();
    setRun({ agents: {}, visits: [], memo: "", running: true, body });
    // On narrow screens the results are below the form.
    if (window.innerWidth < 1024) resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
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
          else if (type === "timetable") update(() => ({ timetable: data.timetable, ttPrefs: data.preferences }));
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
              <Label>Year (optional)</Label>
              <input className={INPUT} type="number" min={2024} max={2100} placeholder="next" value={form.term_year} onChange={set("term_year")} />
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
          <fieldset className="border border-border rounded-xl p-3 space-y-3">
            <legend className="px-1 font-display text-[0.7rem] uppercase tracking-wider text-muted">Your week (for the timetable)</legend>
            <div>
              <Label>Preferred days</Label>
              <div className="flex gap-1.5">
                {WEEKDAYS.map((d) => (
                  <button
                    type="button"
                    key={d}
                    onClick={() => toggle("preferred_days", d)}
                    className={`text-xs font-mono w-9 py-1 rounded-full border ${
                      form.preferred_days.has(d) ? "bg-maroon text-white border-maroon" : "bg-white border-border text-muted"
                    }`}
                  >
                    {DAY_NAMES[d]}
                  </button>
                ))}
              </div>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <label>
                <Label>Not before</Label>
                <select className={INPUT} value={form.earliest_start} onChange={set("earliest_start")}>
                  <option value="">any</option>
                  {["08:00", "09:00", "10:00", "11:00", "12:00"].map((t) => <option key={t}>{t}</option>)}
                </select>
              </label>
              <label>
                <Label>Not after</Label>
                <select className={INPUT} value={form.latest_end} onChange={set("latest_end")}>
                  <option value="">any</option>
                  {["14:00", "15:00", "17:00", "19:00", "21:00"].map((t) => <option key={t}>{t}</option>)}
                </select>
              </label>
              <label>
                <Label>Format</Label>
                <select className={INPUT} value={form.modality} onChange={set("modality")}>
                  <option value="">any</option>
                  <option value="in person">in person</option>
                  <option value="online">online</option>
                  <option value="hybrid">hybrid</option>
                </select>
              </label>
            </div>
            <label className="block">
              <Label>Busy times (work, commute), one per line</Label>
              <textarea className={INPUT} rows={2} placeholder={"TR 12:00-17:00\nSat 9-13"} value={form.busy} onChange={set("busy")} />
            </label>
          </fieldset>
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
          {run?.timetable && (
            <Timetable key={JSON.stringify(run.timetable.courses)} timetable={run.timetable} prefs={run.ttPrefs} />
          )}
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
          {run?.schedule && !run.running && (
            <WhatIf
              body={run.body}
              courses={[...new Set([...(run.body?.completed || []), ...(run.body?.in_progress || []),
                ...run.schedule.courses.map((c) => c.code)])]}
            />
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

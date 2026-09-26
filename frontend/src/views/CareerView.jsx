import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import CoursePicker, { spaced } from "../components/CoursePicker";
import RichText from "../components/RichText";
import { AgentSteps, Badge, Card, INPUT, Label, Section, StepList, SubmitBar, Visits, WakingNotice } from "../components/ui";

/**
 * Career paths: pick a role (or describe one), and a team of agents maps it
 * to the skills it needs, the TXST courses that teach them, and outside
 * courses and certifications for what the catalog doesn't cover, opening
 * every link to check it before it's shown.
 */

export const CAREER_AGENTS = [
  { id: "career_analyst", label: "Career analyst", desc: "Maps the role to the skills it needs" },
  { id: "course_mapper", label: "Course mapper", desc: "Finds the TXST courses that teach each skill" },
  { id: "gap_finder", label: "Gap finder", desc: "Spots skills the catalog doesn't cover" },
  { id: "scout", label: "Resource scout", desc: "Searches for courses and certifications" },
  { id: "link_checker", label: "Link checker", desc: "Opens every link: live and on topic?" },
  { id: "mentor", label: "Mentor", desc: "Writes your roadmap" },
  { id: "verifier", label: "Verifier", desc: "Checks every course in the roadmap" },
];

const COVERAGE = {
  covered: { label: "TXST course", tone: "good", bar: "bg-emerald-600" },
  partial: { label: "partly covered", tone: "gold", bar: "bg-gold" },
  gap: { label: "learn outside class", tone: "warn", bar: "bg-amber-500/70" },
};

const STATUS = {
  eligible: { label: "take next", tone: "good" },
  in_progress: { label: "in progress", tone: "gold" },
  later: { label: "later", tone: "muted" },
  done: { label: "done", tone: "brand" },
};

const GROUPS = [
  ["gap", "Fill the gaps", "Skills the CS catalog doesn't teach, or only mentions."],
  ["deeper", "Go deeper", "Past what the TXST course covers."],
  ["certification", "Certifications", "Optional; most cost money. Worth it once you have the skills."],
];

export function CoverageBar({ coverage }) {
  const total = coverage.covered + coverage.partial + coverage.gap;
  if (!total) return null;
  return (
    <div>
      <div className="flex h-2.5 rounded-full overflow-hidden bg-cream" role="img"
        aria-label={`${coverage.covered} of ${total} skills covered by TXST courses`}>
        {["covered", "partial", "gap"].map((k) =>
          coverage[k] ? <div key={k} className={COVERAGE[k].bar} style={{ width: `${(coverage[k] / total) * 100}%` }} /> : null
        )}
      </div>
      <p className="text-xs text-muted mt-1.5">
        TXST courses cover <span className="font-semibold text-ink">{coverage.covered}</span> of {total} skills
        {coverage.partial > 0 && <>, partly cover {coverage.partial}</>}
        {coverage.gap > 0 && <>, and {coverage.gap} you'll learn outside class</>}.
      </p>
    </div>
  );
}

export function ResourceItem({ r }) {
  const c = r.check || {};
  return (
    <li className="border border-border rounded-lg p-3">
      <div className="flex flex-wrap items-start gap-x-2 gap-y-1">
        <a href={r.url} target="_blank" rel="noreferrer noopener" className="font-semibold text-sm text-maroon hover:underline">
          {r.title}
        </a>
        {c.status === "verified" ? (
          <Badge tone="good" title={c.page_title ? `Page title: ${c.page_title}` : undefined}>
            ✓ link checked {c.checked_on}
          </Badge>
        ) : (
          <Badge tone="warn" title={c.detail}>not checked: {c.reason}</Badge>
        )}
      </div>
      <p className="text-xs text-muted mt-0.5">
        {r.provider} · {r.kind} · {r.cost === "check site" ? "check the site for cost" : r.cost}
        {r.source === "search" && " · found by web search"}
      </p>
      {r.for_skill && <p className="text-[0.7rem] mt-1">For: {r.for_skill}</p>}
      {c.summary && <p className="text-[0.7rem] text-muted mt-1 italic">“{c.summary}”</p>}
    </li>
  );
}

function CourseItem({ c }) {
  const s = STATUS[c.status];
  return (
    <li className="border border-border rounded-lg p-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-display font-bold text-sm">
          {spaced(c.code)} · {c.title}
        </p>
        <Badge tone={s.tone}>{s.label}</Badge>
      </div>
      {c.skills.length > 0 && <p className="text-xs mt-1">Teaches: {c.skills.join(", ")}</p>}
      {c.unlocks?.length > 0 && (
        <p className="text-xs mt-1">
          Unlocks {c.unlocks.length} course{c.unlocks.length > 1 ? "s" : ""} on this path:{" "}
          <span className="font-mono">{c.unlocks.map(spaced).join(", ")}</span>
        </p>
      )}
      {c.missing?.length > 0 && <p className="text-xs text-amber-800 mt-1">Needs {c.missing.join("; ")}</p>}
      {c.evidence?.[0] && <p className="text-[0.7rem] text-muted mt-1 italic">Catalog: “{c.evidence[0]}”</p>}
    </li>
  );
}

export default function CareerView({ initialGoal = "", initialCompleted }) {
  const [paths, setPaths] = useState([]);
  const [catalog, setCatalog] = useState([]);
  const [choice, setChoice] = useState("");
  const [custom, setCustom] = useState(initialGoal);
  const [completed, setCompleted] = useState(() => new Set(initialCompleted || []));
  const [inProgress, setInProgress] = useState("");
  const [freeOnly, setFreeOnly] = useState(false);
  const [certs, setCerts] = useState(true);
  const [run, setRun] = useState(null);
  const [error, setError] = useState(null);
  const abortRef = useRef(null);
  const resultsRef = useRef(null);

  useEffect(() => {
    api.careerPaths().then(setPaths).catch((e) => setError(e.message));
    api.listCourses().then(setCatalog).catch(() => {});
    return () => abortRef.current?.abort();
  }, []);

  const career = custom.trim() || choice;

  function toggle(code) {
    setCompleted((prev) => {
      const next = new Set(prev);
      next.has(code) ? next.delete(code) : next.add(code);
      return next;
    });
  }

  async function submit(e) {
    e.preventDefault();
    if (!career) {
      setError("Pick a career or describe one.");
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setError(null);
    setRun({ agents: {}, visits: [], searches: [], running: true });
    if (window.innerWidth < 1024) resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    const update = (fn) => setRun((r) => ({ ...r, ...fn(r) }));
    const body = {
      career,
      completed: [...completed],
      in_progress: inProgress.split(/[,;\n]/).map((x) => x.trim()).filter(Boolean),
      include_certifications: certs,
      free_only: freeOnly,
    };
    try {
      await api.careerStream(
        body,
        (type, data) => {
          if (type === "slow") update(() => ({ waking: true }));
          else if (type === "agent") update((r) => ({ agents: { ...r.agents, [data.agent]: { ...r.agents[data.agent], ...data } } }));
          else if (type === "career") update(() => ({ career: data.career, note: data.note }));
          else if (type === "courses") update(() => ({ courses: data.courses, experience: data.experience }));
          else if (type === "skills") update(() => ({ skills: data.skills, coverage: data.coverage }));
          else if (type === "search") update((r) => ({ searches: [...r.searches, data] }));
          else if (type === "browse") update((r) => ({ visits: [...r.visits, data] }));
          else if (type === "resources") update(() => ({ resources: data.resources, dropped: data.dropped }));
          else if (type === "memo") update(() => ({ memo: data.text, mode: data.mode }));
          else if (type === "done") update(() => ({ running: false }));
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

  const todo = run?.courses?.filter((c) => c.status !== "done") || [];
  const done = run?.courses?.filter((c) => c.status === "done") || [];

  return (
    <div className="flex-1 overflow-y-auto thin-scroll px-4 md:px-6 py-6">
      <div className="max-w-6xl mx-auto grid lg:grid-cols-[minmax(0,26rem)_minmax(0,1fr)] gap-6">
        <form onSubmit={submit} className="space-y-3">
          <Section n={1} title="Pick a career">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5" role="radiogroup" aria-label="Career paths">
              {paths.map((p) => {
                const on = !custom.trim() && choice === p.id;
                return (
                  <button
                    key={p.id}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    onClick={() => {
                      setChoice(p.id);
                      setCustom("");
                    }}
                    className={`text-left rounded-lg border px-3 py-2 transition-colors ${
                      on ? "bg-maroon text-white border-maroon" : "bg-white border-border hover:border-maroon/40"
                    }`}
                  >
                    <span className="block font-display font-bold text-xs">{p.title}</span>
                    <span className={`block text-[0.68rem] leading-snug mt-0.5 ${on ? "text-white/80" : "text-muted"}`}>{p.summary}</span>
                  </button>
                );
              })}
              {!paths.length && !error && <p className="text-xs text-muted">Loading career paths…</p>}
            </div>
            <label className="block">
              <Label>Or describe it</Label>
              <input className={INPUT} placeholder="e.g. quant developer, robotics, data analyst" value={custom}
                onChange={(e) => setCustom(e.target.value)} maxLength={120} />
            </label>
          </Section>

          <Section n={2} title="Courses you've passed">
            <CoursePicker catalog={catalog} selected={completed} onToggle={toggle} onClear={() => setCompleted(new Set())} />
            <label className="block">
              <Label>Taking this term</Label>
              <input className={INPUT} placeholder="CS 3358" value={inProgress} onChange={(e) => setInProgress(e.target.value)} />
            </label>
          </Section>

          <Section n={3} title="Outside resources">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={freeOnly} onChange={(e) => setFreeOnly(e.target.checked)} className="accent-maroon" />
              Free resources only
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={certs} onChange={(e) => setCerts(e.target.checked)} className="accent-maroon" />
              Include certifications
            </label>
          </Section>

          <SubmitBar busy={run?.running} label="Build my career plan" busyLabel="Agents at work…" error={error} />
        </form>

        <div ref={resultsRef} className="space-y-4 min-w-0 scroll-mt-4">
          {!run && (
            <div className="bg-white border border-border rounded-xl p-5 md:p-6 space-y-5">
              <div>
                <p className="font-display font-bold text-lg leading-tight">From TXST courses to the job you want</p>
                <p className="text-sm text-muted mt-1">
                  Pick a career. You'll see which skills it needs, the TXST courses that teach them (in the order you can take
                  them), and outside courses and certifications for what the catalog doesn't cover. Every link is opened and
                  checked before it's shown.
                </p>
              </div>
              <StepList steps={CAREER_AGENTS} />
            </div>
          )}

          {run && (
            <Card
              title="Agents"
              right={(run.visits.length > 0 || run.searches.length > 0) && (
                <Badge>
                  {run.searches.length > 0 && `${run.searches.length} searches · `}
                  {run.visits.length} links
                </Badge>
              )}
            >
              {run.waking && run.running && Object.keys(run.agents).length === 0 && <WakingNotice />}
              <AgentSteps steps={CAREER_AGENTS} agents={run.agents} running={run.running} />
              <Visits visits={run.visits} />
            </Card>
          )}

          {run?.career && (
            <Card title={run.career.title} right={<Badge tone="brand">career path</Badge>}>
              <p className="text-sm text-muted mb-3">{run.career.summary}</p>
              {run.note && <p className="text-xs text-amber-800 mb-3">{run.note}</p>}
              {run.coverage && <CoverageBar coverage={run.coverage} />}
            </Card>
          )}

          {run?.memo && (
            <Card title="Your roadmap" right={<Badge>{run.mode === "llm" ? "AI-written · verified" : "template"}</Badge>}>
              <div className="text-sm">
                <RichText text={run.memo} />
              </div>
            </Card>
          )}

          {run?.skills && (
            <Card title="Skills for this career">
              <ul className="divide-y divide-border">
                {run.skills.map((s) => (
                  <li key={s.id} className="py-2 flex flex-wrap items-center gap-2 text-sm">
                    <span className={s.importance === "core" ? "font-semibold" : ""}>{s.name}</span>
                    {s.importance === "core" && <Badge tone="brand">core</Badge>}
                    <span className="ml-auto flex flex-wrap gap-1 items-center">
                      {s.courses.map((c) => (
                        <span key={c} className="font-mono text-[0.7rem] text-muted">{spaced(c)}</span>
                      ))}
                      <Badge tone={COVERAGE[s.coverage].tone}>{COVERAGE[s.coverage].label}</Badge>
                    </span>
                    {s.note && <p className="basis-full text-[0.7rem] text-muted">{s.note}</p>}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {todo.length > 0 && (
            <Card title="TXST courses for this path" right={<Badge>{todo.filter((c) => c.status === "eligible").length} available now</Badge>}>
              <ul className="space-y-2">
                {todo.map((c) => <CourseItem key={c.code} c={c} />)}
              </ul>
              {done.length > 0 && (
                <p className="text-xs text-muted mt-3">
                  Already done: <span className="font-mono">{done.map((c) => spaced(c.code)).join(", ")}</span>
                </p>
              )}
              {run.experience?.length > 0 && (
                <div className="mt-3 pt-3 border-t border-border">
                  <p className="font-display text-[0.7rem] uppercase tracking-wider text-muted mb-1">Get experience for credit</p>
                  <ul className="text-xs space-y-0.5">
                    {run.experience.map((e) => (
                      <li key={e.code}>
                        <span className="font-mono">{spaced(e.code)}</span> {e.title}: <span className="text-muted">{e.why}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </Card>
          )}

          {run?.resources && (
            <Card title="Outside class">
              {GROUPS.map(([key, title, hint]) =>
                run.resources[key]?.length ? (
                  <div key={key} className="mb-4 last:mb-0">
                    <p className="font-display font-bold text-xs">{title}</p>
                    <p className="text-[0.7rem] text-muted mb-2">{hint}</p>
                    <ul className="grid md:grid-cols-2 gap-2">
                      {run.resources[key].map((r) => <ResourceItem key={r.url} r={r} />)}
                    </ul>
                  </div>
                ) : null
              )}
              {run.dropped?.length > 0 && (
                <details className="text-xs mt-2">
                  <summary className="cursor-pointer text-muted">{run.dropped.length} links dropped by the link checker</summary>
                  <ul className="mt-1 space-y-0.5">
                    {run.dropped.map((d) => (
                      <li key={d.url} className="break-all">
                        <span className="font-mono">{d.url}</span>: {d.reason}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              <p className="text-[0.68rem] text-muted mt-3">
                Links go to outside providers; Bobcat Advisor isn't affiliated with them. Content and prices change, so check
                the site before you enroll.
              </p>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

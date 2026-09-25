import { useEffect, useMemo, useState } from "react";
import { api } from "../api";

export default function PlannerView({ onAsk }) {
  const [courses, setCourses] = useState([]);
  const [completed, setCompleted] = useState(new Set());
  const [result, setResult] = useState(null);
  const [details, setDetails] = useState({});
  const [error, setError] = useState(null);

  useEffect(() => {
    api.listCourses().then(setCourses).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    if (completed.size === 0) {
      setResult(null);
      return;
    }
    api.plan([...completed]).then(setResult).catch((e) => setError(e.message));
  }, [completed]);

  // Catalog detail (what each course unlocks) for each eligible course.
  useEffect(() => {
    if (!result) return;
    result.eligible.forEach((c) => {
      if (!details[c.code]) {
        api.getCourse(c.code).then((d) => setDetails((prev) => ({ ...prev, [c.code]: d })));
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result]);

  const implied = useMemo(
    () => new Set((result?.completed || []).filter((c) => !completed.has(c))),
    [result, completed]
  );

  function toggle(code) {
    setCompleted((prev) => {
      const next = new Set(prev);
      next.has(code) ? next.delete(code) : next.add(code);
      return next;
    });
  }

  const core = courses.filter((c) => Number(c.code[2]) <= 3);
  const eligible = (result?.eligible || []).slice().sort((a, b) => {
    const ua = details[a.code]?.unlocks?.length || 0;
    const ub = details[b.code]?.unlocks?.length || 0;
    return b.newly_unlocked - a.newly_unlocked || ub - ua;
  });

  return (
    <div className="flex-1 overflow-y-auto thin-scroll px-4 md:px-6 py-6">
      <div className="max-w-5xl mx-auto">
        <p className="text-sm text-muted mb-3">
          Select the courses you've passed. Eligibility is computed from the official catalog's prerequisite
          graph.
        </p>
        {error && <p className="text-sm text-red-600 font-mono mb-4">{error}</p>}

        <div className="flex flex-wrap gap-1.5 mb-6">
          {core.map((c) => (
            <button
              key={c.code}
              onClick={() => toggle(c.code)}
              title={c.title}
              className={`text-xs font-mono px-2.5 py-1.5 rounded-full border transition-colors ${
                completed.has(c.code)
                  ? "bg-maroon text-white border-maroon"
                  : implied.has(c.code)
                  ? "bg-maroon/10 border-maroon/30 text-maroon"
                  : "bg-white border-border text-muted hover:border-maroon/40"
              }`}
            >
              {c.code}
            </button>
          ))}
        </div>

        {implied.size > 0 && (
          <p className="text-xs text-muted mb-4 font-mono">
            Implied by your selection: {[...implied].join(", ")}
          </p>
        )}

        {result && (
          <>
            <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
              <h2 className="font-display font-extrabold text-lg">You can take ({eligible.length})</h2>
              <button
                onClick={() =>
                  onAsk(`I've taken ${[...completed].join(", ")}. What should I take next?`)
                }
                className="text-xs font-display font-bold uppercase tracking-wide bg-maroon text-white rounded-xl px-4 py-2 hover:bg-maroon-light"
              >
                Ask the advisor to plan →
              </button>
            </div>
            <div className="grid md:grid-cols-2 gap-3">
              {eligible.map((c) => {
                const d = details[c.code];
                return (
                  <div key={c.code} className="bg-white border border-border rounded-xl p-4 shadow-card">
                    <div className="flex justify-between gap-2">
                      <p className="font-display font-bold text-sm">
                        {c.code} · {c.title}
                      </p>
                      {c.newly_unlocked && (
                        <span className="text-[0.65rem] font-mono text-emerald-700 shrink-0">unlocked</span>
                      )}
                    </div>
                    {d?.unlocks?.length > 0 && (
                      <p className="font-mono text-[0.7rem] text-muted mt-1">unlocks {d.unlocks.join(", ")}</p>
                    )}
                    {c.conditions.length > 0 && (
                      <p className="text-[0.7rem] text-amber-700 mt-2">Also check: {c.conditions.join("; ")}</p>
                    )}
                  </div>
                );
              })}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

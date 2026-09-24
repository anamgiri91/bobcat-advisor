import { useEffect, useState } from "react";
import { api } from "../api";

const ASPECT_LABELS = {
  difficulty: "Difficulty",
  workload: "Workload",
  grading: "Grading",
  exams: "Exams",
  lectures: "Lectures",
  helpfulness: "Helpfulness",
  curve: "Curves grades",
  attendance_required: "Attendance required",
  recommend: "Would recommend",
};

function Rating({ label, value, n, invert }) {
  if (value == null) return null;
  const pct = (value / 5) * 100;
  const good = invert ? value <= 2.5 : value >= 3.5;
  const bad = invert ? value >= 3.8 : value <= 2.5;
  const color = good ? "bg-emerald-500" : bad ? "bg-rose-500" : "bg-gold";
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-muted">{label}</span>
        <span className="font-mono">
          {value}/5 <span className="text-muted">n={n}</span>
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-border overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function GradeBars({ grades, n }) {
  if (!n) return null;
  return (
    <div>
      <p className="text-xs text-muted mb-1">Self-reported grades (n={n})</p>
      <div className="flex items-end gap-1 h-12">
        {["A", "B", "C", "D", "F"].map((g) => {
          const v = grades[g] || 0;
          return (
            <div key={g} className="flex-1 flex flex-col items-center gap-0.5">
              <div className="w-full bg-maroon/70 rounded-t" style={{ height: `${(v / n) * 40}px` }} title={`${v}`} />
              <span className="text-[0.65rem] font-mono text-muted">{g}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StatsCard({ title, stats }) {
  return (
    <div className="bg-white border border-border rounded-xl p-4 space-y-3 shadow-card">
      <div className="flex justify-between items-baseline">
        <h3 className="font-display font-bold text-sm">{title}</h3>
        <span className="font-mono text-xs text-muted">{stats.review_count} reviews</span>
      </div>
      <Rating label="Quality (RMP)" value={stats.avg_quality} n={stats.quality_n} />
      <Rating label="Difficulty (RMP)" value={stats.avg_difficulty} n={stats.difficulty_n} invert />
      <GradeBars grades={stats.grades} n={stats.grades_n} />
      {Object.keys(stats.aspects).length > 0 && (
        <div>
          <p className="text-xs text-muted mb-1">
            What reviews mention <span className="font-mono">({stats.aspect_method} tags)</span>
          </p>
          <ul className="text-xs space-y-0.5">
            {Object.entries(stats.aspects).map(([aspect, info]) => (
              <li key={aspect} className="flex justify-between gap-2">
                <span>{ASPECT_LABELS[aspect] || aspect}</span>
                <span className="font-mono text-muted text-right">
                  {Object.entries(info.values)
                    .map(([v, c]) => `${c} ${v}`)
                    .join(" · ")}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function ProfessorsView({ onAsk }) {
  const [profs, setProfs] = useState([]);
  const [selected, setSelected] = useState(null);
  const [profile, setProfile] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.listProfessors().then(setProfs).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setProfile(null);
    api.getProfessor(selected).then(setProfile).catch((e) => setError(e.message));
  }, [selected]);

  return (
    <div className="flex-1 overflow-y-auto thin-scroll px-4 md:px-6 py-6">
      <div className="max-w-5xl mx-auto">
        <p className="text-sm text-muted mb-4">
          Numbers are computed directly from the review data — the same statistics the advisor cites.
        </p>
        {error && <p className="text-sm text-red-600 font-mono mb-4">{error}</p>}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2 mb-6">
          {profs.map((p) => (
            <button
              key={p.name}
              onClick={() => setSelected(p.name)}
              className={`text-left rounded-xl border px-3 py-2 transition-colors ${
                selected === p.name ? "border-maroon bg-maroon/5" : "border-border bg-white hover:border-maroon/40"
              }`}
            >
              <p className="font-display font-bold text-sm">{p.name}</p>
              <p className="font-mono text-[0.7rem] text-muted">
                {p.review_count} reviews
                {p.avg_quality != null && ` · ${p.avg_quality}/5`}
              </p>
            </button>
          ))}
        </div>

        {profile && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="font-display font-extrabold text-xl">{profile.name}</h2>
              <button
                onClick={() => onAsk(`What do students say about ${profile.name}?`)}
                className="text-xs font-display font-bold uppercase tracking-wide bg-maroon text-white rounded-xl px-4 py-2 hover:bg-maroon-light"
              >
                Ask the advisor →
              </button>
            </div>
            <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-3">
              <StatsCard title="All courses" stats={profile.overall} />
              {profile.courses.map((c) => (
                <StatsCard key={c.course} title={`${c.course} · ${c.title || ""}`} stats={c} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

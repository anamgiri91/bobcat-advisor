import { useEffect, useState } from "react";
import { api } from "../api";

export default function AnalyticsBar() {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    api.getAnalytics().then(setStats).catch(() => setStats(null));
  }, []);

  if (!stats || stats.total_questions === 0) return null;

  const { total_questions, avg_latency_ms, feedback, verifier_pass_rate } = stats;
  const totalVotes = feedback.helpful + feedback.not_helpful;
  const helpfulPct = totalVotes > 0 ? Math.round((feedback.helpful / totalVotes) * 100) : null;

  return (
    <div className="hidden md:flex items-center gap-4 px-4 py-1.5 font-mono text-[0.7rem] text-white/60 border-t border-white/10">
      <span>{total_questions} questions answered</span>
      {avg_latency_ms != null && <span>· avg {avg_latency_ms}ms</span>}
      {helpfulPct != null && <span>· {helpfulPct}% rated helpful</span>}
      {verifier_pass_rate != null && (
        <span>· {Math.round(verifier_pass_rate * 100)}% of claims verified</span>
      )}
    </div>
  );
}

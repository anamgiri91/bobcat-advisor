/**
 * Live view of the multi-agent pipeline for one answer: router plan, each
 * specialist's status/timing, then writing and verification.
 */

const AGENT_LABELS = {
  search: "Searching the catalog and syllabi",
  kb: "Searching official TXST pages",
  calendar: "Checking the academic calendar",
  catalog: "Checking the catalog",
  planner: "Building your plan",
};

const INTENT_LABELS = {
  policy: "Rules, deadlines and programs",
  instructor: "About an instructor (not answered)",
  compare: "Comparison",
  course_info: "Course question",
  prereq: "Prerequisites",
  plan: "Course planning",
  off_topic: "Off topic",
};

function Step({ state, children }) {
  const dot =
    state === "done" ? "bg-emerald-500" : state === "active" ? "bg-gold animate-pulse" : "bg-border";
  return (
    <li className="flex items-center gap-2">
      <span className={`inline-block w-1.5 h-1.5 rounded-full ${dot}`} />
      <span className={state === "pending" ? "text-muted/60" : "text-muted"}>{children}</span>
    </li>
  );
}

export default function AgentTrace({ message }) {
  const { plan, agents = {}, streaming, content, verification, mode, waking } = message;
  if (!plan && !streaming) return null;

  const agentNames = plan?.agents || [];
  const writing = streaming && content;
  const entities = plan?.courses || [];

  return (
    <ul className="font-mono text-[0.7rem] space-y-1 px-5 pt-3">
      <Step state={plan ? "done" : "active"}>
        {plan
          ? `${INTENT_LABELS[plan.intent] || plan.intent}${entities.length ? ` · ${entities.join(", ")}` : ""}${
              plan.method === "rules" ? " · rule router" : ""
            }`
          : waking
            ? "Waking up the server (it sleeps when idle; this can take up to a minute)…"
            : "Understanding your question…"}
      </Step>
      {agentNames.map((name) => {
        const a = agents[name];
        return (
          <Step key={name} state={a?.status === "done" ? "done" : plan ? "active" : "pending"}>
            {AGENT_LABELS[name] || name}
            {a?.status === "done" && ` · ${a.evidence} sources · ${a.ms}ms`}
          </Step>
        );
      })}
      {agentNames.length > 0 && (
        <Step state={writing || mode ? (streaming ? "active" : "done") : "pending"}>
          {mode === "extractive" ? "Quoting sources directly" : "Writing a cited answer"}
        </Step>
      )}
      {agentNames.length > 0 && mode !== "extractive" && (
        <Step state={verification ? "done" : writing ? "active" : "pending"}>Verifying claims</Step>
      )}
    </ul>
  );
}

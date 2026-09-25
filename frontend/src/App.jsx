import { useEffect, useState } from "react";
import Header from "./components/Header";
import AdvisorView from "./views/AdvisorView";
import ChatView from "./views/ChatView";
import PlannerView from "./views/PlannerView";

const TABS = ["chat", "advisor", "planner"];

function tabFromHash() {
  const t = window.location.hash.replace("#", "");
  return TABS.includes(t) ? t : "chat";
}

export default function App() {
  // Deep links: #advisor / #planner pick a view, ?q=... asks a question.
  const [tab, setTabState] = useState(tabFromHash);
  const [pendingQuestion, setPendingQuestion] = useState(() => {
    const q = new URLSearchParams(window.location.search).get("q");
    // Drop ?q= from the URL so a reload doesn't ask again.
    if (q) window.history.replaceState(null, "", window.location.pathname + window.location.hash);
    return q;
  });

  useEffect(() => {
    const onHash = () => setTabState(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  function setTab(t) {
    window.location.hash = t === "chat" ? "" : t;
    setTabState(t);
  }

  // Other views hand a question to the chat ("Ask the advisor →").
  function ask(question) {
    setPendingQuestion(question);
    setTab("chat");
  }

  return (
    <div className="h-screen flex flex-col bg-cream">
      <Header tab={tab} onTab={setTab} />
      {/* Chat stays mounted so switching tabs doesn't abort a streaming answer. */}
      <div className={tab === "chat" ? "flex-1 flex overflow-hidden" : "hidden"}>
        <ChatView pendingQuestion={pendingQuestion} onPendingHandled={() => setPendingQuestion(null)} />
      </div>
      {tab === "advisor" && <AdvisorView />}
      {tab === "planner" && <PlannerView onAsk={ask} />}
    </div>
  );
}

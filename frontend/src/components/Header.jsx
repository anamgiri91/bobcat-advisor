import AnalyticsBar from "./AnalyticsBar";

const LOGO_SVG = `
<svg width="40" height="40" viewBox="0 0 52 52" fill="none" xmlns="http://www.w3.org/2000/svg">
  <circle cx="26" cy="26" r="25" stroke="rgba(255,255,255,0.3)" stroke-width="1.5"/>
  <circle cx="26" cy="26" r="22" fill="rgba(255,255,255,0.1)"/>
  <ellipse cx="26" cy="27" rx="12" ry="11" fill="rgba(255,255,255,0.95)"/>
  <path d="M16 20 L13 13 L20 17 Z" fill="rgba(255,255,255,0.95)"/>
  <path d="M36 20 L39 13 L32 17 Z" fill="rgba(255,255,255,0.95)"/>
  <path d="M16 19 L14.5 14.5 L18.5 17.5 Z" fill="#c9a227"/>
  <path d="M36 19 L37.5 14.5 L33.5 17.5 Z" fill="#c9a227"/>
  <circle cx="21" cy="25" r="2.5" fill="#4a1942"/>
  <circle cx="31" cy="25" r="2.5" fill="#4a1942"/>
  <circle cx="21.8" cy="24.2" r="0.8" fill="white"/>
  <circle cx="31.8" cy="24.2" r="0.8" fill="white"/>
  <path d="M24.5 29 Q26 31 27.5 29 Q26 28 24.5 29 Z" fill="#c9a227"/>
</svg>`;

const TABS = [
  { id: "chat", label: "Ask" },
  { id: "advisor", label: "Advisor" },
  { id: "planner", label: "Planner" },
];

export default function Header({ tab, onTab }) {
  return (
    <header className="bg-gradient-to-br from-maroon via-maroon-light to-[#8b3a7e]">
      <div className="px-4 md:px-6 py-4 flex flex-wrap items-center gap-3">
        <span dangerouslySetInnerHTML={{ __html: LOGO_SVG }} />
        <div>
          <h1 className="font-display font-extrabold text-xl text-white leading-tight">
            Bobcat <span className="text-gold-light">Advisor</span>
          </h1>
          <p className="text-white/70 text-xs font-body">
            TXST course advising and professor Q&A, grounded in the catalog and real reviews
          </p>
        </div>
        <nav className="ml-auto flex gap-1" aria-label="Views">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => onTab(t.id)}
              aria-current={tab === t.id ? "page" : undefined}
              className={`font-display text-xs font-bold uppercase tracking-wide px-3 py-2 rounded-lg transition-colors ${
                tab === t.id ? "bg-white text-maroon" : "text-white/80 hover:bg-white/10"
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </div>
      <AnalyticsBar />
    </header>
  );
}

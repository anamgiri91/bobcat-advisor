export default function Sidebar({ conversations, activeId, onSelect, onNewChat }) {
  return (
    <aside className="hidden md:flex md:w-64 flex-col border-r border-border bg-white">
      <div className="p-4 border-b border-border">
        <button
          onClick={onNewChat}
          className="w-full font-display font-bold text-sm uppercase tracking-wide bg-maroon text-white rounded-xl py-3 hover:bg-maroon-light transition-colors"
        >
          + New chat
        </button>
      </div>

      <div className="flex-1 overflow-y-auto thin-scroll py-2">
        <p className="px-4 pt-2 pb-1 font-display text-xs uppercase tracking-wider text-muted">
          Recent
        </p>
        {conversations.length === 0 && (
          <p className="px-4 py-3 text-sm text-muted">No conversations yet.</p>
        )}
        {conversations.map((c) => (
          <button
            key={c.id}
            onClick={() => onSelect(c.id)}
            className={`w-full text-left px-4 py-3 text-sm truncate transition-colors ${
              c.id === activeId
                ? "bg-cream border-l-2 border-gold font-medium"
                : "hover:bg-cream/70"
            }`}
            title={c.title}
          >
            {c.title}
          </button>
        ))}
      </div>
    </aside>
  );
}

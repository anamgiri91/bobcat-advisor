/**
 * Minimal, safe Markdown for answers: paragraphs, "- " bullets, **bold**,
 * _italic_, and [n] citations rendered as clickable chips. Builds React
 * elements directly — no dangerouslySetInnerHTML, so model output can never
 * inject HTML.
 */

const INLINE = /(\*\*[^*]+\*\*|_[^_]+_|\[\d+\])/g;

function inline(text, onCite, keyPrefix) {
  return text.split(INLINE).map((part, i) => {
    const key = `${keyPrefix}-${i}`;
    if (!part) return null;
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={key}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("_") && part.endsWith("_") && part.length > 2) {
      return <em key={key} className="text-muted">{part.slice(1, -1)}</em>;
    }
    const cite = part.match(/^\[(\d+)\]$/);
    if (cite) {
      const n = Number(cite[1]);
      return (
        <button
          key={key}
          type="button"
          onClick={() => onCite?.(n)}
          className="align-super text-[0.65rem] font-mono font-semibold text-maroon bg-maroon/10 hover:bg-maroon/20 rounded px-1 mx-0.5"
          aria-label={`Source ${n}`}
        >
          {n}
        </button>
      );
    }
    return <span key={key}>{part}</span>;
  });
}

export default function RichText({ text, onCite }) {
  const blocks = [];
  let list = [];

  const flushList = () => {
    if (list.length) {
      blocks.push(
        <ul key={`ul-${blocks.length}`} className="list-disc pl-5 space-y-1 my-2">
          {list.map((item, i) => (
            <li key={i}>{inline(item, onCite, `li-${blocks.length}-${i}`)}</li>
          ))}
        </ul>
      );
      list = [];
    }
  };

  text.split("\n").forEach((raw, i) => {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/) || line.match(/^\s*\d+\.\s+(.*)$/);
    if (bullet) {
      list.push(bullet[1]);
      return;
    }
    flushList();
    if (!line.trim()) return;
    const heading = line.match(/^#{1,4}\s+(.*)$/);
    if (heading) {
      blocks.push(
        <p key={`h-${i}`} className="font-display font-bold text-sm mt-3 mb-1">
          {inline(heading[1], onCite, `h-${i}`)}
        </p>
      );
      return;
    }
    blocks.push(
      <p key={`p-${i}`} className="my-1.5">
        {inline(line, onCite, `p-${i}`)}
      </p>
    );
  });
  flushList();
  return <div className="font-body text-[0.95rem] leading-relaxed text-ink">{blocks}</div>;
}

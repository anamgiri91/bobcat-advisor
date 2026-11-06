import { useRef, useState } from "react";
import { api } from "../api";
import { spaced } from "./CoursePicker";

const STATUS_WORDS = { document: "", hours: " (from your hours)" };

/** What was read from the audit, with any warnings, and a way to undo it. */
export function ImportSummary({ result, onUndo }) {
  const gpa = result.gpa != null ? ` · GPA ${result.gpa.toFixed(2)}` : "";
  return (
    <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs space-y-1.5" role="status">
      <div className="flex items-center justify-between gap-2">
        <p className="font-display font-bold text-sm text-emerald-900">
          Filled in from your {result.source === "pdf" ? "PDF" : "pasted audit"}
        </p>
        <button type="button" onClick={onUndo} className="text-maroon hover:underline shrink-0">
          Undo
        </button>
      </div>
      <p>
        {result.completed.length} courses passed · {result.in_progress.length} in progress ·{" "}
        <span className="font-semibold">{result.hours_earned} hours earned</span>
        {gpa}
        {result.classification && (
          <>
            {" "}
            · {result.classification}
            {STATUS_WORDS[result.classification_source] || ""}
          </>
        )}
      </p>
      {result.elective_credit.length > 0 && (
        <p>
          Transfer/test credit without a TXST match: {result.elective_credit.map((e) => spaced(e.code)).join(", ")}
        </p>
      )}
      {result.not_counted.length > 0 && (
        <p className="text-muted">Not counted (grade too low or withdrawn): {result.not_counted.map(spaced).join(", ")}</p>
      )}
      {result.warnings.map((w) => (
        <p key={w} className="text-amber-900">
          {w}
        </p>
      ))}
      <p className="text-muted">Check the courses below and fix anything that was misread.</p>
    </div>
  );
}

/**
 * Fill the form from a degree audit or unofficial transcript: a PDF upload,
 * or the audit page's text pasted in. The server reads it in memory and
 * keeps nothing; only courses, hours, GPA and program come back.
 */
export default function ImportAudit({ result, onImported, onUndo }) {
  const [mode, setMode] = useState("pdf");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const fileRef = useRef(null);

  if (result) return <ImportSummary result={result} onUndo={onUndo} />;

  async function read(input, kind) {
    setBusy(true);
    setError(null);
    try {
      const imported = await api.importAudit(input);
      api.track(kind === "pdf" ? "import_pdf" : "import_text");
      setText("");
      onImported(imported);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  return (
    <div className="rounded-lg border border-dashed border-maroon/40 bg-maroon/5 p-3 space-y-2">
      <p className="font-display font-bold text-sm">Fill this in from your degree audit</p>
      <p className="text-xs text-muted">
        Upload the PDF of your degree audit or unofficial transcript. We'll fill in your courses, transfer and AP
        credit, hours and GPA. It's read in memory and never saved; your name and ID aren't read.
      </p>
      {mode === "pdf" ? (
        <div className="flex flex-wrap items-center gap-3">
          <label
            className={`text-xs font-display font-bold uppercase tracking-wide bg-maroon text-white rounded-lg px-3 py-2 cursor-pointer hover:bg-maroon-light ${
              busy ? "opacity-60 pointer-events-none" : ""
            }`}
          >
            {busy ? "Reading…" : "Upload PDF"}
            <input
              ref={fileRef}
              type="file"
              accept="application/pdf,.pdf"
              className="sr-only"
              aria-label="Degree audit or transcript PDF"
              disabled={busy}
              onChange={(e) => e.target.files?.[0] && read(e.target.files[0], "pdf")}
            />
          </label>
          <button type="button" onClick={() => setMode("text")} className="text-xs text-maroon hover:underline">
            or paste the text instead
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          <textarea
            className="w-full text-xs bg-white border border-border rounded-lg px-3 py-2 focus:outline-none focus:border-maroon/50"
            rows={4}
            aria-label="Degree audit text"
            placeholder="Select everything on your degree audit page, copy it and paste it here"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              disabled={busy || !text.trim()}
              onClick={() => read(text, "text")}
              className="text-xs font-display font-bold uppercase tracking-wide bg-maroon text-white rounded-lg px-3 py-2 disabled:opacity-50"
            >
              {busy ? "Reading…" : "Read it"}
            </button>
            <button type="button" onClick={() => setMode("pdf")} className="text-xs text-maroon hover:underline">
              upload a PDF instead
            </button>
          </div>
        </div>
      )}
      {error && (
        <p role="alert" className="text-xs text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}

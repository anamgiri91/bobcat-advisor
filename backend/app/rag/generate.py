"""
generate.py
===========
Compatibility shim. Generation moved to the agent pipeline:

  app/llm.py                  provider-agnostic client (Gemini/Groq): fallback,
                              JSON mode, streaming, budgets
  app/agents/synthesizer.py   grounded, cited answer writing
  app/agents/verifier.py      claim-level support check
  app/agents/orchestrator.py  router -> specialists -> synthesizer -> verifier

answer_question() keeps the old (answer, sources_text) signature for scripts
and notebooks written against the original Gradio-era API.
"""

from __future__ import annotations

from ..agents.orchestrator import answer
from ..tracing import start_trace


def answer_question(query: str, source_filter: str | None = None) -> tuple[str, str]:
    with start_trace():
        result = answer(query, source_filter=source_filter)
    sources = "\n".join(f"{s['n']}. {s['label']}" for s in result.get("sources", []))
    return result.get("answer", ""), sources or "No sources retrieved."

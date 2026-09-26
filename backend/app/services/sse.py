"""
sse.py
======
Relay a pipeline's events to the client as Server-Sent Events.

The pipeline generator runs in one worker thread inside a trace, so the
trace context survives across yields (Starlette advances a sync generator
with a fresh contextvars copy on every next(), which would drop it). The
final `done` event gets the trace attached; an exception becomes an
`error` event, so every stream ends with `done` or `error`.
"""

from __future__ import annotations

import contextvars
import json
import queue
import threading
from collections.abc import Callable, Iterator

from fastapi.responses import StreamingResponse

from ..tracing import start_trace

_SENTINEL = object()


def relay(run: Callable[[], Iterator[dict]], *, kind: str,
          public_errors: tuple[type[Exception], ...] = ()) -> Iterator[dict]:
    """Yield run()'s events from a worker thread. Exceptions of a type in
    public_errors are shown as-is (they're written for the student); others
    are reported by type and message, as before."""
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            with start_trace(kind=kind) as trace:
                for event in run():
                    if event["type"] == "done":
                        trace.attributes["mode"] = event.get("mode") or ""
                        event["trace"] = trace.to_dict()
                    q.put(event)
        except public_errors as e:
            q.put({"type": "error", "message": str(e)})
        except Exception as e:
            q.put({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            q.put(_SENTINEL)

    threading.Thread(target=contextvars.copy_context().run, args=(worker,), daemon=True).start()
    while (event := q.get()) is not _SENTINEL:
        yield event


def response(events: Iterator[dict]) -> StreamingResponse:
    def body():
        for event in events:
            yield f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(body(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

function streamResponse(chunks, status = 200) {
  const encoder = new TextEncoder();
  let i = 0;
  return {
    ok: status < 400,
    status,
    json: async () => ({ detail: [{ msg: "field required" }, { msg: "too long" }] }),
    body: {
      getReader: () => ({
        read: async () => (i < chunks.length ? { value: encoder.encode(chunks[i++]), done: false } : { done: true }),
      }),
    },
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("server-sent events parser", () => {
  it("reassembles events split across network chunks", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      streamResponse(['event: plan\ndata: {"a":', '1}\n\nevent: tok', 'en\ndata: {"text":"hi"}\n\nevent: done\ndata: {}\n\n'])
    ));
    const events = [];
    await api.askStream("q", null, null, (type, data) => events.push([type, data]));
    expect(events).toEqual([["plan", { a: 1 }], ["token", { text: "hi" }], ["done", {}]]);
  });

  it("posts the advising profile to the advise stream", async () => {
    const fetchMock = vi.fn(async () => streamResponse(["event: done\ndata: {}\n\n"]));
    vi.stubGlobal("fetch", fetchMock);
    await api.adviseStream({ year: "junior" }, () => {});
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/advise\/stream$/);
    expect(JSON.parse(init.body)).toEqual({ year: "junior" });
  });

  it("surfaces validation errors as a readable message", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => streamResponse([], 422)));
    await expect(api.adviseStream({}, () => {})).rejects.toThrow("field required; too long");
  });
});

describe("JSON requests", () => {
  it("sends what-if scenarios with the profile", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ scenarios: [] }) }));
    vi.stubGlobal("fetch", fetchMock);
    await api.whatIf({ year: "junior" }, [{ type: "add_minor", minor: "Data Science" }]);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/advise\/whatif$/);
    expect(JSON.parse(init.body).scenarios[0].minor).toBe("Data Science");
  });
});

describe("stream failures never leave the UI spinning", () => {
  it("reports a stream that closes without a final event", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => streamResponse(['event: plan\ndata: {"a":1}\n\n'])));
    const events = [];
    await expect(api.askStream("q", null, null, (t) => events.push(t))).rejects.toThrow(/closed before/);
    expect(events).toEqual(["plan"]);
  });

  it("explains an unreachable server instead of 'Failed to fetch'", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    await expect(api.askStream("q", null, null, () => {})).rejects.toThrow(/Can't reach the server/);
  });

  it("says the server is waking up, then gives up if nothing arrives", async () => {
    vi.stubGlobal("fetch", vi.fn((url, init) => new Promise((_, reject) => {
      init.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    })));
    const events = [];
    await expect(
      api.askStream("q", null, null, (t) => events.push(t), undefined, { slowMs: 10, timeoutMs: 40 })
    ).rejects.toThrow(/didn't respond in time/);
    expect(events).toEqual(["slow"]);
  });

  it("a user cancel stays an AbortError (no error message shown)", async () => {
    vi.stubGlobal("fetch", vi.fn((url, init) => new Promise((_, reject) => {
      init.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    })));
    const controller = new AbortController();
    const pending = api.askStream("q", null, null, () => {}, controller.signal, { slowMs: 1000, timeoutMs: 5000 });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it("server-sent error events end the stream without the cut-off message", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => streamResponse(['event: error\ndata: {"message":"boom"}\n\n'])));
    const events = [];
    await api.askStream("q", null, null, (t, d) => events.push([t, d]));
    expect(events).toEqual([["error", { message: "boom" }]]);
  });
});

describe("misconfigured API address", () => {
  it("explains an HTML page instead of 'Unexpected token <'", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true, status: 200, headers: { get: () => "text/html" }, json: async () => { throw new SyntaxError("Unexpected token '<'"); },
    })));
    await expect(api.listCourses()).rejects.toThrow(/VITE_API_BASE_URL/);
    await expect(api.askStream("q", null, null, () => {})).rejects.toThrow(/VITE_API_BASE_URL/);
  });

  it("also catches HTML without a content type", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => { throw new SyntaxError("x"); } })));
    await expect(api.careerPaths()).rejects.toThrow(/VITE_API_BASE_URL/);
  });

  it("posts career requests to the career stream", async () => {
    const fetchMock = vi.fn(async () => streamResponse(["event: done\ndata: {}\n\n"]));
    vi.stubGlobal("fetch", fetchMock);
    await api.careerStream({ career: "ml_engineer" }, () => {});
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/career\/stream$/);
  });
});

describe("files and counters", () => {
  it("sends a PDF as application/pdf and text as text/plain", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, headers: { get: () => "application/json" }, json: async () => ({ completed: [] }) }));
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["%PDF"], "a.pdf", { type: "application/pdf" });
    await api.importAudit(file);
    await api.importAudit("CS 1428 A 4");
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/advise\/import$/);
    expect(fetchMock.mock.calls[0][1].headers["Content-Type"]).toBe("application/pdf");
    expect(fetchMock.mock.calls[0][1].body).toBe(file);
    expect(fetchMock.mock.calls[1][1].headers["Content-Type"]).toBe("text/plain; charset=utf-8");
  });

  it("returns files as blobs and server errors as messages", async () => {
    const blob = new Blob(["x"]);
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, headers: { get: () => "application/pdf" }, blob: async () => blob })));
    expect(await api.prepSheet({ year: "junior" })).toBe(blob);
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 404, headers: { get: () => "application/json" },
      json: async () => ({ detail: "No upcoming academic calendar dates" }) })));
    await expect(api.calendar("Fall 2026")).rejects.toThrow("No upcoming academic calendar dates");
  });

  it("counting never throws", () => {
    vi.stubGlobal("fetch", vi.fn(() => { throw new Error("blocked"); }));
    expect(() => api.track("view_chat")).not.toThrow();
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("offline"); }));
    expect(() => api.track("view_chat")).not.toThrow();
  });
});

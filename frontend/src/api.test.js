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

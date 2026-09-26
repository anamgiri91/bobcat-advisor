import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import CareerView, { CoverageBar, ResourceItem } from "./CareerView";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const PATHS = [
  { id: "ml_engineer", title: "Machine learning engineer", summary: "Builds and ships ML models." },
  { id: "game_developer", title: "Game developer", summary: "Builds games." },
];

const EVENTS = [
  ["agent", { agent: "career_analyst", status: "done", ms: 1, summary: "Machine learning engineer: 7 core" }],
  ["career", { career: { id: "ml_engineer", title: "Machine learning engineer", summary: "Builds and ships ML models." }, note: "" }],
  ["courses", {
    courses: [
      { code: "CS3358", title: "Data Structures and Algorithms", status: "eligible", skills: [], unlocks: ["CS4347", "CS4337"], missing: [], evidence: [] },
      { code: "CS4347", title: "Introduction to Machine Learning", status: "later", skills: ["Machine learning"], unlocks: [], missing: ["CS3358"], evidence: [] },
      { code: "CS1428", title: "Foundations of Computer Science I", status: "done", skills: ["Programming fundamentals"], unlocks: [], missing: [], evidence: [] },
    ],
    experience: [{ code: "CS4100", title: "Computer Science Internship", why: "Internship credit" }],
  }],
  ["skills", {
    skills: [
      { id: "ml", name: "Machine learning", importance: "core", coverage: "covered", courses: ["CS4347"], note: "" },
      { id: "mlops", name: "ML deployment and MLOps", importance: "core", coverage: "gap", courses: [], note: "" },
    ],
    coverage: { covered: 1, partial: 0, gap: 1 },
  }],
  ["browse", { url: "https://madewithml.com/", ok: true, ms: 120, cached: false }],
  ["resources", {
    resources: {
      gap: [{ title: "Made With ML", provider: "Made With ML", url: "https://madewithml.com/", kind: "course", cost: "free",
        for_skill: "ML deployment and MLOps", source: "seed", check: { status: "verified", checked_on: "2026-09-25" } }],
      deeper: [],
      certification: [{ title: "AWS ML Engineer", provider: "AWS", url: "https://aws.amazon.com/x", kind: "certification",
        cost: "paid", for_skill: "", source: "seed", check: { status: "unchecked", reason: "the site refused an automated check" } }],
    },
    dropped: [{ url: "https://dead.example/", reason: "link failed: HTTP 404" }],
  }],
  ["memo", { text: "**Next term:** CS3358 (Data Structures and Algorithms).", mode: "template" }],
  ["done", {}],
];

beforeEach(() => {
  vi.spyOn(api, "careerPaths").mockResolvedValue(PATHS);
  vi.spyOn(api, "listCourses").mockResolvedValue([{ code: "CS1428", title: "Foundations of Computer Science I" }]);
});

describe("CareerView", () => {
  it("asks for a career before running", async () => {
    const spy = vi.spyOn(api, "careerStream");
    render(<CareerView />);
    fireEvent.click(screen.getByText("Build my career plan"));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("runs the pipeline and shows courses, skills and checked resources", async () => {
    const spy = vi.spyOn(api, "careerStream").mockImplementation(async (body, onEvent) => {
      EVENTS.forEach(([t, d]) => onEvent(t, d));
    });
    render(<CareerView />);
    fireEvent.click(await screen.findByRole("radio", { name: /Machine learning engineer/ }));
    fireEvent.click(screen.getByRole("button", { name: /CS 1428/ }));
    fireEvent.click(screen.getByLabelText("Free resources only"));
    fireEvent.click(screen.getByText("Build my career plan"));
    await waitFor(() => expect(screen.getByText("Your roadmap")).toBeTruthy());
    expect(spy.mock.calls[0][0]).toEqual({ career: "ml_engineer", completed: ["CS1428"], in_progress: [],
      include_certifications: true, free_only: true });
    expect(screen.getByText(/Unlocks 2 courses on this path/)).toBeTruthy();
    expect(screen.getByText("Needs CS3358")).toBeTruthy();
    expect(screen.getByText(/Already done/)).toBeTruthy();
    expect(screen.getByText("learn outside class")).toBeTruthy();
    expect(screen.getByText("✓ link checked 2026-09-25")).toBeTruthy();
    expect(screen.getByText(/not checked: the site refused/)).toBeTruthy();
    expect(screen.getByText("1 links dropped by the link checker")).toBeTruthy();
    expect(screen.getByText("Made With ML").closest("a").getAttribute("rel")).toContain("noopener");
  });

  it("a typed goal wins over the picked card, and errors are shown", async () => {
    const spy = vi.spyOn(api, "careerStream").mockImplementation(async (body, onEvent) => {
      onEvent("error", { message: "Couldn't match that to a career path." });
    });
    render(<CareerView initialGoal="robotics" />);
    fireEvent.click(await screen.findByRole("radio", { name: /Game developer/ }));
    fireEvent.change(screen.getByPlaceholderText(/quant developer/), { target: { value: "robotics" } });
    fireEvent.click(screen.getByText("Build my career plan"));
    expect(await screen.findByText("Couldn't match that to a career path.")).toBeTruthy();
    expect(spy.mock.calls[0][0].career).toBe("robotics");
  });
});

describe("pieces", () => {
  it("coverage bar sentence", () => {
    render(<CoverageBar coverage={{ covered: 9, partial: 2, gap: 3 }} />);
    expect(screen.getByRole("img").getAttribute("aria-label")).toBe("9 of 14 skills covered by TXST courses");
    expect(screen.getByText(/you'll learn outside class/)).toBeTruthy();
  });

  it("search results say the cost must be checked", () => {
    render(<ul><ResourceItem r={{ title: "New MLOps", provider: "coursera.org", url: "https://www.coursera.org/x",
      kind: "course", cost: "check site", source: "search", for_skill: "", check: { status: "verified", checked_on: "d", summary: "Deploy models" } }} /></ul>);
    expect(screen.getByText(/check the site for cost · found by web search/)).toBeTruthy();
    expect(screen.getByText("“Deploy models”")).toBeTruthy();
  });
});

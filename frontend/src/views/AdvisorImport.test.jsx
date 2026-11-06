import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import ImportAudit from "../components/ImportAudit";
import AdvisorView, { AdvisorPrep, Timetable } from "./AdvisorView";

const IMPORTED = {
  source: "pdf",
  completed: ["CS1428", "CS2308", "MATH2471", "HIST1301"],
  in_progress: ["CS3358"],
  not_counted: ["ENG1320 (F)"],
  elective_credit: [{ code: "HIST1XXX", hours: 3, source: "transfer" }],
  hours_earned: 37,
  gpa: 3.447,
  classification: "sophomore",
  classification_source: "document",
  major: "Computer Science",
  degree: "BS",
  catalog_year: "2024-2025",
  warnings: ["CS 2308 was taken 2 times; counted your B grade from Fall 2025."],
  questions: ["Which degree requirements do my transfer credits (HIST 1XXX) count for?"],
};

beforeEach(() => {
  vi.spyOn(api, "track").mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ImportAudit", () => {
  it("uploads a PDF and reports what it read", async () => {
    const spy = vi.spyOn(api, "importAudit").mockResolvedValue(IMPORTED);
    const onImported = vi.fn();
    render(<ImportAudit result={null} onImported={onImported} onUndo={() => {}} />);
    const file = new File(["%PDF-1.4"], "audit.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Degree audit or transcript PDF"), { target: { files: [file] } });
    await waitFor(() => expect(onImported).toHaveBeenCalledWith(IMPORTED));
    expect(spy).toHaveBeenCalledWith(file);
    expect(api.track).toHaveBeenCalledWith("import_pdf");
  });

  it("reads pasted text and shows errors", async () => {
    vi.spyOn(api, "importAudit").mockRejectedValue(new Error("No course records were found."));
    render(<ImportAudit result={null} onImported={() => {}} onUndo={() => {}} />);
    fireEvent.click(screen.getByText("or paste the text instead"));
    const read = screen.getByText("Read it");
    expect(read.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Degree audit text"), { target: { value: "CS 1428 A 4" } });
    fireEvent.click(read);
    expect((await screen.findByRole("alert")).textContent).toBe("No course records were found.");
    expect(api.importAudit).toHaveBeenCalledWith("CS 1428 A 4");
  });

  it("summarises an import with its warnings and an undo", () => {
    const onUndo = vi.fn();
    render(<ImportAudit result={IMPORTED} onImported={() => {}} onUndo={onUndo} />);
    const box = screen.getByRole("status");
    expect(box.textContent).toContain("4 courses passed · 1 in progress");
    expect(box.textContent).toContain("37 hours earned · GPA 3.45 · sophomore");
    expect(box.textContent).toContain("HIST 1XXX");
    expect(box.textContent).toContain("ENG 1320 (F)");
    expect(box.textContent).toContain("taken 2 times");
    fireEvent.click(screen.getByText("Undo"));
    expect(onUndo).toHaveBeenCalled();
  });
});

describe("AdvisorView with an imported audit", () => {
  it("fills the form, sends the real hours, and undo restores it", async () => {
    vi.spyOn(api, "listCourses").mockResolvedValue([
      { code: "CS1428", title: "Foundations of Computer Science I" },
      { code: "CS2308", title: "Foundations of Computer Science II" },
      { code: "CS3358", title: "Data Structures and Algorithms" },
    ]);
    vi.spyOn(api, "importAudit").mockResolvedValue(IMPORTED);
    const stream = vi.spyOn(api, "adviseStream").mockImplementation(async (body, onEvent) => onEvent("done", {}));
    render(<AdvisorView />);
    await screen.findByText("CS 3358");
    fireEvent.change(screen.getByLabelText("Degree audit or transcript PDF"), {
      target: { files: [new File(["%PDF"], "a.pdf", { type: "application/pdf" })] },
    });
    await screen.findByText(/Filled in from your PDF/);
    expect(screen.getByRole("button", { name: /CS 1428/ }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByDisplayValue("MATH 2471, HIST 1301")).toBeTruthy();
    expect(screen.getByDisplayValue("CS 3358")).toBeTruthy();
    expect(screen.getByDisplayValue("3.45")).toBeTruthy();         // rounded: the field steps by 0.01
    expect(screen.getByDisplayValue("2024-2025")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Get my recommendations" }));
    await waitFor(() => expect(stream).toHaveBeenCalled());
    const body = stream.mock.calls[0][0];
    expect(body.completed).toEqual(["CS1428", "CS2308", "MATH 2471", "HIST 1301"]);
    expect(body.in_progress).toEqual(["CS 3358"]);
    expect(body.hours_earned).toBe(37);
    expect(body.import_questions).toEqual(IMPORTED.questions);
    expect(api.track).toHaveBeenCalledWith("advisor_submit");
    expect(api.track).toHaveBeenCalledWith("advisor_complete");

    fireEvent.click(screen.getByText("Undo"));
    expect(screen.getByRole("button", { name: /CS 1428/ }).getAttribute("aria-pressed")).toBe("false");
    expect(screen.queryByDisplayValue("MATH 2471, HIST 1301")).toBeNull();
  });
});

describe("registration help", () => {
  const section = (id, crn, seats, times) => ({
    id, course: id.split(".")[0], crn, seats_open: seats, times, modality: "in person",
    meetings: [{ days: "MW", start: 600, end: 680 }],
  });
  const timetable = {
    term: "Fall 2026", courses: ["CS3358", "PHIL1305"], unplaced: [], note: "",
    options: [{
      penalty: 4, days_on_campus: "MW", crns: "10001, 10030",
      sections: [section("CS3358.001", "10001", 5, ["MW 10:00-11:20"]), section("PHIL1305.001", "10030", 10, ["MW 11:30-12:20"])],
      backups: { CS3358: section("CS3358.251", "10003", 18, ["online / no set time"]), PHIL1305: null },
    }],
  };

  it("shows a backup section per course and copies CRNs", async () => {
    const writeText = vi.fn().mockResolvedValue();
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<Timetable timetable={timetable} prefs={[]} />);
    const row = screen.getByText("CS3358.001").closest("li");
    expect(within(row).getByText("CS3358.251")).toBeTruthy();
    expect(within(screen.getByText("PHIL1305.001").closest("li")).getByText("no other section fits this week")).toBeTruthy();
    fireEvent.click(within(row).getByText("CRN 10003 ⧉"));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("10003"));
    expect(await within(row).findByText("Copied")).toBeTruthy();
    fireEvent.click(screen.getByText("Copy all CRNs"));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("10001, 10030"));
    expect(api.track).toHaveBeenCalledWith("crn_copy");
  });
});

describe("AdvisorPrep", () => {
  it("downloads the prep sheet and reports a missing calendar", async () => {
    const blob = new Blob(["%PDF"], { type: "application/pdf" });
    const prep = vi.spyOn(api, "prepSheet").mockResolvedValue(blob);
    vi.spyOn(api, "calendar").mockRejectedValue(new Error("No upcoming academic calendar dates are loaded for Fall 2026 yet."));
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<AdvisorPrep body={{ year: "junior" }} term="Fall 2026" />);
    fireEvent.click(screen.getByText("Download prep sheet (PDF)"));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(prep).toHaveBeenCalledWith({ year: "junior" });
    expect(api.track).toHaveBeenCalledWith("prep_sheet");
    fireEvent.click(screen.getByText("Add Fall 2026 deadlines to my calendar"));
    expect((await screen.findByRole("alert")).textContent).toContain("No upcoming academic calendar dates");
  });
});

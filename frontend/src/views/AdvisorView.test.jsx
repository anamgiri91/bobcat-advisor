import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { Timetable, WeekGrid, WhatIf, describeScenario } from "./AdvisorView";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const option = {
  penalty: 6,
  days_on_campus: "MWF",
  sections: [
    { id: "MATH2358.001", course: "MATH2358", title: "Discrete", modality: "in person", seats_open: 5, crn: "10020",
      meetings: [{ days: "MWF", start: 540, end: 590 }], times: ["MWF 09:00-09:50"] },
    { id: "CS3358.251", course: "CS3358", title: "DS", modality: "online", seats_open: 18, crn: "10003",
      meetings: [], times: ["online / no set time"] },
  ],
};

describe("WeekGrid", () => {
  it("draws one block per meeting day and skips online sections", () => {
    render(<WeekGrid option={option} />);
    expect(screen.getAllByText("MATH2358")).toHaveLength(3);
    expect(screen.queryByText("CS3358")).toBeNull();
  });

  it("explains an all-online timetable", () => {
    render(<WeekGrid option={{ ...option, sections: [option.sections[1]] }} />);
    expect(screen.getByText(/All sections are online/)).toBeTruthy();
  });
});

describe("Timetable", () => {
  it("lists sections, seats and courses it couldn't place", () => {
    render(
      <Timetable
        timetable={{ term: "Fall 2026", courses: ["MATH2358", "CS3358", "CS9999"], options: [option, option],
          unplaced: [{ course: "CS9999", reason: "no sections listed for Fall 2026" }], note: "" }}
        prefs={["prefers MWF"]}
      />
    );
    expect(screen.getByText("Your week · Fall 2026")).toBeTruthy();
    expect(screen.getByText(/Couldn't place CS9999: no sections listed/)).toBeTruthy();
    expect(screen.getByText("5 seats open")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /option/ })).toHaveLength(2);
  });
});

describe("WhatIf", () => {
  it("describes scenarios", () => {
    expect(describeScenario({ type: "switch_major", major: "Math", degree: "BA" })).toBe("Switch to Math (BA)");
    expect(describeScenario({ type: "change_load", target_credits: 9 })).toBe("9 hours per term");
  });

  it("compares scenarios and shows the change in graduation", async () => {
    const spy = vi.spyOn(api, "whatIf").mockResolvedValue({
      baseline: { description: "Your current plan", graduation_term: "Spring 2027", hours_remaining: 12,
        requirements_remaining: 3, notes: [], delta_terms: null },
      scenarios: [{ description: "Fail CS3358 and retake it", graduation_term: "Fall 2027", hours_remaining: 15,
        requirements_remaining: 3, notes: ["CS3358 is failed the first time"], delta_terms: 1, delta_hours: 3 }],
    });
    render(<WhatIf body={{ year: "senior" }} courses={["CS3358"]} />);
    fireEvent.change(screen.getByDisplayValue("Add a minor"), { target: { value: "fail_course" } });
    fireEvent.change(screen.getByDisplayValue("choose…"), { target: { value: "CS3358" } });
    fireEvent.click(screen.getByText("+ Add"));
    fireEvent.click(screen.getByText("Compare"));
    await waitFor(() => expect(screen.getByText("Fall 2027")).toBeTruthy());
    expect(spy).toHaveBeenCalledWith({ year: "senior" }, [{ type: "fail_course", course: "CS3358" }]);
    expect(screen.getByText("+1 term")).toBeTruthy();
  });
});

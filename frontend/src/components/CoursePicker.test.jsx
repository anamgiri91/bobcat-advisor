import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import CoursePicker, { spaced } from "./CoursePicker";

afterEach(cleanup);

const catalog = [
  { code: "CS1428", title: "Foundations of Computer Science I" },
  { code: "CS3358", title: "Data Structures and Algorithms" },
  { code: "CS4347", title: "Introduction to Machine Learning" },
  { code: "CS5315", title: "Responsible and Trustworthy AI" },
];

describe("CoursePicker", () => {
  it("groups by level, shows titles and hides graduate courses", () => {
    render(<CoursePicker catalog={catalog} selected={new Set()} onToggle={() => {}} onClear={() => {}} />);
    expect(screen.getByText("3000-level")).toBeTruthy();
    expect(screen.getByText("Data Structures and Algorithms")).toBeTruthy();
    expect(screen.queryByText("Responsible and Trustworthy AI")).toBeNull();
  });

  it("searches by code with or without a space, or by title", () => {
    render(<CoursePicker catalog={catalog} selected={new Set()} onToggle={() => {}} onClear={() => {}} />);
    const search = screen.getByLabelText("Search courses");
    fireEvent.change(search, { target: { value: "cs 3358" } });
    expect(screen.getAllByRole("button", { pressed: false })).toHaveLength(1);
    fireEvent.change(search, { target: { value: "machine" } });
    expect(screen.getByText("CS 4347")).toBeTruthy();
    fireEvent.change(search, { target: { value: "zzz" } });
    expect(screen.getByText(/No course matches/)).toBeTruthy();
  });

  it("toggles, counts and clears", () => {
    const onToggle = vi.fn();
    const onClear = vi.fn();
    render(<CoursePicker catalog={catalog} selected={new Set(["CS1428"])} onToggle={onToggle} onClear={onClear} />);
    expect(screen.getByText("1 selected")).toBeTruthy();
    expect(screen.getByRole("button", { name: /CS 1428/ }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: /CS 3358/ }));
    expect(onToggle).toHaveBeenCalledWith("CS3358");
    fireEvent.click(screen.getByText("Clear"));
    expect(onClear).toHaveBeenCalled();
  });

  it("formats codes", () => {
    expect(spaced("CS3358")).toBe("CS 3358");
    expect(spaced("MATH2471")).toBe("MATH 2471");
  });
});

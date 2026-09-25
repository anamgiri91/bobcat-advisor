import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import RichText from "./RichText";

afterEach(cleanup);

describe("RichText", () => {
  it("renders paragraphs, bullets, bold and italic", () => {
    const { container } = render(<RichText text={"Intro **bold** and _quiet_.\n- one\n- two"} />);
    expect(container.querySelector("strong").textContent).toBe("bold");
    expect(container.querySelector("em").textContent).toBe("quiet");
    expect(container.querySelectorAll("li")).toHaveLength(2);
  });

  it("turns [n] into citation buttons that report the number", () => {
    const onCite = vi.fn();
    render(<RichText text="CS3358 requires CS2308 [2]." onCite={onCite} />);
    fireEvent.click(screen.getByRole("button", { name: "Source 2" }));
    expect(onCite).toHaveBeenCalledWith(2);
  });

  it("never renders model output as HTML", () => {
    const evil = '<img src=x onerror="alert(1)"><script>alert(2)</script> [link](javascript:alert(3))';
    const { container } = render(<RichText text={evil} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toContain("<script>");
  });

  it("renders headings", () => {
    const { container } = render(<RichText text={"## Where you stand\nText"} />);
    expect(container.querySelector("p.font-display").textContent).toBe("Where you stand");
  });
});

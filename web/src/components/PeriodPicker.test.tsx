import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import PeriodPicker from "./PeriodPicker";

const TODAY = "2026-10-21";
const MONTH = { from: "2026-09-22", to: TODAY };

afterEach(cleanup);

function setup(value = MONTH) {
  const onChange = vi.fn();
  render(<PeriodPicker value={value} onChange={onChange} today={TODAY} />);
  return onChange;
}

describe("PeriodPicker", () => {
  it("gives the last 7 days, today included, for 7 dias", () => {
    const onChange = setup();
    fireEvent.click(screen.getByRole("button", { name: "7 dias" }));
    expect(onChange).toHaveBeenCalledWith({ from: "2026-10-15", to: "2026-10-21" });
  });

  it("gives the same day twice for Hoje", () => {
    const onChange = setup();
    fireEvent.click(screen.getByRole("button", { name: "Hoje" }));
    expect(onChange).toHaveBeenCalledWith({ from: TODAY, to: TODAY });
  });

  it("marks 30 dias as chosen for the default period", () => {
    setup();
    expect(screen.getByRole("button", { name: "30 dias" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("swaps a custom period whose start is after its end", () => {
    const onChange = setup();
    fireEvent.click(screen.getByRole("button", { name: "Personalizado" }));
    fireEvent.change(screen.getByLabelText("Até"), { target: { value: "2026-09-01" } });
    expect(onChange).toHaveBeenLastCalledWith({ from: "2026-09-01", to: "2026-09-22" });
  });
});

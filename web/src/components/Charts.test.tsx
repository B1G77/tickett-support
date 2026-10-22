import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Columns } from "./Charts";

afterEach(cleanup);

describe("Columns", () => {
  it("gives screen readers the chart's numbers as a table", () => {
    render(
      <Columns data={[{ day: "2026-10-20", received: 12, answered: 11 }, { day: "2026-10-21", received: 3, answered: 0 }]}
        x="day" xName="Dia" xFmt={(v) => v} title={(r) => `dia ${String(r.day)}`} fmt={(v) => `${v} emails`}
        series={[{ key: "received", name: "Recebidos", cls: "series-a" }, { key: "answered", name: "Respondidos", cls: "series-b" }]}
        empty="" label="Emails por dia" />,
    );
    const table = screen.getByRole("table", { name: "Emails por dia" });
    const rows = within(table).getAllByRole("row").map((r) => r.textContent);
    expect(rows).toEqual(["DiaRecebidosRespondidos", "dia 2026-10-2012 emails11 emails", "dia 2026-10-213 emails0 emails"]);
  });

  it("shows a dash in the table for a missing value", () => {
    render(
      <Columns data={[{ week: "W1", median_hours: null, n: 4 }]} x="week" xName="Semana" xFmt={(v) => v}
        title={(r) => String(r.week)} fmt={(v) => `${v} h`} extra={(r) => `${String(r.n)} emails`}
        series={[{ key: "median_hours", name: "Mediana", cls: "series-a" }]} empty="" label="Mediana por semana" />,
    );
    const cells = within(screen.getByRole("table")).getAllByRole("cell").map((c) => c.textContent);
    expect(cells).toEqual(["—", "4 emails"]);
  });
});

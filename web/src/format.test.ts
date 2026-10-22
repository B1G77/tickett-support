import { describe, expect, it } from "vitest";
import { day, eur, hours } from "./format";

describe("hours", () => {
  it("shows a dash when there is no value", () => expect(hours(null)).toBe("—"));
  it("shows minutes under an hour", () => expect(hours(0.75)).toBe("45 min"));
  it("shows hours and minutes under a day", () => expect(hours(3.34)).toBe("3 h 20 min"));
  it("shows days and hours from a day on", () => expect(hours(52)).toBe("2 d 4 h"));
  it("drops a zero minute part", () => expect(hours(2)).toBe("2 h"));
  it("drops a zero hour part", () => expect(hours(48)).toBe("2 d"));
});

describe("eur", () => {
  it("formats in pt-PT with the euro sign", () => {
    const s = eur(12.5);
    expect(s).toContain("12,50");
    expect(s).toContain("€");
  });
});

describe("day", () => {
  it("shows dd/mm", () => expect(day("2026-03-07")).toBe("07/03"));
  it("accepts a full timestamp", () => expect(day("2026-12-25T23:10:00")).toBe("25/12"));
});

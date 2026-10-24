import { describe, expect, it } from "vitest";
import { NAV, titleFor } from "./nav";

describe("nav", () => {
  it("has one Por responder tab at / and then the admin pages", () => {
    expect(NAV.map((n) => [n.path, n.label, !!n.admin])).toEqual([
      ["/", "Por responder", false],
      ["/painel", "Painel", true],
      ["/estado", "Estado", true],
      ["/envio-automatico", "Envio automático", true],
    ]);
    expect(titleFor("/")).toBe("Por responder");
  });
});

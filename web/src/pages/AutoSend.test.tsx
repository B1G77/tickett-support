import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../components/Toast";
import AutoSend from "./AutoSend";

const HOURS = { F1: 0, F2: 1, F3: 0, F4: 0, F5: 0 };
const DATA = {
  settings: { enabled: true, daily_limit: 20, categories: ["F2"], delay_hours: HOURS }, sent_today: 0,
  readiness: [
    { folder: "F1", name: "1 Cancelamentos e Reembolsos", drafts: 2, unchanged: 1 },
    { folder: "F2", name: "2 Bilhetes não recebidos", drafts: 4, unchanged: 3 },
  ],
};

function reply(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function serve() {
  const fetchMock = vi.fn(async (_path: string, init?: RequestInit) =>
    reply(200, init?.method === "POST" ? { ...DATA, settings: JSON.parse(String(init.body)) } : DATA));
  vi.stubGlobal("fetch", fetchMock);
  render(<ToastProvider><AutoSend /></ToastProvider>);
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AutoSend", () => {
  it("has a switch and a wait in hours for each category 1 to 5, and 9 never sends", async () => {
    serve();
    const wait = (await screen.findByLabelText("Esperar (horas): 2 Bilhetes não recebidos")) as HTMLInputElement;
    expect(wait.type).toBe("number");
    expect(wait.min).toBe("0");
    expect(wait.max).toBe("720");
    expect(wait.value).toBe("1");
    expect((screen.getByLabelText("Esperar (horas): 1 Cancelamentos e Reembolsos") as HTMLInputElement).value).toBe("0");
    expect(screen.getByText("Só reembolsos tratados pela Tickett")).toBeTruthy();
    expect(screen.getByText("9 A Rever")).toBeTruthy();
    expect(screen.getByText("Nunca automático")).toBeTruthy();
    expect(screen.queryByText(/Parceiros|Loja/)).toBeNull();
    expect(screen.queryByRole("slider")).toBeNull();
  });

  it("saves a longer wait with one click (it switches nothing on)", async () => {
    const fetchMock = serve();
    fireEvent.change(await screen.findByLabelText("Esperar (horas): 2 Bilhetes não recebidos"), { target: { value: "48" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [path, init] = fetchMock.mock.calls[1];
    expect(path).toBe("/api/admin/autosend");
    expect(JSON.parse(String(init?.body))).toEqual({ enabled: true, daily_limit: 20, categories: ["F2"],
      delay_hours: { ...HOURS, F2: 48 } });
  });

  it("refuses a wait outside 0 to 720 hours without asking the server", async () => {
    const fetchMock = serve();
    fireEvent.change(await screen.findByLabelText("Esperar (horas): 2 Bilhetes não recebidos"), { target: { value: "721" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByText("Atraso inválido (0 a 720 horas).")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("asks for a second click before switching refunds on", async () => {
    const fetchMock = serve();
    fireEvent.click(await screen.findByLabelText("Automático: 1 Cancelamentos e Reembolsos"));
    fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Confirmar e guardar" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body)).categories).toEqual(["F1", "F2"]);
  });

  it("refuses an empty wait instead of saving it as 0", async () => {
    const fetchMock = serve();
    fireEvent.change(await screen.findByLabelText("Esperar (horas): 2 Bilhetes não recebidos"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByText("Atraso inválido (0 a 720 horas).")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

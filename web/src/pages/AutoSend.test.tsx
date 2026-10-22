import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../components/Toast";
import AutoSend from "./AutoSend";

const DATA = {
  settings: { enabled: true, daily_limit: 20, categories: ["F2"], delay_minutes: 60 }, sent_today: 0,
  readiness: [{ folder: "F2", name: "2 Bilhetes não recebidos", drafts: 4, unchanged: 3 }],
};

function reply(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AutoSend delay", () => {
  it("shows the saved wait and labels every step of the slider", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply(200, DATA)));
    render(<ToastProvider><AutoSend /></ToastProvider>);
    const slider = (await screen.findByLabelText(/Esperar antes de responder/)) as HTMLInputElement;
    expect(slider.type).toBe("range");
    expect(slider.min).toBe("0");
    expect(slider.max).toBe("10");
    expect(slider.value).toBe("3");
    expect(screen.getByText("Esperar antes de responder: 1 h")).toBeTruthy();
    const labels = ["0 min — enviar logo", "15 min", "30 min", "1 h", "2 h", "3 h", "4 h", "6 h", "8 h", "12 h", "24 h"];
    labels.forEach((label, i) => {
      fireEvent.change(slider, { target: { value: String(i) } });
      expect(screen.getByText(`Esperar antes de responder: ${label}`)).toBeTruthy();
    });
  });

  it("saves a longer wait with one click (it switches nothing on)", async () => {
    const fetchMock = vi.fn(async (_path: string, init?: RequestInit) =>
      reply(200, init?.method === "POST" ? { ...DATA, settings: JSON.parse(String(init.body)) } : DATA));
    vi.stubGlobal("fetch", fetchMock);
    render(<ToastProvider><AutoSend /></ToastProvider>);
    fireEvent.change(await screen.findByLabelText(/Esperar antes de responder/), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [path, init] = fetchMock.mock.calls[1];
    expect(path).toBe("/api/admin/autosend");
    expect(JSON.parse(String(init?.body))).toEqual({ enabled: true, daily_limit: 20, categories: ["F2"], delay_minutes: 120 });
  });
});

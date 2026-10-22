import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../components/Toast";
import type { AppState, Draft } from "../types";
import Drafts from "./Drafts";

const DRAFT: Draft = {
  id: "abc123", version: "2026-10-20T09:00:00", folder: "2 Bilhetes não recebidos",
  subject: "Bilhetes em falta", from_name: "Cliente Teste", from_email: "cliente@example.test",
  received: "20/10 09:00", text: "Não recebi os bilhetes.", full: "Não recebi os bilhetes.",
  reply: "Olá, já reenviámos os bilhetes.", note: "", blocked: "", to: "cliente@example.test",
  to_differs: false, rewrites: 0, edited: false, auto_at: "",
};

const STATE: AppState = {
  generated_at: "2026-10-20T09:01:00+00:00", mailbox: "apoio@example.test", drafts: [DRAFT], people: [],
  health: { overall: "ok" }, numbers: {},
};

function reply(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Drafts", () => {
  it("keeps the typed text in the edit box when the draft changed meanwhile (409)", async () => {
    const changed = { ...STATE, drafts: [{ ...DRAFT, version: "2026-10-20T09:05:00", reply: "Outra versão." }] };
    const fetchMock = vi.fn(async (path: string) =>
      path === "/api/edit"
        ? reply(409, { error: "Este rascunho mudou ou já foi tratado. A página foi atualizada." })
        : reply(200, fetchMock.mock.calls.length > 1 ? changed : STATE));
    vi.stubGlobal("fetch", fetchMock);
    render(<ToastProvider><Drafts /></ToastProvider>);

    fireEvent.click(await screen.findByRole("button", { name: "Editar" }));
    fireEvent.change(screen.getByLabelText("Texto da resposta"), { target: { value: "O meu texto novo." } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar" }));

    expect(await screen.findByText("Este rascunho mudou ou já foi tratado. A página foi atualizada.")).toBeTruthy();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3)); // state, edit, state again
    expect((screen.getByLabelText("Texto da resposta") as HTMLTextAreaElement).value).toBe("O meu texto novo.");
  });

  it("shows the category chips only when the drafts span two or more categories", async () => {
    const other = { ...DRAFT, id: "def456", folder: "3 Informações sobre eventos", subject: "Horário" };
    vi.stubGlobal("fetch", vi.fn(async () => reply(200, { ...STATE, drafts: [DRAFT, { ...DRAFT, id: "x2" }] })));
    render(<ToastProvider><Drafts /></ToastProvider>);
    await screen.findAllByText("Bilhetes em falta");
    expect(screen.queryByRole("group", { name: "Categoria" })).toBeNull();
    cleanup();
    vi.stubGlobal("fetch", vi.fn(async () => reply(200, { ...STATE, drafts: [DRAFT, other] })));
    render(<ToastProvider><Drafts /></ToastProvider>);
    const chips = await screen.findByRole("group", { name: "Categoria" });
    expect(chips.querySelectorAll("button")).toHaveLength(3); // Todas and the two categories
  });

  it("falls back to all drafts when the chosen category disappears", async () => {
    const other = { ...DRAFT, id: "def456", folder: "3 Informações sobre eventos", subject: "Horário" };
    const third = { ...DRAFT, id: "ghi789", folder: "4 Faturas", subject: "Fatura" };
    let state = { ...STATE, drafts: [DRAFT, other, third] };
    vi.stubGlobal("fetch", vi.fn(async (path: string) => {
      if (path !== "/api/person") return reply(200, state);
      state = { ...STATE, drafts: [DRAFT, third] }; // the only draft of the chosen category is gone
      return reply(200, { message: "Passou para 0 Por Responder." });
    }));
    render(<ToastProvider><Drafts /></ToastProvider>);
    fireEvent.click(await screen.findByRole("button", { name: /3 Informações sobre eventos/ }));
    expect(screen.queryByText("Bilhetes em falta")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Respondo eu" }));
    expect(await screen.findByText("Bilhetes em falta")).toBeTruthy();
    expect(screen.getByText("Fatura")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Todas/ }).getAttribute("aria-pressed")).toBe("true");
  });
});

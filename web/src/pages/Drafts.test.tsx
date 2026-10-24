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
  to_differs: false, rewrites: 0, edited: false, auto_at: "", flag: "", bcc: "", sort: "2026-10-20T09:00:00",
  to_override: "", refusal: "",
};

const STATE: AppState = {
  generated_at: "2026-10-20T09:01:00+00:00", mailbox: "apoio@example.test", drafts: [DRAFT],
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

  it("always shows the category chips, even for a single category", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply(200, { ...STATE, drafts: [DRAFT, { ...DRAFT, id: "x2" }] })));
    render(<ToastProvider><Drafts /></ToastProvider>);
    const chips = await screen.findByRole("group", { name: "Categoria" });
    expect([...chips.querySelectorAll("button")].map((b) => b.textContent)).toEqual(
      ["Todas 2", "2 Bilhetes não recebidos 2"]);
  });

  it("groups the cards by category in folder order, oldest first, and a chip shows one section", async () => {
    const d = (id: string, folder: string, subject: string, sort: string) => ({ ...DRAFT, id, folder, subject, sort });
    vi.stubGlobal("fetch", vi.fn(async () => reply(200, { ...STATE, drafts: [
      d("a", "9 A Rever", "Rever", "2026-10-01"),
      d("b", "2 Bilhetes não recebidos", "Bilhete novo", "2026-10-20"),
      d("c", "1 Cancelamentos e Reembolsos", "Reembolso", "2026-10-15"),
      d("e", "2 Bilhetes não recebidos", "Bilhete antigo", "2026-10-02"),
    ] })));
    render(<ToastProvider><Drafts /></ToastProvider>);
    await screen.findByText("Rever");
    const sections = screen.getAllByRole("region");
    expect(sections.map((r) => r.getAttribute("aria-label"))).toEqual(
      ["1 Cancelamentos e Reembolsos", "2 Bilhetes não recebidos", "9 A Rever"]);
    expect(sections[1].querySelector(".section-count")?.textContent).toBe("2");
    expect([...sections[1].querySelectorAll(".card-subject")].map((h) => h.textContent))
      .toEqual(["Bilhete antigo", "Bilhete novo"]);
    fireEvent.click(screen.getByRole("button", { name: /^9 A Rever/ }));
    expect(screen.getAllByRole("region").map((r) => r.getAttribute("aria-label"))).toEqual(["9 A Rever"]);
    expect(screen.queryByText("Reembolso")).toBeNull();
  });

  it("falls back to all drafts when the chosen category disappears", async () => {
    const other = { ...DRAFT, id: "def456", folder: "3 Compra e Pagamento", subject: "Horário" };
    const third = { ...DRAFT, id: "ghi789", folder: "4 Alterações de Reserva", subject: "Fatura" };
    let state = { ...STATE, drafts: [DRAFT, other, third] };
    const fetchMock = vi.fn(async (path: string, _init?: RequestInit) => {
      if (path !== "/api/answered") return reply(200, state);
      state = { ...STATE, drafts: [DRAFT, third] }; // the only draft of the chosen category is gone
      return reply(200, { message: "Tratado sem resposta: passou para 10 Respondidos." });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ToastProvider><Drafts /></ToastProvider>);
    fireEvent.click(await screen.findByRole("button", { name: /3 Compra e Pagamento/ }));
    expect(screen.queryByText("Bilhetes em falta")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Tratado sem resposta" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirmar: tratado sem resposta" }));
    expect(await screen.findByText("Bilhetes em falta")).toBeTruthy();
    expect(screen.getByText("Fatura")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Todas/ }).getAttribute("aria-pressed")).toBe("true");
    const call = fetchMock.mock.calls.find(([p]) => p === "/api/answered") as unknown as [string, RequestInit];
    expect(JSON.parse(String(call[1].body))).toEqual({ id: "def456", version: DRAFT.version });
  });

  it("saves the address in Para: of a relayed email as a new version", async () => {
    const relayed = { ...DRAFT, flag: "relayed", to: "", refusal: "Escreva o endereço do cliente em Para: antes de enviar." };
    const fetchMock = vi.fn(async (path: string, _init?: RequestInit) =>
      path === "/api/to" ? reply(200, { message: "Endereço guardado.", version: "v2" })
        : reply(200, { ...STATE, drafts: [relayed] }));
    vi.stubGlobal("fetch", fetchMock);
    render(<ToastProvider><Drafts /></ToastProvider>);
    fireEvent.change(await screen.findByLabelText("Para"), { target: { value: "rui@example.test" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar endereço" }));
    expect(await screen.findByText("Endereço guardado.")).toBeTruthy();
    const call = fetchMock.mock.calls.find(([p]) => p === "/api/to") as unknown as [string, RequestInit];
    expect(JSON.parse(String(call[1].body))).toEqual({ id: DRAFT.id, version: DRAFT.version, to: "rui@example.test" });
  });
});

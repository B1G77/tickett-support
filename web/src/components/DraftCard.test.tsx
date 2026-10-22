import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Draft } from "../types";
import DraftCard, { type DraftCardProps } from "./DraftCard";

const BASE: Draft = {
  id: "abc123", version: "2026-10-20T09:00:00", folder: "2 Bilhetes não recebidos",
  subject: "Bilhetes em falta", from_name: "Cliente Teste", from_email: "cliente@example.test",
  received: "20/10 09:00", text: "Não recebi os bilhetes.", full: "Não recebi os bilhetes.",
  reply: "Olá, já reenviámos os bilhetes.", note: "", blocked: "", to: "cliente@example.test",
  to_differs: false, rewrites: 0, edited: false, auto_at: "",
};

function setup(over: Partial<Draft> = {}, props: Partial<DraftCardProps> = {}) {
  const handlers = {
    onSend: vi.fn(), onRewrite: vi.fn(), onEdit: vi.fn(), onPerson: vi.fn(), onForm: vi.fn(),
  };
  render(<DraftCard draft={{ ...BASE, ...over }} busy="" form={null} {...handlers} {...props} />);
  return handlers;
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("DraftCard", () => {
  it("shows when a scheduled draft goes out automatically", () => {
    setup({ auto_at: "14:30" });
    expect(screen.getByText("Envio automático às 14:30")).toBeTruthy();
    cleanup();
    setup();
    expect(screen.queryByText(/Envio automático/)).toBeNull();
  });

  it("renders email-derived strings as text, never as HTML", () => {
    const evil = "<img src=x onerror=alert(1)>";
    const { container } = render(
      <DraftCard draft={{ ...BASE, subject: evil, text: evil, reply: evil, from_name: evil }} busy="" form={null}
        onSend={vi.fn()} onRewrite={vi.fn()} onEdit={vi.fn()} onPerson={vi.fn()} onForm={vi.fn()} />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByRole("heading", { name: evil })).toBeTruthy();
  });

  it("needs a second click on Enviar before it calls onSend", () => {
    const { onSend } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirmar envio" }));
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("disarms Enviar after 5 seconds", () => {
    vi.useFakeTimers();
    const { onSend } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));
    act(() => { vi.advanceTimersByTime(5000); });
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));
    expect(onSend).not.toHaveBeenCalled();
  });

  it("disarms Enviar when a new version of the draft arrives, so the confirming click cannot send unseen text", () => {
    const onSend = vi.fn();
    const props = { busy: "", form: null, onForm: vi.fn(), onSend, onRewrite: vi.fn(), onEdit: vi.fn(), onPerson: vi.fn() };
    const { rerender } = render(<DraftCard draft={BASE} {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));
    rerender(<DraftCard draft={{ ...BASE, version: "2026-10-20T09:05:00", reply: "Outra resposta." }} {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Confirmar envio" })).toBeTruthy(); // re-armed instead
  });

  it("tells the page when Enviar is armed, so the poll can wait", () => {
    const onArmed = vi.fn();
    setup({}, { onArmed });
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));
    expect(onArmed).toHaveBeenLastCalledWith(true);
  });

  it("warns when the reply goes to a different address than the sender", () => {
    setup({ to: "outro@example.test", to_differs: true });
    expect(screen.getByText("O endereço de resposta é diferente do remetente")).toBeTruthy();
    expect(screen.getByText("outro@example.test")).toBeTruthy();
  });

  it("does not warn when the address is the sender's", () => {
    setup();
    expect(screen.queryByText("O endereço de resposta é diferente do remetente")).toBeNull();
  });

  it("disables Enviar and shows why when the draft is blocked", () => {
    setup({ blocked: "o texto ainda tem um marcador como [NAME]" });
    expect((screen.getByRole("button", { name: "Enviar" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/o texto ainda tem um marcador como \[NAME\]/)).toBeTruthy();
  });

  it("shows the internal note and the edited / rewritten badges", () => {
    setup({ note: "Confirmar o evento com o promotor.", edited: true, rewrites: 2 });
    expect(screen.getByText("Confirmar o evento com o promotor.")).toBeTruthy();
    expect(screen.getByText("Editado")).toBeTruthy();
    expect(screen.getByText("Reescrito ×2")).toBeTruthy();
  });

  it("disables every button and shows the progress text while busy", () => {
    setup({}, { busy: "A reescrever…" });
    expect(screen.getByText("A reescrever…")).toBeTruthy();
    for (const b of screen.getAllByRole("button")) expect((b as HTMLButtonElement).disabled).toBe(true);
  });

  it("sends the comment to onRewrite from the Pedir alteração box", () => {
    const { onRewrite } = setup({}, { form: { mode: "ask", text: "Mais curto" } });
    fireEvent.click(screen.getByRole("button", { name: "Reescrever" }));
    expect(onRewrite).toHaveBeenCalledWith("Mais curto");
  });

  it("saves the edited text with onEdit", () => {
    const { onEdit } = setup({}, { form: { mode: "edit", text: "Texto novo" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
    expect(onEdit).toHaveBeenCalledWith("Texto novo");
  });
});

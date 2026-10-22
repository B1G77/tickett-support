import { useCallback, useEffect, useState, type RefObject } from "react";
import { ApiError, request } from "../api";
import type { AppState } from "../types";

const POLL_MS = 30_000;

/** /api/state, read now and every 30 s. While `hold.current` is true (someone is typing in a card, or an
 * action is running) the poll skips its turn, so nothing is redrawn under them; `reload` always reads. */
export function useAppState(hold: RefObject<boolean>) {
  const [data, setData] = useState<AppState | null>(null);
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    try {
      setData(await request<AppState>("/api/state"));
      setError("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Falhou.");
    }
  }, []);

  useEffect(() => {
    void reload();
    const t = setInterval(() => { if (!hold.current) void reload(); }, POLL_MS);
    return () => clearInterval(t);
  }, [reload, hold]);

  return { data, error, reload };
}

/** "Atualizado às 14:05" from the server's generated_at. */
export function updatedAt(iso: string): string {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return "";
  return "Atualizado às " + t.toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit" });
}

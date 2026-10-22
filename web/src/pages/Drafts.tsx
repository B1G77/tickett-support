// Rascunhos: reply drafts waiting for approval, oldest first, filtered by category when there are two or more.
import { useRef, useState } from "react";
import { ApiError, request } from "../api";
import DraftCard, { type DraftForm } from "../components/DraftCard";
import { useToast } from "../components/Toast";
import type { Draft } from "../types";
import { PageStatus, Toolbar } from "./PageStatus";
import { useAppState } from "./useAppState";
import "./agent.css";

export default function Drafts() {
  const toast = useToast();
  const [forms, setForms] = useState<Record<string, DraftForm>>({});
  const [busy, setBusy] = useState<Record<string, string>>({});
  const [armed, setArmed] = useState<Record<string, boolean>>({});
  const [folder, setFolder] = useState("");
  const hold = useRef(false);
  const { data, error, reload } = useAppState(hold);

  const drafts = data?.drafts ?? [];
  const live = new Set(drafts.map((d) => d.id));
  // the poll waits while a card here has an open box, an armed Enviar or a running action (drafts gone don't count)
  hold.current = Object.entries(forms).some(([id, f]) => f && live.has(id))
    || Object.entries(armed).some(([id, a]) => a && live.has(id))
    || Object.keys(busy).some((id) => live.has(id));

  const setForm = (id: string, f: DraftForm) => setForms((all) => ({ ...all, [id]: f }));

  async function act(d: Draft, path: string, body: object, progress: string) {
    setBusy((b) => ({ ...b, [d.id]: progress }));
    try {
      const r = await request<{ message: string }>(`/api/${path}`,
        { method: "POST", body: { id: d.id, version: d.version, ...body } });
      toast(r.message);
      setForm(d.id, null);
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(0, "Falhou.");
      toast(err.message, true);
      // the draft changed: the reload shows what is there now; an edit keeps what the person typed
      if (err.status === 409 && path !== "edit") setForm(d.id, null);
    }
    await reload();
    setBusy(({ [d.id]: _, ...rest }) => rest);
  }

  const counts = new Map<string, number>();
  for (const d of drafts) counts.set(d.folder, (counts.get(d.folder) ?? 0) + 1);
  const folders = [...counts.keys()].sort((a, b) => a.localeCompare(b, "pt-PT", { numeric: true }));
  const current = folders.length >= 2 && counts.has(folder) ? folder : "";
  const shown = current ? drafts.filter((d) => d.folder === current) : drafts;

  return (
    <div className="page">
      <Toolbar data={data} error={error}>
        {folders.length >= 2 && <div className="chips" role="group" aria-label="Categoria">
          <button type="button" className="chip-btn" aria-pressed={current === ""} onClick={() => setFolder("")}>
            Todas <span className="chip-count">{drafts.length}</span>
          </button>
          {folders.map((f) => (
            <button key={f} type="button" className="chip-btn" aria-pressed={current === f} onClick={() => setFolder(f)}>
              {f} <span className="chip-count">{counts.get(f)}</span>
            </button>
          ))}
        </div>}
      </Toolbar>

      <PageStatus data={data} error={error} reload={reload}>
        {data && (
          <p className="page-hint">
            <b>Enviar</b> manda a resposta ao cliente a partir de {data.mailbox}. <b>Pedir alteração</b> reescreve-a
            com as suas indicações. <b>Respondo eu</b> passa o email para Por Responder.
          </p>
        )}
        {data && shown.length === 0 && (
          <div className="empty">
            <p className="empty-title">Não há rascunhos por aprovar.</p>
            <p className="muted">Os novos aparecem aqui assim que o correio chega.</p>
          </div>
        )}
        <div className="card-list">
          {shown.map((d) => (
            <DraftCard key={d.id} draft={d} busy={busy[d.id] ?? ""} form={forms[d.id] ?? null}
              onForm={(f) => setForm(d.id, f)}
              onArmed={(a) => setArmed((all) => ({ ...all, [d.id]: a }))}
              onSend={() => void act(d, "send", {}, "A enviar…")}
              onRewrite={(comment) => void act(d, "rewrite", { comment }, "A reescrever… pode demorar até um minuto.")}
              onEdit={(text) => void act(d, "edit", { body: text }, "A guardar…")}
              onPerson={() => void act(d, "person", {}, "A passar para Por Responder…")} />
          ))}
        </div>
      </PageStatus>
    </div>
  );
}

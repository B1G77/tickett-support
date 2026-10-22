// Por Responder: mail the system must not answer on its own, until someone marks it answered.
import { useRef, useState } from "react";
import { ApiError, request } from "../api";
import { useToast } from "../components/Toast";
import { useConfirm } from "../components/useConfirm";
import { sender, type Person } from "../types";
import { PageStatus, Toolbar } from "./PageStatus";
import { useAppState } from "./useAppState";
import "./agent.css";

function PersonCard({ p, busy, onAnswered }: { p: Person; busy: boolean; onAnswered: () => void }) {
  const done = useConfirm(onAnswered);
  return (
    <article className={"card" + (busy ? " is-busy" : "")} aria-busy={busy} aria-labelledby={`subj-${p.id}`}>
      <header className="card-head">
        <div className="card-meta">
          <span className="badge badge-reason">{p.reason || "Precisa de uma pessoa"}</span>
          <span className="card-from">{sender(p.from_name, p.from_email)}</span>
          <span className="card-time">{p.received}</span>
        </div>
        <h2 className="card-subject" id={`subj-${p.id}`}>{p.subject}</h2>
      </header>
      {p.since && <p className="waiting">À espera desde {p.since}</p>}
      {p.note && (
        <div className="note-box">
          <span className="note-title">Nota</span>
          <p>{p.note}</p>
        </div>
      )}
      <h3 className="col-label">O cliente escreveu</h3>
      <div className="mail-text mail-scroll">{p.text || "(sem texto)"}</div>
      <footer className="card-actions">
        <button type="button" className={"btn btn-primary" + (done.armed ? " is-armed" : "")} disabled={busy}
          onClick={done.click}>{done.armed ? "Confirmar: respondido" : "Respondido"}</button>
        <span className="spacer" />
        {busy && <span className="card-progress"><span className="spinner" aria-hidden="true" />A marcar…</span>}
        {!busy && done.armed && <span className="card-progress">Clique outra vez para confirmar.</span>}
      </footer>
    </article>
  );
}

export default function People() {
  const toast = useToast();
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const hold = useRef(false);
  hold.current = busy.size > 0;
  const { data, error, reload } = useAppState(hold);

  async function answered(p: Person) {
    setBusy((b) => new Set(b).add(p.id));
    try {
      toast((await request<{ message: string }>("/api/answered", { method: "POST", body: { id: p.id } })).message);
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "Falhou.", true);
    }
    await reload();
    setBusy((b) => { const n = new Set(b); n.delete(p.id); return n; });
  }

  const people = data?.people ?? [];
  return (
    <div className="page">
      <Toolbar data={data} error={error}>
        {data && <span className="toolbar-count">{people.length} {people.length === 1 ? "email" : "emails"}</span>}
      </Toolbar>
      <PageStatus data={data} error={error} reload={reload}>
        <p className="page-hint">
          Emails que o sistema não pode responder sozinho. Responda no Mail (pasta 0 Por Responder) e
          carregue em <b>Respondido</b>; quando a resposta sai, o email também deixa esta lista.
        </p>
        {people.length === 0 && (
          <div className="empty">
            <p className="empty-title">Nada à espera de uma pessoa.</p>
          </div>
        )}
        <div className="card-list">
          {people.map((p) => (
            <PersonCard key={p.id} p={p} busy={busy.has(p.id)} onAnswered={() => void answered(p)} />
          ))}
        </div>
      </PageStatus>
    </div>
  );
}

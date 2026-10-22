// One reply draft: the customer's email beside the proposed reply, and the four actions.
// Every string from an email is rendered as React text, never as HTML.
import { useState } from "react";
import { sender, type Draft } from "../types";
import { useConfirm } from "./useConfirm";
import "../pages/agent.css";

export const MAX_COMMENT = 2000;

/** The open box under a card: a rewrite request or the edited reply, with what has been typed so far. */
export type DraftForm = { mode: "ask" | "edit"; text: string } | null;

export type DraftCardProps = {
  draft: Draft;
  busy: string; // progress text while an action runs, else ""
  form: DraftForm;
  onForm: (form: DraftForm) => void;
  onSend: () => void;
  onRewrite: (comment: string) => void;
  onEdit: (body: string) => void;
  onPerson: () => void;
  onArmed?: (armed: boolean) => void; // Enviar waits for its confirming click: the page holds its poll
};

export default function DraftCard(
  { draft: d, busy, form, onForm, onSend, onRewrite, onEdit, onPerson, onArmed }: DraftCardProps,
) {
  const [full, setFull] = useState(false);
  const send = useConfirm(onSend, d.version, onArmed);
  const working = busy !== "";
  const editing = form?.mode === "edit";
  const asking = form?.mode === "ask";
  const hasMore = d.full !== "" && d.full !== d.text;

  return (
    <article className={"card draft-card" + (working ? " is-busy" : "")} aria-busy={working}
      aria-labelledby={`subj-${d.id}`}>
      <header className="card-head">
        <div className="card-meta">
          <span className="chip">{d.folder}</span>
          <span className="card-from">{sender(d.from_name, d.from_email)}</span>
          <span className="card-time">{d.received}</span>
          {d.edited && <span className="badge">Editado</span>}
          {d.rewrites > 0 && <span className="badge">Reescrito ×{d.rewrites}</span>}
          {d.auto_at && <span className="badge">Envio automático às {d.auto_at}</span>}
        </div>
        <h2 className="card-subject" id={`subj-${d.id}`}>{d.subject}</h2>
      </header>

      <div className="draft-cols">
        <section className="draft-col">
          <h3 className="col-label">{full ? "Conversa completa" : "O cliente escreveu"}</h3>
          <div className="mail-text">{(full ? d.full : d.text || d.full) || "(sem texto)"}</div>
          {hasMore && (
            <button type="button" className="link-btn" aria-expanded={full} onClick={() => setFull(!full)}>
              {full ? "Ver só a última mensagem" : "Ver conversa"}
            </button>
          )}
        </section>

        <section className="draft-col draft-reply">
          <h3 className="col-label">{editing ? "Editar a resposta" : "Resposta proposta"}</h3>
          <p className="to-line">Para: <span className="to-addr">{d.to || "—"}</span></p>
          {d.to_differs && (
            <p className="warn-line">O endereço de resposta é diferente do remetente</p>
          )}
          {editing ? (
            <div className="form-box">
              <label className="sr-only" htmlFor={`edit-${d.id}`}>Texto da resposta</label>
              <textarea id={`edit-${d.id}`} className="textarea textarea-tall" value={form.text} disabled={working}
                autoFocus onChange={(e) => onForm({ mode: "edit", text: e.target.value })} />
              <div className="form-row">
                <button type="button" className="btn btn-primary" disabled={working || !form.text.trim()
                  || form.text.trim() === d.reply.trim()} onClick={() => onEdit(form.text)}>Guardar</button>
                <button type="button" className="btn btn-ghost" disabled={working} onClick={() => onForm(null)}>
                  Cancelar
                </button>
              </div>
            </div>
          ) : (
            <div className="mail-text mail-reply">{d.reply || "(vazio)"}</div>
          )}
          {d.note && (
            <div className="note-box">
              <span className="note-title">Nota interna</span>
              <span className="note-sub">não vai para o cliente</span>
              <p>{d.note}</p>
            </div>
          )}
          {d.blocked && (
            <p className="blocked-box">Não pode ser enviado: {d.blocked}. Peça uma alteração ou edite o texto.</p>
          )}
        </section>
      </div>

      {asking && (
        <div className="form-box ask-box">
          <label className="field-label" htmlFor={`ask-${d.id}`}>O que quer mudar?</label>
          <textarea id={`ask-${d.id}`} className="textarea" value={form.text} maxLength={MAX_COMMENT}
            disabled={working} autoFocus
            placeholder="Ex.: mais curto; dizer que o reembolso é feito pelo promotor; pedir o número do pedido"
            onChange={(e) => onForm({ mode: "ask", text: e.target.value })} />
          <div className="form-hint">
            <span>Não escreva nomes, emails ou números.</span>
            <span className="counter">{form.text.length}/{MAX_COMMENT}</span>
          </div>
          <div className="form-row">
            <button type="button" className="btn btn-primary" disabled={working || !form.text.trim()}
              onClick={() => onRewrite(form.text)}>Reescrever</button>
            <button type="button" className="btn btn-ghost" disabled={working} onClick={() => onForm(null)}>
              Cancelar
            </button>
          </div>
        </div>
      )}

      <footer className="card-actions">
        <button type="button" className={"btn btn-primary btn-send" + (send.armed ? " is-armed" : "")}
          disabled={working || !!d.blocked || editing} onClick={send.click}>
          {send.armed ? "Confirmar envio" : "Enviar"}
        </button>
        <button type="button" className="btn" aria-pressed={asking} disabled={working}
          onClick={() => onForm(asking ? null : { mode: "ask", text: "" })}>Pedir alteração</button>
        <button type="button" className="btn" aria-pressed={editing} disabled={working}
          onClick={() => onForm(editing ? null : { mode: "edit", text: d.reply })}>Editar</button>
        <span className="spacer" />
        {working && <span className="card-progress"><span className="spinner" aria-hidden="true" />{busy}</span>}
        {!working && send.armed && <span className="card-progress">Clique outra vez para enviar ao cliente.</span>}
        {!working && editing && <span className="card-progress">Guarde a edição antes de enviar.</span>}
        <button type="button" className="btn btn-ghost" disabled={working} onClick={onPerson}>Respondo eu</button>
      </footer>
    </article>
  );
}

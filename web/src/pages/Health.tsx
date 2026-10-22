// Estado: each part of the stack with its status, and what changed lately in the events Doc.
import { useCallback, useEffect, useState } from "react";
import { ApiError, request } from "../api";
import "./agent.css";
import "./admin.css";

type Status = "good" | "warning" | "critical";
type HealthData = {
  checks: { status: Status; label: string; detail: string }[];
  doc: { fetched_at: string; changes: { at: string; kind: string; name: string }[] };
};

const STATUS: Record<Status, string> = { good: "OK", warning: "Atenção", critical: "Problema" };
const KINDS = new Set(["novo", "removido", "alterado"]);

/** "HH:MM" and "dd/mm/aaaa" in local time; null when it is not a date. */
function parts(iso: string): { time: string; date: string } | null {
  const t = new Date(iso);
  if (!iso || Number.isNaN(t.getTime())) return null;
  return { time: t.toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit" }), date: t.toLocaleDateString("pt-PT") };
}

/** "dd/mm/aaaa, HH:MM", or the text as sent. */
function when(iso: string): string {
  const p = parts(iso);
  return p ? `${p.date}, ${p.time}` : iso;
}

function fetched(iso: string): string {
  const p = parts(iso);
  return p ? `Lido às ${p.time} de ${p.date}` : iso ? `Lido: ${iso}` : "Ainda não lido.";
}

export default function Health() {
  const [data, setData] = useState<HealthData | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setData(await request<HealthData>("/api/admin/health"));
      setError("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Falhou.");
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  if (!data) {
    return error ? (
      <div className="page"><div className="empty" role="alert">
        <p className="empty-title">Não foi possível carregar.</p>
        <p className="muted">{error}</p>
        <button type="button" className="btn" onClick={() => void load()}>Tentar outra vez</button>
      </div></div>
    ) : <div className="page"><div className="panel skeleton health-skeleton" aria-busy="true" aria-label="A carregar" /></div>;
  }

  const worst: Status = data.checks.some((c) => c.status === "critical") ? "critical"
    : data.checks.some((c) => c.status === "warning") ? "warning" : "good";
  return (
    <div className="page admin">
      <section className="panel">
        <header className="panel-head">
          <h2 className="panel-title panel-title-tight">Saúde do sistema</h2>
          <span className={"status-pill status-" + worst}>
            {worst === "good" ? "Tudo bem" : worst === "warning" ? "Precisa de atenção" : "Há um problema"}
          </span>
          <button type="button" className="btn btn-ghost panel-action" onClick={() => void load()}>Atualizar</button>
        </header>
        {error && <p className="form-error" role="alert">Sem atualização: {error}</p>}
        <ul className="checks">
          {data.checks.map((c) => (
            <li key={c.label} className={"check status-" + c.status}>
              <span className="check-dot" aria-hidden="true" />
              <span className="check-label">{c.label}</span>
              <span className="check-detail">{c.detail}</span>
              <span className="check-status">{STATUS[c.status] ?? c.status}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="panel">
        <header className="panel-head">
          <h2 className="panel-title">Documento de eventos</h2>
          <p className="muted">{fetched(data.doc.fetched_at)}</p>
        </header>
        {data.doc.changes.length === 0 ? <p className="chart-empty">Sem alterações registadas.</p> : (
          <ul className="changes">
            {data.doc.changes.map((c, i) => (
              <li key={`${c.at}-${c.name}-${i}`} className="change">
                <time className="change-at" dateTime={c.at}>{when(c.at)}</time>
                <span className={"kind" + (KINDS.has(c.kind) ? " kind-" + c.kind : "")}>{c.kind}</span>
                <span className="change-name">{c.name}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

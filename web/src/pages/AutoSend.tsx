// Envio automático: the master switch, the daily limit and which categories may send without a person.
// Saving anything that switches sending on takes a second click; switching off or a longer wait saves at once.
import { useCallback, useEffect, useState } from "react";
import { ApiError, request } from "../api";
import { useToast } from "../components/Toast";
import { useConfirm } from "../components/useConfirm";
import "./agent.css";
import "./admin.css";

type Settings = { enabled: boolean; daily_limit: number; categories: string[]; delay_minutes: number };
type AutoData = { settings: Settings; sent_today: number;
  readiness: { folder: string; name: string; drafts: number; unchanged: number }[] };

const MAX_LIMIT = 200;
// the waits the server allows (src/autosend.py DELAYS), in minutes from when the router first saw the email
const DELAYS = [0, 15, 30, 60, 120, 180, 240, 360, 480, 720, 1440];

function delayLabel(m: number): string {
  return m === 0 ? "0 min — enviar logo" : m < 60 ? `${m} min` : `${m / 60} h`;
}
// never sent automatically (src/autosend.py NEVER); names as in config/router.json
const NEVER = [
  { folder: "F1", name: "1 Cancelamentos e Reembolsos" },
  { folder: "F7", name: "7 Parceiros e Promotores" },
  { folder: "F9", name: "9 A Rever" },
];

export default function AutoSend() {
  const [data, setData] = useState<AutoData | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setData(await request<AutoData>("/api/admin/autosend"));
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
  return <Form key={JSON.stringify(data.settings)} data={data} onSaved={setData} />;
}

function Form({ data, onSaved }: { data: AutoData; onSaved: (d: AutoData) => void }) {
  const saved = data.settings;
  const [enabled, setEnabled] = useState(saved.enabled);
  const [limit, setLimit] = useState(String(saved.daily_limit));
  const [cats, setCats] = useState<string[]>(saved.categories);
  const [step, setStep] = useState(Math.max(0, DELAYS.indexOf(saved.delay_minutes)));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  const next = { enabled, daily_limit: Number(limit), categories: [...cats].sort(), delay_minutes: DELAYS[step] };
  const dirty = JSON.stringify(next) !== JSON.stringify({ enabled: saved.enabled, daily_limit: saved.daily_limit,
    categories: [...saved.categories].sort(), delay_minutes: saved.delay_minutes });
  // switching on = after saving, some category sends automatically that did not before
  const turnsOn = enabled && (!saved.enabled || cats.some((c) => !saved.categories.includes(c)));

  async function save() {
    if (!Number.isInteger(next.daily_limit) || next.daily_limit < 0 || next.daily_limit > MAX_LIMIT) {
      setError(`O limite diário tem de ser um número de 0 a ${MAX_LIMIT}.`);
      return;
    }
    setBusy(true);
    setError("");
    try {
      onSaved(await request<AutoData>("/api/admin/autosend", { body: next }));
      toast("Envio automático guardado.");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Falhou.");
    } finally {
      setBusy(false);
    }
  }

  const confirm = useConfirm(() => void save(), JSON.stringify(next));
  const click = turnsOn ? confirm.click : () => void save();
  const armed = turnsOn && confirm.armed;

  function toggle(folder: string, on: boolean) {
    setCats((c) => (on ? [...c, folder] : c.filter((x) => x !== folder)));
  }

  return (
    <div className="page admin">
      <section className="panel">
        <div className="auto-master">
          <label className="switch-row">
            <input type="checkbox" role="switch" className="switch" checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)} />
            <span>
              <span className="switch-title">Envio automático</span>
              <span className="muted switch-sub">
                {enabled ? "Ligado: rascunhos das categorias escolhidas saem sem uma pessoa."
                  : "Desligado: todos os rascunhos esperam por uma pessoa."}
              </span>
            </span>
          </label>
          <label className="field auto-limit">
            <span className="field-label">Limite diário</span>
            <input className="input" type="number" inputMode="numeric" min={0} max={MAX_LIMIT} step={1} value={limit}
              onChange={(e) => setLimit(e.target.value)} />
          </label>
          <label className="field auto-delay">
            <span className="field-label">Esperar antes de responder: {delayLabel(DELAYS[step])}</span>
            <input className="range" type="range" min={0} max={DELAYS.length - 1} step={1} value={step}
              aria-valuetext={delayLabel(DELAYS[step])} onChange={(e) => setStep(Number(e.target.value))} />
          </label>
          <p className="auto-today">
            Enviados automaticamente hoje: <b>{data.sent_today}</b>
          </p>
        </div>
      </section>

      <section className="panel">
        <header className="panel-head">
          <h2 className="panel-title">Por categoria</h2>
          <p className="muted">Rascunhos fechados nos últimos 30 dias e quantos foram enviados sem alterações.</p>
        </header>
        <div className="table-wrap">
          <table className="data-table auto-table">
            <thead>
              <tr>
                <th scope="col">Categoria</th>
                <th scope="col" className="num">Rascunhos</th>
                <th scope="col" className="num">Sem alterações</th>
                <th scope="col" className="num">Automático</th>
              </tr>
            </thead>
            <tbody>
              {data.readiness.map((r) => (
                <tr key={r.folder}>
                  <th scope="row">{r.name}</th>
                  <td className="num" data-label="Rascunhos">{r.drafts}</td>
                  <td className="num" data-label="Sem alterações">
                    {r.drafts ? `${Math.round((100 * r.unchanged) / r.drafts)}%` : "—"}
                    <span className="muted"> ({r.unchanged})</span>
                  </td>
                  <td className="num">
                    <input type="checkbox" role="switch" className="switch" aria-label={`Automático: ${r.name}`}
                      checked={cats.includes(r.folder)} onChange={(e) => toggle(r.folder, e.target.checked)} />
                  </td>
                </tr>
              ))}
              {NEVER.map((r) => (
                <tr key={r.folder} className="row-off">
                  <th scope="row">{r.name}</th>
                  <td className="num muted" data-label="Rascunhos">—</td>
                  <td className="num muted" data-label="Sem alterações">—</td>
                  <td className="num">
                    <span className="never">Nunca automático</span>
                    <input type="checkbox" role="switch" className="switch" aria-label={`Automático: ${r.name}`}
                      checked={false} disabled />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="auto-actions">
        {armed && <p className="auto-confirm" role="status">Vai ligar o envio automático. Carregue outra vez para confirmar.</p>}
        <button type="button" className={"btn btn-primary" + (armed ? " btn-armed" : "")} disabled={!dirty || busy}
          onClick={click}>
          {busy ? "A guardar…" : armed ? "Confirmar e guardar" : "Guardar"}
        </button>
      </div>
    </div>
  );
}

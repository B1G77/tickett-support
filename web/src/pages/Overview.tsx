// Painel: how support is doing over a period — volumes, reply time, categories, AI quality and cost.
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, request } from "../api";
import { Columns, Legend, Rows, type Series } from "../components/Charts";
import PeriodPicker, { isValidPeriod, presetRange, todayISO, type Period } from "../components/PeriodPicker";
import Tile from "../components/Tile";
import { day, eur, hours } from "../format";
import { IconInbox, IconPerson, IconReply, IconWaiting } from "../layout/icons";
import "./agent.css";
import "./admin.css";

type Metrics = {
  complete_since: string;
  overview: { received: number; answered: number; waiting_person: number; oldest_waiting_hours: number | null };
  per_day: { day: string; received: number; answered: number }[];
  reply_time: { median_hours: number | null; within_limit_pct: number | null; limit_days: number;
    weekly: { week: string; median_hours: number | null; n: number }[] };
  categories: { folder: string; name: string; n: number }[];
  quality: { folder: string; name: string; drafts: number; unchanged: number; edited: number; rewritten: number;
    handed_off: number }[];
  cost: { period_eur: number; monthly: { month: string; eur: number }[]; unknown_models: string[] };
};

const NUM = new Intl.NumberFormat("pt-PT");
const num = (n: number) => NUM.format(n);
const pct = (part: number, whole: number) => (whole ? `${Math.round((100 * part) / whole)}%` : "—");

/** "dd/mm/aaaa" from "YYYY-MM-DD…". */
function fullDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : iso;
}

/** Monday of an ISO week "2026-W42", as "YYYY-MM-DD". */
function weekStart(week: string): string {
  const m = /^(\d{4})-W(\d{2})$/.exec(week);
  if (!m) return week;
  const jan4 = new Date(Number(m[1]), 0, 4);
  const monday = new Date(jan4);
  monday.setDate(jan4.getDate() - ((jan4.getDay() + 6) % 7) + (Number(m[2]) - 1) * 7);
  return todayISO(monday);
}

const MONTH = new Intl.DateTimeFormat("pt-PT", { month: "short" });
/** "out 2026" from "2026-10". */
function monthName(ym: string): string {
  const [y, m] = ym.split("-").map(Number);
  return y && m ? `${MONTH.format(new Date(y, m - 1, 1)).replace(".", "")} ${y}` : ym;
}

const PER_DAY: Series[] = [
  { key: "received", name: "Recebidos", cls: "series-a" },
  { key: "answered", name: "Respondidos", cls: "series-b" },
];

function usePeriod(): [Period, (p: Period) => void] {
  const [params, setParams] = useSearchParams();
  const asked = { from: params.get("from") ?? "", to: params.get("to") ?? "" };
  const period = isValidPeriod(asked) ? asked : presetRange("30", todayISO());
  return [period, (p) => setParams({ from: p.from, to: p.to })];
}

export default function Overview() {
  const [period, setPeriod] = usePeriod();
  const [data, setData] = useState<Metrics | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const { from, to } = period;

  const load = useCallback(async (signal: { stale: boolean }) => {
    setLoading(true);
    try {
      const m = await request<Metrics>(`/api/admin/metrics?from=${from}&to=${to}`);
      if (!signal.stale) { setData(m); setError(""); }
    } catch (e) {
      if (!signal.stale) setError(e instanceof ApiError ? e.message : "Falhou.");
    } finally {
      if (!signal.stale) setLoading(false);
    }
  }, [from, to]);

  useEffect(() => {
    const signal = { stale: false };
    void load(signal);
    return () => { signal.stale = true; };
  }, [load]);

  return (
    <div className="page admin">
      <div className="admin-bar">
        <PeriodPicker value={period} onChange={setPeriod} />
        {data?.complete_since && (
          <p className="admin-note">Números completos desde {fullDate(data.complete_since)}</p>
        )}
      </div>
      {error && (
        <div className="form-error admin-error" role="alert">
          <span>Não foi possível carregar: {error}</span>
          <button type="button" className="btn" onClick={() => void load({ stale: false })}>Tentar outra vez</button>
        </div>
      )}
      {!data && !error && <div className="tiles" aria-busy="true" aria-label="A carregar">
        {[0, 1, 2, 3].map((i) => <div key={i} className="tile skeleton" />)}
      </div>}
      {data && <Report m={data} busy={loading} />}
    </div>
  );
}

function Report({ m, busy }: { m: Metrics; busy: boolean }) {
  const o = m.overview;
  const rt = m.reply_time;
  const quietDays = m.per_day.every((d) => !d.received && !d.answered);
  const weekly = rt.weekly.map((w) => ({ ...w, start: weekStart(w.week) }));
  return (
    <div className={"admin-report" + (busy ? " is-busy" : "")} aria-busy={busy}>
      <div className="tiles">
        <Tile label="Emails recebidos" value={num(o.received)} icon={<IconInbox />} />
        <Tile label="Emails respondidos" value={num(o.answered)} sub={o.received ? `${pct(o.answered, o.received)} dos recebidos` : undefined}
          icon={<IconReply />} />
        <Tile label="À espera de uma pessoa" value={num(o.waiting_person)} icon={<IconPerson />}
          tone={o.waiting_person ? "warn" : ""} />
        <Tile label="Mais antigo à espera" value={hours(o.oldest_waiting_hours)} sub="sem resposta" icon={<IconWaiting />}
          tone={(o.oldest_waiting_hours ?? 0) > rt.limit_days * 24 ? "warn" : ""} />
      </div>

      <section className="panel chart-panel span-2">
        <header className="panel-head">
          <h2 className="panel-title">Recebidos e respondidos por dia</h2>
          <Legend series={PER_DAY} />
        </header>
        <Columns data={m.per_day} x="day" xName="Dia" series={PER_DAY} xFmt={day} title={(r) => fullDate(String(r.day))}
          fmt={num} empty={quietDays ? "Sem emails neste período." : ""}
          label="Emails recebidos e respondidos por dia" />
      </section>

      <section className="panel chart-panel">
        <header className="panel-head"><h2 className="panel-title">Tempo de resposta</h2></header>
        <div className="stat-row">
          <div className="stat">
            <p className="stat-value">{hours(rt.median_hours)}</p>
            <p className="stat-label">mediana até à resposta</p>
          </div>
          <div className="stat">
            <p className="stat-value">{rt.within_limit_pct === null ? "—" : `${num(rt.within_limit_pct)}%`}</p>
            <p className="stat-label">respondidos em {rt.limit_days} {rt.limit_days === 1 ? "dia" : "dias"} ou menos</p>
          </div>
        </div>
        <h3 className="sub-title">Mediana por semana</h3>
        <Columns data={weekly} x="start" xName="Semana" series={[{ key: "median_hours", name: "Mediana", cls: "series-a" }]}
          xFmt={day} yFmt={(v) => `${v} h`} title={(r) => `Semana de ${fullDate(String(r.start))}`}
          fmt={hours} extra={(r) => `${num(Number(r.n))} emails recebidos`}
          empty={weekly.some((w) => w.median_hours !== null) ? "" : "Ainda sem respostas neste período."}
          label="Mediana do tempo de resposta por semana" />
      </section>

      <section className="panel chart-panel">
        <header className="panel-head"><h2 className="panel-title">Por categoria</h2></header>
        <Rows data={m.categories.map((c) => ({ label: c.name, n: c.n }))} cls="series-a"
          empty="Sem emails neste período." label="Emails recebidos por categoria" />
      </section>

      <Quality rows={m.quality} />

      <section className="panel chart-panel span-2">
        <header className="panel-head"><h2 className="panel-title">Custo da IA</h2></header>
        <div className="stat-row">
          <div className="stat">
            <p className="stat-value">{eur(m.cost.period_eur)}</p>
            <p className="stat-label">neste período</p>
          </div>
        </div>
        <h3 className="sub-title">Por mês</h3>
        <Columns data={m.cost.monthly} x="month" xName="Mês" series={[{ key: "eur", name: "Custo", cls: "series-a" }]}
          xFmt={monthName} yFmt={(v) => `${v} €`} title={(r) => monthName(String(r.month))} fmt={eur}
          empty={m.cost.monthly.some((c) => c.eur > 0) ? "" : "Sem custos neste período."}
          label="Custo da IA por mês" />
        {m.cost.unknown_models.length > 0 && (
          <p className="admin-warn">
            Sem preço configurado (contados a 0 €): {m.cost.unknown_models.join(", ")}
          </p>
        )}
      </section>

    </div>
  );
}

function Quality({ rows }: { rows: Metrics["quality"] }) {
  const total = rows.reduce((t, r) => ({
    drafts: t.drafts + r.drafts, unchanged: t.unchanged + r.unchanged, edited: t.edited + r.edited,
    rewritten: t.rewritten + r.rewritten, handed_off: t.handed_off + r.handed_off,
  }), { drafts: 0, unchanged: 0, edited: 0, rewritten: 0, handed_off: 0 });
  return (
    <section className="panel span-2">
      <header className="panel-head">
        <h2 className="panel-title">Qualidade da IA</h2>
        <p className="muted">Rascunhos fechados no período, sem os enviados automaticamente.</p>
      </header>
      {rows.length === 0 ? <p className="chart-empty">Nenhum rascunho fechado neste período.</p> : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Categoria</th>
                <th scope="col" className="num">Rascunhos</th>
                <th scope="col" className="num">Enviados sem alterações</th>
                <th scope="col" className="num">Editados</th>
                <th scope="col" className="num">Reescritos</th>
                <th scope="col" className="num">Passados a uma pessoa</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => <QualityRow key={r.folder} name={r.name} r={r} />)}
            </tbody>
            {rows.length > 1 && (
              <tfoot><QualityRow name="Total" r={total} /></tfoot>
            )}
          </table>
        </div>
      )}
    </section>
  );
}

function QualityRow({ name, r }: { name: string;
  r: { drafts: number; unchanged: number; edited: number; rewritten: number; handed_off: number } }) {
  const share = r.drafts ? r.unchanged / r.drafts : 0;
  return (
    <tr>
      <th scope="row">{name}</th>
      <td className="num">{num(r.drafts)}</td>
      <td className="num">
        <span className="unchanged">
          <span className="meter" aria-hidden="true"><svg>
            <rect className="meter-track" width="100%" height="6" rx="3" />
            <rect className="meter-fill" width={`${Math.round(share * 100)}%`} height="6" rx="3" />
          </svg></span>
          {num(r.unchanged)} <span className="muted">({pct(r.unchanged, r.drafts)})</span>
        </span>
      </td>
      <td className="num">{num(r.edited)}</td>
      <td className="num">{num(r.rewritten)}</td>
      <td className="num">{num(r.handed_off)}</td>
    </tr>
  );
}

// The admin's period: Hoje, 7 dias, 30 dias or two dates. Days are local dates, both ends included.
import { useState } from "react";

export type Period = { from: string; to: string };
type Preset = "today" | "7" | "30";

const ISO = /^\d{4}-\d{2}-\d{2}$/;

/** Today's local date as YYYY-MM-DD. */
export function todayISO(now = new Date()): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
}

function minusDays(iso: string, n: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  return todayISO(new Date(y, m - 1, d - n));
}

export function presetRange(preset: Preset, today: string): Period {
  const back = preset === "today" ? 0 : Number(preset) - 1;
  return { from: minusDays(today, back), to: today };
}

/** Both ends valid dates, the earlier first. */
export function ordered(from: string, to: string): Period {
  return from <= to ? { from, to } : { from: to, to: from };
}

export function isValidPeriod(p: Partial<Period>): p is Period {
  return ISO.test(p.from ?? "") && ISO.test(p.to ?? "");
}

const PRESETS: { id: Preset; label: string }[] = [
  { id: "today", label: "Hoje" }, { id: "7", label: "7 dias" }, { id: "30", label: "30 dias" },
];

export default function PeriodPicker({ value, onChange, today = todayISO() }:
  { value: Period; onChange: (p: Period) => void; today?: string }) {
  const matching = PRESETS.find((p) => {
    const r = presetRange(p.id, today);
    return r.from === value.from && r.to === value.to;
  })?.id;
  const [custom, setCustom] = useState(!matching);
  const shown = custom ? null : matching;

  function pick(id: Preset) {
    setCustom(false);
    onChange(presetRange(id, today));
  }

  function setEnd(which: keyof Period, v: string) {
    if (!ISO.test(v)) return;   // a half-typed date
    const next = { ...value, [which]: v };
    onChange(ordered(next.from, next.to));
  }

  return (
    <div className="period">
      <div className="segmented" role="group" aria-label="Período">
        {PRESETS.map((p) => (
          <button key={p.id} type="button" className="segment" aria-pressed={shown === p.id} onClick={() => pick(p.id)}>
            {p.label}
          </button>
        ))}
        <button type="button" className="segment" aria-pressed={custom || !matching} onClick={() => setCustom(true)}>
          Personalizado
        </button>
      </div>
      {(custom || !matching) && (
        <div className="period-dates">
          <label className="period-date">
            <span>De</span>
            <input className="input" type="date" value={value.from} max={today}
              onChange={(e) => setEnd("from", e.target.value)} />
          </label>
          <label className="period-date">
            <span>Até</span>
            <input className="input" type="date" value={value.to} max={today}
              onChange={(e) => setEnd("to", e.target.value)} />
          </label>
        </div>
      )}
    </div>
  );
}

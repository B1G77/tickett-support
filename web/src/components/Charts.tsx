// Recharts wrappers for the admin page. Colours come from CSS classes (admin.css) on the series, so both
// themes follow theme.css; the width is measured here instead of ResponsiveContainer, and the tooltip is
// plain text with classes. Nothing on our own elements sets a style attribute (CSP style-src 'self').
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Bar, BarChart, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";

export type Series = { key: string; name: string; cls: string };
type Row = Record<string, string | number | null>;

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.floor(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return { ref, width };
}

/** The chart's box: a short message instead of the chart when there is nothing to draw. The drawing is
 * hidden from screen readers; `table` carries the same numbers for them. */
function ChartBox({ size, empty, table, children }:
  { size: string; empty: string; table: ReactNode; children: (width: number) => ReactNode }) {
  const { ref, width } = useWidth();
  if (empty) return <p className="chart-empty">{empty}</p>;
  return (
    <>
      <div ref={ref} className={"chart " + size} aria-hidden="true">
        {width > 0 && children(width)}
      </div>
      {table}
    </>
  );
}

/** The chart's data as a visually hidden table: one row per point, the same formats as the tooltip. */
function DataTable({ data, xName, series, title, fmt, extra, label }: {
  data: Row[]; xName: string; series: Series[]; title: (row: Row) => string; fmt: (v: number) => string;
  extra?: (row: Row) => string; label: string }) {
  return (
    <table className="sr-only">
      <caption>{label}</caption>
      <thead>
        <tr>
          <th scope="col">{xName}</th>
          {series.map((s) => <th key={s.key} scope="col">{s.name}</th>)}
          {extra && <th scope="col">Detalhe</th>}
        </tr>
      </thead>
      <tbody>
        {data.map((row, i) => (
          <tr key={i}>
            <th scope="row">{title(row)}</th>
            {series.map((s) => (
              <td key={s.key}>{row[s.key] === null || row[s.key] === undefined ? "—" : fmt(Number(row[s.key]))}</td>
            ))}
            {extra && <td>{extra(row)}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Tooltip text: the row's label, then one line per series. */
type TipEntry = { dataKey?: unknown; name?: unknown; value?: unknown; payload?: unknown };

function Tip({ active, payload, title, fmt, extra, classes }: {
  active?: boolean; payload?: ReadonlyArray<TipEntry>; title: (row: Row) => string; fmt: (v: number) => string; extra?: (row: Row) => string;
  classes: Record<string, string> }) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload as Row;
  return (
    <div className="chart-tip">
      <p className="chart-tip-title">{title(row)}</p>
      {payload.map((p) => (
        <p key={String(p.dataKey)} className="chart-tip-row">
          <span className={"swatch " + (classes[String(p.dataKey)] ?? "")} />
          {String(p.name)}: <b>{fmt(Number(p.value ?? 0))}</b>
        </p>
      ))}
      {extra && <p className="chart-tip-extra">{extra(row)}</p>}
    </div>
  );
}

/** Legend for two or more series, as text beside a colour mark. */
export function Legend({ series }: { series: Series[] }) {
  return (
    <ul className="legend">
      {series.map((s) => <li key={s.key}><span className={"swatch " + s.cls} />{s.name}</li>)}
    </ul>
  );
}

/** Vertical bars over time (one or more series side by side). */
export function Columns({ data, x, xName, series, xFmt, title, fmt, yFmt = String, extra, empty, label }: {
  data: Row[]; x: string; xName: string; series: Series[]; xFmt: (v: string) => string; title: (row: Row) => string;
  fmt: (v: number) => string; yFmt?: (v: number) => string; extra?: (row: Row) => string; empty: string;
  label: string }) {
  return (
    <ChartBox size="chart-md" empty={empty}
      table={<DataTable data={data} xName={xName} series={series} title={title} fmt={fmt} extra={extra} label={label} />}>
      {(width) => (
        <BarChart width={width} height={220} data={data} margin={{ top: 8, right: 4, bottom: 0, left: 0 }}
          barGap={2} barCategoryGap="20%">
          <CartesianGrid vertical={false} className="chart-grid" />
          <XAxis dataKey={x} tickFormatter={xFmt} tickLine={false} axisLine={false} minTickGap={16}
            className="chart-axis" />
          <YAxis allowDecimals={false} tickFormatter={yFmt} tickLine={false} axisLine={false} width={44}
            className="chart-axis" />
          <Tooltip cursor={{ className: "chart-cursor" }} isAnimationActive={false}
            content={(p) => <Tip active={p.active} payload={p.payload} title={title} fmt={fmt} extra={extra}
              classes={Object.fromEntries(series.map((s) => [s.key, s.cls]))} />} />
          {series.map((s) => (
            <Bar key={s.key} dataKey={s.key} name={s.name} className={s.cls} radius={[4, 4, 0, 0]} maxBarSize={28} />
          ))}
        </BarChart>
      )}
    </ChartBox>
  );
}

/** Horizontal bars, one series, longest first (categories): a list with the name and count over
 * each bar, so long names wrap on a phone instead of squeezing an axis. Bar lengths are SVG attributes. */
export function Rows({ data, cls, empty, label }:
  { data: { label: string; n: number }[]; cls: string; empty: string; label: string }) {
  if (!data.length) return <p className="chart-empty">{empty}</p>;
  const top = Math.max(...data.map((d) => d.n), 1);
  return (
    <ol className="hbars" aria-label={label}>
      {data.map((d) => (
        <li key={d.label} className="hbar">
          <span className="hbar-name">{d.label}</span>
          <span className="hbar-n">{d.n}</span>
          <svg className={"hbar-track " + cls} aria-hidden="true">
            <rect className="hbar-bg" width="100%" height="8" rx="4" />
            <rect className="hbar-fill" width={`${Math.max(1, (100 * d.n) / top)}%`} height="8" rx="4" />
          </svg>
        </li>
      ))}
    </ol>
  );
}

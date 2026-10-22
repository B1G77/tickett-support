// Display formats for numbers the API sends (pt-PT).

/** A duration given in hours: "—", "45 min", "3 h 20 min", "2 d 4 h". */
export function hours(h: number | null): string {
  if (h === null || !Number.isFinite(h)) return "—";
  const mins = Math.round(h * 60);
  if (mins < 60) return `${mins} min`;
  if (mins < 24 * 60) {
    const m = mins % 60;
    return `${Math.floor(mins / 60)} h` + (m ? ` ${m} min` : "");
  }
  const totalH = Math.round(h);
  const rest = totalH % 24;
  return `${Math.floor(totalH / 24)} d` + (rest ? ` ${rest} h` : "");
}

const EUR = new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" });

/** Euros, e.g. "12,50 €". */
export function eur(n: number): string {
  return EUR.format(n);
}

/** "dd/mm" from an ISO date or timestamp (read as written, no time-zone shift). */
export function day(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${m[3]}/${m[2]}` : iso;
}

// A KPI tile: a label, one big number, and an optional line under it.
import type { ReactNode } from "react";

export default function Tile({ label, value, sub, icon, tone = "" }:
  { label: string; value: string; sub?: string; icon?: ReactNode; tone?: "" | "warn" }) {
  return (
    <section className={"tile" + (tone ? " tile-" + tone : "")}>
      {icon && <span className="tile-icon">{icon}</span>}
      <div className="tile-body">
        <h3 className="tile-label">{label}</h3>
        <p className="tile-value">{value}</p>
        {sub && <p className="tile-sub">{sub}</p>}
      </div>
    </section>
  );
}

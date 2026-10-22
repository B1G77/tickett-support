#!/usr/bin/env python3
"""Stage 9 - a local status page for the whole support-inbox stack.

The router calls write() after every cycle (and heartbeat() before it), so
~/lebillet-data/router/dashboard.html is always at most one cycle old. The page
reloads itself every minute and turns red on its own when it stops being
rewritten, which means the router is down or the Mac is asleep. It holds counts
only - no subjects, addresses or customer text - and stays in the private data
folder (mode 600). No server, no open port.

Usage: dashboard.py            (rebuild the page now and open it)
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

PAGE = "dashboard.html"
HEARTBEAT = "heartbeat.json"
COUNTS = "counts.json"
SITE_EVENTS = Path.home() / "lebillet-data" / "site" / "events.json"
SITE_DOC = Path.home() / "lebillet-data" / "site" / "doc.json"
STALE_MINUTES = 6   # three missed 120 s cycles
_PROBLEM = re.compile(r"error|fail|will retry|no draft|leak|warning|unavailable|abort|timeout", re.I)
_DIGEST = re.compile(r"Por responder: (\d+) · atrasadas \(mais de \d+ dias\): (\d+)")
_LINE = re.compile(r"^(\S+)\s{2}(\S+)\s{2}(.*)$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def heartbeat(state_dir: Path, ok: bool, error: str = "", now: datetime | None = None) -> None:
    """Record one router cycle: when, whether it worked, and the last few failures."""
    now = now or _now()
    hb = _read_json(state_dir / HEARTBEAT)
    hb.setdefault("started_at", now.isoformat(timespec="seconds"))
    hb["last_cycle"] = now.isoformat(timespec="seconds")
    if ok:
        hb["last_ok"] = hb["last_cycle"]
        hb["failures_in_a_row"] = 0
    else:
        hb["failures_in_a_row"] = hb.get("failures_in_a_row", 0) + 1
        hb["failures"] = (hb.get("failures", []) + [[hb["last_cycle"], error[:120]]])[-50:]
    _write(state_dir / HEARTBEAT, json.dumps(hb, ensure_ascii=False))


def save_counts(state_dir: Path, box) -> None:
    """Folder sizes and mailbox quota, taken while the router is logged in."""
    used = total = None
    q = str(box.quota())
    m = re.search(r"STORAGE (\d+) (\d+)", q)
    if m:
        used, total = int(m.group(1)), int(m.group(2))
    _write(state_dir / COUNTS, json.dumps({"at": _now().isoformat(timespec="seconds"),
                                           "folders": dict(box.counts()), "quota_used_kb": used,
                                           "quota_total_kb": total}, ensure_ascii=False))


def _log_lines(path: Path, since: datetime) -> list[tuple[datetime, str, str]]:
    out = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-20000:]
    except OSError:
        return out
    for line in lines:
        m = _LINE.match(line)
        if not m:
            continue
        try:
            t = datetime.fromisoformat(m.group(1))
        except ValueError:
            continue
        if t >= since:
            out.append((t, m.group(2), m.group(3)))
    return out


def _problem_kind(msg: str) -> str:
    """The reason only: strip notes, numbers and anything after a dash, so no customer text survives."""
    msg = re.split(r" - |: ", msg)[0]
    return re.sub(r"\d+", "N", msg)[:70]


def _cmd(args: list[str]) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def system_checks() -> dict:
    batt = _cmd(["pmset", "-g", "batt"])
    return {"on_battery": ("Battery Power" in batt) if batt else None}


def collect(cfg: dict, now: datetime | None = None, system: dict | None = None) -> dict:
    now = now or _now()
    sd = Path(cfg["state_dir"])
    day0 = (now - timedelta(days=13)).date()
    hb, counts = _read_json(sd / HEARTBEAT), _read_json(sd / COUNTS)
    db = sqlite3.connect(sd / "router.db")
    try:
        per_day = dict(db.execute("SELECT substr(moved_at,1,10), COUNT(*) FROM decisions WHERE moved_at >= ? "
                                  "GROUP BY 1", (day0.isoformat(),)).fetchall())
        week = (now - timedelta(days=7)).isoformat()
        by_folder = dict(db.execute("SELECT folder, COUNT(*) FROM decisions WHERE moved_at >= ? GROUP BY 1",
                                    (week,)).fetchall())
        by_source = dict(db.execute("SELECT source, COUNT(*) FROM decisions WHERE moved_at >= ? GROUP BY 1",
                                    (week,)).fetchall())
        try:
            drafts = db.execute("SELECT COUNT(*), SUM(sent_at IS NULL), AVG(kept) FROM drafts").fetchone()
        except sqlite3.OperationalError:
            drafts = (0, 0, None)
        try:
            handoffs = dict(db.execute("SELECT reason, COUNT(*) FROM handoffs WHERE created_at >= ? GROUP BY 1",
                                       (week,)).fetchall())
        except sqlite3.OperationalError:
            handoffs = {}
    finally:
        db.close()
    lines = _log_lines(sd / "router.log", now - timedelta(days=14))
    day_ago = now - timedelta(days=1)
    problems = Counter(_problem_kind(msg) for t, _, msg in lines if t >= day_ago and _PROBLEM.search(msg))
    answered = Counter(t.date().isoformat() for t, _, msg in lines if msg.startswith("answered ->"))
    digest = next((m for _, who, msg in reversed(lines) if who == "digest" and (m := _DIGEST.search(msg))), None)
    site = _read_json(SITE_EVENTS)
    events = site.get("events", [])
    days = [(day0 + timedelta(days=i)).isoformat() for i in range(14)]
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "heartbeat": hb, "counts": counts,
        "per_day": [[d, per_day.get(d, 0), answered.get(d, 0)] for d in days],
        "by_folder": {cfg["folders"].get(f, cfg.get("answered_folder", f) if f == "A" else f): n
                      for f, n in sorted(by_folder.items(), key=lambda kv: -kv[1])},
        "by_source": by_source,
        "problems_24h": problems.most_common(8),
        "waiting": int(digest.group(1)) if digest else None,
        "overdue": int(digest.group(2)) if digest else None,
        "drafts_enabled": bool(cfg.get("drafts", {}).get("enabled")),
        "drafts": {"total": drafts[0] or 0, "open": int(drafts[1] or 0), "avg_kept": drafts[2]},
        "handoffs": handoffs,
        "handoff_waiting": next((n for name, n in (counts.get("folders") or {}).items()
                                 if cfg.get("handoff_folder") and name.endswith(cfg["handoff_folder"])), None),
        "doc": {"fetched_at": (doc := _read_json(SITE_DOC)).get("fetched_at"),
                "with_info": sum(1 for e in doc.get("events", []) if e.get("fields")),
                "without": sum(1 for e in doc.get("events", []) if not e.get("fields")),
                "general": len(doc.get("general", []))},
        "site": {"crawled_at": site.get("crawled_at"), "listed": sum(1 for e in events if e.get("listed")),
                 "packages": sum(len(e.get("packages", [])) for e in events)},
        "system": system if system is not None else system_checks(),
    }


def checks(d: dict, now: datetime) -> list[tuple[str, str, str]]:
    """(status, label, detail) for each part of the stack; status is good / warning / critical."""
    out = []
    hb, counts = d["heartbeat"], d["counts"]
    last = hb.get("last_cycle")
    age = (now - datetime.fromisoformat(last)).total_seconds() / 60 if last else None
    if age is None or age > STALE_MINUTES:
        out.append(("critical", "Router", "nenhum ciclo registado" if age is None else f"último ciclo há {age:.0f} min"))
    elif hb.get("failures_in_a_row"):
        out.append(("warning", "Router", f"os últimos {hb['failures_in_a_row']} ciclo(s) falharam: "
                                         f"{(hb.get('failures') or [['', '?']])[-1][1]}"))
    else:
        out.append(("good", "Router", f"último ciclo há {age:.0f} min"))
    inbox = (counts.get("folders") or {}).get("INBOX")
    out.append(("good" if inbox in (0, None) else "warning", "Caixa de entrada",
                "vazia" if inbox == 0 else "desconhecida" if inbox is None else f"{inbox} por arquivar"))
    used, total = counts.get("quota_used_kb"), counts.get("quota_total_kb")
    if used and total:
        pct = used / total
        out.append(("critical" if pct > 0.9 else "warning" if pct > 0.8 else "good", "Espaço da caixa de correio",
                    f"{used / 1048576:.2f} de {total / 1048576:.0f} GB ({pct:.0%})"))
    n = sum(c for _, c in d["problems_24h"])
    out.append(("good" if n == 0 else "warning", "Erros nas últimas 24 h", "nenhum" if n == 0 else f"{n} registados"))
    crawled = d["site"]["crawled_at"]
    if crawled:
        # crawled_at is the Mac's local time without a zone
        hours = (now.astimezone().replace(tzinfo=None) - datetime.fromisoformat(crawled)).total_seconds() / 3600
        status = "good" if hours < 36 or not d["drafts_enabled"] else "warning"
        out.append((status, "Site tickett.com", f"lido há {hours:.0f} h: {d['site']['listed']} eventos, "
                                                f"{d['site']['packages']} pacotes"))
    doc = d.get("doc", {})
    if doc.get("fetched_at"):
        mins = (now - datetime.fromisoformat(doc["fetched_at"])).total_seconds() / 60
        out.append(("good" if mins < 60 else "warning", "Documento de eventos",
                    f"lido há {mins:.0f} min: {doc['general']} respostas gerais, {doc['with_info']} eventos "
                    f"({doc['without']} ainda vazios)"))
    else:
        out.append(("warning", "Documento de eventos", "ainda não lido"))
    out.append(("good" if d["drafts_enabled"] else "warning", "Rascunhos de resposta",
                "ligados" if d["drafts_enabled"] else "desligados"))
    sysc = d["system"]
    if sysc.get("on_battery") is not None:   # only on a Mac
        out.append(("warning" if sysc["on_battery"] else "good", "Energia",
                    "na bateria: o router para quando o Mac adormece" if sysc["on_battery"] else "ligado à corrente"))
    return out


STATUS_LABEL = {"good": "OK", "warning": "Atenção", "critical": "Problema"}
STATUS_ICON = {"good": "✓", "warning": "!", "critical": "✕"}


def _columns(per_day: list) -> str:
    """Emails filed per day: one series, columns with a 4px rounded cap, value labels on hover."""
    w, h, pad_l, pad_b, pad_t = 640, 180, 34, 24, 10
    top = max([n for _, n, _ in per_day] + [1])
    step = 10 ** max(len(str(top)) - 1, 0)
    top = ((top + step - 1) // step) * step
    band = (w - pad_l) / len(per_day)
    bar = min(24, band * 0.6)
    y = lambda v: pad_t + (h - pad_t - pad_b) * (1 - v / top)
    parts = [f'<line class="grid" x1="{pad_l}" x2="{w}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
             f'<text class="tick" x="{pad_l - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{v:,}</text>'
             for v in (0, top // 2, top)]
    for i, (day, n, answered) in enumerate(per_day):
        x = pad_l + band * i + (band - bar) / 2
        tip = html.escape(f"{day}: {n} filed, {answered} answered")
        if n:
            r = min(4, (y(0) - y(n)) / 2)
            yt, yb = y(n), y(0)
            parts.append(f'<path class="col" d="M{x:.1f},{yb:.1f}V{yt + r:.1f}Q{x:.1f},{yt:.1f} {x + r:.1f},{yt:.1f}'
                         f'H{x + bar - r:.1f}Q{x + bar:.1f},{yt:.1f} {x + bar:.1f},{yt + r:.1f}V{yb:.1f}Z"/>')
        parts.append(f'<rect class="hit" x="{pad_l + band * i:.1f}" y="0" width="{band:.1f}" height="{h}">'
                     f'<title>{tip}</title></rect>')
        if i % 2 == 0 or i == len(per_day) - 1:
            parts.append(f'<text class="tick" x="{x + bar / 2:.1f}" y="{h - 6}" text-anchor="middle">'
                         f'{day[8:]}/{day[5:7]}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Emails filed per day, last 14 days">'
            + "".join(parts) + "</svg>")


def _bars(rows: dict) -> str:
    top = max(list(rows.values()) + [1])
    return "".join(f'<div class="hrow"><span class="hlabel">{html.escape(k)}</span>'
                   f'<span class="htrack"><span class="hbar" style="width:{v / top * 100:.1f}%"></span></span>'
                   f'<span class="hval">{v:,}</span></div>' for k, v in rows.items()) or '<p class="muted">None this week.</p>'


def render(d: dict, now: datetime | None = None) -> str:
    now = now or _now()
    cs = checks(d, now)
    worst = "critical" if any(s == "critical" for s, _, _ in cs) else "warning" if any(
        s == "warning" for s, _, _ in cs) else "good"
    rows = "".join(f'<li class="check {s}"><span class="badge {s}"><b>{STATUS_ICON[s]}</b> {STATUS_LABEL[s]}</span>'
                   f'<span class="clabel">{html.escape(label)}</span><span class="cdetail">{html.escape(detail)}</span></li>'
                   for s, label, detail in cs)
    def tile(label, value, note=""):
        v = "–" if value is None else f"{value:,}" if isinstance(value, int) else html.escape(str(value))
        return f'<div class="tile"><div class="tlabel">{label}</div><div class="tval">{v}</div><div class="tnote">{note}</div></div>'
    today = d["per_day"][-1]
    src = d["by_source"]
    week_total = sum(src.values())
    claude_share = f"{src.get('claude', 0) / week_total:.0%} by Claude" if week_total else ""
    problems = "".join(f"<tr><td>{html.escape(k)}</td><td class='num'>{n}</td></tr>" for k, n in d["problems_24h"]) \
        or "<tr><td colspan='2' class='muted'>Nothing logged in the last 24 hours.</td></tr>"
    data = json.dumps({"generated_at": d["generated_at"], "stale": STALE_MINUTES})
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="60">
<title>Support Inbox Status</title>
<style>
:root{{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;
--base:#c3c2b7;--series:#2a78d6;--ring:rgba(11,11,11,.10);--good:#0ca30c;--goodt:#006300;--warn:#fab219;--crit:#d03b3b}}
@media (prefers-color-scheme:dark){{:root{{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;
--grid:#2c2c2a;--base:#383835;--series:#3987e5;--ring:rgba(255,255,255,.10);--goodt:#0ca30c}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1080px;margin:0 auto;padding:20px 16px 40px;display:grid;gap:16px}}
header{{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline;justify-content:space-between}}
h1{{font-size:20px;margin:0}}h2{{font-size:13px;margin:0 0 10px;color:var(--ink2);font-weight:600;text-transform:uppercase;letter-spacing:.05em}}
.muted,.stamp{{color:var(--muted)}}.card{{background:var(--surface);border:1px solid var(--ring);border-radius:10px;padding:14px 16px;min-width:0}}
.grid2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:12px}}
.tile{{background:var(--surface);border:1px solid var(--ring);border-radius:10px;padding:12px 14px}}
.tlabel{{color:var(--ink2);font-size:12px}}.tval{{font-size:28px;font-weight:600;margin-top:2px}}.tnote{{color:var(--muted);font-size:12px;min-height:1em}}
ul.checks{{list-style:none;margin:0;padding:0;display:grid;gap:6px}}
.check{{display:grid;grid-template-columns:110px 160px 1fr;gap:10px;align-items:baseline}}
.badge{{font-size:12px;font-weight:600;border-radius:999px;padding:1px 8px;justify-self:start;color:var(--ink);border:1.5px solid}}
.badge.good{{border-color:var(--good)}}.badge.warning{{border-color:var(--warn)}}.badge.critical{{border-color:var(--crit)}}
.badge b{{display:inline-block;width:1em;text-align:center}}.clabel{{font-weight:600}}.cdetail{{color:var(--ink2)}}
.overall{{font-size:13px;font-weight:600;border-radius:999px;padding:3px 12px;border:2px solid}}
.overall.good{{border-color:var(--good)}}.overall.warning{{border-color:var(--warn)}}.overall.critical{{border-color:var(--crit)}}
svg{{width:100%;height:auto;display:block}}.grid{{stroke:var(--grid);stroke-width:1}}.tick{{fill:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}}
.col{{fill:var(--series)}}.hit{{fill:transparent}}.hit:hover{{fill:var(--grid);fill-opacity:.5}}
.hrow{{display:grid;grid-template-columns:minmax(0,10em) minmax(80px,1fr) 40px;gap:10px;align-items:center;margin:6px 0}}
.hlabel{{overflow-wrap:anywhere}}.htrack{{height:12px}}.hbar{{display:block;height:12px;background:var(--series);border-radius:0 4px 4px 0;min-width:2px}}
.hval,.num{{text-align:right;font-variant-numeric:tabular-nums}}table{{width:100%;border-collapse:collapse}}
td{{padding:5px 0;border-bottom:1px solid var(--grid)}}#stale{{display:none;background:var(--crit);color:#fff;border-radius:10px;padding:12px 16px;font-weight:600}}
@media (max-width:560px){{.check{{grid-template-columns:1fr;gap:2px}}}}
</style></head><body><main>
<div id="stale" role="alert"></div>
<header><h1>Support inbox status</h1><span class="overall {worst}">{STATUS_ICON[worst]} {STATUS_LABEL[worst]}</span>
<span class="stamp">Updated <span id="age"></span> · refreshes every minute</span></header>
<section class="tiles">
{tile("Waiting for a reply", d["waiting"], f"{d['overdue']:,} older than 2 days" if d["overdue"] is not None else "from the morning summary")}
{tile("Filed today", today[1], f"{today[2]} answered today")}
{tile("Filed, last 7 days", week_total, claude_share)}
{tile("Waiting for a person", d.get("handoff_waiting"), f"{sum(d.get('handoffs', {}).values())} handed off this week" if d.get("handoffs") else "in 0 Por Responder")}
{tile("Answered, last 7 days", sum(a for _, _, a in d["per_day"][-7:]), "moved to 10 Respondidos")}
{tile("Drafts waiting", d["drafts"]["open"] if d["drafts_enabled"] else "Off", "" if d["drafts_enabled"] else "waiting for the events document")}
</section>
<section class="card"><h2>Health</h2><ul class="checks">{rows}</ul></section>
<section class="grid2">
<div class="card"><h2>Emails filed per day, last 14 days</h2>{_columns(d["per_day"])}</div>
<div class="card"><h2>Where mail went, last 7 days</h2>{_bars(d["by_folder"])}</div>
</section>
<section class="card"><h2>Problems logged, last 24 hours</h2><table>{problems}</table></section>
</main>
<script>
const D = {data};
function tick() {{
  const mins = (Date.now() - new Date(D.generated_at).getTime()) / 60000;
  document.getElementById("age").textContent = mins < 1 ? "just now" : Math.round(mins) + " min ago";
  const s = document.getElementById("stale");
  if (mins > D.stale) {{ s.style.display = "block";
    s.textContent = "✕ This page stopped updating " + Math.round(mins) + " minutes ago. The router is not running, or the Mac is asleep."; }}
}}
tick(); setInterval(tick, 15000);
</script></body></html>"""


def write(cfg: dict) -> Path:
    path = Path(cfg.get("dashboard_path") or Path(cfg["state_dir"]) / PAGE)   # a folder of its own on the server
    path.parent.mkdir(parents=True, exist_ok=True)
    _write(path, render(collect(cfg)))
    return path


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import router as RT
    path = write(RT.load_config())
    subprocess.run(["open", str(path)])
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

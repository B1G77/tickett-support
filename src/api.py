"""JSON builders for the dashboard API (read-only views over the router state)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import actions as AC
import autosend as AS
import dashboard as DB
import events_doc as ED
import ledger as LG
import router as RT

def _when(iso: str | None) -> str:
    """dd/mm HH:MM in the server's local time, from an ISO or e-mail date."""
    if not iso:
        return ""
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return iso[:16]
    return (t.astimezone() if t.tzinfo else t).strftime("%d/%m %H:%M")


def _auto_at(iso: str | None) -> str:
    """When a scheduled automatic send goes out, in local time: HH:MM today, else dd/mm HH:MM."""
    if not iso:
        return ""
    t = datetime.fromisoformat(iso).astimezone()
    return t.strftime("%H:%M" if t.date() == datetime.now().astimezone().date() else "%d/%m %H:%M")


def _differs(to: str, sender: str | None) -> bool:
    return bool(to and sender and to.lower() != sender.lower())


def health_checks(d: dict, now: datetime) -> list[dict]:
    return [{"status": s, "label": label, "detail": detail} for s, label, detail in DB.checks(d, now)]


def overall(checks: list[dict]) -> str:
    statuses = {c["status"] for c in checks}
    return "bad" if "critical" in statuses else "warn" if "warning" in statuses else "ok"


def health_json(cfg: dict, state) -> dict:
    """The admin's health page: every check, and what changed in the events Doc lately."""
    now = datetime.now(timezone.utc)
    LG.init(state)
    rows = state.db.execute("SELECT at, kind, name FROM doc_changes ORDER BY at DESC, rowid DESC LIMIT 50").fetchall()
    return {"checks": health_checks(DB.collect(cfg, now=now), now),
            "doc": {"fetched_at": (ED.load(Path(cfg["site_dir"])) if cfg.get("site_dir") else ED.load())
                    .get("fetched_at", ""),
                    "changes": [{"at": a, "kind": k, "name": n} for a, k, n in rows]}}


def _sort(r: dict) -> str:
    """The key the tab orders by, oldest customer email first (as actions.pending)."""
    return r.get("received_at") or r.get("created_at") or ""


def state_json(cfg: dict, state) -> dict:
    names = RT.folder_names(cfg)
    auto = AS.settings(state)
    on = set(auto["categories"]) if auto["enabled"] else set()   # no badge for a send that would not happen
    drafts = [{
        "id": AC.card_id(r["message_id"]), "version": r["created_at"], "folder": names.get(r["folder"], r["folder"]),
        "subject": r.get("subject") or "(sem assunto)", "from_name": r.get("from_name") or "",
        "from_email": r.get("from_email") or "", "received": _when(r.get("received_at")),
        "text": r.get("newest_text") or "", "full": r.get("full_text") or "", "reply": r.get("body") or "",
        "note": r.get("note") or "", "blocked": AC.sendable(r.get("body") or ""),
        "to": AC.recipient(r), "to_override": r.get("to_override") or "", "refusal": AC.refusal(r),
        "to_differs": r.get("flag") != "relayed" and (not AC.reply_to_ok(r)
                                                     or _differs(AC.recipient(r), r.get("from_email"))),
        "rewrites": r.get("rewrites") or 0, "edited": bool(r.get("edited")),
        "auto_at": _auto_at(r.get("auto_after") if r["folder"] in on else None),
        "flag": r.get("flag") or "", "bcc": r.get("bcc") or "", "sort": _sort(r),
    } for r in AC.pending(state)]
    now = datetime.now(timezone.utc)
    d = DB.collect(cfg, now=now)
    sent_today = state.db.execute("SELECT COUNT(*) FROM drafts WHERE closed_how='sent' AND closed_at >= ?",
                                  (now.date().isoformat(),)).fetchone()[0]
    return {"generated_at": now.isoformat(timespec="seconds"), "mailbox": cfg["imap"]["user"],
            "drafts": drafts,
            "health": {"overall": overall(health_checks(d, now))},
            "numbers": {"drafts": len(drafts), "sent_today": sent_today,
                        "waiting": d.get("waiting"), "overdue": d.get("overdue")}}

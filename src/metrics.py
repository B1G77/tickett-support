#!/usr/bin/env python3
"""The numbers on the admin page for a chosen period, computed from router.db.

Reads the tables the ledger and drafts modules keep (replies, ai_usage, drafts, handoffs); writes nothing.
Day boundaries are the server's local time zone: a timestamp belongs to the local date it falls on, and
the period's first and last day are both included.
"""
from __future__ import annotations

import statistics
from collections import Counter
from datetime import date, datetime, timedelta

import actions as AC
import drafts as DR
import ledger as L
import router as RT

OPEN_FOLDERS = ("F1", "F2", "F3", "F4", "F5", "F6", "F7", "F9", "P")   # not A (answered), not F8


def _waiting(state) -> set[str]:
    """Unanswered emails still waiting: in an open folder, and not found gone by their draft (a thank-you
    moved to folder 8 or mail moved out by hand is not waiting, though the ledger never saw it answered)."""
    marks = ",".join("?" * len(OPEN_FOLDERS))
    return {r[0] for r in state.db.execute(
        f"""SELECT r.message_id FROM replies r JOIN decisions d ON d.message_id=r.message_id
            WHERE r.answered_at IS NULL AND d.folder IN ({marks}) AND NOT EXISTS
            (SELECT 1 FROM drafts WHERE drafts.message_id=r.message_id AND closed_how='gone')""", OPEN_FOLDERS)}


def _local(ts: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(ts).astimezone() if ts else None
    except ValueError:
        return None


def _day(ts: str | None) -> date | None:
    d = _local(ts)
    return d.date() if d else None


def _rows(state, sql: str) -> list[dict]:
    cur = state.db.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def _months(start: date, end: date) -> list[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _reply_time(rows: list[dict], now: datetime, limit_days: int, waiting: set[str]) -> dict:
    """Answer times of the replies received in the period; `rows` carry their local received date. An
    unanswered one counts as late only while it is still `waiting`."""
    limit = timedelta(days=limit_days)
    hours: dict[str, list[float]] = {}
    count: Counter = Counter()
    answered, within, late_open = [], 0, 0
    for r in rows:
        iso = r["received_day"].isocalendar()
        week = f"{iso[0]}-W{iso[1]:02d}"
        count[week] += 1
        got, ans = _local(r["received_at"]), _local(r["answered_at"])
        if ans and got:
            h = (ans - got).total_seconds() / 3600
            answered.append(h)
            hours.setdefault(week, []).append(h)
            within += ans - got <= limit
        elif got and now - got > limit and r["message_id"] in waiting:
            late_open += 1
    denom = len(answered) + late_open
    return {"median_hours": _median(answered),
            "within_limit_pct": round(100 * within / denom, 1) if denom else None,
            "limit_days": limit_days,
            "weekly": [{"week": w, "median_hours": _median(hours.get(w, [])), "n": count[w]} for w in sorted(count)]}


def _quality(state, cfg: dict, start: date, end: date) -> list[dict]:
    names = RT.folder_names(cfg)
    groups: dict[str, list[dict]] = {}
    for d in _rows(state, "SELECT * FROM drafts WHERE closed_at IS NOT NULL"):
        day = _day(d["closed_at"])
        if day and start <= day <= end and d["closed_how"] != "auto":   # sent automatically: never reviewed
            groups.setdefault(d["folder"], []).append(d)
    return [{"folder": f, "name": names.get(f, f), "drafts": len(ds),
             "unchanged": sum(AC.unchanged(d) for d in ds),
             "edited": sum(d["closed_how"] == "sent" and d["edited"] == 1 for d in ds),
             "rewritten": sum(d["closed_how"] == "sent" and (d["rewrites"] or 0) > 0 and not d["edited"] for d in ds),
             "handed_off": sum(d["closed_how"] == "person" for d in ds)} for f, ds in sorted(groups.items())]


def _cost(state, cfg: dict, start: date, end: date) -> dict:
    pricing = cfg.get("pricing", {})
    rows = [r for r in _rows(state, "SELECT * FROM ai_usage") if (d := _day(r["at"])) and start <= d <= end]
    euros, unknown = L.cost_eur(rows, pricing)
    monthly = []
    for month in _months(start, end):
        eur, _ = L.cost_eur([r for r in rows if _day(r["at"]).strftime("%Y-%m") == month], pricing)
        monthly.append({"month": month, "eur": round(eur, 2)})
    return {"period_eur": round(euros, 2), "monthly": monthly, "unknown_models": unknown}


def report(cfg: dict, state, start: date, end: date, now: datetime) -> dict:
    L.init(state)
    DR.init(state)
    if start > end:
        start, end = end, start
    replies = _rows(state, "SELECT * FROM replies")
    for r in replies:
        r["received_day"], r["answered_day"] = _day(r["received_at"]), _day(r["answered_at"])
    got = [r for r in replies if r["received_day"] and start <= r["received_day"] <= end]
    done = [r for r in replies if r["answered_day"] and start <= r["answered_day"] <= end]
    waiting = _waiting(state)
    open_ages = [(now - t).total_seconds() / 3600 for r in replies if r["message_id"] in waiting
                 if (t := _local(r["received_at"]))]
    since = state.db.execute("SELECT value FROM meta WHERE key='ledger_since'").fetchone()
    names = RT.folder_names(cfg)
    folders = Counter(r["folder"] for r in got)
    return {
        "complete_since": since[0] if since else "",
        "overview": {"received": len(got), "answered": len(done),
                     "waiting_person": state.db.execute(
                         "SELECT COUNT(*) FROM handoffs WHERE answered_at IS NULL").fetchone()[0],
                     "oldest_waiting_hours": round(max(open_ages), 1) if open_ages else None},
        "per_day": [{"day": (d := start + timedelta(days=i)).isoformat(),
                     "received": sum(r["received_day"] == d for r in got),
                     "answered": sum(r["answered_day"] == d for r in done)}
                    for i in range((end - start).days + 1)],
        "reply_time": _reply_time(got, now, cfg.get("overdue_days", 2), waiting),
        "categories": [{"folder": f, "name": names.get(f, f), "n": n}
                       for f, n in sorted(folders.items(), key=lambda x: (-x[1], x[0]))],
        "quality": _quality(state, cfg, start, end),
        "cost": _cost(state, cfg, start, end),
    }

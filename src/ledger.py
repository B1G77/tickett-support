#!/usr/bin/env python3
"""Reply record: per customer email, when it came in and when it was first answered.

Kept in router.db next to the router's own tables, for the dashboard's admin numbers:
  replies    one row per customer email: folder, received_at, answered_at, how, event
  mail_text  its subject, sender and text, for search
  note_received  the router calls it after filing a message (first sight wins)
  note_answered  a reply was sent: dashboard, auto, mail (sent by hand) or respondido
  backfill       once: fill both tables from what decisions, drafts and handoffs already hold
  ai_usage       one row per Claude call (classify, draft, handoff, rewrite) with its tokens, for the cost page
  note_usage     the router, drafts and actions call it after a model call; cost_eur prices the rows
  backfill_usage once: the tokens drafts already hold, as draft rows
Only customer mail filed in COUNTED folders is recorded (not folder 8, not the answered folder).
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone

import drafts as DR
import events_doc as ED
import triage as T

COUNTED = ("F1", "F2", "F3", "F4", "F5", "F6", "F7", "F9", "P")
MAX_TEXT = 20000
SENDER_DAYS = 7   # replies.from_email: no longer needed after a week
SKIP_WORDS = {"dr", "dra", "the", "and", "de", "do", "da", "dos", "das", "e", "o", "a"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _undo(state) -> None:
    """After a failed write: roll back, so a half-written change does not hold router.db's write lock."""
    try:
        state.db.rollback()
    except Exception:
        pass


def init(state) -> None:
    state.db.execute("""CREATE TABLE IF NOT EXISTS replies (
        message_id TEXT PRIMARY KEY, folder TEXT, received_at TEXT, answered_at TEXT, how TEXT,
        event TEXT, from_email TEXT)""")
    state.db.execute("""CREATE TABLE IF NOT EXISTS mail_text (
        message_id TEXT PRIMARY KEY, subject TEXT, from_name TEXT, from_email TEXT, received_at TEXT,
        folder TEXT, text TEXT)""")
    state.db.execute("""CREATE TABLE IF NOT EXISTS ai_usage (
        at TEXT, kind TEXT, model TEXT, tokens_in INT, tokens_out INT, cache_read INT, cache_write INT)""")
    state.db.execute("CREATE TABLE IF NOT EXISTS doc_changes (at TEXT, kind TEXT, name TEXT)")
    state.db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    state.db.execute("INSERT OR IGNORE INTO meta VALUES ('ledger_since', ?)", (_now()[:10],))
    state.db.commit()


def _plain(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _plain(s))


def match_event(text: str, names: list[str]) -> str:
    """The first event (in Doc order) whose first two significant words are both in the text."""
    have = set(_words(text))
    for name in names:
        key = [w for w in _words(re.sub(r"\([^)]*\)", " ", name)) if w not in SKIP_WORDS and len(w) >= 3][:2]
        if key and all(w in have for w in key):
            return name
    return ""


def event_names() -> list[str]:
    """Event names from the Doc; [] for a missing or malformed copy."""
    try:
        return [e["name"] for e in ED.load()["events"] if isinstance(e, dict) and isinstance(e.get("name"), str)
                and e["name"]]
    except Exception:
        return []


def note_received(state, mid: str, rec: dict, folder: str, events: list[str] | None = None, log=None) -> None:
    """Never raises: the record is bookkeeping and must not stop the filing."""
    try:
        _received(state, mid, rec, folder, events)
    except Exception as exc:
        _undo(state)
        if log:
            log(mid, f"ledger not updated ({type(exc).__name__})")


def _received(state, mid: str, rec: dict, folder: str, events: list[str] | None) -> None:
    if folder not in COUNTED or T.classify(rec)[0] != "customer":
        return
    init(state)
    text = (rec.get("body_text") or "")[:MAX_TEXT]
    subject = rec.get("subject", "")
    now = _now()
    state.db.execute("INSERT OR IGNORE INTO replies (message_id, folder, received_at, event, from_email) "
                     "VALUES (?,?,?,?,?)",
                     (mid, folder, now, match_event(subject + "\n" + text, events or []), rec.get("from_email", "")))
    state.db.execute("INSERT OR IGNORE INTO mail_text VALUES (?,?,?,?,?,?,?)",
                     (mid, subject, rec.get("from_name", ""), rec.get("from_email", ""), now, folder, text))
    state.db.commit()


def note_answered(state, mid: str, how: str, log=None) -> None:
    """Never raises, like note_received."""
    try:
        init(state)
        state.db.execute("UPDATE replies SET answered_at=?, how=? WHERE message_id=? AND answered_at IS NULL",
                         (_now(), how, mid))
        state.db.commit()
    except Exception as exc:
        _undo(state)
        if log:
            log(mid, f"ledger not updated ({type(exc).__name__})")


def backfill(state) -> int:
    """Once: rows for mail the router filed before the ledger existed. Returns how many were added."""
    init(state)
    DR.init(state)   # the drafts and handoffs tables
    if state.db.execute("SELECT 1 FROM meta WHERE key='ledger_backfilled'").fetchone():
        return 0
    marks = ",".join("?" * len(COUNTED))
    cur = state.db.execute(f"""INSERT OR IGNORE INTO replies (message_id, folder, received_at)
        SELECT message_id, folder, decided_at FROM decisions
        WHERE folder IN ({marks}) AND decided_at IS NOT NULL""", COUNTED)
    added = cur.rowcount
    state.db.execute("""UPDATE replies SET
        answered_at=(SELECT closed_at FROM drafts WHERE drafts.message_id=replies.message_id),
        how=(SELECT CASE closed_how WHEN 'sent' THEN 'dashboard' ELSE 'mail' END FROM drafts
             WHERE drafts.message_id=replies.message_id)
        WHERE answered_at IS NULL AND EXISTS (SELECT 1 FROM drafts WHERE drafts.message_id=replies.message_id
            AND closed_how IN ('sent','answered') AND closed_at IS NOT NULL)""")
    state.db.execute("""UPDATE replies SET
        answered_at=(SELECT answered_at FROM handoffs WHERE handoffs.message_id=replies.message_id), how='mail'
        WHERE answered_at IS NULL AND EXISTS (SELECT 1 FROM handoffs WHERE handoffs.message_id=replies.message_id
            AND answered_at IS NOT NULL)""")
    first = state.db.execute("SELECT MIN(received_at) FROM replies").fetchone()[0]
    if first:
        state.db.execute("INSERT OR REPLACE INTO meta VALUES ('ledger_since', ?)", (first[:10],))
    state.db.execute("INSERT OR REPLACE INTO meta VALUES ('ledger_backfilled', ?)", (_now(),))
    state.db.commit()
    return added


def note_usage(state, kind: str, model: str, usage: dict, log=None) -> None:
    """Never raises, like note_received. usage keys: in, out, cache_read, cache_write (missing = 0)."""
    try:
        init(state)
        u = usage or {}
        state.db.execute("INSERT INTO ai_usage VALUES (?,?,?,?,?,?,?)",
                         (_now(), kind, model or "", u.get("in", 0), u.get("out", 0), u.get("cache_read", 0),
                          u.get("cache_write", 0)))
        state.db.commit()
    except Exception as exc:
        _undo(state)
        if log:
            log(kind, f"usage not recorded ({type(exc).__name__})")


def cost_eur(rows, pricing: dict) -> tuple[float, list[str]]:
    """Euros for rows of model, tokens_in, tokens_out, cache_read, cache_write, and the sorted model
    names without a price (those cost 0)."""
    prices = pricing.get("per_million_usd", {})
    usd = 0.0
    unknown = set()
    for r in rows:
        p = prices.get(r["model"])
        if p is None:
            unknown.add(r["model"])
            continue
        usd += sum((r[col] or 0) * p[key] for col, key in (("tokens_in", "in"), ("tokens_out", "out"),
                                                          ("cache_read", "cache_read"),
                                                          ("cache_write", "cache_write"))) / 1_000_000
    return usd * pricing.get("usd_to_eur", 1.0), sorted(unknown)


def backfill_usage(state) -> int:
    """Once: the tokens already stored on drafts, as draft rows of the model used until now. Drafts made
    after note_usage began (rows already in ai_usage) are not copied again."""
    init(state)
    DR.init(state)
    if state.db.execute("SELECT 1 FROM meta WHERE key='usage_backfilled'").fetchone():
        return 0
    cur = state.db.execute("""INSERT INTO ai_usage SELECT created_at, 'draft', 'claude-opus-5',
        COALESCE(tokens_in,0), COALESCE(tokens_out,0), COALESCE(cache_read,0), COALESCE(cache_write,0)
        FROM drafts WHERE created_at < COALESCE((SELECT MIN(at) FROM ai_usage), '9')""")
    state.db.execute("INSERT OR REPLACE INTO meta VALUES ('usage_backfilled', ?)", (_now(),))
    state.db.commit()
    return cur.rowcount


def record_doc_changes(state, changes: list[dict], at: str) -> None:
    init(state)
    state.db.executemany("INSERT INTO doc_changes VALUES (?,?,?)", [(at, c["kind"], c["name"]) for c in changes])
    state.db.commit()


def purge_text(state, days: int, now: datetime) -> int:
    """Clears the customer text of drafts and hand-offs closed (or, if open, created) more than `days` ago and
    deletes the stored mail older than that, and the sender of replies older than a week; counts, times and
    tokens stay. Returns the rows touched."""
    init(state)
    DR.init(state)
    cutoff = (now - timedelta(days=days)).astimezone(timezone.utc).isoformat(timespec="seconds")
    n = state.db.execute("""UPDATE drafts SET full_text=NULL, newest_text=NULL, body=NULL, first_body=NULL
        WHERE COALESCE(closed_at, created_at) < ? AND (full_text IS NOT NULL OR newest_text IS NOT NULL
        OR body IS NOT NULL OR first_body IS NOT NULL)""", (cutoff,)).rowcount
    n += state.db.execute("""UPDATE handoffs SET full_text=NULL, newest_text=NULL
        WHERE COALESCE(answered_at, created_at) < ? AND (full_text IS NOT NULL OR newest_text IS NOT NULL)""",
                          (cutoff,)).rowcount
    n += state.db.execute("DELETE FROM mail_text WHERE received_at < ?", (cutoff,)).rowcount
    week = (now - timedelta(days=SENDER_DAYS)).astimezone(timezone.utc).isoformat(timespec="seconds")
    n += state.db.execute("UPDATE replies SET from_email=NULL WHERE received_at < ? AND from_email IS NOT NULL",
                          (week,)).rowcount
    state.db.commit()
    return n


def after_cycle(state, cfg: dict, log) -> None:
    """The once-only backfills, then the text purge at most once a (local) day. Never raises."""
    try:
        backfill(state)
        backfill_usage(state)
        day = datetime.now().astimezone().date().isoformat()
        row = state.db.execute("SELECT value FROM meta WHERE key='purge_day'").fetchone()
        if row and row[0] == day:
            return
        state.db.execute("INSERT OR REPLACE INTO meta VALUES ('purge_day', ?)", (day,))
        state.db.commit()
        days = cfg.get("text_retention_days", 90)
        n = purge_text(state, days, datetime.now(timezone.utc))
        if n:
            log("ledger", f"text older than {days} days cleared: {n}")
    except Exception as exc:
        _undo(state)
        log("ledger", f"cycle bookkeeping failed ({type(exc).__name__})")

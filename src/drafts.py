#!/usr/bin/env python3
"""Stage 8c - drafted replies: which mail gets one, where it goes, how it did.

Run by the router when config drafts.enabled is true:
  maybe_crawl   once a day, refresh the lebillet.eu facts (site_facts.crawl)
  try_draft     after a message is filed in folders 1-7, write a reply into Drafts
  note_sent     when a reply in Sent answers a drafted message, store how much of
                the draft was kept (1.0 = sent unchanged)
  report        router.py --drafts
A draft that cannot be written is logged and skipped; filing never waits for it.
"""
from __future__ import annotations

import difflib
import imaplib
import statistics
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import cases as K
import drafter as D
import events_doc as ED
import extract as E
import site_facts as SF

DRAFT_FOLDERS = ("F1", "F2", "F3", "F4", "F5", "F6", "F7")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# The customer's email is kept next to each draft and hand-off so the dashboard can show it
# without asking the mail server. router.db stays in the private data folder.
EMAIL_COLS = ("from_name", "from_email", "reply_to", "received_at", "newest_text", "full_text")
MAX_FULL = 20000


def _add_columns(state, table: str, cols: tuple[str, ...]) -> None:
    """Columns missing from an older table; a bare name is TEXT, "name INT DEFAULT 0" gives its own type."""
    have = {r[1] for r in state.db.execute(f"PRAGMA table_info({table})")}
    for c in cols:
        if c.split()[0] not in have:
            state.db.execute(f"ALTER TABLE {table} ADD COLUMN {c if ' ' in c else c + ' TEXT'}")


def init(state) -> None:
    state.db.execute("""CREATE TABLE IF NOT EXISTS drafts (
        message_id TEXT PRIMARY KEY, draft_mid TEXT, folder TEXT, rulebook_sha TEXT, note TEXT,
        body TEXT, subject TEXT, created_at TEXT, sent_at TEXT, kept REAL, tokens_in INT, tokens_out INT,
        cache_read INT, cache_write INT)""")
    _add_columns(state, "drafts", EMAIL_COLS + ("closed_at", "closed_how", "first_body", "rewrites INT DEFAULT 0",
                                         "edited INT DEFAULT 0", "auto_after"))
    # rows from before first_body (not rows whose text was purged: those stay as they are)
    if state.db.execute("SELECT 1 FROM drafts WHERE first_body IS NULL AND body IS NOT NULL").fetchone():
        state.db.execute("UPDATE drafts SET first_body=body WHERE first_body IS NULL AND body IS NOT NULL")
        state.db.commit()
    state.db.execute("""CREATE TABLE IF NOT EXISTS handoffs (message_id TEXT PRIMARY KEY, reason TEXT, note TEXT,
        created_at TEXT)""")
    _add_columns(state, "handoffs", EMAIL_COLS + ("subject", "answered_at"))


def email_fields(rec: dict) -> dict:
    full = rec.get("body_text", "") or ""
    return {"from_name": rec.get("from_name", ""), "from_email": rec.get("from_email", ""),
            "reply_to": rec.get("reply_to", ""), "received_at": rec.get("date", ""),
            "newest_text": K.strip_quotes(full, K.is_forward(rec.get("subject", ""), full)).strip(),
            "full_text": full[:MAX_FULL]}


def mark_answered(state, mid: str) -> None:
    """The customer got a reply sent by hand: the hand-off is done and an open draft is closed."""
    init(state)
    state.db.execute("UPDATE handoffs SET answered_at=? WHERE message_id=? AND answered_at IS NULL", (_now(), mid))
    state.db.execute("UPDATE drafts SET closed_at=?, closed_how='answered' WHERE message_id=? AND closed_at IS NULL",
                     (_now(), mid))
    state.db.commit()


def relayed(d, reason: str) -> bool:
    """Customer mail that came through a partner or the website form: a reply would go to the
    partner or the form mailer, so a person answers the customer directly."""
    return d.folder in DRAFT_FOLDERS and (reason.startswith("website contact form")
                                          or reason.startswith("forwarded to support"))


def has_draft(state, mid: str) -> bool:
    init(state)
    return state.db.execute("SELECT 1 FROM drafts WHERE message_id=?", (mid,)).fetchone() is not None


def qualifies(d, bucket: str, state, mid: str, reason: str = "") -> bool:
    """False for the website contact form and partner-forward buckets: the From address there
    is the form mailer or the partner, not the customer a reply would be addressed to."""
    if reason.startswith("website contact form") or reason.startswith("forwarded to support"):
        return False
    return d.folder in DRAFT_FOLDERS and not d.urgent and bucket == "customer" and not has_draft(state, mid)


def draft_case(rec: dict, redactor) -> dict | None:
    """The whole body, quoted history included, redacted; None when the leak check fails."""
    subject, text, _, leaks = redactor.redact(rec.get("subject", ""), rec.get("body_text", "")[:D.MAX_TEXT],
                                              [rec.get("from_name", "")], [rec.get("from_email", "")])
    return None if leaks else {"subject": subject, "text": text, "language": rec.get("lang_guess", "")}


def prepare(rec: dict, mid: str, d, drafter, redactor, cfg: dict, log) -> dict | None:
    """Claude's answer for one email (a reply or a hand-off), or None when there is none to use.
    Never raises: a missing draft must never hold up the filing."""
    try:
        case = draft_case(rec, redactor)
        if case is None:
            log(mid, "no draft - leak check")
            return None
        out = drafter.draft(case, cfg["folders"][d.folder], rec.get("subject", "") + "\n" + rec.get("body_text", ""),
                            date.today())
        if out is None:
            log(mid, "no draft - model answer unusable")
        return out
    except Exception as exc:
        log(mid, f"no draft ({type(exc).__name__})")
        return None


def _usage(state, kind: str, out: dict, model: str, log=None) -> None:
    """Record the model call behind out (ledger imports this module, so it is imported here)."""
    import ledger as LG
    LG.note_usage(state, kind, out.get("model") or model, out.get("usage", {}), log)


def note_handoff(state, mid: str, out: dict, log, rec: dict | None = None) -> None:
    """A hand-off to a person: why, what the team needs to know, and the customer's email."""
    init(state)
    note = (out.get("action_note") or "").strip()
    e = email_fields(rec) if rec else dict.fromkeys(EMAIL_COLS)
    state.db.execute("INSERT OR REPLACE INTO handoffs (message_id, reason, note, created_at, subject, "
                     f"{', '.join(EMAIL_COLS)}) VALUES ({','.join('?' * (5 + len(EMAIL_COLS)))})",
                     (mid, out.get("handoff_reason", ""), note, _now(), (rec or {}).get("subject"),
                      *[e[c] for c in EMAIL_COLS]))
    state.db.commit()
    if "usage" in out:   # a hand-off made without a model call (legal mail) has none
        _usage(state, "handoff", out, D.MODEL, log)
    log(mid, f"hand-off ({out.get('handoff_reason', '')})" + (f" - nota: {note}" if note else ""))


def save(box, drafts_folder: str, rec: dict, mid: str, d, state, out: dict, drafter, cfg: dict, log) -> bool:
    """Write the reply into Drafts, inside the conversation, and remember it."""
    try:
        text, body, note = D.compose(out, D.first_name(rec.get("from_name", "")))
        raw, draft_mid = D.reply_message(rec, text, cfg["imap"]["user"])
        box.append(drafts_folder, "(\\Draft)", imaplib.Time2Internaldate(time.time()), raw)
        init(state)
        u = out.get("usage", {})
        e = email_fields(rec)
        state.db.execute("INSERT OR REPLACE INTO drafts (message_id, draft_mid, folder, rulebook_sha, note, body, "
                         "subject, created_at, tokens_in, tokens_out, cache_read, cache_write, first_body, "
                         f"{', '.join(EMAIL_COLS)}) VALUES ({','.join('?' * (13 + len(EMAIL_COLS)))})",
                         (mid, draft_mid, d.folder, drafter.sha, note, body, rec.get("subject", ""), _now(),
                          u.get("in", 0), u.get("out", 0), u.get("cache_read", 0), u.get("cache_write", 0), body,
                          *[e[c] for c in EMAIL_COLS]))
        state.db.commit()
        _usage(state, "draft", out, cfg.get("model", D.MODEL), log)
        log(mid, "draft saved" + (f" - nota: {note}" if note else ""))
        return True
    except Exception as exc:
        log(mid, f"no draft ({type(exc).__name__})")
        return False


def make_draft(box, drafts_folder: str, rec: dict, mid: str, d, state, drafter, redactor, cfg: dict, log) -> bool:
    """Prepare and save in one go (draft_backlog.py); a hand-off is noted, not drafted."""
    out = prepare(rec, mid, d, drafter, redactor, cfg, log)
    if out is None:
        return False
    if out.get("handoff"):
        note_handoff(state, mid, out, log, rec)
        return False
    return save(box, drafts_folder, rec, mid, d, state, out, drafter, cfg, log)


def try_draft(box, drafts_folder: str, rec: dict, mid: str, d, state, drafter, redactor, cfg: dict, log) -> bool:
    try:
        return make_draft(box, drafts_folder, rec, mid, d, state, drafter, redactor, cfg, log)
    except Exception as exc:   # a missing draft must never hold up the filing
        log(mid, f"no draft ({type(exc).__name__})")
        return False


def _norm(text: str) -> str:
    return " ".join(K.strip_quotes(text).split())


def similarity(draft: str, sent: str) -> float:
    return round(difflib.SequenceMatcher(None, _norm(draft), _norm(sent)).ratio(), 3)


def note_sent(state, in_reply_to: str, references: list[str], fetch_raw, log) -> None:
    """Score a sent reply against the draft for the message it directly answers, and close
    (without scoring) any other open draft superseded by this reply, per References."""
    init(state)
    row = state.db.execute("SELECT body FROM drafts WHERE message_id=? AND sent_at IS NULL",
                           (in_reply_to,)).fetchone() if in_reply_to else None
    if row:
        try:
            sent = E.record_from_bytes(fetch_raw(), "imap:sent").get("body_text", "")
            if D.NOTE.strip()[:14] in sent:
                log(in_reply_to, "WARNING: a reply was sent with the NOTA INTERNA still in it")
            kept = similarity(row[0], sent)
            state.db.execute("UPDATE drafts SET sent_at=?, kept=? WHERE message_id=?", (_now(), kept, in_reply_to))
            state.db.commit()
            log(in_reply_to, f"reply sent, {kept:.0%} of the draft kept")
        except Exception as exc:   # measuring a draft must never stop the answered-mail pass
            log(in_reply_to, f"no measurement ({type(exc).__name__})")
    others = [r for r in references if r and r != in_reply_to]
    if not others:
        return
    q = ",".join("?" * len(others))
    superseded = state.db.execute(f"SELECT message_id FROM drafts WHERE sent_at IS NULL AND "
                                  f"message_id IN ({q})", others).fetchall()
    for (mid,) in superseded:
        state.db.execute("UPDATE drafts SET sent_at=? WHERE message_id=?", (_now(), mid))
        log(mid, "draft superseded by a later reply")
    if superseded:
        state.db.commit()


def open_notes(state, days: int = 14) -> list[tuple[str, str]]:
    init(state)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return state.db.execute("SELECT subject, note FROM drafts WHERE sent_at IS NULL AND note != '' "
                            "AND created_at >= ? ORDER BY created_at", (cutoff,)).fetchall()


def format_notes(rows: list[tuple[str, str]]) -> str:
    if not rows:
        return ""
    return "\n".join([f"Rascunhos com nota interna ({len(rows)})"] + [f"  - {s}: {n}" for s, n in rows])


def report(state) -> str:
    init(state)
    rows = state.db.execute("SELECT folder, kept, note, tokens_in, tokens_out, cache_read, cache_write "
                            "FROM drafts").fetchall()
    lines = ["folder  drafted  sent  median kept  kept >= 95%"]
    for f in sorted({r[0] for r in rows}):
        mine = [r for r in rows if r[0] == f]
        kept = [r[1] for r in mine if r[1] is not None]
        med = f"{statistics.median(kept):.0%}" if kept else "-"
        high = f"{sum(k >= 0.95 for k in kept) / len(kept):.0%}" if kept else "-"
        lines.append(f"{f:<6}  drafted {len(mine):<3}  sent {len(kept):<3}  {med:>6}  {high:>6}")
    notes = Counter(r[2][:60] for r in rows if r[2])
    if notes:
        lines += ["", "most frequent notes:"] + [f"  {n:>3}  {t}" for t, n in notes.most_common(5)]
    lines += ["", f"tokens: {sum(r[3] or 0 for r in rows)} in, {sum(r[5] or 0 for r in rows)} cached read, "
                 f"{sum(r[6] or 0 for r in rows)} cache write, {sum(r[4] or 0 for r in rows)} out"]
    return "\n".join(lines)


def maybe_refresh_doc(state, log, now: datetime | None = None, refresh=None, every_minutes: int = 15,
                      load=None) -> None:
    """Re-read the events document every quarter of an hour; a failure keeps the last good copy."""
    now = now or datetime.now(timezone.utc)
    row = state.db.execute("SELECT value FROM meta WHERE key='doc_at'").fetchone()
    if row and now - datetime.fromisoformat(row[0]) < timedelta(minutes=every_minutes):
        return
    state.db.execute("INSERT OR REPLACE INTO meta VALUES ('doc_at', ?)", (now.isoformat(timespec="seconds"),))
    state.db.commit()
    old = (load or ED.load)()
    try:
        new = (refresh or ED.refresh)()
    except Exception as exc:
        log("doc", f"events document not read, keeping the last copy ({type(exc).__name__})")
        return
    try:
        import ledger as LG   # ledger imports this module
        LG.record_doc_changes(state, ED.diff(old, new), now.isoformat(timespec="seconds"))
    except Exception as exc:
        log("doc", f"document changes not recorded ({type(exc).__name__})")


def maybe_crawl(state, log, today: date | None = None, crawl=SF.crawl) -> None:
    day = (today or date.today()).isoformat()
    row = state.db.execute("SELECT value FROM meta WHERE key='crawl_day'").fetchone()
    if row and row[0] == day:
        return
    state.db.execute("INSERT OR REPLACE INTO meta VALUES ('crawl_day', ?)", (day,))   # one try a day
    state.db.commit()
    try:
        log("site", f"tickett.com crawled: {crawl()} events listed")
    except Exception as exc:
        log("site", f"crawl failed, keeping the previous facts ({type(exc).__name__})")

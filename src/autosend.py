#!/usr/bin/env python3
"""Automatic sending: a fresh draft in a category the admin switched on goes out without a person.

Off by default, and never for refunds and cancellations (F1), partners (F7) or mail to review (F9).
The router calls maybe_send right after a draft is saved; anything doubtful is not sent and stays a
draft for a person, with the reason in the router log:
  settings / save_settings   meta key autosend: enabled, daily_limit (0-200), categories (F2-F6),
                             delay_minutes (one of DELAYS, counted from when the router first saw the email)
  why_not                    "" when a draft may go out, else the first reason it may not
  send_due                   each router cycle: the drafts whose wait is over, checked again and sent
  status                     the admin page: settings, automatic sends today, 30-day readiness
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import drafts as DR
import ledger as LG
from imap_util import Mailbox

NEVER = ("F1", "F7", "F9")
ALLOWED = ("F2", "F3", "F4", "F5", "F6")
DEFAULT = {"enabled": False, "daily_limit": 20, "categories": [], "delay_minutes": 0}
DELAYS = (0, 15, 30, 60, 120, 180, 240, 360, 480, 720, 1440)
MAX_LIMIT = 200
READINESS_DAYS = 30
# the host part of a link, as a browser reads it: slashes or backslashes after the scheme are skipped and
# a backslash ends the host, so "evil.com\@tickett.com" is evil.com; an empty host is refused
_LINK = re.compile(r"(?i)(?:https?:[/\\]*|\b(?=www\.))([^\s/\\?#<>\"'()\[\]]*)")
_OURS = re.compile(r"(?:[a-z0-9-]+\.)*tickett\.com")
# a bare domain ("evil.com/x", which mail apps turn into a link); not part of an email address
_BARE = re.compile(r"(?i)(?<![@\w.-])([a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,})(?![\w-])(?![\w.-]*@)")
_SCHEME_LINK = re.compile(r"(?i)https?:\S*")


def _clean(s) -> dict:
    if not isinstance(s, dict):
        raise ValueError("Pedido inválido.")
    enabled, limit, cats = s.get("enabled", False), s.get("daily_limit", 20), s.get("categories", [])
    if not isinstance(enabled, bool):
        raise ValueError("Pedido inválido.")
    if type(limit) is not int or not 0 <= limit <= MAX_LIMIT:
        raise ValueError(f"O limite diário tem de ser um número de 0 a {MAX_LIMIT}.")
    if not isinstance(cats, list) or any(c not in ALLOWED for c in cats):
        raise ValueError("Categoria não permitida.")
    delay = s.get("delay_minutes", 0)
    if type(delay) is not int or delay not in DELAYS:
        raise ValueError("Atraso não permitido.")
    return {"enabled": enabled, "daily_limit": limit, "categories": sorted(set(cats)), "delay_minutes": delay}


def settings(state) -> dict:
    """The saved settings; anything unreadable or not allowed reads as all off."""
    row = state.db.execute("SELECT value FROM meta WHERE key='autosend'").fetchone()
    try:
        return _clean(json.loads(row[0])) if row else dict(DEFAULT)
    except (ValueError, TypeError):
        return dict(DEFAULT)


def save_settings(state, s: dict) -> dict:
    """Raises ValueError (in Portuguese, for the page) and saves nothing when `s` is not allowed."""
    clean = _clean(s)
    state.db.execute("INSERT OR REPLACE INTO meta VALUES ('autosend', ?)", (json.dumps(clean),))
    state.db.commit()
    return clean


def _auto_today(state) -> list[str]:
    """The senders (lower case) answered automatically on today's local date, one per send: the drafts
    claimed for automatic sending, which are closed before the mail leaves."""
    DR.init(state)
    today = datetime.now().astimezone().date()
    out = []
    for closed_at, sender in state.db.execute("SELECT closed_at, lower(from_email) FROM drafts "
                                              "WHERE closed_how='auto'"):
        try:
            if datetime.fromisoformat(closed_at).astimezone().date() == today:
                out.append((sender or "").lower())
        except (TypeError, ValueError):
            continue
    return out


def sent_today(state) -> int:
    return len(_auto_today(state))


def sender_today(state, email: str) -> bool:
    return (email or "").lower() in _auto_today(state)


def _foreign_link(body: str) -> bool:
    for m in _LINK.finditer(body):
        host = m.group(1)
        if "@" in host:
            return True
        host = host.split(":")[0].rstrip(".,;:!").lower()
        if not _OURS.fullmatch(host):
            return True
    return any(not _OURS.fullmatch(m.group(1).lower()) for m in _BARE.finditer(_SCHEME_LINK.sub(" ", body)))


def why_not(rec: dict, row: dict, d, s: dict, sent_today: int, sender_today: bool) -> str:
    """"" when the draft may be sent automatically, else the first reason not (English, for the log)."""
    import actions as AC   # actions imports the router, which imports this module
    body = row.get("body") or ""
    if not s.get("enabled"):
        return "automatic sending is off"
    if d.folder in NEVER or d.folder not in ALLOWED or d.folder not in s.get("categories", []):
        return "category not switched on"
    if d.urgent:
        return "urgent"
    if not AC.reply_to_ok(rec) or AC.recipient(rec).lower() != (rec.get("from_email") or "").lower():
        return "Reply-To differs from the sender"
    if (rec.get("auto_submitted") or "").strip().lower() not in ("", "no"):
        return "auto-submitted mail"
    if (rec.get("precedence") or "").strip().lower() in ("bulk", "list", "junk"):
        return "bulk or list mail"
    if rec.get("list_unsubscribe"):
        return "mailing-list mail"
    if AC.sendable(body):
        return "draft not sendable"
    if (row.get("note") or "").strip():   # needs action, missing facts, low confidence: a person reads it
        return "draft has an internal note"
    if _foreign_link(body):
        return "link to a site other than tickett.com"
    if sent_today >= s.get("daily_limit", 0):
        return "daily limit reached"
    if sender_today:
        return "sender already answered automatically today"
    return ""


def _open_draft(state, mid: str) -> dict | None:
    cur = state.db.execute("SELECT * FROM drafts WHERE message_id=? AND closed_at IS NULL AND sent_at IS NULL", (mid,))
    r = cur.fetchone()
    return dict(zip([c[0] for c in cur.description], r)) if r else None


def _utc(iso: str) -> datetime:
    t = datetime.fromisoformat(iso)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _seen_at(state, row: dict) -> datetime:
    """When the router first saw the email: its decision, else the ledger, else the draft; never its Date header."""
    LG.init(state)
    for sql in ("SELECT decided_at FROM decisions WHERE message_id=?", "SELECT received_at FROM replies WHERE message_id=?"):
        r = state.db.execute(sql, (row["message_id"],)).fetchone()
        if r and r[0]:
            return _utc(r[0])
    return _utc(row["created_at"])


def maybe_send(cfg, state, box, rec: dict, mid: str, d, log, send_mail=None) -> bool:
    """Send the draft just saved for `mid` when everything allows it. Never raises. "Sent" means this call
    claimed the draft and SMTP accepted the mail. The router's loop goes on in the Inbox, so the Inbox is
    selected again after any mailbox work."""
    sent = False
    try:
        s = settings(state)
        if not s["enabled"] or d.folder not in s["categories"]:
            return False
        row = _open_draft(state, mid)
        if row is None:
            log(mid, "not sent automatically: no open draft")
            return False
        sender = (rec.get("from_email") or "").lower()
        today = _auto_today(state)
        why = why_not(rec, row, d, s, len(today), sender in today)
        if why:
            log(mid, f"not sent automatically: {why}")
            return False
        wait = s.get("delay_minutes", 0)
        at = _seen_at(state, row) + timedelta(minutes=wait) if wait else None
        if at and at > datetime.now(timezone.utc):
            state.db.execute("UPDATE drafts SET auto_after=? WHERE message_id=? AND created_at=? AND closed_at IS NULL "
                             "AND sent_at IS NULL", (at.isoformat(timespec="seconds"), mid, row["created_at"]))
            state.db.commit()
            log(mid, f"automatic send scheduled for {at.astimezone():%H:%M}")
            return False
        import actions as AC
        try:
            try:
                raw = AC.claim_and_send(cfg, state, row, rec, "auto", send_mail or AC.smtp_send)
            except AC.Stale:
                log(mid, "not sent automatically: draft already handled")
                return False
            sent = True   # closed as auto before it left, so it counts toward the limits
            AC.after_send(state, box, row, raw, log, "auto")
        finally:
            box.select(Mailbox.INBOX)
        log(mid, "sent automatically")
        return True
    except Exception as exc:
        if sent:
            LG.note_answered(state, mid, "auto", log)
        log(mid, ("sent automatically, then " if sent else "not sent automatically: ") + f"error ({type(exc).__name__})")
        return sent


def _send_one(cfg, state, box, folders: dict, row: dict, log, send_mail) -> int:
    """One draft whose wait is over: every check again, then the same claim-then-send as maybe_send."""
    import actions as AC
    import router as RT
    mid, claiming, sent = row["message_id"], False, False
    try:
        rec, _ = AC._original(box, folders, row)
        dec = state.db.execute("SELECT urgent FROM decisions WHERE message_id=?", (mid,)).fetchone()
        why = "email no longer in its folder" if rec is None else "no routing decision" if dec is None else ""
        if not why:
            d = RT.Decision(row["folder"], bool(dec[0]), "", "", "")
            today = _auto_today(state)
            why = why_not(rec, row, d, settings(state), len(today), (rec.get("from_email") or "").lower() in today)
        if why:
            AC._stop_auto(state, mid)   # the draft now waits for a person
            log(mid, f"not sent automatically: {why}")
            return 0
        claiming = True
        try:
            raw = AC.claim_and_send(cfg, state, row, rec, "auto", send_mail, scheduled=True)
        except AC.Stale:   # a person sent, edited, rewrote or took it meanwhile
            AC._stop_auto(state, mid)
            log(mid, "not sent automatically: draft already handled")
            return 0
        sent = True
        AC.after_send(state, box, row, raw, log, "auto")
        log(mid, "sent automatically")
        return 1
    except Exception as exc:
        err = f"error ({type(exc).__name__})"
        if not claiming:   # nothing was claimed: the schedule stays and the next cycle tries again
            log(mid, f"automatic send delayed: {err}, trying again next cycle")
            return 0
        try:
            if sent:
                LG.note_answered(state, mid, "auto", log)
            else:
                AC._stop_auto(state, mid)   # a failed send: a person checks Sent and decides
        finally:
            log(mid, ("sent automatically, then " if sent else "not sent automatically: ") + err)
        return int(sent)


def send_due(cfg, state, box, log, now=None, send_mail=None) -> int:
    """Each router cycle: send the scheduled drafts whose wait is over when everything still allows it; a draft
    refused now waits for a person. The wait is counted again with the current delay, so a changed delay moves
    drafts already scheduled. Returns how many went out. Never raises; the Inbox is selected after."""
    import actions as AC
    import router as RT
    sent = 0
    try:
        DR.init(state)
        now = now or datetime.now(timezone.utc)
        cur = state.db.execute("SELECT * FROM drafts WHERE closed_at IS NULL AND sent_at IS NULL AND auto_after IS NOT NULL")
        cols = [c[0] for c in cur.description]
        wait = timedelta(minutes=settings(state).get("delay_minutes", 0))
        rows = []
        for r in (dict(zip(cols, v)) for v in cur.fetchall()):
            at = _seen_at(state, r) + wait
            if at <= now:
                rows.append(r)
            elif at.isoformat(timespec="seconds") != r["auto_after"]:
                state.db.execute("UPDATE drafts SET auto_after=? WHERE message_id=? AND auto_after IS NOT NULL",
                                 (at.isoformat(timespec="seconds"), r["message_id"]))
                state.db.commit()
                log(r["message_id"], f"automatic send rescheduled for {at.astimezone():%H:%M}")
        if not rows:
            return 0
        try:
            folders = box.ensure_folders(RT.folder_names(cfg))
            for row in rows:
                sent += _send_one(cfg, state, box, folders, row, log, send_mail or AC.smtp_send)
        finally:
            box.select(Mailbox.INBOX)
    except Exception as exc:
        log("autosend", f"automatic sends failed ({type(exc).__name__})")
    return sent


def status(cfg: dict, state) -> dict:
    """For the admin page; readiness is the drafts closed in the last 30 days and how many went unchanged."""
    import metrics as M
    import router as RT
    DR.init(state)
    end = datetime.now().astimezone().date()
    rows = {q["folder"]: q for q in M._quality(state, cfg, end - timedelta(days=READINESS_DAYS - 1), end)}
    names = RT.folder_names(cfg)
    return {"settings": settings(state), "sent_today": sent_today(state),
            "readiness": [{"folder": f, "name": names.get(f, f), "drafts": rows.get(f, {}).get("drafts", 0),
                           "unchanged": rows.get(f, {}).get("unchanged", 0)} for f in ALLOWED if f in cfg["folders"]]}

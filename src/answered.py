#!/usr/bin/env python3
"""Stage 8b - answered mail and the daily list of mail still to answer.

Run by the router on every cycle:
  1. your replies that reached Sent since the last cycle move the messages they
     answer (from INBOX or the conversation's folder) into 10 Respondidos.
     The router still remembers each conversation's category, so a customer
     who writes again lands back in that category folder, open again.
  2. once a day after digest_hour, a summary of unanswered mail since
     route_since is saved in Drafts, replacing the previous day's summary.

`router.py --open` prints the same summary in the terminal.
"""
from __future__ import annotations

import email.utils
import imaplib
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import drafts as DR
import ledger as LG
from imap_util import Mailbox

OPEN_FOLDERS = ("F1", "F2", "F3", "F4", "F5", "F9")   # 8 Não-cliente needs no answer


def get_meta(state, key: str) -> str | None:
    row = state.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(state, key: str, value: str) -> None:
    state.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))
    state.db.commit()


def targets(references: list[str], state, folders: dict[str, str]) -> list[tuple[str, str]]:
    """(encoded folder, Message-ID) pairs where the mail a reply answers may sit."""
    out = []
    for ref in references:
        out.append((Mailbox.INBOX, ref))
        fid = state.thread_folder([ref])
        if fid in folders:
            out.append((folders[fid], ref))
        if "P" in folders and fid != "P":   # handed off after the conversation was filed
            out.append((folders["P"], ref))
    return out


def _special(box: Mailbox, flag: str, fallback: str) -> str:
    return next((n for n, f in box.folders().items() if flag in f), box.prefix + fallback)


def sync(box: Mailbox, state, cfg: dict, folders: dict[str, str], log) -> int:
    """Move the mail answered by new replies in Sent into the answered folder."""
    dest = box.ensure_folders({"A": cfg["answered_folder"]})["A"]
    box.select(_special(box, "\\Sent", "Sent"), readonly=True)
    last = int(get_meta(state, "sent_uid") or 0)
    new = [u for u in box.uids(since=cfg.get("route_since")) if int(u) > last]
    pairs = []
    for uid in new:
        h = box.headers(uid)
        references = str(h.get("References", "")).split()
        DR.note_sent(state, str(h.get("In-Reply-To", "")).strip(), references, lambda u=uid: box.fetch(u), log)
        refs = references + [str(h.get("In-Reply-To", "")).strip()]
        pairs += targets([r for r in dict.fromkeys(refs) if r], state, folders)
    by_folder: dict[str, list[str]] = {}
    for enc, ref in dict.fromkeys(pairs):
        by_folder.setdefault(enc, []).append(ref)
    moved = 0
    for enc, refs in by_folder.items():
        box.select(enc)
        for ref in refs:
            for uid in box.find(ref):
                box.move(uid, dest)
                moved += 1
                log(ref, f"answered -> {cfg['answered_folder']}")
                DR.mark_answered(state, ref)
                LG.note_answered(state, ref, "mail", log)
    if new:
        set_meta(state, "sent_uid", new[-1].decode() if isinstance(new[-1], bytes) else str(new[-1]))
    return moved


def _date(value) -> datetime | None:
    try:
        d = email.utils.parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def open_rows(box: Mailbox, cfg: dict, folders: dict[str, str]) -> list[tuple[str, datetime | None, str]]:
    """(folder name, date, subject) for every unanswered message since route_since."""
    rows = []
    extra = [(cfg["handoff_folder"], folders["P"])] if "P" in folders and cfg.get("handoff_folder") else []
    for name, enc in [("INBOX", Mailbox.INBOX)] + extra + [(cfg["folders"][f], folders[f]) for f in OPEN_FOLDERS]:
        box.select(enc, readonly=True)
        for uid in box.uids(since=cfg.get("route_since")):
            h = box.headers(uid)
            rows.append((name, _date(h.get("Date")), str(h.get("Subject", "")) or "(sem assunto)"))
    return rows


def format_digest(rows: list[tuple[str, datetime | None, str]], now: datetime, overdue_days: int) -> str:
    limit = timedelta(days=overdue_days)
    late = sum(1 for _, d, _ in rows if d and now - d > limit)
    lines = [f"Por responder: {len(rows)} · atrasadas (mais de {overdue_days} dias): {late}"]
    by_folder: dict[str, list[tuple[datetime | None, str]]] = {}
    for name, d, subject in rows:
        by_folder.setdefault(name, []).append((d, subject))
    for name, items in by_folder.items():
        lines += ["", f"{name} ({len(items)})"]
        for d, subject in sorted(items, key=lambda it: it[0] or now):
            mark = "⚠" if d and now - d > limit else " "
            age = f"{(now - d).days} d" if d else "? d"
            lines.append(f"  {mark} {age:>5}  {subject}")
    return "\n".join(lines)


def digest_due(state, local_now: datetime, hour: int) -> bool:
    return local_now.hour >= hour and get_meta(state, "digest_day") != local_now.date().isoformat()


def draft_message(text: str, user: str, day: str, message_id: str = "") -> bytes:
    m = EmailMessage()
    m["From"] = user
    m["To"] = user
    m["Subject"] = f"Por responder - {day}"
    m["Date"] = email.utils.formatdate(localtime=True)
    if message_id:
        m["Message-ID"] = message_id
    m.set_content(text)
    return m.as_bytes()


def daily_draft(box: Mailbox, state, cfg: dict, folders: dict[str, str], log) -> None:
    local = datetime.now()
    if not digest_due(state, local, cfg.get("digest_hour", 8)):
        return
    day, user = local.date().isoformat(), cfg["imap"]["user"]
    text = format_digest(open_rows(box, cfg, folders), datetime.now(timezone.utc), cfg.get("overdue_days", 2))
    if notes := DR.format_notes(DR.open_notes(state)):
        text += "\n\n" + notes
    drafts, trash = _special(box, "\\Drafts", "Drafts"), box.trash()
    previous = get_meta(state, "digest_mid")
    if previous and trash:
        box.select(drafts)
        for uid in box.find(previous):
            box.move(uid, trash)
    mid = email.utils.make_msgid(domain=user.rpartition("@")[2])
    box.append(drafts, "(\\Draft)", imaplib.Time2Internaldate(time.time()), draft_message(text, user, day, mid))
    set_meta(state, "digest_mid", mid)
    set_meta(state, "digest_day", day)
    log("digest", f"summary saved in Drafts: {text.splitlines()[0]}")

#!/usr/bin/env python3
"""Stage 8c - drafted replies: which mail gets one, where it goes, how it did.

Run by the router when config drafts.enabled is true:
  maybe_crawl   once a day, refresh the lebillet.eu facts (site_facts.crawl)
  try_draft     after a customer email is filed in folders 1-5 or 9, write a reply into Drafts
  note_sent     when a reply in Sent answers a drafted message, store how much of
                the draft was kept (1.0 = sent unchanged)
  report        router.py --drafts
A draft that cannot be written is logged and skipped; filing never waits for it.
"""
from __future__ import annotations

import difflib
import imaplib
import sqlite3
import statistics
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import cases as K
import drafter as D
import events_doc as ED
import extract as E
import relay as RL
import site_facts as SF
import triage as T

DRAFT_FOLDERS = ("F1", "F2", "F3", "F4", "F5", "F9")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# The customer's email is kept next to each draft (and old hand-off) so the dashboard can show it
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
                                         "edited INT DEFAULT 0", "auto_after", "flag", "bcc", "to_override",
                                         "to_set_by_person INT DEFAULT 0", "forwarded INT DEFAULT 0"))
    import relayed_check   # it uses this module
    relayed_check.run(state)
    # relayed drafts from before the Para: field: the customer's address from their stored text, once
    old = state.db.execute("SELECT message_id, from_email, COALESCE(full_text, newest_text, '') FROM drafts "
                           "WHERE flag='relayed' AND to_override IS NULL").fetchall()
    for mid, relayer, text in old:
        state.db.execute("UPDATE drafts SET to_override=? WHERE message_id=? AND to_override IS NULL",
                         (RL.customer_address(text, relayer or ""), mid))
    if old:
        state.db.commit()
    # rows from before first_body (not rows whose text was purged: those stay as they are)
    if "folder" in {r[1] for r in state.db.execute("PRAGMA table_info(drafts)")} and \
            state.db.execute("SELECT 1 FROM drafts WHERE folder IN ('F6', 'F7')").fetchone():   # merged into 9
        state.db.execute("UPDATE drafts SET folder='F9' WHERE folder IN ('F6', 'F7')")
        state.db.commit()
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


def relayed(d, reason: str, rec: dict) -> bool:
    """Customer mail that came through the website form (the sender is the form mailer), or a forward: a reply to
    the sender would go to the partner or the form mailer. Not relayed only on positive evidence that a customer
    forwarded their own email (relay.own_forward: a forwarded block with only tickett, no-reply or their own
    addresses in it)."""
    if d.folder not in DRAFT_FOLDERS:
        return False
    if reason.startswith("website contact form"):
        return True
    return reason.startswith("forwarded to support") and \
        not RL.own_forward(rec.get("body_text") or "", rec.get("from_email") or "")


def flag_for(d, reason: str, rec: dict) -> str:
    """The router's own flag, which wins over the model's: relayed (the reply would go to the partner or the form
    mailer), else legal (urgent), else ""."""
    return "relayed" if relayed(d, reason, rec) else "legal" if d.urgent else ""


def greet_name(flag: str, rec: dict) -> str:
    """The first name the reply greets: the sender's, but for relayed mail the customer's name next to the
    address found in the text (never the partner's or the form mailer's); "" when there is none."""
    if flag == "relayed":
        return D.first_name(RL.customer(rec.get("body_text") or "", rec.get("from_email") or "")[1])
    return D.first_name(rec.get("from_name", ""))


RECHECK_KEY = "relayed_checked_4"   # bumped whenever the relayed rule gets stricter: relayed_check runs again


def forwarded(reason: str) -> bool:
    """A forward by triage: never sent automatically, even when it is the customer's own (a normal draft)."""
    return reason.startswith("forwarded to support")


def has_draft(state, mid: str) -> bool:
    init(state)
    return state.db.execute("SELECT 1 FROM drafts WHERE message_id=?", (mid,)).fetchone() is not None


def qualifies(d, bucket: str, state, mid: str) -> bool:
    """Every customer email in a draft folder gets one draft, legal and relayed mail too (flag_for marks those)."""
    return d.folder in DRAFT_FOLDERS and bucket == "customer" and not has_draft(state, mid)


def draft_case(rec: dict, redactor) -> dict | None:
    """The whole body, quoted history included, redacted; None when the leak check fails."""
    subject, text, _, leaks = redactor.redact(rec.get("subject", ""), rec.get("body_text", "")[:D.MAX_TEXT],
                                              [rec.get("from_name", "")], [rec.get("from_email", "")])
    return None if leaks else {"subject": subject, "text": text, "language": rec.get("lang_guess", "")}


def prepare(rec: dict, mid: str, d, drafter, redactor, cfg: dict, log, reason: str = "") -> dict | None:
    """Claude's reply for one email, with its flag (the router's from flag_for, else the model's), or None when
    there is none to use. `reason` is the triage reason. Never raises: a missing draft must never hold up the filing."""
    try:
        case = draft_case(rec, redactor)
        if case is None:
            log(mid, "no draft - leak check")
            return None
        out = drafter.draft(case, cfg["folders"][d.folder], rec.get("subject", "") + "\n" + rec.get("body_text", ""),
                            date.today())
        if out is None:
            log(mid, "no draft - model answer unusable")
            return None
        out["forwarded"] = forwarded(reason)
        if flag_for(d, reason, rec):
            out["tickett_refund"] = out.get("flag") == "tickett_refund"   # finance still gets its copy (save)
            out["flag"] = flag_for(d, reason, rec)
        return out
    except Exception as exc:
        log(mid, f"no draft ({type(exc).__name__})")
        return None


def _usage(state, kind: str, out: dict, model: str, log=None) -> None:
    """Record the model call behind out (ledger imports this module, so it is imported here)."""
    import ledger as LG
    LG.note_usage(state, kind, out.get("model") or model, out.get("usage", {}), log)


EMPTY_NOTE = "Não foi possível escrever um rascunho; escreva a resposta aqui."


def category(state, mid: str, fallback: str = "F9") -> str:
    """The email's real topic for its draft: the category a hand-off (folder P) came from, else its folder;
    the old 6 and 7, and anything unknown, read as 9."""
    import classify as C
    row = state.db.execute("SELECT folder, suggested FROM decisions WHERE message_id=?", (mid,)).fetchone()
    fid = C.current_folder((row[1] if row[0] == "P" else row[0]) if row else fallback)
    return fid if fid in DRAFT_FOLDERS else "F9"


def placeholder(row: dict | None) -> bool:
    """An empty draft nobody has touched yet (save_empty, adopt_handoffs): a real draft may replace it."""
    return bool(row) and row.get("closed_at") is None and row.get("sent_at") is None and not row.get("body") \
        and row.get("note") == EMPTY_NOTE and not row.get("edited") and not row.get("rewrites") \
        and not row.get("to_set_by_person")


# the untouched empty draft of placeholder(), as SQL: the only kind of row a new draft may replace
_UNTOUCHED = ("closed_at IS NULL AND sent_at IS NULL AND COALESCE(body, '')='' AND COALESCE(edited, 0)=0 "
              "AND COALESCE(rewrites, 0)=0 AND COALESCE(to_set_by_person, 0)=0 AND note=?")


def _claimable(state, mid: str) -> bool:
    """No draft yet, or only an untouched empty one."""
    init(state)
    return state.db.execute(f"SELECT 1 FROM drafts WHERE message_id=? AND NOT ({_UNTOUCHED})",
                            (mid, EMPTY_NOTE)).fetchone() is None


def to_override(flag: str, text: str, relayer: str) -> str:
    """Where a relayed reply goes: the customer's address found in the text ("" when none); "" for other mail."""
    return RL.customer_address(text or "", relayer or "") if flag == "relayed" else ""


def _insert_empty(state, mid: str, folder: str, subject: str, fields: dict, flag: str, fwd: bool) -> None:
    to = to_override(flag, fields.get("full_text") or fields.get("newest_text") or "", fields.get("from_email"))
    state.db.execute("INSERT OR IGNORE INTO drafts (message_id, draft_mid, folder, rulebook_sha, note, body, first_body, "
                     f"subject, created_at, flag, bcc, to_override, forwarded, {', '.join(EMAIL_COLS)}) "
                     f"VALUES (?,'',?,'',?,'','',?,?,?,'',?,?,{','.join('?' * len(EMAIL_COLS))})",
                     (mid, folder, EMPTY_NOTE, subject, _now(), flag, to, int(fwd), *[fields.get(c) for c in EMAIL_COLS]))


def handoff_rec(h: dict) -> dict:
    """An email rebuilt from the text stored with a hand-off (no mailbox), enough for triage and drafting."""
    sender = h.get("from_email") or ""
    return {"message_id": h["message_id"], "subject": h.get("subject") or "", "from_name": h.get("from_name") or "",
            "from_email": sender, "from_domain": sender.rpartition("@")[2].lower(), "reply_to": h.get("reply_to") or "",
            "date": h.get("received_at") or "", "body_text": h.get("full_text") or h.get("newest_text") or ""}


def save_empty(state, rec: dict, mid: str, d, log, reason: str = "") -> None:
    """No draft could be written: an empty one, so the card still shows with a reply to write. It keeps the
    router's flag (relayed, legal), else person_must_act. Never raises."""
    try:
        init(state)
        _insert_empty(state, mid, d.folder, rec.get("subject", ""), email_fields(rec),
                      flag_for(d, reason, rec) or "person_must_act", forwarded(reason))
        state.db.commit()
        log(mid, "empty draft saved - write the reply on the dashboard")
    except Exception as exc:
        state.db.rollback()
        log(mid, f"no empty draft ({type(exc).__name__})")


def adopt_handoffs(state) -> None:
    """Open hand-offs from before drafts for every email, still without a draft: an empty draft each, in the
    email's real topic, so they show as normal cards. The hand-off row stays for history."""
    import router as RT   # router imports this module
    rows = state.db.execute("""SELECT h.* FROM handoffs h WHERE h.answered_at IS NULL
        AND NOT EXISTS (SELECT 1 FROM drafts WHERE drafts.message_id=h.message_id)""")
    cols = [c[0] for c in rows.description]
    have_replies = state.db.execute("SELECT 1 FROM sqlite_master WHERE name='replies'").fetchone()
    added = 0
    for h in (dict(zip(cols, r)) for r in rows.fetchall()):
        if have_replies and state.db.execute("SELECT 1 FROM replies WHERE message_id=? AND answered_at IS NOT NULL",
                                             (h["message_id"],)).fetchone():
            continue
        mid = h["message_id"]
        urgent = state.db.execute("SELECT urgent FROM decisions WHERE message_id=?", (mid,)).fetchone()
        d = RT.Decision(category(state, mid), bool(urgent and urgent[0]), "handoff", "", "")
        r = handoff_rec(h)
        why = T.classify(r)[1]
        _insert_empty(state, mid, d.folder, h.get("subject") or "", h, flag_for(d, why, r) or "person_must_act",
                      forwarded(why))
        added += 1
    if added:
        state.db.commit()


def save(box, drafts_folder: str, rec: dict, mid: str, d, state, out: dict, drafter, cfg: dict, log) -> bool:
    """Write the reply into Drafts, inside the conversation, and remember it. A relayed reply stays on the
    dashboard only: in Mail's Drafts it would be addressed to the partner or the form mailer."""
    try:
        if not _claimable(state, mid):   # sent, closed or worked on meanwhile: never replaced
            log(mid, "no draft - the email already has a draft a person handled")
            return False
        flag = out.get("flag") or ""
        text, body, note = D.compose(out, greet_name(flag, rec))
        refund = flag == "tickett_refund" or out.get("tickett_refund")
        bcc = cfg.get("refund_bcc", "") if refund else ""   # finance sees Tickett's refunds, whatever the flag shown
        draft_mid = put_draft(box, drafts_folder, rec, text, cfg, flag, bcc)   # removed again on any failure below
        u = out.get("usage", {})
        try:
            e = email_fields(rec)
            # only an untouched empty draft makes way; anything else makes the INSERT fail (checked again in SQL)
            state.db.execute(f"DELETE FROM drafts WHERE message_id=? AND {_UNTOUCHED}", (mid, EMPTY_NOTE))
            _insert_draft(state, mid, draft_mid, d, drafter, note, body, rec, u, flag, bcc, e, out.get("forwarded"))
        except sqlite3.IntegrityError:
            state.db.rollback()
            _drop_copy(box, drafts_folder, draft_mid)
            log(mid, "no draft - the email already has a draft a person handled")
            return False
        except Exception:
            state.db.rollback()
            _drop_copy(box, drafts_folder, draft_mid)   # no row points at it: it must not stay in Drafts
            raise
        try:
            state.db.commit()
        except Exception:
            state.db.rollback()
            _drop_copy(box, drafts_folder, draft_mid)
            raise
        _usage(state, "draft", out, cfg.get("model", D.MODEL), log)
        log(mid, "draft saved" + (f" ({flag})" if flag else "") + (f" - nota: {note}" if note else ""))
        return True
    except Exception as exc:
        state.db.rollback()   # never leave the DELETE of the empty draft pending
        log(mid, f"no draft ({type(exc).__name__})")
        return False


def _drop_copy(box, drafts_folder: str, draft_mid: str) -> None:
    if box is not None and draft_mid:
        box.select(drafts_folder)
        for uid in box.find(draft_mid):
            box.delete(uid)


def _insert_draft(state, mid, draft_mid, d, drafter, note, body, rec, u, flag, bcc, e, fwd=False) -> None:
    state.db.execute("INSERT INTO drafts (message_id, draft_mid, folder, rulebook_sha, note, body, "
                     "subject, created_at, tokens_in, tokens_out, cache_read, cache_write, first_body, flag, bcc, "
                     f"forwarded, to_override, {', '.join(EMAIL_COLS)}) VALUES ({','.join('?' * (17 + len(EMAIL_COLS)))})",
                     (mid, draft_mid, d.folder, drafter.sha, note, body, rec.get("subject", ""), _now(),
                      u.get("in", 0), u.get("out", 0), u.get("cache_read", 0), u.get("cache_write", 0), body,
                      flag, bcc, int(bool(fwd)), to_override(flag, rec.get("body_text", ""), rec.get("from_email")),
                      *[e[c] for c in EMAIL_COLS]))


def put_draft(box, drafts_folder: str, rec: dict, text: str, cfg: dict, flag: str, bcc: str) -> str:
    """The draft's copy in the mailbox Drafts folder, and its Message-ID; none ("") for a relayed draft or
    without a mailbox."""
    if flag == "relayed" or box is None:   # no mailbox: draft_all.py --db-only
        return ""
    raw, draft_mid = D.reply_message(rec, text, cfg["imap"]["user"], bcc)
    box.append(drafts_folder, "(\\Draft)", imaplib.Time2Internaldate(time.time()), raw)
    return draft_mid


def make_draft(box, drafts_folder: str, rec: dict, mid: str, d, state, drafter, redactor, cfg: dict, log,
               reason: str = "") -> bool:
    """Prepare and save in one go (draft_backlog.py); `reason` is the triage reason, for flag_for."""
    out = prepare(rec, mid, d, drafter, redactor, cfg, log, reason)
    if out is None:
        return False
    return save(box, drafts_folder, rec, mid, d, state, out, drafter, cfg, log)


def try_draft(box, drafts_folder: str, rec: dict, mid: str, d, state, drafter, redactor, cfg: dict, log,
              reason: str = "") -> bool:
    try:
        return make_draft(box, drafts_folder, rec, mid, d, state, drafter, redactor, cfg, log, reason)
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

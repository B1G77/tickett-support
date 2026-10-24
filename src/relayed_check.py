"""One-off (per RECHECK_KEY) check of open drafts against the relayed rule, run by drafts.init.

The rule (drafts.relayed) got stricter over time; each time RECHECK_KEY is bumped, the open drafts are judged once
more: a relayed draft that is really the customer's own forward loses the flag, one a looser rule un-flagged gets it
back, and every forward is marked `forwarded` (never sent automatically).
"""
from __future__ import annotations

import drafter as D
import drafts as DR
import relay as RL
import triage as T

_RELAYED_NOTES = (D.RELAYED, "Responda diretamente ao cliente: o endereço dele está no texto do email.")


def run(state) -> None:
    """Once: open drafts are checked with the relayed rule (relayed()): a forward stays relayed unless there is
    positive evidence that the customer forwarded their own email; website-form mail always is. A draft that
    should not be relayed loses the flag, the Para: address and the relayed note (legal, or the Tickett-refund
    verdict from its Bcc, comes back); one the earlier looser check un-flagged gets relayed back, with the
    address found in its text; every forward is marked `forwarded`. Never touched: closed drafts, a draft with a
    Para: address or one a person typed, and a draft whose text was purged."""
    cols = {r[1] for r in state.db.execute("PRAGMA table_info(drafts)")}
    if not {"flag", "note", "bcc", "from_email", "full_text", "newest_text", "closed_at", "sent_at", "subject",
            "to_override", "to_set_by_person", "folder", "forwarded"} <= cols or \
            not state.db.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone() or \
            state.db.execute("SELECT 1 FROM meta WHERE key=?", (DR.RECHECK_KEY,)).fetchone():
        return   # a table from before these columns has no relayed drafts
    rows = state.db.execute("""SELECT message_id, from_email, subject, COALESCE(NULLIF(full_text, ''), newest_text, ''),
        bcc, note, flag, folder FROM drafts WHERE closed_at IS NULL AND sent_at IS NULL
        AND COALESCE(to_override, '')='' AND COALESCE(to_set_by_person, 0)=0""").fetchall()
    for mid, sender, subject, text, bcc, note, flag, folder in rows:
        if not text.strip():
            continue   # purged: nothing to judge by
        dec = state.db.execute("SELECT urgent, reason FROM decisions WHERE message_id=?", (mid,)).fetchone()
        rec = {"from_email": sender or "", "from_domain": (sender or "").rpartition("@")[2].lower(),
               "subject": subject or "", "body_text": text}
        why = T.classify(rec)[1]
        if (dec and dec[1] or "").startswith(("forwarded to support", "website contact form")):
            why = dec[1]
        urgent = bool(dec and dec[0])
        d = type("D", (), {"folder": folder if folder in DR.DRAFT_FOLDERS else "F9", "urgent": urgent})()
        should = DR.relayed(d, why, rec)
        if DR.forwarded(why):   # never sent automatically from now on, relayed or not
            state.db.execute("UPDATE drafts SET forwarded=1 WHERE message_id=?", (mid,))
        if flag == "relayed" and not should:
            new = "legal" if urgent else "tickett_refund" if bcc else ""
            note = "; ".join(n for n in (note or "").split("; ") if n and n not in _RELAYED_NOTES)
            state.db.execute("UPDATE drafts SET flag=?, to_override='', note=? WHERE message_id=?", (new, note, mid))
        elif flag != "relayed" and should:
            note = "; ".join([D.RELAYED] + [n for n in (note or "").split("; ") if n and n not in _RELAYED_NOTES])
            state.db.execute("UPDATE drafts SET flag='relayed', to_override=?, note=? WHERE message_id=?",
                             (RL.customer_address(text, sender or ""), note, mid))
    state.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (DR.RECHECK_KEY, DR._now()))
    state.db.commit()

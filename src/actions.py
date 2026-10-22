#!/usr/bin/env python3
"""What a person does with a reply draft on the dashboard.

  send       the reply goes out from the support mailbox (SMTP), a copy is saved in Sent
             and the draft leaves Drafts; the router then files the email as answered
  rewrite    Claude writes a new version following the person's comment (or hands it off)
  to_person  the person answers it themselves: the email moves to the hand-off folder
             and the draft is removed
  answered_by_hand  the person has answered a hand-off: the email moves to the answered folder

Every action names the draft version the person saw; if the draft changed since, nothing
happens. A draft with the internal note or a placeholder still in it is never sent.
"""
from __future__ import annotations

import email
import hashlib
import imaplib
import smtplib
import ssl
import time
from datetime import date
from email.utils import getaddresses, parseaddr

import answered as A
import drafter as D
import drafts as DR
import extract as E
import ledger as LG
import router as RT

SMTP_PORT = 465


class Stale(Exception):
    """The draft changed (or was handled) after the page was loaded."""


def card_id(message_id: str) -> str:
    return hashlib.sha1(message_id.encode("utf-8")).hexdigest()[:12]


def sendable(body: str) -> str:
    """"" when the draft can go to the customer as it is, else why not (in Portuguese, for the page)."""
    if D.NOTE.strip()[:14] in body:
        return "a nota interna ainda está no texto"
    if D._PLACEHOLDER.search(body):
        return "o texto ainda tem um marcador como [NAME]"
    if not body.strip():
        return "o texto está vazio"
    return ""


BAD_REPLY_TO = "O endereço de resposta não é válido. Responda pelo Mail."


def reply_to_ok(row: dict) -> bool:
    """No Reply-To, or one that reads as exactly one address."""
    raw = (row.get("reply_to") or "").strip()
    return not raw or "@" in parseaddr(raw)[1]


def recipient(row: dict) -> str:
    """Where a reply to this email goes: its Reply-To, else the sender (as drafter.reply_message);
    a Reply-To that is not one address is shown as it is (such a draft is never sent from here)."""
    raw = (row.get("reply_to") or "").strip()
    if not raw:
        return row.get("from_email") or ""
    return parseaddr(raw)[1] if reply_to_ok(row) else raw


def unchanged(row: dict) -> bool:
    """A sent draft that nobody rewrote or edited."""
    return (row.get("closed_how") == "sent" and not row.get("rewrites") and not row.get("edited")
            and row.get("body") == row.get("first_body"))


def _rows(state, sql: str, args=()) -> list[dict]:
    cur = state.db.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def pending(state) -> list[dict]:
    DR.init(state)
    return _rows(state, "SELECT * FROM drafts WHERE sent_at IS NULL AND closed_at IS NULL "
                 "ORDER BY COALESCE(NULLIF(received_at, ''), created_at)")


def waiting_for_person(state) -> list[dict]:
    DR.init(state)
    return _rows(state, "SELECT * FROM handoffs WHERE answered_at IS NULL "
                 "ORDER BY COALESCE(NULLIF(received_at, ''), created_at)")


def _row(state, cid: str, version: str) -> dict:
    row = next((r for r in pending(state) if card_id(r["message_id"]) == cid), None)
    if row is None or row["created_at"] != version:
        raise Stale("Este rascunho mudou ou já foi tratado. A página foi atualizada.")
    return row


def _stop_auto(state, mid: str) -> None:
    """A person is working on the draft: no scheduled automatic send may claim it (autosend.send_due)."""
    state.db.execute("UPDATE drafts SET auto_after=NULL WHERE message_id=?", (mid,))
    state.db.commit()


def _replace(state, row: dict, sets: str, args: tuple) -> bool:
    """Write the person's new version, only over the version they saw and only while it is still open."""
    cur = state.db.execute(f"UPDATE drafts SET {sets}, auto_after=NULL WHERE message_id=? AND closed_at IS NULL "
                           "AND sent_at IS NULL AND created_at=?", (*args, row["message_id"], row["created_at"]))
    state.db.commit()
    return cur.rowcount == 1


STALE = "Este rascunho mudou ou já foi tratado. A página foi atualizada."


def _close(state, mid: str, how: str) -> None:
    state.db.execute("UPDATE drafts SET closed_at=?, closed_how=? WHERE message_id=?", (DR._now(), how, mid))
    state.db.commit()


def _original(box, folders: dict, row: dict) -> tuple[dict, bytes] | tuple[None, None]:
    """The email the draft answers, while it still waits in its folder: (record, uid)."""
    enc = folders.get(row["folder"])
    if not enc:
        return None, None
    box.select(enc, readonly=True)
    uids = box.find(row["message_id"])
    return (E.record_from_bytes(box.fetch(uids[0]), "imap:dashboard"), uids[0]) if uids else (None, None)


def _delete_draft(box, draft_mid: str) -> None:
    box.select(A._special(box, "\\Drafts", "Drafts"))
    for uid in box.find(draft_mid or ""):
        box.delete(uid)


def smtp_send(cfg: dict, raw: bytes) -> None:
    i = cfg["imap"]
    to = [a for _, a in getaddresses(email.message_from_bytes(raw).get_all("To", [])) if a]
    if not to:
        raise ValueError("no recipient")
    s = smtplib.SMTP_SSL(i.get("smtp_host", i["host"]), i.get("smtp_port", SMTP_PORT), timeout=60,
                         context=ssl.create_default_context())
    try:
        s.login(i["user"], RT.keychain_password(i["keychain_service"], i["user"]))
        s.sendmail(i["user"], to, raw)
    finally:
        try:   # once sendmail returned the mail has left: a failed goodbye must not count as a failed send
            s.quit()
        except Exception:
            s.close()


def claim(state, row: dict, how: str, scheduled: bool = False) -> bool:
    """Close the draft version in `row` as `how` before it is sent; True only for the one caller (router or
    dashboard, each with its own connection to router.db) that closed it. `scheduled`: only while its automatic
    send is still scheduled (a person who starts working on the draft clears that)."""
    try:
        cur = state.db.execute("UPDATE drafts SET closed_at=?, closed_how=? WHERE message_id=? AND closed_at IS NULL "
                               "AND sent_at IS NULL AND created_at=?"
                               + (" AND auto_after IS NOT NULL" if scheduled else ""),
                               (DR._now(), how, row["message_id"], row["created_at"]))
        state.db.commit()
    except Exception:
        state.db.rollback()   # nothing claimed, and no write lock left behind
        raise
    return cur.rowcount == 1


def claim_and_send(cfg, state, row: dict, rec: dict, how: str, send_mail=smtp_send, scheduled: bool = False) -> bytes:
    """Claim the draft, then mail it to the sender of `rec`; Stale when another caller has it. A failed SMTP
    send opens the draft again. Returns the message sent."""
    if not reply_to_ok(rec):
        raise ValueError(BAD_REPLY_TO)
    raw, _ = D.reply_message(rec, row["body"], cfg["imap"]["user"])
    if not claim(state, row, "auto" if how == "auto" else "sent", scheduled):
        raise Stale("Este rascunho mudou ou já foi tratado. A página foi atualizada.")
    try:
        send_mail(cfg, raw)
    except Exception:
        try:
            state.db.execute("UPDATE drafts SET closed_at=NULL, closed_how=NULL WHERE message_id=?",
                             (row["message_id"],))
            state.db.commit()
        except Exception:
            state.db.rollback()   # it stays closed: nothing is sent twice, a person checks Sent
        raise
    return raw


def after_send(state, box, row: dict, raw: bytes, log, how: str) -> None:
    """The mail has left: a copy in Sent, the draft out of Drafts, the answer in the ledger. A failure here
    never opens the draft again."""
    box.append(A._special(box, "\\Sent", "Sent"), "(\\Seen)", imaplib.Time2Internaldate(time.time()), raw)
    _delete_draft(box, row["draft_mid"])
    LG.note_answered(state, row["message_id"], how, log)


def deliver(cfg, state, box, row: dict, rec: dict, log, how: str, send_mail=smtp_send) -> None:
    """The draft in `row` goes to the sender of `rec`: claimed, sent, copied to Sent, removed from Drafts
    and recorded as answered `how` (dashboard, or auto for automatic sending)."""
    raw = claim_and_send(cfg, state, row, rec, how, send_mail)
    after_send(state, box, row, raw, log, how)


def send(cfg, state, cid: str, version: str, log, connect=RT.connect, send_mail=smtp_send) -> str:
    row = _row(state, cid, version)
    why = sendable(row["body"] or "")
    if why:
        raise ValueError(f"Não pode ser enviado: {why}.")
    box = connect(cfg)
    try:
        folders = box.ensure_folders(RT.folder_names(cfg))
        rec, _ = _original(box, folders, row)
        if rec is None:
            _close(state, row["message_id"], "gone")
            raise Stale("O email já não está à espera de resposta (foi respondido ou movido).")
        deliver(cfg, state, box, row, rec, log, "dashboard", send_mail)
    finally:
        box.logout()
    log(row["message_id"], "reply sent from the dashboard")
    return f"Enviado para {recipient(rec) or 'o cliente'}."


def to_person(cfg, state, cid: str, version: str, log, connect=RT.connect, note: str = "",
              reason: str = "person_must_act") -> str:
    row = _row(state, cid, version)
    _stop_auto(state, row["message_id"])
    box = connect(cfg)
    try:
        folders = box.ensure_folders(RT.folder_names(cfg))
        rec, uid = _original(box, folders, row)
        if rec is not None and RT.HANDOFF in folders:
            box.select(folders[row["folder"]])
            for u in box.find(row["message_id"]):
                box.move(u, folders[RT.HANDOFF])
        _delete_draft(box, row["draft_mid"])
    finally:
        box.logout()
    if not claim(state, row, "person"):   # sent automatically meanwhile: its record stays as it is
        raise Stale(STALE)
    state.db.execute("UPDATE decisions SET folder=? WHERE message_id=?", (RT.HANDOFF, row["message_id"]))
    state.db.execute("UPDATE threads SET folder=? WHERE ref=?", (RT.HANDOFF, row["message_id"]))
    state.db.commit()
    DR.note_handoff(state, row["message_id"], {"handoff_reason": reason,
                                               "action_note": note or "Passado para uma pessoa no painel."}, log, rec)
    return "Passou para 0 Por Responder."


def answered_by_hand(cfg, state, cid: str, log, connect=RT.connect) -> str:
    """A hand-off the person has answered: the email leaves the hand-off folder for the answered one."""
    row = next((r for r in waiting_for_person(state) if card_id(r["message_id"]) == cid), None)
    if row is None:
        raise Stale("Este email já foi tratado. A página foi atualizada.")
    mid = row["message_id"]
    box = connect(cfg)
    try:
        folders = box.ensure_folders(RT.folder_names(cfg))
        if RT.HANDOFF in folders and RT.ANSWERED in folders:
            box.select(folders[RT.HANDOFF])
            for u in box.find(mid):
                box.move(u, folders[RT.ANSWERED])
    finally:
        box.logout()
    DR.mark_answered(state, mid)
    state.db.execute("UPDATE decisions SET folder=? WHERE message_id=?", (RT.ANSWERED, mid))
    state.db.commit()
    LG.note_answered(state, mid, "respondido", log)
    log(mid, "marked as answered from the dashboard")
    return "Marcado como respondido."


def rewrite(cfg, state, cid: str, version: str, comment: str, log, drafter, redactor,
            connect=RT.connect) -> str:
    row = _row(state, cid, version)
    _stop_auto(state, row["message_id"])
    comment = comment.strip()[:2000]
    if not comment:
        raise ValueError("Escreva o que quer mudar.")
    box = connect(cfg)
    try:
        folders = box.ensure_folders(RT.folder_names(cfg))
        rec, _ = _original(box, folders, row)
        if rec is None:
            _close(state, row["message_id"], "gone")
            raise Stale("O email já não está à espera de resposta (foi respondido ou movido).")
        case = DR.draft_case(rec, redactor)
        names, addrs = [rec.get("from_name", "")], [rec.get("from_email", "")]
        _, previous, _, leaks = redactor.redact("", row["body"] or "", names, addrs)
        _, comment, _, comment_leaks = redactor.redact("", comment, names, addrs)
        if comment_leaks:
            raise ValueError("O comentário tem dados pessoais que não podem ir para o modelo. "
                             "Reescreva sem nomes, emails ou números.")
        if case is None or leaks:
            raise ValueError("Não foi possível reescrever: o texto não passou a verificação de privacidade.")
        out = drafter.draft(case, cfg["folders"][row["folder"]], rec.get("subject", "") + "\n" + rec.get("body_text", ""),
                            date.today(), review={"previous": previous, "instruction": comment})
        if out is None:
            raise ValueError("A resposta do modelo não pôde ser usada. Tente outra vez.")
        DR._usage(state, "rewrite", out, cfg.get("model", D.MODEL), log)
        if out.get("handoff"):
            box.logout()
            box = None
            return to_person(cfg, state, cid, version, log, connect,
                             note=out.get("action_note", ""), reason=out.get("handoff_reason") or "person_must_act")
        text, body, note = D.compose(out, D.first_name(rec.get("from_name", "")))
        raw, draft_mid = D.reply_message(rec, text, cfg["imap"]["user"])
        box.append(A._special(box, "\\Drafts", "Drafts"), "(\\Draft)", imaplib.Time2Internaldate(time.time()), raw)
        u = out.get("usage", {})
        if not _replace(state, row, "draft_mid=?, rulebook_sha=?, note=?, body=?, created_at=?, "
                        "tokens_in=tokens_in+?, tokens_out=tokens_out+?, rewrites=rewrites+1, "
                        "cache_read=COALESCE(cache_read,0)+?, cache_write=COALESCE(cache_write,0)+?",
                        (draft_mid, drafter.sha, note, body, DR._now(), u.get("in", 0), u.get("out", 0),
                         u.get("cache_read", 0), u.get("cache_write", 0))):
            _delete_draft(box, draft_mid)   # handled meanwhile: the new copy goes too
            raise Stale(STALE)
        _delete_draft(box, row["draft_mid"])
    finally:
        if box is not None:
            box.logout()
    log(row["message_id"], "rewritten from the dashboard" + (f" - nota: {note}" if note else ""))
    return "Nova versão pronta."


def edit(cfg, state, cid: str, version: str, body: str, log, connect=RT.connect) -> tuple[str, str]:
    """The person's own text replaces the draft in the app and in Drafts: (message, new version)."""
    row = _row(state, cid, version)
    _stop_auto(state, row["message_id"])
    body = body.strip()
    if not body:
        raise ValueError("O texto está vazio.")
    if len(body) > DR.MAX_FULL:
        raise ValueError("O texto é demasiado longo (máximo 20 000 caracteres).")
    box = connect(cfg)
    try:
        folders = box.ensure_folders(RT.folder_names(cfg))
        rec, _ = _original(box, folders, row)
        if rec is None:
            _close(state, row["message_id"], "gone")
            raise Stale("O email já não está à espera de resposta (foi respondido ou movido).")
        raw, draft_mid = D.reply_message(rec, body, cfg["imap"]["user"])
        box.append(A._special(box, "\\Drafts", "Drafts"), "(\\Draft)", imaplib.Time2Internaldate(time.time()), raw)
        new_version = DR._now()
        if not _replace(state, row, "draft_mid=?, body=?, edited=1, created_at=?", (draft_mid, body, new_version)):
            _delete_draft(box, draft_mid)   # handled meanwhile: the new copy goes too
            raise Stale(STALE)
        _delete_draft(box, row["draft_mid"])
    finally:
        box.logout()
    log(row["message_id"], "edited from the dashboard")
    return "Rascunho guardado.", new_version


def fill_missing(cfg, state, connect=RT.connect) -> int:
    """Drafts and hand-offs made before the customer's email was stored: fetch it once."""
    DR.init(state)
    todo = [("drafts", r) for r in pending(state) if not r.get("from_email")] + \
           [("handoffs", r) for r in waiting_for_person(state) if not r.get("from_email")]
    if not todo:
        return 0
    box = connect(cfg)
    filled = 0
    try:
        folders = box.ensure_folders(RT.folder_names(cfg))
        for table, r in todo:
            places = [folders.get(r.get("folder")), folders.get(RT.HANDOFF)] if table == "drafts" \
                else [folders.get(RT.HANDOFF)]
            for enc in [p for p in places if p]:
                box.select(enc, readonly=True)
                uids = box.find(r["message_id"])
                if uids:
                    rec = E.record_from_bytes(box.fetch(uids[0]), "imap:dashboard")
                    e = DR.email_fields(rec)
                    sets = ", ".join(f"{c}=?" for c in DR.EMAIL_COLS)
                    extra = ", subject=?" if table == "handoffs" else ""
                    args = [e[c] for c in DR.EMAIL_COLS] + ([rec.get("subject", "")] if extra else [])
                    state.db.execute(f"UPDATE {table} SET {sets}{extra} WHERE message_id=?", (*args, r["message_id"]))
                    filled += 1
                    break
        state.db.commit()
    finally:
        box.logout()
    return filled

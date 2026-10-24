#!/usr/bin/env python3
"""What a person does with a reply draft on the dashboard.

  send       the reply goes out from the support mailbox (SMTP), a copy is saved in Sent
             and the draft leaves Drafts; the router then files the email as answered
  rewrite    Claude writes a new version following the person's comment (flag and Bcc stay as they were)
  edit       the person's own text replaces the draft
  answered_by_hand  "Tratado sem resposta": the email is closed without a reply and moves to the
             answered folder; the draft is closed and leaves Drafts

Every action names the draft version the person saw; if the draft changed since, nothing
happens. A draft with the internal note or a placeholder still in it is never sent.
"""
from __future__ import annotations

import email
import email.policy
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
import relay as RL
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


NO_TO = "Escreva o endereço do cliente em Para: antes de enviar."
BAD_TO = "Escreva um só endereço de email do cliente (não o do parceiro nem um @tickett.com)."


def refusal(row: dict) -> str:
    """"" when Enviar may send this draft at all, else why not: a relayed email's sender is the partner or the
    website form, so it goes only to one valid customer address in Para: (to_override)."""
    if row.get("flag") == "relayed" and not RL.valid(row.get("to_override") or "", row.get("from_email") or ""):
        return NO_TO
    return ""


BAD_REPLY_TO = "O endereço de resposta não é válido. Responda pelo Mail."


def reply_to_ok(row: dict) -> bool:
    """No Reply-To, or one that reads as exactly one address."""
    raw = (row.get("reply_to") or "").strip()
    return not raw or "@" in parseaddr(raw)[1]


def recipient(row: dict) -> str:
    """Where a reply to this email goes: its Reply-To, else the sender (as drafter.reply_message);
    a Reply-To that is not one address is shown as it is (such a draft is never sent from here). A relayed
    draft goes to its Para: address."""
    if row.get("flag") == "relayed":
        return (row.get("to_override") or "").strip()
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
    """Every open draft, oldest customer email first. Read only (old hand-offs get theirs in ledger.after_cycle)."""
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


def _places(folders: dict, row: dict) -> list[str]:
    """Where the email a draft answers may wait: its category folder, and 0 Por Responder (old hand-offs)."""
    return [enc for enc in dict.fromkeys([folders.get(row["folder"]), folders.get(RT.HANDOFF)]) if enc]


def _original(box, folders: dict, row: dict) -> tuple[dict, bytes] | tuple[None, None]:
    """The email the draft answers, while it still waits in its folder: (record, uid)."""
    for enc in _places(folders, row):
        box.select(enc, readonly=True)
        uids = box.find(row["message_id"])
        if uids:
            return E.record_from_bytes(box.fetch(uids[0]), "imap:dashboard"), uids[0]
    return None, None


def _delete_draft(box, draft_mid: str) -> None:
    if not draft_mid:   # a relayed draft lives on the dashboard only
        return
    box.select(A._special(box, "\\Drafts", "Drafts"))
    for uid in box.find(draft_mid or ""):
        box.delete(uid)


def smtp_send(cfg: dict, raw: bytes) -> None:
    i = cfg["imap"]
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    to = [a for _, a in getaddresses(msg.get_all("To", [])) if a]
    bcc = [a for _, a in getaddresses(msg.get_all("Bcc", [])) if a]
    if not to:
        raise ValueError("no recipient")
    if msg.get_all("Bcc"):   # in the envelope only: the customer's copy carries no Bcc header
        del msg["Bcc"]
        raw = msg.as_bytes()
    s = smtplib.SMTP_SSL(i.get("smtp_host", i["host"]), i.get("smtp_port", SMTP_PORT), timeout=60,
                         context=ssl.create_default_context())
    try:
        s.login(i["user"], RT.keychain_password(i["keychain_service"], i["user"]))
        s.sendmail(i["user"], to + bcc, raw)
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
    to = ""
    if row.get("flag") == "relayed" and how == "auto":   # autosend.why_not refuses it too
        raise ValueError("Um email reencaminhado nunca é enviado automaticamente.")
    if row.get("flag") == "relayed":   # only to the customer's address in Para:, checked against today's sender
        to = (row.get("to_override") or "").strip()
        if not RL.valid(to, rec.get("from_email") or "") or refusal(row):
            raise ValueError(NO_TO)
    elif not reply_to_ok(rec):
        raise ValueError(BAD_REPLY_TO)
    raw, _ = D.reply_message(rec, row["body"], cfg["imap"]["user"], row.get("bcc") or "", to)
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
    if refusal(row):
        raise ValueError(refusal(row))
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
    to = recipient(row) if row.get("flag") == "relayed" else recipient(rec)
    return f"Enviado para {to or 'o cliente'}."


def set_to(cfg, state, cid: str, version: str, to: str, log) -> tuple[str, str]:
    """The person's customer address for a relayed draft (Para:), saved as a new version: (message, version).
    Marked to_set_by_person (not edited: the reply text is the AI's), so a later draft never replaces it."""
    row = _row(state, cid, version)
    if row.get("flag") != "relayed":
        raise ValueError("Só um email reencaminhado tem Para: editável.")
    to = (to or "").strip()
    if to and not RL.valid(to, row.get("from_email") or ""):
        raise ValueError(BAD_TO)
    new_version = DR._now()
    if not _replace(state, row, "to_override=?, to_set_by_person=1, created_at=?", (to, new_version)):
        raise Stale(STALE)
    log(row["message_id"], "customer address set from the dashboard" if to else "customer address cleared")
    return "Endereço guardado.", new_version


MOVE_FAILED = "Não foi possível mover o email no servidor de correio. Nada foi alterado; tente outra vez."


def answered_by_hand(cfg, state, cid: str, version: str, log, connect=RT.connect) -> str:
    """Tratado sem resposta: the email is closed without a reply. The draft is claimed first (so nothing is sent
    automatically meanwhile), then the email moves to the answered folder; only then is it recorded as answered
    and the draft taken out of Drafts. A failed move opens the draft again and records nothing."""
    row = _row(state, cid, version)
    mid = row["message_id"]
    _stop_auto(state, mid)   # the person chose not to send it: no scheduled send may take it, even after a failure
    if not claim(state, row, "answered"):   # sent automatically meanwhile: its record stays as it is
        raise Stale(STALE)
    box = None
    try:
        box = connect(cfg)
        folders = box.ensure_folders(RT.folder_names(cfg))
        for enc in _places(folders, row):
            box.select(enc)
            uids = box.find(mid)
            for u in uids:
                box.move(u, folders[RT.ANSWERED])
            if uids:
                break
    except Exception as exc:
        state.db.execute("UPDATE drafts SET closed_at=NULL, closed_how=NULL WHERE message_id=? AND closed_how='answered'",
                         (mid,))
        state.db.commit()
        if box is not None:
            try:   # a failed goodbye must not hide the real error
                box.logout()
            except Exception:
                pass
        log(mid, f"not closed: the move failed ({type(exc).__name__})")
        raise ValueError(MOVE_FAILED) from exc
    try:
        DR.mark_answered(state, mid)   # an old hand-off row is closed too
        state.db.execute("UPDATE decisions SET folder=? WHERE message_id=?", (RT.ANSWERED, mid))
        state.db.commit()
        LG.note_answered(state, mid, "respondido", log)
        _delete_draft(box, row["draft_mid"])
    finally:
        box.logout()
    log(mid, "closed without a reply from the dashboard")
    return f"Tratado sem resposta: passou para {cfg['answered_folder']}."


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
        flag, bcc = row.get("flag") or "", row.get("bcc") or ""   # a draft keeps its flag and Bcc through a rewrite
        if DR.placeholder(row):   # an empty one takes the model's verdict, as drafts.save does
            model = out.get("flag") or ""
            flag = flag if flag in ("relayed", "legal") else model
            bcc = cfg.get("refund_bcc", "") if model == "tickett_refund" else ""
        out["flag"] = flag
        text, body, note = D.compose(out, DR.greet_name(flag, rec))
        draft_mid = DR.put_draft(box, A._special(box, "\\Drafts", "Drafts"), rec, text, cfg, flag, bcc)
        u = out.get("usage", {})
        if not _replace(state, row, "draft_mid=?, rulebook_sha=?, note=?, body=?, created_at=?, flag=?, bcc=?, "
                        "tokens_in=COALESCE(tokens_in,0)+?, tokens_out=COALESCE(tokens_out,0)+?, rewrites=rewrites+1, "
                        "cache_read=COALESCE(cache_read,0)+?, cache_write=COALESCE(cache_write,0)+?",
                        (draft_mid, drafter.sha, note, body, DR._now(), flag, bcc, u.get("in", 0), u.get("out", 0),
                         u.get("cache_read", 0), u.get("cache_write", 0))):
            _delete_draft(box, draft_mid)   # handled meanwhile: the new copy goes too
            raise Stale(STALE)
        _delete_draft(box, row["draft_mid"])
    finally:
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
        draft_mid = DR.put_draft(box, A._special(box, "\\Drafts", "Drafts"), rec, body, cfg, row.get("flag") or "",
                                 row.get("bcc") or "")
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

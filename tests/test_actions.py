"""Tests for the dashboard's draft actions (no IMAP, no SMTP, no API)."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import actions as AC
import drafter as D
import drafts as DR
import extract as E
import ledger as LG
import router as RT

CFG = {"folders": {"F1": "1 Cancelamentos e Reembolsos"}, "answered_folder": "10 Respondidos",
       "handoff_folder": "0 Por Responder", "imap": {"user": "support@tickett.com"}}
RAW = (b"Subject: Reembolso\r\nFrom: ANA SILVA <ana@gmail.com>\r\nMessage-ID: <m1@x>\r\n"
       b"Content-Type: text/plain; charset=utf-8\r\n\r\nQuando recebo o reembolso?\r\n\r\n> antes\r\n")
BODY = "Olá Ana,\n\nO reembolso é automático.\n\nCom os melhores cumprimentos,\nEquipa Tickett"
V = "2026-10-19T10:00:00+00:00"
CID = AC.card_id("<m1@x>")


class Box:
    prefix = "INBOX."

    def __init__(self, has_original=True):
        self.has_original, self.selected = has_original, None
        self.appended, self.deleted, self.moved, self.closed = [], [], [], False

    def ensure_folders(self, names):
        return {k: "INBOX." + v for k, v in names.items()}

    def folders(self):
        return {"INBOX.Sent": "\\Sent", "INBOX.Drafts": "\\Drafts"}

    def select(self, enc, readonly=False):
        self.selected = enc

    def find(self, mid):
        if self.selected == "INBOX.Drafts":
            return [b"9"] if mid == "<d1@tickett.com>" else []
        return [b"1"] if self.has_original and mid == "<m1@x>" else []

    def fetch(self, uid):
        return RAW

    def append(self, enc, flags, when, raw):
        self.appended.append((enc, flags, raw))

    def delete(self, uid):
        self.deleted.append((self.selected, uid))

    def move(self, uid, dest):
        self.moved.append((self.selected, dest))

    def logout(self):
        self.closed = True


class Redactor:
    def redact(self, subject, text, names=(), addrs=()):
        return subject, text.replace("Ana", "[NAME]"), {}, []


def state_with_draft(body=BODY, flag="", bcc=""):
    st = RT.State(Path(tempfile.mkdtemp()) / "router.db")
    st.record("<m1@x>", [], RT.Decision("F1", False, "claude", "high", "x"), moved=True)

    class Drafter:
        sha = "s"
    out = {"reply": "x", "language": "pt-PT", "needs_action": False, "action_note": "", "missing_facts": [],
           "confidence": "high", "usage": {}}
    DR.save(Box(), "INBOX.Drafts", E.record_from_bytes(RAW, "t"), "<m1@x>", RT.Decision("F1", False, "claude", "high", "x"),
            st, out, Drafter(), CFG, lambda *a: None)
    st.db.execute("UPDATE drafts SET body=?, first_body=?, draft_mid='<d1@tickett.com>', created_at=?, flag=?, bcc=? "
                  "WHERE message_id='<m1@x>'", (body, body, V, flag, bcc))
    st.db.commit()
    return st


def test_a_saved_draft_keeps_the_customers_email():
    row = AC.pending(state_with_draft())[0]
    assert row["from_email"] == "ana@gmail.com" and row["newest_text"] == "Quando recebo o reembolso?"
    assert "> antes" in row["full_text"]


def test_send_mails_the_reply_files_it_in_sent_removes_the_draft_and_closes_the_card():
    st, box, sent = state_with_draft(), Box(), []
    msg = AC.send(CFG, st, CID, V, lambda *a: None, connect=lambda cfg: box, send_mail=lambda cfg, raw: sent.append(raw))
    assert msg == "Enviado para ana@gmail.com." and len(sent) == 1 and b"In-Reply-To: <m1@x>" in sent[0]
    assert box.appended[0][0] == "INBOX.Sent" and box.deleted == [("INBOX.Drafts", b"9")] and box.closed
    assert AC.pending(st) == []
    try:   # a second click finds nothing to send
        AC.send(CFG, st, CID, V, lambda *a: None, connect=lambda cfg: Box(), send_mail=lambda cfg, raw: sent.append(raw))
        raise AssertionError("should be stale")
    except AC.Stale:
        assert len(sent) == 1


def test_an_old_version_or_an_unsendable_draft_is_refused():
    st = state_with_draft()
    for version in ("old", ""):
        try:
            AC.send(CFG, st, CID, version, lambda *a: None, connect=lambda cfg: Box(), send_mail=lambda c, r: None)
            raise AssertionError("should be stale")
        except AC.Stale:
            pass
    st2 = state_with_draft(D.NOTE + "ligar ao promotor\n\n" + BODY)
    try:
        AC.send(CFG, st2, CID, V, lambda *a: None, connect=lambda cfg: Box(), send_mail=lambda c, r: None)
        raise AssertionError("should refuse")
    except ValueError as exc:
        assert "nota interna" in str(exc)


def test_an_email_answered_meanwhile_is_not_sent():
    st, sent = state_with_draft(), []
    try:
        AC.send(CFG, st, CID, V, lambda *a: None, connect=lambda cfg: Box(has_original=False),
                send_mail=lambda cfg, raw: sent.append(raw))
        raise AssertionError("should be stale")
    except AC.Stale:
        assert sent == [] and AC.pending(st) == []


class InP(Box):
    """An old hand-off: the customer's email sits in 0 Por Responder, not in its category folder."""

    def find(self, mid):
        if self.selected == "INBOX.0 Por Responder":
            return [b"4"] if self.has_original and mid == "<m1@x>" else []
        return [] if self.selected != "INBOX.Drafts" else super().find(mid)


def test_tratado_sem_resposta_closes_any_open_draft_and_files_the_email_as_answered():
    st, box = state_with_draft(), Box()
    LG.init(st)
    st.db.execute("INSERT INTO replies (message_id, folder, received_at) VALUES ('<m1@x>', 'F1', 'x')")
    st.db.commit()
    try:   # an old version is refused
        AC.answered_by_hand(CFG, st, CID, "old", lambda *a: None, connect=lambda cfg: Box())
        raise AssertionError("should be stale")
    except AC.Stale:
        pass
    assert AC.answered_by_hand(CFG, st, CID, V, lambda *a: None, connect=lambda cfg: box) == \
        "Tratado sem resposta: passou para 10 Respondidos."
    assert box.moved == [("INBOX.1 Cancelamentos e Reembolsos", "INBOX.10 Respondidos")] and box.closed
    assert box.deleted == [("INBOX.Drafts", b"9")] and AC.pending(st) == []
    assert closed_row(st)["closed_how"] == "answered" and st.get("<m1@x>")["folder"] == "A"
    assert st.db.execute("SELECT how FROM replies").fetchone()[0] == "respondido"
    assert st.thread_folder(["<m1@x>"]) == "F1"   # a follow-up opens the conversation again in its category
    try:   # a second click finds nothing open
        AC.answered_by_hand(CFG, st, CID, V, lambda *a: None, connect=lambda cfg: Box())
        raise AssertionError("should be stale")
    except AC.Stale:
        pass


def test_tratado_sem_resposta_finds_an_old_hand_off_in_0_por_responder_or_nowhere():
    st, box = state_with_draft(), InP()
    DR.init(st)
    st.db.execute("INSERT INTO handoffs (message_id, reason, created_at) VALUES ('<m1@x>', 'legal', 'x')")
    st.db.commit()
    AC.answered_by_hand(CFG, st, CID, V, lambda *a: None, connect=lambda cfg: box)
    assert box.moved == [("INBOX.0 Por Responder", "INBOX.10 Respondidos")] and AC.waiting_for_person(st) == []
    st2, gone = state_with_draft(), Box(has_original=False)
    AC.answered_by_hand(CFG, st2, CID, V, lambda *a: None, connect=lambda cfg: gone)
    assert gone.moved == [] and AC.pending(st2) == []


def test_the_customers_email_is_also_looked_for_in_0_por_responder():
    st, box, sent = state_with_draft(), InP(), []
    msg, _ = AC.edit(CFG, st, CID, V, "Olá Ana,\n\nTexto.", lambda *a: None, connect=lambda cfg: box)
    assert msg == "Rascunho guardado." and box.appended[0][0] == "INBOX.Drafts"
    AC.send(CFG, st, CID, AC.pending(st)[0]["created_at"], lambda *a: None, connect=lambda cfg: InP(),
            send_mail=lambda c, r: sent.append(r))
    assert len(sent) == 1


def test_an_open_hand_off_without_a_draft_becomes_an_empty_editable_draft():
    st = RT.State(Path(tempfile.mkdtemp()) / "router.db")
    DR.init(st)
    LG.init(st)
    st.db.execute("INSERT INTO decisions (message_id, folder, suggested) VALUES ('<h1@x>', 'P', 'F3')")
    for mid, answered in (("<h1@x>", None), ("<h2@x>", "2026-10-20"), ("<h3@x>", None)):
        st.db.execute("INSERT INTO handoffs (message_id, reason, created_at, subject, from_email, newest_text, "
                      "answered_at) VALUES (?, 'legal', '2026-10-19T10:00:00+00:00', 'Fatura', 'ana@gmail.com', "
                      "'Quero a fatura.', ?)", (mid, answered))
    st.db.execute("INSERT INTO replies (message_id, answered_at) VALUES ('<h3@x>', '2026-10-21')")   # answered
    st.db.commit()
    assert AC.pending(st) == []   # reading the dashboard writes nothing; the router cycle adopts them
    LG.after_cycle(st, {}, lambda *a: None)
    rows = AC.pending(st)
    assert [r["message_id"] for r in rows] == ["<h1@x>"]
    r = rows[0]
    assert (r["folder"], r["body"], r["flag"], r["note"], r["draft_mid"]) == ("F3", "", "person_must_act",
                                                                              DR.EMPTY_NOTE, "")
    assert (r["subject"], r["from_email"], r["newest_text"]) == ("Fatura", "ana@gmail.com", "Quero a fatura.")
    DR.adopt_handoffs(st)
    assert len(AC.pending(st)) == 1   # once
    assert DR.EMPTY_NOTE == "Não foi possível escrever um rascunho; escreva a resposta aqui."
    try:
        AC.send(CFG, st, AC.card_id("<h1@x>"), r["created_at"], lambda *a: None, connect=lambda cfg: Box())
        raise AssertionError("should refuse")
    except ValueError as exc:
        assert str(exc) == "Não pode ser enviado: o texto está vazio."


def test_a_failed_move_reopens_tratado_sem_resposta_and_records_nothing():
    st = state_with_draft()
    LG.init(st)
    st.db.execute("INSERT INTO replies (message_id, folder, received_at) VALUES ('<m1@x>', 'F1', 'x')")
    st.db.commit()

    st.db.execute("UPDATE drafts SET auto_after='2026-10-19T11:00:00+00:00'")   # a scheduled automatic send
    st.db.commit()

    class Broken(Box):
        def move(self, uid, dest):
            raise OSError("imap down")

        def logout(self):   # a failed goodbye must not hide the real error
            raise OSError("bye")
    try:
        AC.answered_by_hand(CFG, st, CID, V, lambda *a: None, connect=lambda cfg: Broken())
        raise AssertionError("should raise")
    except ValueError as exc:
        assert str(exc) == "Não foi possível mover o email no servidor de correio. Nada foi alterado; tente outra vez."
    row = closed_row(st)
    assert row["closed_at"] is None and row["closed_how"] is None and len(AC.pending(st)) == 1
    assert row["auto_after"] is None   # the reply the person chose not to send never goes out on its own
    assert st.get("<m1@x>")["folder"] == "F1"
    assert st.db.execute("SELECT answered_at FROM replies").fetchone()[0] is None


def test_a_rewritten_empty_draft_takes_the_models_flag_and_the_finance_copy():
    class Drafter:
        sha = "abc"

        def draft(self, *a, **k):
            return {"reply": "Recebemos o pedido de reembolso.", "flag": "tickett_refund", "language": "pt-PT",
                    "needs_action": False, "action_note": "", "missing_facts": [], "confidence": "high", "usage": {}}
    cfg = {**CFG, "refund_bcc": "finance@tickett.com"}
    for start, flag in (("person_must_act", "tickett_refund"), ("relayed", "relayed")):
        st, box = state_with_draft(body="", flag=start), Box()
        st.db.execute("UPDATE drafts SET note=?, first_body=''", (DR.EMPTY_NOTE,))
        st.db.commit()
        AC.rewrite(cfg, st, CID, V, "escrever", lambda *a: None, Drafter(), Redactor(), connect=lambda c: box)
        row = AC.pending(st)[0]
        assert (row["flag"], row["bcc"]) == (flag, "finance@tickett.com"), start
        assert (b"Bcc: finance@tickett.com" in box.appended[0][2]) if flag != "relayed" else box.appended == []
    st, box = state_with_draft(flag="person_must_act"), Box()   # a real draft keeps what it had
    AC.rewrite(cfg, st, CID, V, "x", lambda *a: None, Drafter(), Redactor(), connect=lambda c: box)
    assert (AC.pending(st)[0]["flag"], AC.pending(st)[0]["bcc"]) == ("person_must_act", "")


def test_respondo_eu_is_gone():
    assert not hasattr(AC, "to_person") and not hasattr(DR, "note_handoff")


def test_rewrite_follows_the_comment_and_replaces_the_draft():
    st, box, seen = state_with_draft(), Box(), {}

    class Drafter:
        sha = "abc"

        def draft(self, case, folder, match, today, review=None):
            seen.update(review)
            return {"reply": "Versão mais curta.", "language": "pt-PT", "needs_action": False, "action_note": "",
                    "missing_facts": [], "confidence": "high", "handoff": False, "usage": {"in": 5, "out": 2}}
    assert AC.rewrite(CFG, st, CID, V, "mais curto", lambda *a: None, Drafter(), Redactor(),
                      connect=lambda cfg: box) == "Nova versão pronta."
    assert seen["instruction"] == "mais curto" and "Ana" not in seen["previous"]
    row = AC.pending(st)[0]
    assert "Versão mais curta." in row["body"] and row["created_at"] != V
    assert box.appended[0][0] == "INBOX.Drafts" and box.deleted == [("INBOX.Drafts", b"9")]


def test_a_rewrite_or_an_edit_keeps_the_flag_and_the_finance_copy():
    class Drafter:
        sha = "abc"

        def draft(self, *a, **k):
            return {"reply": "Outra versão.", "flag": "", "language": "pt-PT", "needs_action": False,
                    "action_note": "", "missing_facts": [], "confidence": "high", "usage": {}}
    for change in (lambda st, box: AC.rewrite(CFG, st, CID, V, "x", lambda *a: None, Drafter(), Redactor(),
                                              connect=lambda cfg: box),
                   lambda st, box: AC.edit(CFG, st, CID, V, "Olá,\n\nTexto meu.", lambda *a: None,
                                           connect=lambda cfg: box)):
        st, box = state_with_draft(flag="tickett_refund", bcc="finance@tickett.com"), Box()
        change(st, box)
        row = AC.pending(st)[0]
        assert (row["flag"], row["bcc"]) == ("tickett_refund", "finance@tickett.com") and row["created_at"] != V
        assert b"Bcc: finance@tickett.com" in box.appended[0][2]


NO_TO = "Escreva o endereço do cliente em Para: antes de enviar."


def relayed_state(to):
    st = state_with_draft(flag="relayed")
    st.db.execute("UPDATE drafts SET to_override=?, draft_mid=''", (to,))
    st.db.commit()
    return st


def test_enviar_refuses_relayed_mail_without_one_valid_address_in_para():
    for to in ("", None, "rui@gmail.com, eva@gmail.com", "ana@gmail.com", "apoio@tickett.com", "nada"):
        st, sent = relayed_state(to), []   # ana@gmail.com is the relaying sender in RAW
        try:
            AC.send(CFG, st, CID, V, lambda *a: None, connect=lambda cfg: Box(), send_mail=lambda c, r: sent.append(r))
            raise AssertionError(f"should refuse {to!r}")
        except ValueError as exc:
            assert str(exc) == NO_TO, to
        assert sent == [] and AC.pending(st)[0]["closed_at"] is None
    assert AC.refusal({"flag": "relayed", "from_email": "ana@gmail.com"}) == NO_TO
    assert AC.refusal({"flag": "relayed", "from_email": "ana@gmail.com", "to_override": "rui@gmail.com"}) == ""
    assert AC.refusal({"flag": "legal"}) == ""


def test_enviar_sends_a_relayed_reply_to_the_address_in_para():
    import email as em
    st, box, sent = relayed_state("rui@gmail.com"), Box(), []
    msg = AC.send(CFG, st, CID, V, lambda *a: None, connect=lambda cfg: box, send_mail=lambda c, r: sent.append(r))
    m = em.message_from_bytes(sent[0])
    assert msg == "Enviado para rui@gmail.com." and m["To"] == "rui@gmail.com" and m["In-Reply-To"] == "<m1@x>"
    assert AC.recipient({"flag": "relayed", "to_override": "rui@gmail.com", "from_email": "x@y.pt"}) == "rui@gmail.com"


def test_the_para_field_is_saved_as_a_new_version():
    st = relayed_state("")
    msg, version = AC.set_to(CFG, st, CID, V, " rui@gmail.com ", lambda *a: None)
    row = AC.pending(st)[0]
    assert msg == "Endereço guardado." and version == row["created_at"] != V and row["to_override"] == "rui@gmail.com"
    assert not DR.placeholder({**row, "body": "", "note": DR.EMPTY_NOTE})   # a person touched it: never replaced
    assert (row["edited"], row["to_set_by_person"]) == (0, 1)   # not an edit of the text (AI-quality numbers)
    for bad in ("rui@gmail.com, eva@gmail.com", "ana@gmail.com", "x@tickett.com", "rui"):
        try:
            AC.set_to(CFG, st, CID, version, bad, lambda *a: None)
            raise AssertionError(f"accepted {bad!r}")
        except ValueError as exc:
            assert str(exc) == "Escreva um só endereço de email do cliente (não o do parceiro nem um @tickett.com)."
    assert AC.set_to(CFG, st, CID, version, "", lambda *a: None)[0] == "Endereço guardado."   # cleared
    try:
        AC.set_to(CFG, st, CID, V, "eva@gmail.com", lambda *a: None)   # the version shown is an old one
        raise AssertionError("should be stale")
    except AC.Stale:
        pass
    try:
        AC.set_to(CFG, state_with_draft(), CID, V, "rui@gmail.com", lambda *a: None)
        raise AssertionError("only relayed drafts")
    except ValueError as exc:
        assert str(exc) == "Só um email reencaminhado tem Para: editável."


def test_a_tickett_refund_is_sent_with_a_copy_to_finance():
    st, box, sent = state_with_draft(flag="tickett_refund", bcc="finance@tickett.com"), Box(), []
    AC.send(CFG, st, CID, V, lambda *a: None, connect=lambda cfg: box, send_mail=lambda c, r: sent.append(r))
    assert b"Bcc: finance@tickett.com" in sent[0]   # smtp_send takes it off the customer's copy
    assert box.appended[0][0] == "INBOX.Sent" and b"Bcc: finance@tickett.com" in box.appended[0][2]


def test_smtp_puts_the_bcc_in_the_envelope_only():
    import os
    seen = {}

    class SMTP:
        def __init__(self, *a, **k):
            pass

        def login(self, user, pw):
            pass

        def sendmail(self, frm, to, data):
            seen.update(frm=frm, to=to, data=data)

        def quit(self):
            pass
    raw, _ = D.reply_message(E.record_from_bytes(RAW, "t"), BODY, "support@tickett.com", "finance@tickett.com")
    saved, AC.smtplib.SMTP_SSL = AC.smtplib.SMTP_SSL, SMTP
    os.environ["IMAP_PASSWORD"] = "test-only"
    try:
        AC.smtp_send({"imap": {"host": "h", "user": "support@tickett.com", "keychain_service": "s"}}, raw)
        assert seen["to"] == ["ana@gmail.com", "finance@tickett.com"] and seen["frm"] == "support@tickett.com"
        assert b"finance@tickett.com" not in seen["data"] and b"Bcc" not in seen["data"]
        assert b"In-Reply-To: <m1@x>" in seen["data"]
        plain, _ = D.reply_message(E.record_from_bytes(RAW, "t"), BODY, "support@tickett.com")
        AC.smtp_send({"imap": {"host": "h", "user": "support@tickett.com", "keychain_service": "s"}}, plain)
        assert seen["to"] == ["ana@gmail.com"] and seen["data"] == plain   # nothing re-encoded without a Bcc
    finally:
        AC.smtplib.SMTP_SSL = saved
        del os.environ["IMAP_PASSWORD"]


def test_the_finance_address_is_a_config_value():
    import json
    cfg = json.loads((Path(__file__).resolve().parent.parent / "config" / "router.json").read_text(encoding="utf-8"))
    assert cfg["refund_bcc"] == "finance@tickett.com"


def test_an_empty_comment_is_refused():
    try:
        AC.rewrite(CFG, state_with_draft(), CID, V, "   ", lambda *a: None, None, None, connect=lambda cfg: Box())
        raise AssertionError("should refuse")
    except ValueError:
        pass


def sent_state():
    st = state_with_draft()
    AC.send(CFG, st, CID, V, lambda *a: None, connect=lambda cfg: Box(), send_mail=lambda c, r: None)
    return st


def closed_row(st):
    return AC._rows(st, "SELECT * FROM drafts WHERE message_id='<m1@x>'")[0]


def test_edit_replaces_the_draft_and_marks_it_edited():
    st, box = state_with_draft(), Box()
    msg, version = AC.edit(CFG, st, CID, V, "  Olá Ana,\n\nTexto novo.  ", lambda *a: None, connect=lambda cfg: box)
    row = AC.pending(st)[0]
    assert msg == "Rascunho guardado." and version == row["created_at"] != V
    assert row["body"] == "Olá Ana,\n\nTexto novo." and row["edited"] == 1 and row["first_body"] == BODY
    assert b"Texto novo." in box.appended[0][2] and box.appended[0][0] == "INBOX.Drafts"
    assert box.deleted == [("INBOX.Drafts", b"9")] and box.closed and row["draft_mid"] != "<d1@tickett.com>"
    try:
        AC.edit(CFG, st, CID, version, "   ", lambda *a: None, connect=lambda cfg: Box())
        raise AssertionError("should refuse")
    except ValueError as exc:
        assert str(exc) == "O texto está vazio."


def test_an_edit_or_a_rewrite_cancels_a_scheduled_automatic_send():
    class Drafter:
        sha = "abc"

        def draft(self, *a, **k):
            return {"reply": "Outra.", "language": "pt-PT", "needs_action": False, "action_note": "",
                    "missing_facts": [], "confidence": "high", "handoff": False, "usage": {}}
    for change in (lambda st: AC.edit(CFG, st, CID, V, "Texto novo.", lambda *a: None, connect=lambda cfg: Box()),
                   lambda st: AC.rewrite(CFG, st, CID, V, "mais curto", lambda *a: None, Drafter(), Redactor(),
                                         connect=lambda cfg: Box())):
        st = state_with_draft()
        st.db.execute("UPDATE drafts SET auto_after='2026-10-19T11:00:00+00:00'")
        st.db.commit()
        change(st)
        assert AC.pending(st)[0]["auto_after"] is None


def test_edit_on_an_old_version_is_refused():
    st, box = state_with_draft(), Box()
    try:
        AC.edit(CFG, st, CID, "old", "texto", lambda *a: None, connect=lambda cfg: box)
        raise AssertionError("should be stale")
    except AC.Stale:
        assert box.appended == [] and AC.pending(st)[0]["body"] == BODY


def test_a_rewritten_draft_is_not_counted_unchanged():
    st = sent_state()
    assert AC.unchanged(closed_row(st)) is True
    st2, box = state_with_draft(), Box()

    class Drafter:
        sha = "abc"

        def draft(self, case, folder, match, today, review=None):
            return {"reply": "Versão mais curta.", "language": "pt-PT", "needs_action": False, "action_note": "",
                    "missing_facts": [], "confidence": "high", "handoff": False,
                    "usage": {"in": 5, "out": 2, "cache_read": 7, "cache_write": 3}}
    AC.rewrite(CFG, st2, CID, V, "mais curto", lambda *a: None, Drafter(), Redactor(), connect=lambda cfg: box)
    row = AC.pending(st2)[0]
    assert row["rewrites"] == 1 and row["cache_read"] == 7 and row["cache_write"] == 3
    AC.send(CFG, st2, CID, row["created_at"], lambda *a: None, connect=lambda cfg: Box(), send_mail=lambda c, r: None)
    assert AC.unchanged(closed_row(st2)) is False
    st3 = state_with_draft()
    AC.edit(CFG, st3, CID, V, "Olá,\n\nOutro texto.", lambda *a: None, connect=lambda cfg: Box())
    AC.send(CFG, st3, CID, AC.pending(st3)[0]["created_at"], lambda *a: None, connect=lambda cfg: Box(),
            send_mail=lambda c, r: None)
    assert AC.unchanged(closed_row(st3)) is False


def test_sending_records_the_answer_in_the_ledger():
    import ledger as LG
    st = state_with_draft()
    LG.init(st)
    st.db.execute("INSERT INTO replies (message_id, folder, received_at) VALUES ('<m1@x>', 'F1', ?)", (V,))
    AC.send(CFG, st, CID, V, lambda *a: None, connect=lambda cfg: Box(), send_mail=lambda c, r: None)
    row = st.db.execute("SELECT how FROM replies WHERE message_id='<m1@x>'").fetchone()
    assert row == ("dashboard",)


def test_a_rewrite_comment_with_an_email_address_is_refused():
    st, box = state_with_draft(), Box()
    seen = []

    class Leaky:
        def redact(self, subject, text, names=(), addrs=()):
            seen.append(text)
            return subject, text, {}, (["email"] if "@" in text else [])

    class Drafter:
        sha = "abc"

        def draft(self, *a, **k):
            raise AssertionError("must not reach the model")
    try:
        AC.rewrite(CFG, st, CID, V, "responde a joao@x.com", lambda *a: None, Drafter(), Leaky(), connect=lambda cfg: box)
        raise AssertionError("should refuse")
    except ValueError as exc:
        assert str(exc) == ("O comentário tem dados pessoais que não podem ir para o modelo. "
                            "Reescreva sem nomes, emails ou números.")
    assert any("joao@x.com" in t for t in seen) and box.appended == [] and AC.pending(st)[0]["rewrites"] == 0


def test_recipient_is_the_reply_to_address_or_the_sender():
    assert AC.recipient({"reply_to": "Ana <ana.s@gmail.com>", "from_email": "ana@gmail.com"}) == "ana.s@gmail.com"
    assert AC.recipient({"reply_to": "", "from_email": "ana@gmail.com"}) == "ana@gmail.com"
    assert AC.recipient({"reply_to": None, "from_email": "ana@gmail.com"}) == "ana@gmail.com"


def test_cards_are_ordered_by_the_customers_email_not_by_the_last_rewrite():
    st = RT.State(Path(tempfile.mkdtemp()) / "router.db")
    DR.init(st)
    # <old> arrived first but was rewritten last; <bare> has no received_at and falls back to created_at
    for mid, received, created in [("<new>", "2026-10-19T09:00:00+00:00", "2026-10-19T09:01:00+00:00"),
                                   ("<old>", "2026-10-18T08:00:00+00:00", "2026-10-19T12:00:00+00:00"),
                                   ("<bare>", "", "2026-10-18T20:00:00+00:00")]:
        st.db.execute("INSERT INTO drafts (message_id, received_at, created_at) VALUES (?,?,?)", (mid, received, created))
        st.db.execute("INSERT INTO handoffs (message_id, received_at, created_at) VALUES (?,?,?)", (mid, received, created))
    st.db.commit()
    assert [r["message_id"] for r in AC.pending(st)] == ["<old>", "<bare>", "<new>"]
    assert [r["message_id"] for r in AC.waiting_for_person(st)] == ["<old>", "<bare>", "<new>"]


def test_the_draft_is_claimed_before_the_mail_leaves():
    st, seen = state_with_draft(), []

    def send_mail(cfg, raw):   # by the time SMTP runs the draft is closed, so no later step can leave it open
        seen.append(closed_row(st)["closed_at"])
    AC.send(CFG, st, CID, V, lambda *a: None, connect=lambda cfg: Box(), send_mail=send_mail)
    assert len(seen) == 1 and seen[0] is not None and closed_row(st)["closed_how"] == "sent"


def test_a_claim_lost_meanwhile_sends_nothing():
    st, sent = state_with_draft(), []

    def connect(cfg):   # the router claims this version while the page's request is on its way
        assert AC.claim(st, closed_row(st), "auto")
        return Box()
    try:
        AC.send(CFG, st, CID, V, lambda *a: None, connect=connect, send_mail=lambda c, r: sent.append(r))
        raise AssertionError("should be stale")
    except AC.Stale:
        assert sent == [] and closed_row(st)["closed_how"] == "auto"


def test_two_claims_on_the_same_version_only_one_wins():
    st = state_with_draft()
    other = RT.State(Path(st.db.execute("PRAGMA database_list").fetchone()[2]))   # the other process's connection
    row = closed_row(st)
    assert [AC.claim(st, row, "auto"), AC.claim(other, row, "sent")] == [True, False]
    assert AC.claim(st, {**row, "created_at": "old"}, "sent") is False


def test_a_failed_smtp_send_reopens_the_draft():
    st, box = state_with_draft(), Box()

    def fail(cfg, raw):
        raise OSError("smtp down")
    try:
        AC.send(CFG, st, CID, V, lambda *a: None, connect=lambda cfg: box, send_mail=fail)
        raise AssertionError("should raise")
    except OSError:
        pass
    row = closed_row(st)
    assert row["closed_at"] is None and row["closed_how"] is None and box.appended == []
    assert len(AC.pending(st)) == 1


def test_a_failure_after_the_mail_left_keeps_the_draft_closed():
    st, box, sent = state_with_draft(), Box(), []
    box.append = lambda *a: (_ for _ in ()).throw(OSError("imap"))
    try:
        AC.send(CFG, st, CID, V, lambda *a: None, connect=lambda cfg: box, send_mail=lambda c, r: sent.append(r))
        raise AssertionError("should raise")
    except OSError:
        pass
    assert len(sent) == 1 and closed_row(st)["closed_how"] == "sent" and AC.pending(st) == []


def test_a_reply_to_that_is_not_one_address_is_shown_raw_and_never_sent():
    for bad in ("a@x.com, b@y.com", "garbage", "a@x.com b@y.com"):
        r = {"reply_to": bad, "from_email": "ana@gmail.com"}
        assert AC.recipient(r) == bad and not AC.reply_to_ok(r), bad
    assert AC.reply_to_ok({"reply_to": "", "from_email": "ana@gmail.com"})
    assert AC.reply_to_ok({"reply_to": "Ana <ana.s@gmail.com>"})
    st, sent = state_with_draft(), []
    raw = RAW.replace(b"Message-ID", b"Reply-To: a@x.com, b@y.com\r\nMessage-ID")

    class B(Box):
        def fetch(self, uid):
            return raw
    try:
        AC.send(CFG, st, CID, V, lambda *a: None, connect=lambda cfg: B(), send_mail=lambda c, r: sent.append(r))
        raise AssertionError("should refuse")
    except ValueError as exc:
        assert str(exc) == "O endereço de resposta não é válido. Responda pelo Mail."
    assert sent == [] and len(AC.pending(st)) == 1


def test_the_sent_message_names_the_reply_to_address():
    st = state_with_draft()
    raw = RAW.replace(b"Message-ID", b"Reply-To: Ana <ana.s@gmail.com>\r\nMessage-ID")

    class B(Box):
        def fetch(self, uid):
            return raw
    msg = AC.send(CFG, st, CID, V, lambda *a: None, connect=lambda cfg: B(), send_mail=lambda c, r: None)
    assert msg == "Enviado para ana.s@gmail.com."


def test_an_edit_over_20000_characters_is_refused():
    try:
        AC.edit(CFG, state_with_draft(), CID, V, "x" * 20001, lambda *a: None, connect=lambda cfg: Box())
        raise AssertionError("should refuse")
    except ValueError as exc:
        assert str(exc) == "O texto é demasiado longo (máximo 20 000 caracteres)."


def test_a_relayed_draft_is_rewritten_and_edited_without_a_mailbox_draft():
    class Drafter:
        sha = "abc"

        def draft(self, *a, **k):
            return {"reply": "Outra versão.", "flag": "", "language": "pt-PT", "needs_action": False,
                    "action_note": "", "missing_facts": [], "confidence": "high", "usage": {}}
    for change in (lambda st, box: AC.rewrite(CFG, st, CID, V, "x", lambda *a: None, Drafter(), Redactor(),
                                              connect=lambda cfg: box),
                   lambda st, box: AC.edit(CFG, st, CID, V, "Olá,\n\nTexto meu.", lambda *a: None,
                                           connect=lambda cfg: box)):
        st, box = state_with_draft(flag="relayed"), Box()
        st.db.execute("UPDATE drafts SET draft_mid=''")
        st.db.commit()
        change(st, box)
        row = AC.pending(st)[0]
        assert row["flag"] == "relayed" and row["draft_mid"] == "" and row["created_at"] != V
        assert box.appended == [] and box.deleted == []


def test_a_relayed_draft_is_never_sent_automatically_even_if_asked():
    import extract as Ex
    st, sent = relayed_state("rui@gmail.com"), []
    row = AC.pending(st)[0]
    try:
        AC.claim_and_send(CFG, st, row, Ex.record_from_bytes(RAW, "t"), "auto", lambda c, r: sent.append(r))
        raise AssertionError("should refuse")
    except ValueError as exc:
        assert str(exc) == "Um email reencaminhado nunca é enviado automaticamente."
    assert sent == [] and AC.pending(st)[0]["closed_at"] is None


def test_a_rewritten_relayed_reply_does_not_greet_the_partner():
    class Drafter:
        sha = "abc"

        def draft(self, *a, **k):
            return {"reply": "Vamos verificar.", "flag": "", "language": "pt-PT", "needs_action": False,
                    "action_note": "", "missing_facts": [], "confidence": "high", "usage": {}}
    st = relayed_state("rui@gmail.com")
    AC.rewrite(CFG, st, CID, V, "x", lambda *a: None, Drafter(), Redactor(), connect=lambda c: Box())
    assert AC.pending(st)[0]["body"].startswith("Olá,\n\n")   # RAW's sender is ANA SILVA; no name in the text

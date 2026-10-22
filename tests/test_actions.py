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


def state_with_draft(body=BODY):
    st = RT.State(Path(tempfile.mkdtemp()) / "router.db")
    st.record("<m1@x>", [], RT.Decision("F1", False, "claude", "high", "x"), moved=True)

    class Drafter:
        sha = "s"
    out = {"reply": "x", "language": "pt-PT", "needs_action": False, "action_note": "", "missing_facts": [],
           "confidence": "high", "usage": {}}
    DR.save(Box(), "INBOX.Drafts", E.record_from_bytes(RAW, "t"), "<m1@x>", RT.Decision("F1", False, "claude", "high", "x"),
            st, out, Drafter(), CFG, lambda *a: None)
    st.db.execute("UPDATE drafts SET body=?, first_body=?, draft_mid='<d1@tickett.com>', created_at=? "
                  "WHERE message_id='<m1@x>'", (body, body, V))
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


def test_to_person_moves_the_email_and_lists_it_as_waiting():
    st, box = state_with_draft(), Box()
    assert AC.to_person(CFG, st, CID, V, lambda *a: None, connect=lambda cfg: box) == "Passou para 0 Por Responder."
    assert box.moved == [("INBOX.1 Cancelamentos e Reembolsos", "INBOX.0 Por Responder")]
    waiting = AC.waiting_for_person(st)
    assert waiting[0]["from_email"] == "ana@gmail.com" and AC.pending(st) == []
    assert st.thread_folder(["<m1@x>"]) == "P"
    DR.mark_answered(st, "<m1@x>")
    assert AC.waiting_for_person(st) == []


def test_respondido_moves_the_handoff_to_answered_and_records_it():
    st = state_with_draft()
    AC.to_person(CFG, st, CID, V, lambda *a: None, connect=lambda cfg: Box())
    LG.init(st)
    st.db.execute("INSERT INTO replies (message_id, folder, received_at) VALUES ('<m1@x>', 'P', 'x')")
    st.db.commit()
    box = Box()
    assert AC.answered_by_hand(CFG, st, CID, lambda *a: None, connect=lambda cfg: box) == "Marcado como respondido."
    assert box.moved == [("INBOX.0 Por Responder", "INBOX.10 Respondidos")] and box.closed
    assert AC.waiting_for_person(st) == [] and st.thread_folder(["<m1@x>"]) == "P"
    assert st.db.execute("SELECT how FROM replies").fetchone()[0] == "respondido"
    try:   # a second click finds nothing open
        AC.answered_by_hand(CFG, st, CID, lambda *a: None, connect=lambda cfg: Box())
        raise AssertionError("should be stale")
    except AC.Stale:
        pass


def test_respondido_on_a_handoff_already_moved_only_closes_it():
    st = state_with_draft()
    AC.to_person(CFG, st, CID, V, lambda *a: None, connect=lambda cfg: Box())
    box = Box(has_original=False)
    AC.answered_by_hand(CFG, st, CID, lambda *a: None, connect=lambda cfg: box)
    assert box.moved == [] and AC.waiting_for_person(st) == []


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


def test_a_rewrite_that_hands_off_goes_to_a_person():
    st = state_with_draft()

    class Drafter:
        sha = "abc"

        def draft(self, *a, **k):
            return {"reply": "", "handoff": True, "handoff_reason": "tickett_refund", "action_note": "reembolso Tickett",
                    "language": "pt-PT", "needs_action": False, "missing_facts": [], "confidence": "high"}
    AC.rewrite(CFG, st, CID, V, "x", lambda *a: None, Drafter(), Redactor(), connect=lambda cfg: Box())
    assert AC.pending(st) == [] and AC.waiting_for_person(st)[0]["reason"] == "tickett_refund"


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

"""Tests for draft_all.py: a draft for every open email (a fake mailbox, a stub drafter; no IMAP, no API)."""
import contextlib
import email
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import draft_all as DA
import drafts as DR
import ledger as LG
import router as RT

CFG = {"folders": {"F1": "1 Cancelamentos e Reembolsos", "F2": "2 Bilhetes não recebidos", "F3": "3 Compra e Pagamento",
                   "F5": "5 Informações do Evento", "F8": "8 Não-cliente", "F9": "9 A Rever"},
       "answered_folder": "10 Respondidos", "handoff_folder": "0 Por Responder",
       "imap": {"user": "support@tickett.com"}, "refund_bcc": "finance@tickett.com"}


def raw(mid, sender="ana@gmail.com", subject="Pedido", body="Quando recebo o reembolso?"):
    return (f"Subject: {subject}\r\nFrom: Cliente <{sender}>\r\nMessage-ID: {mid}\r\nDate: Mon, 19 Oct 2026 10:00:00 +0000\r\n"
            f"Content-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n").encode()


MAILS = {
    "1 Cancelamentos e Reembolsos": {b"1": raw("<new@x>")},
    "2 Bilhetes não recebidos": {b"2": raw("<drafted@x>", "rui@gmail.com")},
    "0 Por Responder": {b"3": raw("<handoff@x>", "eva@gmail.com"), b"4": raw("<form@x>", "wp@calemamusic.com")},
    "9 A Rever": {b"5": raw("<answered@x>", "joao@gmail.com"), b"8": raw("<staff@x>", "eu@tickett.com", "Interno")},
    "5 Informações do Evento": {b"6": raw("<empty@x>", "rita@gmail.com")},
    "8 Não-cliente": {b"7": raw("<spam@x>", "x@spam.example")},
}


class Box:
    prefix = "INBOX."

    def __init__(self):
        self.selected, self.appended, self.created, self.moved = None, [], [], []

    def folders(self):
        return {**{"INBOX." + n: "" for n in MAILS}, "INBOX.Drafts": "\\Drafts"}

    def encoded(self, names):
        return {k: "INBOX." + v for k, v in names.items()}

    def ensure_folders(self, names):
        self.created.append(names)
        return self.encoded(names)

    def select(self, enc, readonly=False):
        assert readonly, "draft_all only reads the mailbox"
        self.selected = enc

    def flagged(self):
        return []

    def uids(self, since=None):
        return list(MAILS.get(self.selected.removeprefix("INBOX."), {}))

    def fetch(self, uid):
        return MAILS[self.selected.removeprefix("INBOX.")][uid]

    def append(self, enc, flags, when, data):
        self.appended.append((enc, data))

    def move(self, uid, dest):
        self.moved.append(uid)

    def logout(self):
        pass


class Redactor:
    def redact(self, subject, text, names=(), addrs=()):
        return subject, text, {}, []


class Drafter:
    sha = "s"

    def __init__(self, flag=""):
        self.flag, self.calls = flag, []

    def draft(self, case, folder_name, match_text, today):
        self.calls.append(folder_name)
        if "Empty" in match_text:
            return None
        return {"reply": "Vamos verificar.", "language": "pt-PT", "needs_action": False, "action_note": "",
                "missing_facts": [], "confidence": "high", "flag": self.flag, "usage": {}}


def state(tmp_path):
    st = RT.State(tmp_path / "router.db")
    DR.init(st)
    LG.init(st)
    st.db.executemany("INSERT INTO decisions (message_id, folder, suggested, urgent) VALUES (?,?,?,0)",
                      [("<handoff@x>", "P", "F3"), ("<form@x>", "P", "F7"), ("<new@x>", "F1", "F1")])
    st.db.execute("INSERT INTO drafts (message_id, folder, body, created_at) VALUES ('<drafted@x>', 'F2', 'x', 'v')")
    st.db.execute("INSERT INTO replies (message_id, answered_at) VALUES ('<answered@x>', '2026-10-20')")
    st.db.execute("INSERT INTO drafts (message_id, folder, body, first_body, note, flag, draft_mid, created_at) "
                  "VALUES ('<empty@x>', 'F5', '', '', ?, 'person_must_act', '', 'v')", (DR.EMPTY_NOTE,))
    st.db.commit()
    return st


def test_open_emails_without_a_usable_draft_are_found_with_their_real_topic(tmp_path):
    st, box = state(tmp_path), Box()
    todo = DA.from_mailbox(box, st, CFG)
    assert sorted((r["message_id"], d.folder) for r, d, _ in todo) == [
        ("<empty@x>", "F5"), ("<form@x>", "F9"), ("<handoff@x>", "F3"), ("<new@x>", "F1")]
    assert box.created == [] and box.appended == [] and box.moved == []   # read-only
    assert DA.counts(todo, CFG) == {"1 Cancelamentos e Reembolsos": 1, "3 Compra e Pagamento": 1,
                                    "5 Informações do Evento": 1, "9 A Rever": 1}


def test_apply_drafts_them_like_the_router(tmp_path):
    st, box = state(tmp_path), Box()
    n = DA.draft(DA.from_mailbox(box, st, CFG), box, "INBOX.Drafts", st, Drafter("tickett_refund"), Redactor(), CFG,
                 lambda *a: None)
    rows = {r[0]: r[1:] for r in st.db.execute("SELECT message_id, folder, flag, bcc, draft_mid FROM drafts")}
    assert n == 4
    assert rows["<new@x>"][:3] == ("F1", "tickett_refund", "finance@tickett.com") and rows["<new@x>"][3]
    assert rows["<handoff@x>"][:2] == ("F3", "tickett_refund")
    assert rows["<form@x>"] == ("F9", "relayed", "finance@tickett.com", "")   # dashboard only
    assert rows["<empty@x>"][1] == "tickett_refund"   # the empty placeholder was replaced
    to = sorted(email.message_from_bytes(d)["To"] for _, d in box.appended)
    assert to == ["ana@gmail.com", "eva@gmail.com", "rita@gmail.com"]
    assert DA.from_mailbox(Box(), st, CFG) == []   # nothing left


def test_an_email_no_draft_can_be_written_for_gets_an_empty_one(tmp_path):
    st, box = state(tmp_path), Box()
    st.db.execute("UPDATE decisions SET folder='F1' WHERE message_id='<handoff@x>'")
    st.db.commit()
    todo = [t for t in DA.from_mailbox(box, st, CFG) if t[0]["message_id"] == "<new@x>"]
    todo[0][0]["subject"] = "Empty"
    assert DA.draft(todo, box, "INBOX.Drafts", st, Drafter(), Redactor(), CFG, lambda *a: None) == 0
    assert st.db.execute("SELECT body, flag, note FROM drafts WHERE message_id='<new@x>'").fetchone() == (
        "", "person_must_act", DR.EMPTY_NOTE)


def handoffs(st):
    st.db.executemany("INSERT INTO handoffs (message_id, reason, created_at, subject, from_name, from_email, "
                      "received_at, newest_text, full_text, answered_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      [("<handoff@x>", "tickett_refund", "c", "Reembolso", "Eva", "eva@gmail.com",
                        "2026-10-19T10:00:00+00:00", "Quero o reembolso.", "Quero o reembolso.\n> antes", None),
                       ("<form@x>", "person_must_act", "c", "Contacto", "", "wp@calemamusic.com", "", "Ola", "Ola",
                        None),
                       ("<done@x>", "legal", "c", "x", "", "a@b.pt", "", "x", "x", "2026-10-20")])
    st.db.commit()


def test_db_only_drafts_open_hand_offs_from_the_stored_text_without_a_mailbox(tmp_path):
    st = state(tmp_path)
    handoffs(st)
    todo = DA.from_db(st)
    assert sorted((r["message_id"], d.folder) for r, d, _ in todo) == [("<form@x>", "F9"), ("<handoff@x>", "F3")]
    drafter = Drafter()
    assert DA.draft(todo, None, "", st, drafter, Redactor(), CFG, lambda *a: None) == 2
    rows = {r[0]: r[1:] for r in st.db.execute("SELECT message_id, folder, flag, draft_mid, subject, from_email, "
                                               "newest_text, body FROM drafts")}
    assert rows["<handoff@x>"][:6] == ("F3", "", "", "Reembolso", "eva@gmail.com", "Quero o reembolso.")
    assert "Vamos verificar." in rows["<handoff@x>"][6]
    assert rows["<form@x>"][:3] == ("F9", "relayed", "")
    assert sorted(drafter.calls) == ["3 Compra e Pagamento", "9 A Rever"]
    assert DA.from_db(st) == []


def test_main_plans_read_only_and_applies_with_the_lock(tmp_path):
    st = state(tmp_path)
    handoffs(st)
    cfg = {**CFG, "state_dir": tmp_path}
    saved = RT.load_config, RT.connect, RT.build_drafter, RT.build_redactor, RT.load_secret_env
    connects = []
    RT.load_config = lambda: cfg
    RT.connect = lambda c: connects.append(1) or Box()
    RT.build_drafter = lambda *a: Drafter()
    RT.build_redactor = lambda: Redactor()
    RT.load_secret_env = lambda: None
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert DA.main(["--plan", "--db-only"]) == 0
        assert "3 Compra e Pagamento" in out.getvalue() and "2 open emails without a draft" in out.getvalue()
        assert connects == [] and st.db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 2
        with contextlib.redirect_stdout(io.StringIO()):
            assert DA.main(["--apply", "--db-only", "--limit", "10"]) == 0
        assert connects == [] and st.db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 4
        with contextlib.redirect_stdout(io.StringIO()):
            assert DA.main(["--plan"]) == 0
        assert connects == [1] and (tmp_path / "router.lock").exists()
    finally:
        RT.load_config, RT.connect, RT.build_drafter, RT.build_redactor, RT.load_secret_env = saved


def test_the_category_is_the_folder_the_email_sits_in_unless_it_is_0_por_responder(tmp_path):
    st, box = state(tmp_path), Box()
    st.db.execute("INSERT INTO decisions (message_id, folder) VALUES ('<empty@x>', 'F1')")   # moved by hand to 5
    st.db.commit()
    cats = {r["message_id"]: d.folder for r, d, _ in DA.from_mailbox(box, st, CFG)}
    assert cats["<empty@x>"] == "F5" and cats["<handoff@x>"] == "F3"


def test_a_row_sent_meanwhile_stays_sent(tmp_path):
    st, box = state(tmp_path), Box()
    todo = [t for t in DA.from_mailbox(box, st, CFG) if t[0]["message_id"] == "<empty@x>"]
    st.db.execute("UPDATE drafts SET body='Olá, enviado.', closed_at='c', closed_how='sent', sent_at='s' "
                  "WHERE message_id='<empty@x>'")   # a person wrote and sent it while the list was being made
    st.db.commit()
    drafter = Drafter()
    assert DA.draft(todo, box, "INBOX.Drafts", st, drafter, Redactor(), CFG, lambda *a: None) == 0
    assert st.db.execute("SELECT closed_how, body FROM drafts WHERE message_id='<empty@x>'").fetchone() == (
        "sent", "Olá, enviado.")
    assert drafter.calls == [] and box.appended == []


def test_skipped_mail_is_counted_and_since_moves_the_start(tmp_path):
    st, skipped = state(tmp_path), __import__("collections").Counter()

    class Dated(Box):
        def uids(self, since=None):
            every = super().uids()
            return every[:0] if since == "2027-01-01" else every
    todo = DA.from_mailbox(Dated(), st, CFG, since="2027-01-01", skipped=skipped)
    assert todo == [] and skipped["before 2027-01-01"] == 7
    skipped.clear()
    DA.from_mailbox(Box(), st, CFG, skipped=skipped)
    assert skipped["newest message is not a customer email"] == 1   # the internal one in 9
    assert skipped["already answered or drafted"] == 2


def test_apply_needs_a_limit_at_least_as_big_as_the_to_do_list(tmp_path):
    st = state(tmp_path)
    handoffs(st)
    cfg = {**CFG, "state_dir": tmp_path}
    saved = RT.load_config, RT.build_drafter, RT.build_redactor, RT.load_secret_env
    RT.load_config, RT.build_drafter, RT.build_redactor = lambda: cfg, lambda *a: Drafter(), lambda: Redactor()
    RT.load_secret_env = lambda: None
    try:
        for args in (["--apply", "--db-only"], ["--apply", "--db-only", "--limit", "1"]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                try:
                    code = DA.main(args)
                except SystemExit as exc:
                    code = exc.code
            assert code == 2, args
            assert st.db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 2
        assert "2 open emails" in out.getvalue() and "--limit 1" in out.getvalue()
        with contextlib.redirect_stdout(io.StringIO()):
            assert DA.main(["--apply", "--db-only", "--limit", "2"]) == 0
        assert st.db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 4
    finally:
        RT.load_config, RT.build_drafter, RT.build_redactor, RT.load_secret_env = saved

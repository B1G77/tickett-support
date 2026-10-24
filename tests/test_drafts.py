"""Tests for drafting in the router: which mail gets one, where it goes, failures (no IMAP, no API)."""
import email
import email.policy
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import drafter as D
import drafts as DR
import extract as E
import router as RT

CFG = {"folders": {"F1": "1 Cancelamentos e Reembolsos", "F5": "5 Informações do Evento"},
       "imap": {"user": "atendimento@lebillet.eu"}}
OUT = {"reply": "O reembolso é automático.", "language": "pt-PT", "needs_action": False, "action_note": "",
       "missing_facts": [], "confidence": "high", "usage": {"in": 100, "out": 20, "cache_read": 0}}


def state():
    return RT.State(Path(tempfile.mkdtemp()) / "router.db")


def rec(body="O concerto foi cancelado, quando recebo o reembolso?\n\nANA SILVA"):
    raw = (f"Subject: Reembolso\r\nFrom: ANA SILVA <ana@gmail.com>\r\nMessage-ID: <m1@x>\r\n"
           f"Content-Type: text/plain; charset=utf-8\r\n\r\n{body}").encode()
    return E.record_from_bytes(raw, "imap:1")


class Box:
    def __init__(self):
        self.appended = []

    def append(self, enc, flags, when, raw):
        self.appended.append((enc, flags, raw))


class Redactor:
    def __init__(self, leaks=()):
        self.leaks = list(leaks)

    def redact(self, subject, text, names=(), addrs=()):
        return subject, text.replace("ANA", "[NAME]"), {}, self.leaks


class Drafter:
    sha = "abc123def456"

    def __init__(self, out=OUT, boom=False):
        self.out, self.boom, self.calls = out, boom, []

    def draft(self, case, folder_name, match_text, today):
        if self.boom:
            raise OSError("network down")
        self.calls.append((case, folder_name))
        return dict(self.out) if self.out is not None else None


def dec(folder="F1", urgent=False):
    return RT.Decision(folder, urgent, "claude", "high", "x")


def test_every_customer_email_in_folders_1_to_5_and_9_gets_a_draft():
    st = state()
    assert DR.DRAFT_FOLDERS == ("F1", "F2", "F3", "F4", "F5", "F9")
    for f in DR.DRAFT_FOLDERS:
        assert DR.qualifies(dec(f), "customer", st, "<m1@x>")
    assert DR.qualifies(dec("F1", urgent=True), "customer", st, "<m1@x>")   # legal mail too
    assert not DR.qualifies(dec("F8"), "customer", st, "<m1@x>")
    assert not DR.qualifies(dec(RT.ANSWERED), "customer", st, "<m1@x>")
    assert not DR.qualifies(dec(RT.HANDOFF), "customer", st, "<m1@x>")
    assert not DR.qualifies(dec("F1"), "staff_reply", st, "<m1@x>")


PARTNER_FWD = {"from_email": "bilheteira@promotor.pt", "body_text": "Segue.\n\n---------- Forwarded message ---------\n"
               "From: Rui Costa <rui@gmail.com>\nSubject: Reembolso\n\nQuero o reembolso."}
OWN_FWD = {"from_email": "ana@gmail.com", "body_text": "Não recebi.\n\n---------- Forwarded message ---------\n"
           "From: Tickett <noreply@tickett.com>\nTo: <ana@gmail.com>\nSubject: A sua encomenda\n"}


def test_the_router_flags_legal_and_relayed_mail():
    fwd = "forwarded to support by a partner"
    assert DR.flag_for(dec("F1"), fwd, PARTNER_FWD) == "relayed"
    assert DR.flag_for(dec("F1", urgent=True), "website contact form (calemamusic.com)", {"body_text": ""}) == "relayed"
    assert DR.flag_for(dec("F1", urgent=True), "no exclusion rule matched", PARTNER_FWD) == "legal"
    assert DR.flag_for(dec("F1"), "no exclusion rule matched", PARTNER_FWD) == ""


def test_a_customer_forwarding_their_own_tickett_email_is_not_relayed():
    fwd = "forwarded to support by a partner"
    assert DR.flag_for(dec("F1"), fwd, OWN_FWD) == ""
    assert DR.flag_for(dec("F1", urgent=True), fwd, OWN_FWD) == "legal"
    own = {"from_email": "ana@gmail.com", "body_text": "-------- Mensagem encaminhada --------\nDe: Ana <ana@gmail.com>\n"}
    assert DR.flag_for(dec("F1"), fwd, own) == "relayed"   # nothing from Tickett in it: no evidence, stays relayed
    for text in ("Segue.\n\n________________________________\nDe: Ana Silva\nEnviado: segunda\n"
                 "Para: Bilheteira <bilheteira@promotor.pt>\n",
                 "Cliente ana@gmail.com pediu reembolso.\n\n---------- Forwarded message ---------\n"
                 "From: Tickett <noreply@tickett.com>\nTo: bilheteira@promotor.pt\n"):
        assert DR.flag_for(dec("F1"), fwd, {"from_email": "bilheteira@promotor.pt", "body_text": text}) == "relayed"


def test_a_draft_lands_in_drafts_threaded_and_is_recorded_once():
    st, box, drafter = state(), Box(), Drafter()
    assert DR.make_draft(box, "INBOX.Drafts", rec(), "<m1@x>", dec(), st, drafter, Redactor(), CFG, lambda *a: None)
    enc, flags, raw = box.appended[0]
    assert enc == "INBOX.Drafts" and flags == "(\\Draft)"
    m = email.message_from_bytes(raw, policy=email.policy.default)
    assert m["In-Reply-To"] == "<m1@x>" and m.get_content().startswith("Olá Ana,")
    assert drafter.calls[0][1] == "1 Cancelamentos e Reembolsos" and "[NAME]" in drafter.calls[0][0]["text"]
    row = st.db.execute("SELECT folder, rulebook_sha, note, tokens_in FROM drafts WHERE message_id='<m1@x>'").fetchone()
    assert row == ("F1", "abc123def456", "", 100)
    assert not DR.qualifies(dec(), "customer", st, "<m1@x>")


def test_a_leak_or_an_unusable_answer_gives_no_draft():
    st, box = state(), Box()
    assert not DR.make_draft(box, "D", rec(), "<m1@x>", dec(), st, Drafter(), Redactor(["url"]), CFG, lambda *a: None)
    assert not DR.make_draft(box, "D", rec(), "<m1@x>", dec(), st, Drafter(out=None), Redactor(), CFG, lambda *a: None)
    assert box.appended == []


def test_a_drafter_failure_never_raises_and_is_logged():
    st, box, logged = state(), Box(), []
    ok = DR.try_draft(box, "D", rec(), "<m1@x>", dec(), st, Drafter(boom=True), Redactor(), CFG,
                      lambda mid, msg: logged.append(msg))
    assert not ok and box.appended == [] and logged == ["no draft (OSError)"]


def test_the_site_is_crawled_once_a_day_even_if_the_crawl_fails():
    st, calls, logged = state(), [], []

    def crawl():
        calls.append(1)
        raise OSError("site down")
    DR.maybe_crawl(st, lambda mid, msg: logged.append(msg), date(2026, 9, 28), crawl)
    DR.maybe_crawl(st, lambda mid, msg: logged.append(msg), date(2026, 9, 28), crawl)
    assert calls == [1] and "crawl failed" in logged[0]


def sent_raw(body, reply_to="<m1@x>"):
    return (f"Subject: Re: Reembolso\r\nFrom: atendimento@lebillet.eu\r\nTo: ana@gmail.com\r\n"
            f"In-Reply-To: {reply_to}\r\nMessage-ID: <s1@x>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}").encode()


def drafted_state():
    st = state()
    DR.make_draft(Box(), "D", rec(), "<m1@x>", dec(), st, Drafter(), Redactor(), CFG, lambda *a: None)
    return st


def test_similarity_ignores_the_quoted_history():
    draft = "Olá Ana,\n\nO reembolso é automático.\n\nCom os melhores cumprimentos,\nEquipa LeBillet"
    assert DR.similarity(draft, draft + "\n\nEm 1/09/2026, Ana escreveu:\n> quando recebo?") == 1.0
    assert DR.similarity(draft, "Olá Ana, já tratámos do seu caso.") < 0.6


def test_a_sent_reply_is_scored_against_the_draft_it_answers():
    st, logged = drafted_state(), []
    body = st.db.execute("SELECT body FROM drafts").fetchone()[0]
    DR.note_sent(st, "<m1@x>", [], lambda: sent_raw(body), lambda mid, msg: logged.append(msg))
    sent_at, kept = st.db.execute("SELECT sent_at, kept FROM drafts").fetchone()
    assert sent_at and kept == 1.0 and "100%" in logged[0]


def test_only_the_direct_parent_counts_and_nothing_is_fetched_without_a_draft():
    st, fetched = drafted_state(), []
    DR.note_sent(st, "<later@x>", [], lambda: fetched.append(1) or sent_raw("x", "<later@x>"), lambda *a: None)
    assert fetched == [] and st.db.execute("SELECT sent_at FROM drafts").fetchone()[0] is None


def test_an_earlier_draft_named_in_references_is_closed_without_a_fetch_or_a_kept_score():
    st, fetched, logged = state(), [], []
    DR.make_draft(Box(), "D", rec(), "<early@x>", dec(), st, Drafter(), Redactor(), CFG, lambda *a: None)
    DR.note_sent(st, "<later@x>", ["<early@x>"], lambda: fetched.append(1), lambda mid, msg: logged.append(msg))
    assert fetched == []
    sent_at, kept = st.db.execute("SELECT sent_at, kept FROM drafts WHERE message_id='<early@x>'").fetchone()
    assert sent_at and kept is None
    assert logged == ["draft superseded by a later reply"]


def test_sending_the_internal_note_is_warned_about():
    st, logged = drafted_state(), []
    DR.note_sent(st, "<m1@x>", [], lambda: sent_raw("▶ NOTA INTERNA (apagar antes de enviar): x\n\nOlá"),
                 lambda mid, msg: logged.append(msg))
    assert any("NOTA INTERNA" in m for m in logged)


def test_a_failed_measurement_is_logged_and_never_raises():
    st, logged = drafted_state(), []

    def boom():
        raise OSError("imap down")
    DR.note_sent(st, "<m1@x>", [], boom, lambda mid, msg: logged.append(msg))
    assert logged == ["no measurement (OSError)"]
    assert st.db.execute("SELECT sent_at FROM drafts").fetchone()[0] is None


def test_open_notes_and_report():
    st = state()
    out = {**OUT, "needs_action": True, "action_note": "anexar o PDF"}
    DR.make_draft(Box(), "D", rec(), "<m1@x>", dec(), st, Drafter(out=out), Redactor(), CFG, lambda *a: None)
    assert DR.open_notes(st) == [("Reembolso", "anexar o PDF")]
    assert "Reembolso: anexar o PDF" in DR.format_notes(DR.open_notes(st))
    assert DR.format_notes([]) == ""
    rep = DR.report(st)
    assert "F1" in rep and "drafted 1" in rep and "anexar o PDF" in rep


def test_open_notes_only_cover_the_last_14_days():
    st = state()
    out = {**OUT, "needs_action": True, "action_note": "anexar o PDF"}
    DR.make_draft(Box(), "D", rec(), "<m1@x>", dec(), st, Drafter(out=out), Redactor(), CFG, lambda *a: None)
    old = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat(timespec="seconds")
    st.db.execute("UPDATE drafts SET created_at=? WHERE message_id='<m1@x>'", (old,))
    st.db.commit()
    assert DR.open_notes(st) == []


def test_report_shows_cache_read_and_cache_write_tokens():
    st = state()
    out = {**OUT, "usage": {"in": 100, "out": 20, "cache_read": 5, "cache_write": 7}}
    DR.make_draft(Box(), "D", rec(), "<m1@x>", dec(), st, Drafter(out=out), Redactor(), CFG, lambda *a: None)
    assert "tokens: 100 in, 5 cached read, 7 cache write, 20 out" in DR.report(st)


def test_a_flagged_answer_is_drafted_and_a_tickett_refund_gets_the_finance_copy():
    st, box = state(), Box()
    cfg = {**CFG, "refund_bcc": "finance@tickett.com"}
    out = {**OUT, "flag": "tickett_refund"}
    assert DR.make_draft(box, "D", rec(), "<m1@x>", dec(), st, Drafter(out=out), Redactor(), cfg, lambda *a: None)
    assert email.message_from_bytes(box.appended[0][2])["Bcc"] == "finance@tickett.com"
    assert st.db.execute("SELECT flag, bcc FROM drafts").fetchone() == ("tickett_refund", "finance@tickett.com")
    assert st.db.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0] == 0
    st2, box2 = state(), Box()
    out2 = {**OUT, "flag": "no_answer_in_sources"}
    assert DR.make_draft(box2, "D", rec(), "<m1@x>", dec(), st2, Drafter(out=out2), Redactor(), cfg, lambda *a: None)
    assert st2.db.execute("SELECT flag, bcc FROM drafts").fetchone() == ("no_answer_in_sources", "")
    assert email.message_from_bytes(box2.appended[0][2])["Bcc"] is None


def test_the_router_flag_wins_over_the_models():
    st, box = state(), Box()
    out = {**OUT, "flag": "tickett_refund"}
    assert DR.make_draft(box, "D", rec(), "<m1@x>", dec(urgent=True), st, Drafter(out=out), Redactor(),
                         {**CFG, "refund_bcc": "finance@tickett.com"}, lambda *a: None, "no exclusion rule matched")
    assert st.db.execute("SELECT flag, bcc FROM drafts").fetchone() == ("legal", "finance@tickett.com")
    st2, box2 = state(), Box()   # relayed: the model's refund verdict still sends finance its copy
    assert DR.make_draft(box2, "D", rec(), "<m1@x>", dec(), st2, Drafter(out=out), Redactor(),
                         {**CFG, "refund_bcc": "finance@tickett.com"}, lambda *a: None,
                         "website contact form (calemamusic.com)")
    assert st2.db.execute("SELECT flag, bcc FROM drafts").fetchone() == ("relayed", "finance@tickett.com")


def test_a_relayed_draft_stays_on_the_dashboard_and_out_of_the_mailbox_drafts():
    st, box = state(), Box()
    assert DR.make_draft(box, "D", rec(), "<m1@x>", dec(), st, Drafter(), Redactor(), CFG, lambda *a: None,
                         "website contact form (calemamusic.com)")
    assert box.appended == []
    assert st.db.execute("SELECT flag, draft_mid, body FROM drafts").fetchone()[:2] == ("relayed", "")
    assert DR.has_draft(st, "<m1@x>")


def test_open_drafts_from_folders_6_and_7_move_to_9():
    st = state()
    st.db.execute("CREATE TABLE drafts (message_id TEXT PRIMARY KEY, folder TEXT, body TEXT, created_at TEXT)")
    st.db.executemany("INSERT INTO drafts (message_id, folder) VALUES (?,?)", [("a", "F6"), ("b", "F7"), ("c", "F2")])
    st.db.commit()
    for _ in range(2):   # idempotent
        DR.init(st)
        assert dict(st.db.execute("SELECT message_id, folder FROM drafts")) == {"a": "F9", "b": "F9", "c": "F2"}


def test_the_events_document_is_reread_every_quarter_of_an_hour_and_failures_are_logged():
    st, calls, logs = state(), [], []
    t0 = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    DR.maybe_refresh_doc(st, lambda m, t: logs.append(t), now=t0, refresh=lambda: calls.append(1) or {}, load=dict)
    DR.maybe_refresh_doc(st, lambda m, t: logs.append(t), now=t0 + timedelta(minutes=5), refresh=lambda: calls.append(1) or {}, load=dict)
    assert calls == [1]
    def boom():
        raise OSError("offline")
    DR.maybe_refresh_doc(st, lambda m, t: logs.append(t), now=t0 + timedelta(minutes=20), refresh=boom, load=dict)
    assert logs and "keeping the last copy" in logs[-1]


def test_refresh_records_changes():
    st, logs = state(), []
    t0 = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    old = {"general": [], "events": [{"name": "Concerto Exemplo", "fields": {"Estado": "ativo"}}]}
    new = {"general": [], "events": [{"name": "Concerto Exemplo", "fields": {"Estado": "cancelado"}}]}
    DR.maybe_refresh_doc(st, lambda m, t: logs.append(t), now=t0, refresh=lambda: new, load=lambda: old)

    def boom():
        raise OSError("offline")
    DR.maybe_refresh_doc(st, lambda m, t: logs.append(t), now=t0 + timedelta(minutes=20), refresh=boom,
                         load=lambda: new)
    assert st.db.execute("SELECT at, kind, name FROM doc_changes").fetchall() == [
        ("2026-10-18T12:00:00+00:00", "alterado", "Concerto Exemplo")]


def test_partner_forwards_and_website_forms_are_relayed():
    d = dec()
    assert DR.relayed(d, "forwarded to support by a partner", PARTNER_FWD)
    assert DR.relayed(d, "website contact form (x)", {"body_text": "sem endereço"})   # the sender is never the customer
    assert not DR.relayed(d, "customer email", PARTNER_FWD)
    assert not DR.relayed(d, "forwarded to support by a partner", OWN_FWD)


def test_old_drafts_get_first_body_and_the_new_columns():
    st = state()
    st.db.execute("CREATE TABLE drafts (message_id TEXT PRIMARY KEY, body TEXT, created_at TEXT, sent_at TEXT)")
    st.db.execute("INSERT INTO drafts (message_id, body) VALUES ('<o@x>', 'texto antigo')")
    DR.init(st)
    assert st.db.execute("SELECT first_body, rewrites, edited, reply_to, flag, bcc FROM drafts").fetchone() == (
        "texto antigo", 0, 0, None, None, None)


def test_init_leaves_purged_drafts_alone():
    st = state()
    DR.init(st)
    st.db.execute("INSERT INTO drafts (message_id, body, first_body) VALUES ('<p@x>', NULL, NULL)")   # text purged
    st.db.commit()
    before = st.db.total_changes
    DR.init(st)
    assert st.db.total_changes == before


class Inbox:
    """One customer email in the Inbox (as in the router's loop); records flags and moves."""
    prefix = "INBOX."

    def __init__(self):
        self.flagged, self.moved, self.appended, self.selected = [], [], [], None

    def ensure_folders(self, names):
        return {k: "INBOX." + v for k, v in names.items()}

    def folders(self):
        return {"INBOX.Drafts": "\\Drafts"}

    def select(self, enc, readonly=False):
        self.selected = enc

    def uids(self, since=None):
        return [b"1"]

    def fetch(self, uid):
        return (b"Subject: Reembolso\r\nFrom: ANA SILVA <ana@gmail.com>\r\nMessage-ID: <m1@x>\r\n"
                b"Content-Type: text/plain; charset=utf-8\r\n\r\nVou recorrer ao livro de reclamacoes.\r\n")

    def flag(self, uid):
        self.flagged.append(uid)

    def move(self, uid, dest):
        self.moved.append(dest)

    def append(self, enc, flags, when, raw):
        self.appended.append((enc, raw))


def file_one(decision, why="no exclusion rule matched", drafter=None, extra=()):
    import autosend as AS
    import ledger as LG
    st, logs = state(), []
    cfg = {"folders": {"F1": "1 Cancelamentos e Reembolsos"}, "answered_folder": "10 Respondidos",
           "handoff_folder": "0 Por Responder", "imap": {"user": "support@tickett.com"}}
    patches = [(RT, "build_classifier", lambda: object()), (RT, "build_redactor", lambda: Redactor()),
               (RT, "build_drafter", lambda *a: drafter or Drafter()), (RT, "decide", lambda *a, **k: decision),
               (RT.T, "classify", lambda rec: ("customer", why)), (RT, "purge_phishing", lambda *a, **k: None),
               (RT.A, "sync", lambda *a, **k: 0), (RT.A, "daily_draft", lambda *a, **k: None),
               (RT.DR, "maybe_crawl", lambda *a, **k: None), (RT.DR, "maybe_refresh_doc", lambda *a, **k: None),
               (LG, "event_names", lambda: []), (LG, "after_cycle", lambda *a, **k: None),
               (AS, "send_due", lambda *a, **k: 0), *extra]
    saved = [(o, n, getattr(o, n)) for o, n, _ in patches]
    for o, n, v in patches:
        setattr(o, n, v)
    try:
        box = Inbox()
        assert RT.file_inbox(box, st, lambda m, t: logs.append(t), cfg, False) == 1
    finally:
        for o, n, v in saved:
            setattr(o, n, v)
    return st, box


def test_legal_mail_gets_a_draft_stays_in_its_folder_and_keeps_the_red_flag():
    st, box = file_one(RT.Decision("F1", True, "claude", "high", "lawyer letter"))
    assert box.moved == ["INBOX.1 Cancelamentos e Reembolsos"] and box.flagged == [b"1"]
    assert st.db.execute("SELECT folder, flag FROM drafts").fetchone() == ("F1", "legal")
    assert st.get("<m1@x>")["folder"] == "F1" and st.thread_folder(["<m1@x>"]) == "F1"
    assert st.db.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0] == 0
    assert box.appended and box.appended[0][0] == "INBOX.Drafts"


def test_relayed_mail_gets_a_draft_flagged_relayed():
    st, box = file_one(RT.Decision("F1", False, "claude", "high", "x"), "website contact form (calemamusic.com)")
    assert box.moved == ["INBOX.1 Cancelamentos e Reembolsos"] and box.flagged == []
    assert st.db.execute("SELECT flag FROM drafts").fetchone() == ("relayed",)
    assert st.db.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0] == 0


def test_when_no_draft_can_be_written_the_router_stores_an_empty_one():
    for drafter in (Drafter(out=None), Drafter(boom=True)):
        st, box = file_one(RT.Decision("F1", False, "claude", "high", "x"), drafter=drafter)
        row = st.db.execute("SELECT folder, body, first_body, flag, note, draft_mid, from_email, closed_at "
                            "FROM drafts").fetchone()
        assert row == ("F1", "", "", "person_must_act", DR.EMPTY_NOTE, "", "ana@gmail.com", None)
        assert box.appended == [] and box.moved == ["INBOX.1 Cancelamentos e Reembolsos"]


def test_the_category_of_a_draft_is_the_emails_real_topic():
    st = state()
    st.db.executemany("INSERT INTO decisions (message_id, folder, suggested) VALUES (?,?,?)",
                      [("p", "P", "F3"), ("f", "F2", "F9"), ("old", "P", "F6"), ("x", "P", "F8"), ("n", "P", None)])
    st.db.commit()
    assert [DR.category(st, m) for m in ("p", "f", "old", "x", "n", "none")] == ["F3", "F2", "F9", "F9", "F9", "F9"]
    assert DR.category(st, "none", "F4") == "F4"


def test_a_draft_saved_without_a_mailbox_has_no_mailbox_copy():
    st = state()
    assert DR.save(None, "", rec(), "<m1@x>", dec(), st, dict(OUT), Drafter(), CFG, lambda *a: None)
    assert st.db.execute("SELECT draft_mid, body FROM drafts").fetchone()[0] == ""


def test_a_save_that_fails_in_the_router_still_leaves_an_empty_draft():
    st, box = file_one(RT.Decision("F1", False, "claude", "high", "x"), extra=[(RT.DR, "save", lambda *a, **k: False)])
    assert st.db.execute("SELECT body, note FROM drafts").fetchone() == ("", DR.EMPTY_NOTE)


def test_empty_drafts_keep_the_router_flags():
    st, box = file_one(RT.Decision("F1", False, "claude", "high", "x"), "website contact form (calemamusic.com)",
                       drafter=Drafter(out=None))
    assert st.db.execute("SELECT flag FROM drafts").fetchone() == ("relayed",)
    st, box = file_one(RT.Decision("F1", True, "claude", "high", "x"), drafter=Drafter(out=None))
    assert st.db.execute("SELECT flag FROM drafts").fetchone() == ("legal",)


def test_adopted_hand_offs_keep_the_router_flags():
    st = state()
    DR.init(st)
    st.db.executemany("INSERT INTO decisions (message_id, folder, suggested, urgent) VALUES (?,?,?,?)",
                      [("<f@x>", "P", "F2", 0), ("<l@x>", "P", "F1", 1), ("<n@x>", "P", "F3", 0)])
    st.db.executemany("INSERT INTO handoffs (message_id, created_at, from_email, newest_text) VALUES (?,?,?,?)",
                      [("<f@x>", "c", "wp@calemamusic.com", "Ola"), ("<l@x>", "c", "ana@gmail.com", "Advogado"),
                       ("<n@x>", "c", "rui@gmail.com", "Fatura")])
    st.db.commit()
    DR.adopt_handoffs(st)
    assert dict(st.db.execute("SELECT message_id, flag FROM drafts")) == {
        "<f@x>": "relayed", "<l@x>": "legal", "<n@x>": "person_must_act"}


class DraftsBox(Box):
    def __init__(self):
        super().__init__()
        self.deleted, self.selected = [], None

    def select(self, enc, readonly=False):
        self.selected = enc

    def find(self, mid):
        return [b"7"] if any(mid in raw.decode(errors="replace") for _, _, raw in self.appended) else []

    def delete(self, uid):
        self.deleted.append((self.selected, uid))


def test_a_save_never_overwrites_a_draft_a_person_sent_or_touched():
    st = state()
    DR.init(st)
    st.db.execute("INSERT INTO drafts (message_id, folder, body, note, created_at, closed_at, closed_how, sent_at) "
                  "VALUES ('<m1@x>', 'F1', '', ?, 'v', 'c', 'sent', 's')", (DR.EMPTY_NOTE,))
    st.db.commit()
    box, logs = DraftsBox(), []
    assert not DR.save(box, "INBOX.Drafts", rec(), "<m1@x>", dec(), st, dict(OUT), Drafter(), CFG,
                       lambda m, t: logs.append(t))
    assert st.db.execute("SELECT closed_how, body FROM drafts").fetchone() == ("sent", "")
    assert box.appended == [] and logs == ["no draft - the email already has a draft a person handled"]
    st.db.execute("UPDATE drafts SET closed_at=NULL, closed_how=NULL, sent_at=NULL, body='Olá, eu escrevi.', edited=1")
    st.db.commit()
    assert not DR.save(box, "INBOX.Drafts", rec(), "<m1@x>", dec(), st, dict(OUT), Drafter(), CFG, lambda *a: None)
    assert st.db.execute("SELECT body FROM drafts").fetchone() == ("Olá, eu escrevi.",)
    st.db.execute("UPDATE drafts SET body='', edited=0")   # an untouched empty one may be replaced
    st.db.commit()
    assert DR.save(box, "INBOX.Drafts", rec(), "<m1@x>", dec(), st, dict(OUT), Drafter(), CFG, lambda *a: None)
    assert "O reembolso é automático." in st.db.execute("SELECT body FROM drafts").fetchone()[0]


def test_a_draft_taken_meanwhile_loses_its_mailbox_copy():
    st, box = state(), DraftsBox()
    real = DR._claimable

    def taken(state, mid):   # a person sends the empty draft between the check and the write
        ok = real(state, mid)
        state.db.execute("INSERT INTO drafts (message_id, folder, body, created_at, closed_at, closed_how) "
                         "VALUES ('<m1@x>', 'F1', 'x', 'v', 'c', 'sent')")
        state.db.commit()
        return ok
    DR._claimable = taken
    try:
        assert not DR.save(box, "INBOX.Drafts", rec(), "<m1@x>", dec(), st, dict(OUT), Drafter(), CFG,
                           lambda *a: None)
    finally:
        DR._claimable = real
    assert len(box.appended) == 1 and box.deleted == [("INBOX.Drafts", b"7")]
    assert st.db.execute("SELECT closed_how, body FROM drafts").fetchone() == ("sent", "x")


def test_any_failure_after_the_mailbox_copy_removes_it():
    import sqlite3
    st, box = state(), DraftsBox()
    real = DR._insert_draft
    DR._insert_draft = lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked"))
    try:
        assert not DR.save(box, "INBOX.Drafts", rec(), "<m1@x>", dec(), st, dict(OUT), Drafter(), CFG, lambda *a: None)
    finally:
        DR._insert_draft = real
    assert len(box.appended) == 1 and box.deleted == [("INBOX.Drafts", b"7")] and not DR.has_draft(st, "<m1@x>")


FWD = ("Olá, reencaminho o pedido do cliente.\n\n---------- Forwarded message ---------\n"
       "From: Rui Costa <rui@gmail.com>\nSubject: Reembolso\n\nQuero o reembolso.")


def test_a_relayed_draft_keeps_the_customers_address_from_the_text():
    st = state()
    r = {**rec(FWD), "from_email": "bilheteira@promotor.example"}
    assert DR.make_draft(Box(), "D", r, "<m1@x>", dec(), st, Drafter(), Redactor(), CFG, lambda *a: None,
                         "forwarded to support by a partner")
    assert st.db.execute("SELECT flag, to_override, draft_mid FROM drafts").fetchone() == ("relayed", "rui@gmail.com", "")
    st2 = state()
    DR.make_draft(Box(), "D", {**rec(FWD), "from_email": "bilheteira@promotor.example"}, "<m1@x>", dec(), st2,
                  Drafter(), Redactor(), CFG, lambda *a: None)   # not relayed: no override
    assert st2.db.execute("SELECT to_override FROM drafts").fetchone() == ("",)
    st3 = state()
    DR.save_empty(st3, r, "<m1@x>", dec(), lambda *a: None, "forwarded to support by a partner")
    assert st3.db.execute("SELECT flag, to_override FROM drafts").fetchone() == ("relayed", "rui@gmail.com")


def test_adopted_hand_offs_and_old_relayed_drafts_get_the_address_once():
    st = state()
    DR.init(st)
    st.db.execute("INSERT INTO handoffs (message_id, created_at, from_email, full_text) VALUES "
                  "('<h@x>', 'c', 'wp@calemamusic.com', 'Nome: Eva\nE-mail: eva@sapo.pt')")
    st.db.commit()
    DR.adopt_handoffs(st)
    assert st.db.execute("SELECT flag, to_override FROM drafts").fetchone() == ("relayed", "eva@sapo.pt")
    st.db.execute("INSERT INTO drafts (message_id, folder, body, created_at, from_email, flag, full_text) VALUES "
                  "('<old@x>', 'F1', 'x', 'v', 'bilheteira@promotor.example', 'relayed', ?)", (FWD,))
    st.db.execute("INSERT INTO drafts (message_id, folder, body, created_at, from_email, flag, to_override, full_text) "
                  "VALUES ('<set@x>', 'F1', 'x', 'v', 'b@p.example', 'relayed', 'eu@gmail.com', ?)", (FWD,))
    st.db.execute("INSERT INTO drafts (message_id, folder, body, created_at, flag) VALUES ('<p@x>', 'F1', 'x', 'v', 'relayed')")
    st.db.commit()
    for _ in range(2):   # idempotent; a value already there (or typed) is never touched
        DR.init(st)
        assert dict(st.db.execute("SELECT message_id, to_override FROM drafts WHERE message_id != '<h@x>'")) == {
            "<old@x>": "rui@gmail.com", "<set@x>": "eu@gmail.com", "<p@x>": ""}


def test_a_relayed_reply_greets_the_customer_not_the_partner():
    st = state()
    r = {**rec(FWD), "from_email": "bilheteira@promotor.example", "from_name": "Joana Promotora"}
    DR.make_draft(Box(), "D", r, "<m1@x>", dec(), st, Drafter(), Redactor(), CFG, lambda *a: None,
                  "forwarded to support by a partner")
    assert st.db.execute("SELECT body FROM drafts").fetchone()[0].startswith("Olá Rui,\n\n")
    st2 = state()
    form = {**rec("E-mail: eva@sapo.pt\nMensagem: olá"), "from_email": "wp@calemamusic.com", "from_name": "WordPress"}
    DR.make_draft(Box(), "D", form, "<m1@x>", dec(), st2, Drafter(), Redactor(), CFG, lambda *a: None,
                  "website contact form (calemamusic.com)")
    assert st2.db.execute("SELECT body, to_override FROM drafts").fetchone()[0].startswith("Olá,\n\n")
    assert DR.greet_name("", {"from_name": "ANA SILVA"}) == "Ana"


def test_open_drafts_wrongly_flagged_relayed_are_checked_again_once():
    st = state()
    DR.init(st)
    rows = [("<own@x>", "ana@gmail.com", OWN_FWD["body_text"], "", None, "Email reencaminhado: confirme o endereço do "
             "cliente em Para: antes de enviar.; falta: data"),
            ("<refund@x>", "eva@gmail.com", OWN_FWD["body_text"].replace("ana@", "eva@"), "finance@tickett.com", None,
             "Responda diretamente ao cliente: o endereço dele está no texto do email."),
            ("<partner@x>", "bilheteira@promotor.pt", PARTNER_FWD["body_text"], "", None, ""),
            ("<form@x>", "wp@calemamusic.com", "sem endereço", "", None, ""),
            ("<closed@x>", "rui@gmail.com", OWN_FWD["body_text"], "", "2026-10-20", "")]
    for mid, sender, text, bcc, closed, note in rows:
        st.db.execute("INSERT INTO drafts (message_id, folder, body, created_at, from_email, full_text, flag, bcc, "
                      "closed_at, note) VALUES (?, 'F1', 'x', 'v', ?, ?, 'relayed', ?, ?, ?)",
                      (mid, sender, text, bcc, closed, note))
    st.db.execute("INSERT INTO decisions (message_id, folder, urgent) VALUES ('<own@x>', 'F1', 1)")
    st.db.execute("DELETE FROM meta WHERE key=?", (DR.RECHECK_KEY,))
    st.db.commit()
    for _ in range(2):
        DR.init(st)
        got = {r[0]: r[1:] for r in st.db.execute("SELECT message_id, flag, to_override, note FROM drafts")}
        assert got["<own@x>"] == ("legal", "", "falta: data")
        assert got["<refund@x>"] == ("tickett_refund", "", "")
        assert got["<partner@x>"][:2] == ("relayed", "rui@gmail.com")
        assert got["<form@x>"][:2] == ("relayed", "")
        assert got["<closed@x>"][0] == "relayed"   # closed rows are history
    st.db.execute("UPDATE drafts SET flag='relayed' WHERE message_id='<own@x>'")   # once only
    st.db.commit()
    DR.init(st)
    assert st.db.execute("SELECT flag FROM drafts WHERE message_id='<own@x>'").fetchone() == ("relayed",)


def test_a_typed_para_keeps_a_draft_from_being_replaced():
    row = {"closed_at": None, "sent_at": None, "body": "", "note": DR.EMPTY_NOTE, "edited": 0, "rewrites": 0}
    assert DR.placeholder(row) and not DR.placeholder({**row, "to_set_by_person": 1})
    st = state()
    DR.init(st)
    st.db.execute("INSERT INTO drafts (message_id, folder, body, note, created_at, to_set_by_person) "
                  "VALUES ('<m1@x>', 'F1', '', ?, 'v', 1)", (DR.EMPTY_NOTE,))
    st.db.commit()
    assert not DR.save(Box(), "D", rec(), "<m1@x>", dec(), st, dict(OUT), Drafter(), CFG, lambda *a: None)


def test_a_failure_before_the_insert_also_removes_the_mailbox_copy():
    st, box = state(), DraftsBox()
    real = DR.email_fields
    calls = []

    def boom(r):
        calls.append(1)
        raise ValueError("bad header")
    DR.email_fields = boom
    try:
        assert not DR.save(box, "INBOX.Drafts", rec(), "<m1@x>", dec(), st, dict(OUT), Drafter(), CFG, lambda *a: None)
    finally:
        DR.email_fields = real
    assert calls and len(box.appended) == 1 and box.deleted == [("INBOX.Drafts", b"7")]


def test_partner_forwards_without_evidence_of_an_own_forward_stay_relayed():
    fwd = "forwarded to support by a partner"
    for text in ("Segue o pedido.\n\n________________________________\nDe: ana@gmail.com\nEnviado: segunda\n",
                 "Segue.\nFrom: Ana Silva ana@gmail.com\nSent: Monday", "Email cliente: ana@gmail.com",
                 "Bom dia, segue o pedido da cliente Ana (ana@gmail.com).",
                 "On Mon, 20 Oct 2026 at 10:00, Ana <ana@gmail.com> wrote:\n> Não recebi",
                 "Em seg., 20/10/2026 às 10:00, Ana Silva <ana@gmail.com> escreveu:\n> Olá",
                 "Segue em anexo o pedido do cliente."):   # nothing found: still relayed, Para must be filled
        r = {"from_email": "bilheteira@promotor.pt", "body_text": text}
        assert DR.flag_for(dec("F1"), fwd, r) == "relayed", text
    st = state()
    r = {**rec("Segue o pedido.\n\n________________________________\nDe: ana@gmail.com\nEnviado: segunda\n"),
         "from_email": "bilheteira@promotor.pt"}
    DR.save_empty(st, r, "<m1@x>", dec(), lambda *a: None, fwd)
    assert st.db.execute("SELECT flag, to_override FROM drafts").fetchone() == ("relayed", "ana@gmail.com")


def test_the_corrected_check_restores_relayed_and_never_touches_typed_or_purged_drafts():
    st = state()
    DR.init(st)
    partner = "Segue.\n\n________________________________\nDe: ana@gmail.com\nEnviado: segunda\n"
    rows = [  # (mid, sender, subject, text, flag, to_override, by_person, note)
        ("<lost@x>", "bilheteira@promotor.pt", "Fwd: Reembolso", partner, "", "", 0, "falta: data"),   # un-flagged before
        ("<own@x>", "ana@gmail.com", "Fwd: encomenda", OWN_FWD["body_text"], "relayed", "", 0, ""),
        ("<typed@x>", "ana@gmail.com", "Fwd: encomenda", OWN_FWD["body_text"], "relayed", "eva@gmail.com", 1, ""),
        ("<found@x>", "ana@gmail.com", "Fwd: encomenda", OWN_FWD["body_text"], "relayed", "rui@gmail.com", 0, ""),
        ("<purged@x>", "ana@gmail.com", "Fwd: encomenda", None, "relayed", "", 0, ""),
        ("<plain@x>", "rui@gmail.com", "Bilhetes", "Não recebi os bilhetes.", "", "", 0, ""),
    ]
    for mid, sender, subject, text, flag, to, by, note in rows:
        st.db.execute("INSERT INTO drafts (message_id, folder, body, created_at, from_email, subject, full_text, flag, "
                      "to_override, to_set_by_person, note) VALUES (?, 'F1', 'x', 'v', ?, ?, ?, ?, ?, ?, ?)",
                      (mid, sender, subject, text, flag, to, by, note))
    st.db.execute("DELETE FROM meta WHERE key=?", (DR.RECHECK_KEY,))
    st.db.commit()
    for _ in range(2):
        DR.init(st)
        got = {r[0]: r[1:] for r in st.db.execute("SELECT message_id, flag, to_override, note FROM drafts")}
        assert got["<lost@x>"] == ("relayed", "ana@gmail.com", D.RELAYED + "; falta: data")
        assert got["<own@x>"][:2] == ("", "")
        assert got["<typed@x>"][:2] == ("relayed", "eva@gmail.com")   # a typed Para survives init
        assert got["<found@x>"][:2] == ("relayed", "rui@gmail.com")
        assert got["<purged@x>"][0] == "relayed"
        assert got["<plain@x>"][:2] == ("", "")


def test_every_forward_is_marked_so_it_is_never_sent_automatically():
    fwd = "forwarded to support by a partner"
    own = {**rec(OWN_FWD["body_text"]), "from_email": "ana@gmail.com"}
    st = state()
    DR.make_draft(Box(), "D", own, "<m1@x>", dec(), st, Drafter(), Redactor(), CFG, lambda *a: None, fwd)
    assert st.db.execute("SELECT flag, forwarded FROM drafts").fetchone() == ("", 1)   # a normal draft, still a forward
    st2 = state()
    DR.make_draft(Box(), "D", rec(), "<m1@x>", dec(), st2, Drafter(), Redactor(), CFG, lambda *a: None,
                  "no exclusion rule matched")
    assert st2.db.execute("SELECT forwarded FROM drafts").fetchone() == (0,)
    st3 = state()
    DR.save_empty(st3, own, "<m1@x>", dec(), lambda *a: None, fwd)
    assert st3.db.execute("SELECT forwarded FROM drafts").fetchone() == (1,)
    assert DR.RECHECK_KEY == "relayed_checked_4"
    st4 = state()
    DR.init(st4)
    st4.db.execute("INSERT INTO drafts (message_id, folder, body, created_at, from_email, subject, full_text, flag) "
                   "VALUES ('<o@x>', 'F1', 'x', 'v', 'ana@gmail.com', 'Fwd: encomenda', ?, '')", (OWN_FWD["body_text"],))
    st4.db.execute("DELETE FROM meta WHERE key=?", (DR.RECHECK_KEY,))
    st4.db.commit()
    DR.init(st4)
    assert st4.db.execute("SELECT flag, forwarded FROM drafts").fetchone() == ("", 1)   # the re-check marks it too

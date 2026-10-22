"""Tests for drafting in the router: which mail gets one, where it goes, failures (no IMAP, no API)."""
import email
import email.policy
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

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


def test_only_customer_mail_in_folders_1_to_7_that_is_not_urgent_gets_a_draft():
    st = state()
    assert DR.qualifies(dec("F1"), "customer", st, "<m1@x>")
    assert not DR.qualifies(dec("F8"), "customer", st, "<m1@x>")
    assert not DR.qualifies(dec("F9"), "customer", st, "<m1@x>")
    assert not DR.qualifies(dec(RT.ANSWERED), "customer", st, "<m1@x>")
    assert not DR.qualifies(dec("F1", urgent=True), "customer", st, "<m1@x>")
    assert not DR.qualifies(dec("F1"), "staff_reply", st, "<m1@x>")


def test_a_form_or_forwarded_reason_never_gets_a_draft_the_wrong_recipient_would_get():
    st = state()
    assert not DR.qualifies(dec("F1"), "customer", st, "<m1@x>", "website contact form (calemamusic.com)")
    assert not DR.qualifies(dec("F1"), "customer", st, "<m1@x>", "forwarded to support by a partner")
    assert DR.qualifies(dec("F1"), "customer", st, "<m1@x>", "no exclusion rule matched")


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


def test_a_handoff_is_noted_not_drafted():
    st, box, logs = state(), Box(), []
    out = {**OUT, "handoff": True, "handoff_reason": "no_answer_in_sources", "action_note": "evento sem informação",
           "reply": ""}
    assert not DR.make_draft(box, "D", rec(), "<m1@x>", dec(), st, Drafter(out=out), Redactor(), CFG,
                             lambda m, t: logs.append(t))
    assert box.appended == [] and not DR.has_draft(st, "<m1@x>")
    row = st.db.execute("SELECT reason, note FROM handoffs WHERE message_id='<m1@x>'").fetchone()
    assert row == ("no_answer_in_sources", "evento sem informação") and logs[-1].startswith("hand-off")


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


def test_partner_forwards_and_website_forms_go_to_a_person():
    d = dec()
    assert DR.relayed(d, "forwarded to support by a partner") and DR.relayed(d, "website contact form (x)")
    assert not DR.relayed(d, "customer email")


def test_old_drafts_get_first_body_and_the_new_columns():
    st = state()
    st.db.execute("CREATE TABLE drafts (message_id TEXT PRIMARY KEY, body TEXT, created_at TEXT, sent_at TEXT)")
    st.db.execute("INSERT INTO drafts (message_id, body) VALUES ('<o@x>', 'texto antigo')")
    DR.init(st)
    assert st.db.execute("SELECT first_body, rewrites, edited, reply_to FROM drafts").fetchone() == (
        "texto antigo", 0, 0, None)


def test_init_leaves_purged_drafts_alone():
    st = state()
    DR.init(st)
    st.db.execute("INSERT INTO drafts (message_id, body, first_body) VALUES ('<p@x>', NULL, NULL)")   # text purged
    st.db.commit()
    before = st.db.total_changes
    DR.init(st)
    assert st.db.total_changes == before

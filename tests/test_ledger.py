"""Tests for ledger.py: reply record, stored mail text, event match (no IMAP, no API)."""
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import drafts as DR
import extract as E
import ledger as LG
import router as RT


def state():
    return RT.State(Path(tempfile.mkdtemp()) / "router.db")


def rec(subject="Reembolso", body="Quando recebo o reembolso?", sender="rita@example.com"):
    raw = (f"Subject: {subject}\r\nFrom: Rita Exemplo <{sender}>\r\nContent-Type: text/plain; charset=utf-8\r\n"
           f"Message-ID: <{abs(hash(subject + body))}@x>\r\n\r\n{body}").encode()
    return E.record_from_bytes(raw, "imap:1")


def row(st, table, mid):
    cur = st.db.execute(f"SELECT * FROM {table} WHERE message_id=?", (mid,))
    cols = [c[0] for c in cur.description]
    r = cur.fetchone()
    return dict(zip(cols, r)) if r else None


def test_customer_mail_in_counted_folders_is_recorded_once():
    st = state()
    LG.note_received(st, "m1", rec(), "F2", ["Melody Fest"])
    first = row(st, "replies", "m1")
    assert first["folder"] == "F2" and first["from_email"] == "rita@example.com"
    assert first["received_at"] and first["answered_at"] is None and first["event"] == ""
    t = row(st, "mail_text", "m1")
    assert t["subject"] == "Reembolso" and t["from_name"] == "Rita Exemplo" and "reembolso" in t["text"]
    LG.note_received(st, "m1", rec(body="outra coisa"), "F5")   # first sight wins
    assert row(st, "replies", "m1") == first
    assert "outra" not in row(st, "mail_text", "m1")["text"]
    LG.note_received(st, "m2", rec(body="x" * 30000), "P")
    assert len(row(st, "mail_text", "m2")["text"]) == 20000


def test_non_customer_mail_and_folder_8_are_not_counted():
    st = state()
    LG.init(st)
    LG.note_received(st, "m1", rec(), "F8")
    LG.note_received(st, "m2", rec(sender="x@mkt.ctt.pt"), "F2")   # vendor
    LG.note_received(st, "m3", rec(), "A")
    for mid in ("m1", "m2", "m3"):
        assert row(st, "replies", mid) is None and row(st, "mail_text", mid) is None


def test_answered_keeps_the_first_reply_only():
    st = state()
    LG.note_received(st, "m1", rec(), "F1")
    LG.note_answered(st, "m1", "auto")
    first = row(st, "replies", "m1")
    assert first["answered_at"] and first["how"] == "auto"
    LG.note_answered(st, "m1", "mail")
    assert row(st, "replies", "m1") == first
    LG.note_answered(st, "unknown", "mail")   # no record, nothing happens
    assert row(st, "replies", "unknown") is None


def test_event_names_match_without_accents_titles_or_parentheses():
    ana = "Dra. Ana Beatriz Barbosa (Geneva and London)"
    assert LG.match_event("bilhetes para a ana beatriz em genebra", [ana]) == ana
    assert LG.match_event("bilhete e o tchan", ["É o Tchan"]) == "É o Tchan"
    assert LG.match_event("brazilian summer", ["Brazilian Summer Festival"]) == "Brazilian Summer Festival"
    assert LG.match_event("melodyne", ["Melody"]) == ""
    assert LG.match_event("Ana", [ana]) == ""
    assert LG.match_event("Melody e Ana Beatriz", ["Melody", ana]) == "Melody"   # Doc order
    assert LG.match_event("nada", []) == ""


def test_backfill_fills_from_decisions_drafts_and_handoffs_once():
    st = state()
    DR.init(st)
    ins = "INSERT INTO decisions (message_id, folder, decided_at) VALUES (?,?,?)"
    for mid, folder, at in (("a", "F2", "2026-09-03T10:00:00+00:00"), ("b", "P", "2026-09-05T10:00:00+00:00"),
                            ("c", "F3", "2026-09-07T10:00:00+00:00"), ("d", "F8", "2026-09-01T10:00:00+00:00"),
                            ("e", "F4", "2026-09-09T10:00:00+00:00")):
        st.db.execute(ins, (mid, folder, at))
    st.db.execute("INSERT INTO drafts (message_id, closed_at, closed_how) VALUES ('a','2026-09-04T09:00:00+00:00','sent')")
    st.db.execute("INSERT INTO drafts (message_id, closed_at, closed_how) VALUES ('c','2026-09-08T09:00:00+00:00','answered')")
    st.db.execute("INSERT INTO handoffs (message_id, answered_at) VALUES ('b','2026-09-06T09:00:00+00:00')")
    st.db.commit()
    assert LG.backfill(st) == 4
    a, b, c, e = (row(st, "replies", m) for m in "abce")
    assert (a["answered_at"], a["how"], a["received_at"]) == ("2026-09-04T09:00:00+00:00", "dashboard", "2026-09-03T10:00:00+00:00")
    assert (b["answered_at"], b["how"]) == ("2026-09-06T09:00:00+00:00", "mail")
    assert c["how"] == "mail" and e["answered_at"] is None
    assert row(st, "replies", "d") is None
    since = st.db.execute("SELECT value FROM meta WHERE key='ledger_since'").fetchone()[0]
    assert since == "2026-09-03"
    assert LG.backfill(st) == 0   # once


def test_ledger_since_starts_today():
    st = state()
    LG.init(st)
    LG.init(st)
    since = st.db.execute("SELECT value FROM meta WHERE key='ledger_since'").fetchone()[0]
    assert since == datetime.now(timezone.utc).date().isoformat()


class Box:
    def __init__(self):
        self.moved = []

    def ensure_folders(self, names):
        return {k: "INBOX." + v for k, v in names.items()}

    def select(self, enc, readonly=False):
        pass

    def uids(self, since=None):
        return [b"1"]

    def fetch(self, uid):
        return (b"Subject: Reembolso\r\nFrom: Rita Exemplo <rita@example.com>\r\nMessage-ID: <r1@x>\r\n"
                b"Content-Type: text/plain; charset=utf-8\r\n\r\nQuando recebo o reembolso?\r\n")

    def flag(self, uid):
        pass

    def move(self, uid, dest):
        self.moved.append(dest)


def test_the_router_records_received_mail():
    st = state()
    LG.init(st)
    cfg = {"folders": {"F2": "2 Reembolsos"}, "answered_folder": "10 Respondidos"}
    patches = [(RT, "build_classifier", lambda: None),
               (RT, "decide", lambda *a, **k: RT.Decision("F2", False, "rule", "high", "stub")),
               (RT, "purge_phishing", lambda *a, **k: None),
               (RT.A, "sync", lambda *a, **k: 0),
               (RT.A, "daily_draft", lambda *a, **k: None),
               (RT.DR, "maybe_crawl", lambda *a, **k: None),
               (RT.DR, "maybe_refresh_doc", lambda *a, **k: None),
               (LG, "event_names", lambda: [])]
    saved = [(o, n, getattr(o, n)) for o, n, _ in patches]
    for o, n, v in patches:
        setattr(o, n, v)
    try:
        RT.file_inbox(Box(), st, lambda *a: None, cfg, True)
        assert row(st, "replies", "<r1@x>") is None   # a dry run files nothing
        RT.file_inbox(Box(), st, lambda *a: None, cfg, False)
    finally:
        for o, n, v in saved:
            setattr(o, n, v)
    r = row(st, "replies", "<r1@x>")
    assert r["folder"] == "F2" and r["from_email"] == "rita@example.com"
    assert "reembolso" in row(st, "mail_text", "<r1@x>")["text"]


def test_a_ledger_failure_never_stops_filing():
    st = state()
    LG.init(st)
    st.db.execute("DROP TABLE replies")
    st.db.execute("CREATE VIEW replies AS SELECT 1 AS x")   # writes to a view fail
    st.db.commit()
    logged = []
    LG.note_received(st, "m1", rec(), "F2", [], lambda mid, msg: logged.append((mid, msg)))
    LG.note_answered(st, "m1", "mail", lambda mid, msg: logged.append((mid, msg)))
    assert [m for _, m in logged] == ["ledger not updated (OperationalError)"] * 2
    LG.note_received(st, "m2", rec(), "F2")   # no log given: still silent


def test_a_malformed_doc_gives_no_event_names():
    orig = LG.ED.load
    try:
        for bad in ([], {"events": "x"}, {"events": [{"fields": 1}]}, {"events": [None]}, {"events": [{"name": 5}]}):
            LG.ED.load = lambda bad=bad: bad
            assert LG.event_names() == []
        LG.ED.load = lambda: {"events": [None, {"name": "Melody"}]}
        assert LG.event_names() == ["Melody"]
    finally:
        LG.ED.load = orig


PRICING = {"usd_to_eur": 0.5, "per_million_usd": {
    "m-a": {"in": 4.0, "out": 20.0, "cache_read": 0.4, "cache_write": 5.0}}}


def usage_rows(st):
    cur = st.db.execute("SELECT kind, model, tokens_in, tokens_out, cache_read, cache_write FROM ai_usage ORDER BY rowid")
    return cur.fetchall()


def test_every_claude_call_is_recorded_with_its_tokens():
    st = state()
    LG.init(st)
    LG.note_usage(st, "classify", "m-a", {"in": 10, "out": 2, "cache_read": 5})
    LG.note_usage(st, "rewrite", "m-a", {"in": 1, "out": 1, "cache_read": 0, "cache_write": 7})
    LG.note_usage(st, "draft", None, {})
    assert usage_rows(st) == [("classify", "m-a", 10, 2, 5, 0), ("rewrite", "m-a", 1, 1, 0, 7), ("draft", "", 0, 0, 0, 0)]
    # a failure never raises and is logged when a log is given
    st.db.execute("DROP TABLE ai_usage")
    st.db.execute("CREATE VIEW ai_usage AS SELECT 1 AS x")
    st.db.commit()
    logged = []
    LG.note_usage(st, "draft", "m-a", {}, lambda k, m: logged.append((k, m)))
    LG.note_usage(st, "draft", "m-a", {})
    assert logged == [("draft", "usage not recorded (OperationalError)")]


def test_cost_uses_the_model_price_and_the_euro_rate():
    rows = [{"model": "m-a", "tokens_in": 1_000_000, "tokens_out": 500_000, "cache_read": 1_000_000,
             "cache_write": 200_000}]
    euros, unknown = LG.cost_eur(rows, PRICING)
    assert unknown == []
    assert abs(euros - (4 + 10 + 0.4 + 1.0) * 0.5) < 1e-9
    assert LG.cost_eur([], PRICING) == (0.0, [])


def test_unknown_models_cost_zero_and_are_listed():
    rows = [{"model": "zz", "tokens_in": 9, "tokens_out": 9, "cache_read": 9, "cache_write": 9},
            {"model": "", "tokens_in": 1, "tokens_out": 1, "cache_read": 0, "cache_write": 0},
            {"model": "zz", "tokens_in": 1, "tokens_out": 1, "cache_read": 0, "cache_write": 0},
            {"model": "m-a", "tokens_in": 1_000_000, "tokens_out": None, "cache_read": None, "cache_write": 0}]
    euros, unknown = LG.cost_eur(rows, PRICING)
    assert unknown == ["", "zz"] and abs(euros - 2.0) < 1e-9


def test_usage_backfill_copies_draft_tokens_once():
    st = state()
    DR.init(st)
    LG.init(st)
    for i, t in enumerate(("2026-10-01T10:00:00+00:00", "2026-10-02T10:00:00+00:00")):
        st.db.execute("INSERT INTO drafts (message_id, created_at, tokens_in, tokens_out, cache_read, cache_write) "
                      "VALUES (?,?,?,?,?,?)", (f"m{i}", t, 100 + i, 10, 50, None))
    st.db.commit()
    st.db.execute("INSERT INTO drafts (message_id, created_at, tokens_in) VALUES ('new', '2026-12-01T00:00:00+00:00', 5)")
    LG.note_usage(st, "draft", "claude-opus-5", {"in": 5})   # recorded live, so its drafts row is not copied again
    st.db.execute("UPDATE ai_usage SET at='2026-12-01T00:00:00+00:00'")
    assert LG.backfill_usage(st) == 2
    assert LG.backfill_usage(st) == 0
    st.db.execute("DELETE FROM ai_usage WHERE at >= '2026-12'")
    cur = st.db.execute("SELECT at, kind, model, tokens_in, tokens_out, cache_read, cache_write FROM ai_usage ORDER BY at")
    assert cur.fetchall() == [("2026-10-01T10:00:00+00:00", "draft", "claude-opus-5", 100, 10, 50, 0),
                              ("2026-10-02T10:00:00+00:00", "draft", "claude-opus-5", 101, 10, 50, 0)]


def test_the_draft_and_handoff_hooks_record_usage():
    st = state()
    u = {"in": 3, "out": 2, "cache_read": 1, "cache_write": 4}

    class Box:
        def append(self, *a):
            pass
    d = type("D", (), {"folder": "F2"})()
    drafter = type("Dr", (), {"sha": "s"})()
    cfg = {"imap": {"user": "s@x"}, "model": "m-cfg"}
    orig_compose, orig_reply = DR.D.compose, DR.D.reply_message
    DR.D.compose = lambda o, n: ("t", "b", "")
    DR.D.reply_message = lambda r, t, user: (b"raw", "<dm>")
    try:
        assert DR.save(Box(), "Drafts", rec(), "<a>", d, st, {"usage": u}, drafter, cfg, lambda *a: None)
        DR.note_handoff(st, "<b>", {"handoff_reason": "legal", "usage": u, "model": "m-h"}, lambda *a: None, rec())
        DR.note_handoff(st, "<c>", {"handoff_reason": "legal"}, lambda *a: None, rec())   # no model call: nothing
    finally:
        DR.D.compose, DR.D.reply_message = orig_compose, orig_reply
    assert usage_rows(st) == [("draft", "m-cfg", 3, 2, 1, 4), ("handoff", "m-h", 3, 2, 1, 4)]


def test_purge_clears_old_text_and_keeps_counts():
    st = state()
    DR.init(st)
    LG.init(st)
    now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    old, fresh = "2026-06-01T10:00:00+00:00", "2026-10-15T10:00:00+00:00"
    for mid, created in (("<old@x>", old), ("<new@x>", fresh)):
        st.db.execute("INSERT INTO drafts (message_id, created_at, body, first_body, full_text, newest_text, kept, "
                      "tokens_in) VALUES (?,?,?,?,?,?,0.9,10)", (mid, created, "resp", "resp0", "tudo", "novo"))
        st.db.execute("INSERT INTO handoffs (message_id, created_at, full_text, newest_text) VALUES (?,?,?,?)",
                      (mid, created, "tudo", "novo"))
        st.db.execute("INSERT INTO mail_text VALUES (?,?,?,?,?,?,?)", (mid, "Assunto", "Rita", "r@example.com",
                                                                      created, "F1", "texto"))
    st.db.commit()
    n = LG.purge_text(st, 90, now)
    assert n == 3
    d = row(st, "drafts", "<old@x>")
    assert (d["body"], d["first_body"], d["full_text"], d["newest_text"]) == (None, None, None, None)
    assert d["kept"] == 0.9 and d["tokens_in"] == 10 and d["created_at"] == old
    h = row(st, "handoffs", "<old@x>")
    assert h["full_text"] is None and h["newest_text"] is None and h["created_at"] == old
    assert row(st, "drafts", "<new@x>")["body"] == "resp" and row(st, "handoffs", "<new@x>")["full_text"] == "tudo"
    assert row(st, "mail_text", "<old@x>") is None and row(st, "mail_text", "<new@x>")["text"] == "texto"
    assert LG.purge_text(st, 90, now) == 0   # nothing left to clear


def test_purge_clears_the_sender_of_replies_after_a_week():
    st = state()
    LG.init(st)
    now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    for mid, received in (("<w@x>", "2026-10-13T10:00:00+00:00"), ("<d@x>", "2026-10-19T10:00:00+00:00")):
        st.db.execute("INSERT INTO replies (message_id, folder, received_at, from_email) VALUES (?,?,?,?)",
                      (mid, "F2", received, "rita@example.com"))
    st.db.commit()
    assert LG.purge_text(st, 90, now) == 1
    assert row(st, "replies", "<w@x>")["from_email"] is None and row(st, "replies", "<w@x>")["folder"] == "F2"
    assert row(st, "replies", "<d@x>")["from_email"] == "rita@example.com"
    assert LG.purge_text(st, 90, now) == 0


def test_a_failed_ledger_write_leaves_no_transaction_open():
    st = state()
    LG.init(st)
    st.db.execute("DROP TABLE mail_text")
    st.db.execute("CREATE VIEW mail_text AS SELECT 1 AS x")   # the second insert fails after the first
    st.db.commit()
    logged = []
    LG.note_received(st, "m1", rec(), "F2", [], lambda mid, msg: logged.append(msg))
    assert logged == ["ledger not updated (OperationalError)"] and not st.db.in_transaction
    assert row(st, "replies", "m1") is None


def test_after_cycle_purges_once_a_day_and_never_raises():
    st, logs = state(), []
    DR.init(st)
    st.db.execute("INSERT INTO drafts (message_id, created_at, body) VALUES ('<o@x>', '2020-01-01T00:00:00+00:00', 'x')")
    st.db.commit()
    LG.after_cycle(st, {}, lambda m, t: logs.append(t))
    assert row(st, "drafts", "<o@x>")["body"] is None and any("older than 90 days cleared: 1" in t for t in logs)
    st.db.execute("INSERT INTO drafts (message_id, created_at, body) VALUES ('<p@x>', '2020-01-01T00:00:00+00:00', 'y')")
    st.db.commit()
    LG.after_cycle(st, {}, lambda m, t: logs.append(t))
    assert row(st, "drafts", "<p@x>")["body"] == "y"   # same day: not again
    st.db.close()
    LG.after_cycle(st, {}, lambda m, t: logs.append(t))
    assert "cycle bookkeeping failed" in logs[-1]

"""Tests for the wait before an automatic reply (no IMAP, no SMTP, no API)."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import actions as AC
import autosend as AS
import ledger as LG
import router as RT
from test_autosend import CFG, MID, ON, RAW, ZERO, Box, Drafter, Inbox, draft_row, setup

NOW = datetime.now(timezone.utc).replace(microsecond=0)
LATER = {**ON, "delay_hours": {**ZERO, "F2": 1}}


def iso(t):
    return t.isoformat(timespec="seconds")


def seen(st, at):
    """The router first saw the email at `at` (decisions.decided_at)."""
    st.record(MID, [], RT.Decision("F2", False, "claude", "high", "x"), moved=True)
    st.db.execute("UPDATE decisions SET decided_at=? WHERE message_id=?", (iso(at), MID))
    st.db.commit()


class Folder(Box):
    """The customer's email still waits in 2 Pasta 2; Sent and Drafts as in Box."""

    def __init__(self, mails=None):
        super().__init__()
        self.mails = {MID: RAW} if mails is None else mails

    def ensure_folders(self, names):
        return {k: "INBOX." + v for k, v in names.items()}

    def find(self, mid):
        if self.selects[-1:] == ["INBOX.2 Pasta 2"]:
            return [b"5"] if mid in self.mails else []
        return super().find(mid)

    def fetch(self, uid):
        assert self.selects[-1] == "INBOX.2 Pasta 2"
        return self.mails[MID]

    def move(self, uid, dest):
        self.moved = getattr(self, "moved", []) + [(uid, dest)]

    def logout(self):
        pass


def run(st, rec, d):
    logs, sent = [], []
    ok = AS.maybe_send(CFG, st, box := Box(), rec, MID, d, lambda m, msg: logs.append(msg),
                       send_mail=lambda cfg, raw: sent.append(raw))
    return ok, logs, sent, box


def due(st, box=None, now=None, send_mail=None):
    logs, sent = [], []
    n = AS.send_due(CFG, st, box := box or Folder(), lambda m, msg: logs.append((m, msg)),
                    now=now or NOW + timedelta(hours=2), send_mail=send_mail or (lambda cfg, raw: sent.append(raw)))
    return n, logs, sent, box


def scheduled(tmp_path):
    st, rec, d = setup(tmp_path)
    AS.save_settings(st, LATER)
    seen(st, NOW)
    ok, logs, sent, _ = run(st, rec, d)
    assert ok is False and sent == [] and draft_row(st)["auto_after"] == iso(NOW + timedelta(minutes=60))
    return st


def test_the_wait_is_hours_per_category_and_defaults_to_zero(tmp_path):
    st, _, _ = setup(tmp_path)
    assert AS.settings(st)["delay_hours"] == ZERO
    st.db.execute("INSERT OR REPLACE INTO meta VALUES ('autosend', ?)",   # saved with the old slider in minutes
                  ('{"enabled": true, "daily_limit": 20, "categories": ["F2"], "delay_minutes": 60}',))
    st.db.commit()
    assert AS.settings(st) == {**ON, "delay_hours": ZERO}
    assert AS.save_settings(st, {**ON, "delay_hours": {"F3": 5}})["delay_hours"] == {**ZERO, "F3": 5}   # rest 0
    for h in (0, 1, 24, 720):
        assert AS.save_settings(st, {**ON, "delay_hours": {**ZERO, "F1": h}})["delay_hours"]["F1"] == h
    for bad in ({"F2": -1}, {"F2": 721}, {"F2": "1"}, {"F2": 1.0}, {"F2": True}, {"F2": None}, {"F9": 1},
                {"F6": 1}, [1], "F2", 5, None):
        try:
            AS.save_settings(st, {**ON, "delay_hours": bad})
            raise AssertionError(f"accepted {bad!r}")
        except ValueError as exc:
            assert str(exc) == "Atraso inválido (0 a 720 horas).", bad
    assert AS.settings(st)["delay_hours"] == {**ZERO, "F1": 720}


def test_each_category_waits_its_own_hours(tmp_path):
    st, rec, d = setup(tmp_path, folder="F3")
    AS.save_settings(st, {**ON, "categories": ["F2", "F3"], "delay_hours": {**ZERO, "F2": 5}})
    seen(st, NOW)
    assert run(st, rec, d)[:2] == (True, ["sent automatically"])   # F3 waits 0 hours
    st2, rec2, d2 = setup(tmp_path / "b")
    AS.save_settings(st2, {**ON, "categories": ["F2", "F3"], "delay_hours": {**ZERO, "F2": 5}})
    seen(st2, NOW)
    assert run(st2, rec2, d2)[0] is False and draft_row(st2)["auto_after"] == iso(NOW + timedelta(hours=5))


def test_a_draft_not_yet_due_is_scheduled_and_not_sent(tmp_path):
    st, rec, d = setup(tmp_path)
    AS.save_settings(st, LATER)
    seen(st, NOW)
    ok, logs, sent, box = run(st, rec, d)
    at = (NOW + timedelta(minutes=60)).astimezone().strftime("%H:%M")
    assert ok is False and sent == [] and logs == [f"automatic send scheduled for {at}"] and box.selects == []
    row = draft_row(st)
    assert row["auto_after"] == iso(NOW + timedelta(minutes=60)) and row["closed_at"] is None
    # a refused draft is not scheduled
    st2, rec2, d2 = setup(tmp_path / "b", note="falta: data")
    AS.save_settings(st2, LATER)
    seen(st2, NOW)
    assert run(st2, rec2, d2)[1] == ["not sent automatically: draft has an internal note"]
    assert draft_row(st2)["auto_after"] is None


def test_the_delay_counts_from_when_the_router_saw_the_email_not_its_date(tmp_path):
    st, rec, d = setup(tmp_path)
    AS.save_settings(st, LATER)
    old = iso(NOW - timedelta(days=3))
    st.db.execute("UPDATE drafts SET received_at=? WHERE message_id=?", (old, MID))   # the Date header
    st.db.commit()
    seen(st, NOW)
    ok, logs, sent, _ = run(st, {**rec, "date": old}, d)
    assert ok is False and sent == [] and logs[0].startswith("automatic send scheduled for ")
    st2, rec2, d2 = setup(tmp_path / "b")
    AS.save_settings(st2, LATER)
    seen(st2, NOW - timedelta(minutes=61))   # already past: sent now
    ok, logs, sent, _ = run(st2, {**rec2, "date": iso(NOW)}, d2)
    assert ok is True and len(sent) == 1 and logs == ["sent automatically"]
    # no decision: the ledger's received_at, which is also when the router saw it
    st3, rec3, d3 = setup(tmp_path / "c")
    AS.save_settings(st3, LATER)
    st3.db.execute("UPDATE replies SET received_at=? WHERE message_id=?", (iso(NOW - timedelta(hours=2)), MID))
    st3.db.commit()
    assert run(st3, rec3, d3)[:2] == (True, ["sent automatically"])


def test_a_due_draft_is_sent_by_send_due(tmp_path):
    st = scheduled(tmp_path)
    n, logs, sent, box = due(st, now=NOW + timedelta(minutes=59))   # not yet
    assert n == 0 and sent == [] and box.selects == [] and draft_row(st)["closed_at"] is None
    n, logs, sent, box = due(st, now=NOW + timedelta(minutes=60))
    assert n == 1 and len(sent) == 1 and logs == [(MID, "sent automatically")]
    assert b"To: ana@gmail.com" in sent[0] and b"In-Reply-To: <m1@x>" in sent[0]
    assert box.appended == ["INBOX.Sent"] and box.deleted == [("INBOX.Drafts", b"9")] and box.selects[-1] == "INBOX"
    assert draft_row(st)["closed_how"] == "auto"
    assert st.db.execute("SELECT how FROM replies WHERE message_id=?", (MID,)).fetchone()[0] == "auto"
    assert due(st)[:3] == (0, [], [])   # never twice


def test_a_staff_edit_meanwhile_means_the_claim_fails_and_nothing_is_sent(tmp_path):
    st = scheduled(tmp_path)
    real = AC._original

    def edited_meanwhile(box, folders, row):   # a person saves an edit between the read and the claim
        st.db.execute("UPDATE drafts SET created_at=?, edited=1 WHERE message_id=?", ("new version", MID))
        st.db.commit()
        return real(box, folders, row)
    AC._original = edited_meanwhile
    try:
        n, logs, sent, box = due(st)
    finally:
        AC._original = real
    assert n == 0 and sent == [] and logs == [(MID, "not sent automatically: draft already handled")]
    assert draft_row(st)["closed_at"] is None and draft_row(st)["auto_after"] is None and box.selects[-1] == "INBOX"


def test_a_category_switched_off_meanwhile_is_not_sent_and_waits_for_a_person(tmp_path):
    st = scheduled(tmp_path)
    AS.save_settings(st, {**LATER, "categories": ["F3"]})
    n, logs, sent, box = due(st)
    assert n == 0 and sent == [] and logs == [(MID, "not sent automatically: category not switched on")]
    row = draft_row(st)
    assert row["auto_after"] is None and row["closed_at"] is None and box.selects[-1] == "INBOX"
    st2 = scheduled(tmp_path / "b")
    AS.save_settings(st2, {**LATER, "enabled": False})
    assert due(st2)[1] == [(MID, "not sent automatically: automatic sending is off")]
    st3 = scheduled(tmp_path / "c")
    st3.db.execute("INSERT INTO drafts (message_id, folder, from_email, created_at, closed_at, closed_how) "
                   "VALUES ('a1', 'F3', 'ana@gmail.com', ?, ?, 'auto')", (LG._now(), LG._now()))
    st3.db.commit()
    assert due(st3)[1] == [(MID, "not sent automatically: sender already answered automatically today")]
    assert draft_row(st3)["auto_after"] is None


def test_an_email_gone_from_its_folder_clears_the_schedule(tmp_path):
    st = scheduled(tmp_path)
    n, logs, sent, box = due(st, box=Folder(mails={}))
    assert n == 0 and sent == [] and logs == [(MID, "not sent automatically: email no longer in its folder")]
    assert draft_row(st)["auto_after"] is None and box.selects[-1] == "INBOX"


def test_send_due_never_raises(tmp_path):
    st = scheduled(tmp_path)

    class Broken(Folder):
        def ensure_folders(self, names):
            raise OSError("imap down")
    n, logs, sent, box = due(st, box=Broken())
    assert n == 0 and sent == [] and logs == [("autosend", "automatic sends failed (OSError)")]
    assert box.selects[-1:] == ["INBOX"] and draft_row(st)["auto_after"]   # tried again next cycle

    class NoSelect(Folder):
        def select(self, enc, readonly=False):
            raise OSError("imap down")
    assert due(st, box=NoSelect())[:3] == (0, [(MID, "automatic send delayed: error (OSError), trying again next cycle"),
                                               ("autosend", "automatic sends failed (OSError)")], [])
    assert draft_row(st)["auto_after"] == iso(NOW + timedelta(minutes=60))   # nothing claimed: kept for a retry

    def fail(cfg, raw):
        raise OSError("smtp down")
    n, logs, sent, box = due(st, send_mail=fail)   # a failed send: reopened, and left for a person
    assert n == 0 and logs == [(MID, "not sent automatically: error (OSError)")] and box.selects[-1] == "INBOX"
    row = draft_row(st)
    assert row["closed_at"] is None and row["auto_after"] is None


def test_the_router_schedules_and_then_calls_send_due_at_the_end(tmp_path):
    st, sent, logs, calls = RT.State(tmp_path / "router.db"), [], [], []
    AS.save_settings(st, LATER)
    out = {"reply": "Os bilhetes estão na sua conta em https://www.tickett.com/conta.", "language": "pt-PT",
           "needs_action": False, "action_note": "", "missing_facts": [], "confidence": "high", "usage": {}}
    patches = [(RT, "build_classifier", lambda: None), (RT, "build_drafter", lambda *a: Drafter()),
               (RT, "decide", lambda *a, **k: RT.Decision("F2", False, "rule", "high", "stub")),
               (RT.DR, "prepare", lambda *a, **k: dict(out)), (RT, "purge_phishing", lambda *a, **k: None),
               (RT.A, "sync", lambda *a, **k: 0), (RT.A, "daily_draft", lambda *a, **k: None),
               (RT.DR, "maybe_crawl", lambda *a, **k: None), (RT.DR, "maybe_refresh_doc", lambda *a, **k: None),
               (LG, "event_names", lambda: []), (LG, "after_cycle", lambda *a, **k: calls.append("after_cycle")),
               (AS, "send_due", lambda *a, **k: calls.append(("send_due", a[1] is st))),
               (AC, "smtp_send", lambda cfg, raw: sent.append(raw))]
    saved = [(o, n, getattr(o, n)) for o, n, _ in patches]
    for o, n, v in patches:
        setattr(o, n, v)
    try:
        assert RT.file_inbox(Inbox(), st, lambda m, msg: logs.append((m, msg)), CFG, False) == 2
        RT.file_inbox(Inbox(), st, lambda m, msg: None, CFG, True)   # a dry run sends nothing
    finally:
        for o, n, v in saved:
            setattr(o, n, v)
    assert sent == [] and calls == ["after_cycle", ("send_due", True)]
    assert sum(msg.startswith("automatic send scheduled for ") for _, msg in logs) == 2


def test_a_changed_delay_moves_a_scheduled_draft_both_ways(tmp_path):
    st = scheduled(tmp_path)   # due at NOW + 60 min
    AS.save_settings(st, {**LATER, "delay_hours": {**ZERO, "F2": 2}})
    n, logs, sent, box = due(st, now=NOW + timedelta(minutes=61))
    at = (NOW + timedelta(minutes=120)).astimezone().strftime("%H:%M")
    assert n == 0 and sent == [] and box.selects == [] and logs == [(MID, f"automatic send rescheduled for {at}")]
    assert draft_row(st)["auto_after"] == iso(NOW + timedelta(minutes=120))
    assert due(st, now=NOW + timedelta(minutes=61))[1] == []   # logged once
    AS.save_settings(st, {**LATER, "delay_hours": ZERO})
    n, logs, sent, _ = due(st, now=NOW + timedelta(minutes=20))   # a shorter wait releases it earlier
    assert n == 1 and len(sent) == 1 and logs == [(MID, "sent automatically")]


def test_a_draft_no_longer_scheduled_is_not_claimed_by_send_due(tmp_path):
    st = scheduled(tmp_path)
    real = AC._original

    def taken_meanwhile(box, folders, row):   # a person starts an edit: the schedule is cleared at once
        st.db.execute("UPDATE drafts SET auto_after=NULL WHERE message_id=?", (MID,))
        st.db.commit()
        return real(box, folders, row)
    AC._original = taken_meanwhile
    try:
        n, logs, sent, _ = due(st)
    finally:
        AC._original = real
    assert n == 0 and sent == [] and logs == [(MID, "not sent automatically: draft already handled")]
    assert draft_row(st)["closed_at"] is None


class Redactor:
    def redact(self, subject, text, names=(), addrs=()):
        return subject, text, {}, []


def test_a_rewrite_in_progress_stops_the_scheduled_send(tmp_path):
    st = scheduled(tmp_path)
    mid_flight = []

    class Slow(Drafter):
        def draft(self, *a, **k):   # send_due runs while Claude writes the new version
            mid_flight.append(due(st))
            return {"reply": "Nova versão.", "language": "pt-PT", "needs_action": False, "action_note": "",
                    "missing_facts": [], "confidence": "high", "handoff": False, "usage": {}}
    row = draft_row(st)
    assert AC.rewrite(CFG, st, AC.card_id(MID), row["created_at"], "mais curto", lambda *a: None, Slow(), Redactor(),
                      connect=lambda cfg: Folder()) == "Nova versão pronta."
    assert mid_flight[0][:3] == (0, [], [])
    row = draft_row(st)
    assert "Nova versão." in row["body"] and row["closed_at"] is None and row["auto_after"] is None


class Claims(Folder):
    """The router claims and sends the draft while the person's request is talking to the mail server."""

    def __init__(self, st):
        super().__init__()
        self.st = st

    def ensure_folders(self, names):
        assert AC.claim(self.st, draft_row(self.st), "auto")
        return super().ensure_folders(names)


def test_an_edit_racing_an_automatic_send_is_stale_and_leaves_the_sent_row(tmp_path):
    st = scheduled(tmp_path)
    before = draft_row(st)
    box = Claims(st)
    try:
        AC.edit(CFG, st, AC.card_id(MID), before["created_at"], "Texto meu.", lambda *a: None, connect=lambda cfg: box)
        raise AssertionError("should be stale")
    except AC.Stale:
        pass
    row = draft_row(st)
    assert row["closed_how"] == "auto" and row["body"] == before["body"] and not row["edited"]
    assert row["created_at"] == before["created_at"] and row["draft_mid"] == before["draft_mid"]
    assert box.appended == ["INBOX.Drafts"] and box.deleted == []   # the new copy was looked for again, not the old


def test_tratado_sem_resposta_after_an_automatic_send_is_stale_and_keeps_closed_how(tmp_path):
    st = scheduled(tmp_path)
    version = draft_row(st)["created_at"]
    assert AC.claim(st, draft_row(st), "auto")   # the router sends it first
    try:
        AC.answered_by_hand(CFG, st, AC.card_id(MID), version, lambda *a: None, connect=lambda cfg: Folder())
        raise AssertionError("should be stale")
    except AC.Stale:
        pass
    assert draft_row(st)["closed_how"] == "auto" and AS.sent_today(st) == 1

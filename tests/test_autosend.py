"""Tests for automatic sending (no IMAP, no SMTP, no API): a fake mailbox and an injected send_mail."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import autosend as AS
import drafter as D
import drafts as DR
import extract as E
import ledger as LG
import router as RT

CFG = {"folders": {f"F{i}": f"{i} Pasta {i}" for i in range(1, 10)}, "answered_folder": "10 Respondidos",
       "handoff_folder": "0 Por Responder", "imap": {"user": "support@tickett.com"}}
RAW = (b"Subject: Bilhetes\r\nFrom: Ana Silva <ana@gmail.com>\r\nMessage-ID: <m1@x>\r\n"
       b"Content-Type: text/plain; charset=utf-8\r\n\r\nNao recebi os bilhetes.\r\n")
MID = "<m1@x>"
BODY = "Olá Ana,\n\nOs bilhetes estão em https://www.tickett.com/conta.\n\nCom os melhores cumprimentos,\nEquipa Tickett"
ON = {"enabled": True, "daily_limit": 20, "categories": ["F2"]}
ZERO = {"F1": 0, "F2": 0, "F3": 0, "F4": 0, "F5": 0}   # hours to wait, per category
OFF = {"enabled": False, "daily_limit": 20, "categories": [], "delay_hours": ZERO}


class Box:
    prefix = "INBOX."

    def __init__(self):
        self.selects, self.appended, self.deleted = [], [], []

    def folders(self):
        return {"INBOX.Sent": "\\Sent", "INBOX.Drafts": "\\Drafts"}

    def select(self, enc, readonly=False):
        self.selects.append(enc)

    def find(self, mid):
        return [b"9"] if self.selects[-1:] == ["INBOX.Drafts"] and mid == "<d1@tickett.com>" else []

    def append(self, enc, flags, when, raw):
        self.appended.append(enc)

    def delete(self, uid):
        self.deleted.append((self.selects[-1], uid))


class Drafter:
    sha = "s"


def setup(tmp_path, folder="F2", body=BODY, note="", flag=""):
    st = RT.State(tmp_path / "router.db")
    rec = E.record_from_bytes(RAW, "t")
    d = RT.Decision(folder, False, "claude", "high", "x")
    out = {"reply": "x", "language": "pt-PT", "needs_action": False, "action_note": "", "missing_facts": [],
           "confidence": "high", "usage": {}}
    assert DR.save(Box(), "INBOX.Drafts", rec, MID, d, st, out, Drafter(), CFG, lambda *a: None)
    st.db.execute("UPDATE drafts SET body=?, note=?, flag=?, draft_mid='<d1@tickett.com>' WHERE message_id=?",
                  (body, note, flag, MID))
    LG.init(st)
    st.db.execute("INSERT INTO replies (message_id, folder, received_at, from_email) VALUES (?,?,?,?)",
                  (MID, folder, LG._now(), "ana@gmail.com"))
    st.db.commit()
    return st, rec, d


def auto_reply(st, mid, sender, when, how="auto"):
    """A draft closed `how` at `when` (the limits count drafts, not the ledger)."""
    st.db.execute("INSERT INTO drafts (message_id, folder, from_email, created_at, closed_at, closed_how) "
                  "VALUES (?,?,?,?,?,?)", (mid, "F3", sender, when, when, how))
    st.db.commit()


def run(st, rec, d, send_mail=None):
    logs, sent = [], []
    ok = AS.maybe_send(CFG, st, box := Box(), rec, MID, d, lambda m, msg: logs.append(msg),
                       send_mail=send_mail or (lambda cfg, raw: sent.append(raw)))
    return ok, logs, sent, box


def draft_row(st):
    cur = st.db.execute("SELECT * FROM drafts WHERE message_id=?", (MID,))
    return dict(zip([c[0] for c in cur.description], cur.fetchone()))


def test_everything_is_off_by_default(tmp_path):
    st, rec, d = setup(tmp_path)
    assert AS.settings(st) == OFF
    ok, logs, sent, box = run(st, rec, d)
    assert ok is False and logs == [] and sent == [] and box.selects == []
    AS.save_settings(st, {"enabled": True, "daily_limit": 20, "categories": []})   # on, but no category
    assert run(st, rec, d)[:3] == (False, [], [])
    AS.save_settings(st, {"enabled": False, "daily_limit": 20, "categories": ["F2"]})   # category, but off
    assert run(st, rec, d)[:3] == (False, [], [])
    assert draft_row(st)["closed_at"] is None


def test_review_can_never_be_switched_on_and_refunds_only_for_tickett_refunds(tmp_path):
    st, rec, d = setup(tmp_path)
    assert AS.ALLOWED == ("F1", "F2", "F3", "F4", "F5") and AS.NEVER == ("F9",)
    for cats in (["F9"], ["F6"], ["F7"], ["F2", "F9"], ["F8"], ["P"], ["X"], [2], "F2"):
        try:
            AS.save_settings(st, {"enabled": True, "daily_limit": 20, "categories": cats})
            raise AssertionError(f"accepted {cats!r}")
        except ValueError as exc:
            assert str(exc) == "Categoria não permitida."
    for bad in ({"enabled": True, "daily_limit": 201, "categories": []},
                {"enabled": True, "daily_limit": -1, "categories": []},
                {"enabled": True, "daily_limit": "5", "categories": []},
                {"enabled": True, "daily_limit": True, "categories": []},
                {"enabled": "yes", "daily_limit": 5, "categories": []}, ["F2"]):
        try:
            AS.save_settings(st, bad)
            raise AssertionError(f"accepted {bad!r}")
        except ValueError:
            pass
    assert AS.settings(st) == OFF
    assert AS.save_settings(st, {"enabled": True, "daily_limit": 0, "categories": ["F1", "F2", "F2"]}) == \
        {"enabled": True, "daily_limit": 0, "categories": ["F1", "F2"], "delay_hours": ZERO}
    # even settings forged straight into the database cannot switch F9 on
    st.db.execute("INSERT OR REPLACE INTO meta VALUES ('autosend', ?)",
                  ('{"enabled": true, "daily_limit": 20, "categories": ["F9", "F2"]}',))
    st.db.commit()
    assert AS.settings(st) == OFF
    on1 = {**ON, "categories": ["F1"]}
    st1, rec1, d1 = setup(tmp_path / "f1", folder="F1")
    assert AS.why_not(rec1, draft_row(st1), d1, on1, 0, False) == "refunds go out automatically only when Tickett handles them"
    st2, rec2, d2 = setup(tmp_path / "f1r", folder="F1", flag="tickett_refund")
    assert AS.why_not(rec2, draft_row(st2), d2, on1, 0, False) == ""
    st9, rec9, d9 = setup(tmp_path / "f9", folder="F9")
    assert AS.why_not(rec9, draft_row(st9), d9, {**ON, "categories": ["F9"]}, 0, False) == "category not switched on"


def test_a_tickett_refund_goes_out_automatically_with_the_copy_to_finance(tmp_path):
    st, rec, d = setup(tmp_path, folder="F1", flag="tickett_refund")
    st.db.execute("UPDATE drafts SET bcc='finance@tickett.com'")
    st.db.commit()
    AS.save_settings(st, {**ON, "categories": ["F1"]})
    ok, logs, sent, box = run(st, rec, d)
    assert ok is True and logs == ["sent automatically"] and b"Bcc: finance@tickett.com" in sent[0]


def test_each_refusal_reason(tmp_path):
    st, rec, d = setup(tmp_path)
    row = draft_row(st)
    ok = lambda r=rec, w=row, dd=d, s=ON, n=0, same=False: AS.why_not(r, w, dd, s, n, same)
    assert ok() == ""
    assert ok(s={**ON, "enabled": False}) == "automatic sending is off"
    assert ok(s={**ON, "categories": ["F3"]}) == "category not switched on"
    assert ok(dd=RT.Decision("F2", True, "claude", "high", "x")) == "urgent"
    assert ok(r={**rec, "reply_to": "Outro <outro@gmail.com>"}) == "Reply-To differs from the sender"
    assert ok(r={**rec, "reply_to": "ANA@gmail.com"}) == ""   # same address, other case
    for bad in ("ana@gmail.com, outro@gmail.com", "garbage", "ana@gmail.com outro@gmail.com"):   # not one address
        assert ok(r={**rec, "reply_to": bad}) == "Reply-To differs from the sender", bad
    assert ok(r={**rec, "auto_submitted": "auto-replied"}) == "auto-submitted mail"
    assert ok(r={**rec, "auto_submitted": "no"}) == ""
    assert ok(r={**rec, "precedence": "bulk"}) == "bulk or list mail"
    assert ok(r={**rec, "list_unsubscribe": True}) == "mailing-list mail"
    assert ok(w={**row, "body": D.NOTE + "ligar\n\n" + BODY}) == "draft not sendable"
    assert ok(w={**row, "body": "Olá [NAME],"}) == "draft not sendable"
    assert ok(w={**row, "body": None}) == "draft not sendable"
    assert ok(w={**row, "note": "falta: data do evento"}) == "draft has an internal note"
    for flag in ("legal", "relayed", "no_answer_in_sources", "person_must_act", "something new"):
        assert ok(w={**row, "flag": flag}) == f"draft flagged {flag}", flag
    assert ok(w={**row, "flag": "tickett_refund"}) == "" and ok(w={**row, "flag": None}) == ""
    assert ok(w={**row, "flag": "relayed", "to_override": "rui@gmail.com"}) == "draft flagged relayed"
    assert ok(w={**row, "forwarded": 1}) == "forwarded mail: a person sends it"   # even when it is a normal draft
    fwd = {**rec, "subject": "Fwd: Bilhetes", "body_text": "---------- Forwarded message ---------\n"
           "From: Tickett <noreply@tickett.com>\nTo: <ana@gmail.com>\n"}
    assert ok(r=fwd) == "forwarded mail: a person sends it"   # the triage reason, also without the marker
    for link in ("https://evil.com/x", "http://tickett.com.evil.com", "www.evil.com", "https://tickett.com@evil.com",
                 "https://evil.com\\@tickett.com", "HTTPS://EVIL.COM", "https://faketickett.com", "https:///evil.com",
                 "https:\\\\evil.com", "https:evil.com", "https://evil%2ecom", "https://tickett.com/ e www.evil.com"):
        assert ok(w={**row, "body": f"Veja {link} hoje."}) == "link to a site other than tickett.com", link
    for link in ("evil.com", "evil.com/bilhetes", "EVIL.COM", "loja.evil.co.uk/x", "tickett.com.evil.com",
                 "faketickett.com", "(evil.com)"):   # bare domains, without a scheme or www
        assert ok(w={**row, "body": f"Veja {link} hoje."}) == "link to a site other than tickett.com", link
    for link in ("https://tickett.com.", "http://ajuda.tickett.com/a?b=1", "www.tickett.com,", "https://TICKETT.com:443/",
                 "tickett.com", "ajuda.Tickett.com/conta.", "ana@gmail.com", "joao.silva@empresa.co.uk", "12.50 €",
                 "às 10.30h"):
        assert ok(w={**row, "body": f"Veja {link} hoje."}) == "", link
    assert ok(n=20) == "daily limit reached"
    assert ok(s={**ON, "daily_limit": 0}) == "daily limit reached"
    assert ok(same=True) == "sender already answered automatically today"


def test_an_allowed_draft_is_sent_and_recorded_as_auto(tmp_path):
    st, rec, d = setup(tmp_path)
    AS.save_settings(st, ON)
    ok, logs, sent, box = run(st, rec, d)
    assert ok is True and logs == ["sent automatically"] and len(sent) == 1
    assert b"To: ana@gmail.com" in sent[0] and b"In-Reply-To: <m1@x>" in sent[0]
    assert box.appended == ["INBOX.Sent"] and box.deleted == [("INBOX.Drafts", b"9")]
    assert box.selects[-1] == "INBOX"   # the router's loop goes on in the Inbox
    row = draft_row(st)
    assert row["closed_how"] == "auto" and row["closed_at"]
    assert st.db.execute("SELECT how FROM replies WHERE message_id=?", (MID,)).fetchone()[0] == "auto"
    assert AS.sent_today(st) == 1 and AS.sender_today(st, "ANA@gmail.com")
    assert run(st, rec, d)[:3] == (False, ["not sent automatically: no open draft"], [])   # never twice


def test_a_draft_with_an_internal_note_is_not_sent(tmp_path):
    st, rec, d = setup(tmp_path, note="ação necessária: ligar ao promotor")
    AS.save_settings(st, ON)
    ok, logs, sent, _ = run(st, rec, d)
    assert ok is False and sent == [] and logs == ["not sent automatically: draft has an internal note"]


def test_the_daily_limit_and_one_per_sender_hold(tmp_path):
    st, rec, d = setup(tmp_path)
    now = datetime.now(timezone.utc)
    yesterday = (datetime.now().astimezone() - timedelta(days=1)).replace(hour=12).astimezone(timezone.utc)
    auto_reply(st, "old", "ana@gmail.com", yesterday.isoformat(timespec="seconds"))   # yesterday: not counted
    auto_reply(st, "dash", "ana@gmail.com", now.isoformat(timespec="seconds"), how="sent")   # by a person
    st.db.execute("INSERT INTO replies (message_id, answered_at, how, from_email) VALUES ('l1', ?, 'auto', "
                  "'ana@gmail.com')", (now.isoformat(timespec="seconds"),))   # the ledger alone does not count
    st.db.commit()
    assert AS.sent_today(st) == 0 and not AS.sender_today(st, "ana@gmail.com")
    auto_reply(st, "b1", "bruno@gmail.com", now.isoformat(timespec="seconds"))
    AS.save_settings(st, {**ON, "daily_limit": 1})
    ok, logs, sent, _ = run(st, rec, d)
    assert ok is False and sent == [] and logs == ["not sent automatically: daily limit reached"]
    AS.save_settings(st, ON)
    auto_reply(st, "a1", "Ana@Gmail.com", now.isoformat(timespec="seconds"))
    ok, logs, sent, _ = run(st, rec, d)
    assert ok is False and sent == [] and logs == ["not sent automatically: sender already answered automatically today"]
    assert draft_row(st)["closed_at"] is None


def test_a_send_failure_is_logged_and_does_not_raise(tmp_path):
    st, rec, d = setup(tmp_path)
    AS.save_settings(st, ON)

    def fail(cfg, raw):
        raise OSError("smtp down")
    ok, logs, sent, box = run(st, rec, d, send_mail=fail)
    assert ok is False and logs == ["not sent automatically: error (OSError)"]
    assert draft_row(st)["closed_at"] is None and draft_row(st)["closed_how"] is None   # claimed, then reopened
    assert box.appended == [] and box.selects[-1:] == ["INBOX"]
    assert st.db.execute("SELECT answered_at FROM replies WHERE message_id=?", (MID,)).fetchone()[0] is None
    assert AS.sent_today(st) == 0
    # a failure after the mail left is logged as sent, and counts toward the limits
    st2, rec2, d2 = setup(tmp_path / "b")
    AS.save_settings(st2, ON)
    box2 = Box()
    box2.delete = lambda uid: (_ for _ in ()).throw(OSError("imap"))
    logs2, sent2 = [], []
    assert AS.maybe_send(CFG, st2, box2, rec2, MID, d2, lambda m, msg: logs2.append(msg),
                         send_mail=lambda cfg, raw: sent2.append(raw)) is True
    assert len(sent2) == 1 and logs2 == ["sent automatically, then error (OSError)"] and box2.selects[-1] == "INBOX"
    assert AS.sent_today(st2) == 1


def test_status_shows_settings_sent_today_and_readiness(tmp_path):
    st, rec, d = setup(tmp_path)
    for i, how in enumerate(("sent", "sent", "auto")):
        st.db.execute("INSERT INTO drafts (message_id, folder, body, first_body, closed_at, closed_how) "
                      "VALUES (?,?,?,?,?,?)", (f"q{i}", "F3", "x", "y" if i else "x", LG._now(), how))
    st.db.commit()
    s = AS.status(CFG, st)
    assert s["settings"] == AS.settings(st) and s["sent_today"] == 1   # q2, closed auto today
    assert [r["folder"] for r in s["readiness"]] == ["F1", "F2", "F3", "F4", "F5"]
    assert s["readiness"][2] == {"folder": "F3", "name": "3 Pasta 3", "drafts": 2, "unchanged": 1}
    assert s["readiness"][1]["drafts"] == 0


def test_a_draft_claimed_by_the_dashboard_meanwhile_is_not_sent(tmp_path):
    st, rec, d = setup(tmp_path)
    AS.save_settings(st, ON)
    real = AS._open_draft

    def open_then_lost(state, mid):   # a person clicks Enviar between the read and the claim
        row = real(state, mid)
        import actions as AC
        assert AC.claim(state, row, "sent")
        return row
    AS._open_draft = open_then_lost
    try:
        ok, logs, sent, box = run(st, rec, d)
    finally:
        AS._open_draft = real
    assert ok is False and sent == [] and logs == ["not sent automatically: draft already handled"]
    assert draft_row(st)["closed_how"] == "sent" and box.selects[-1:] == ["INBOX"]


class Inbox(Box):
    """Two customer emails in the Inbox; fetch and move only work on the Inbox."""
    MAILS = {b"1": RAW, b"2": RAW.replace(b"<m1@x>", b"<m2@x>").replace(b"ana@gmail.com", b"rui@gmail.com")
             .replace(b"Ana Silva", b"Rui Costa")}

    def __init__(self):
        super().__init__()
        self.fetched, self.moved = [], []

    def ensure_folders(self, names):
        return {k: "INBOX." + v for k, v in names.items()}

    def uids(self, since=None):
        return list(self.MAILS)

    def fetch(self, uid):
        assert self.selects[-1] == "INBOX", f"fetch {uid} with {self.selects[-1]} selected"
        self.fetched.append(uid)
        return self.MAILS[uid]

    def move(self, uid, dest):
        assert self.selects[-1] == "INBOX"
        self.moved.append((uid, dest))

    def flag(self, uid):
        pass


def test_the_router_files_the_next_email_after_an_automatic_send(tmp_path):
    import actions as AC
    st, sent, logs = RT.State(tmp_path / "router.db"), [], []
    AS.save_settings(st, ON)
    out = {"reply": "Os bilhetes estão na sua conta em https://www.tickett.com/conta.", "language": "pt-PT",
           "needs_action": False, "action_note": "", "missing_facts": [], "confidence": "high", "usage": {}}
    patches = [(RT, "build_classifier", lambda: None), (RT, "build_drafter", lambda *a: Drafter()),
               (RT, "decide", lambda *a, **k: RT.Decision("F2", False, "rule", "high", "stub")),
               (RT.DR, "prepare", lambda *a, **k: dict(out)), (RT, "purge_phishing", lambda *a, **k: None),
               (RT.A, "sync", lambda *a, **k: 0), (RT.A, "daily_draft", lambda *a, **k: None),
               (RT.DR, "maybe_crawl", lambda *a, **k: None), (RT.DR, "maybe_refresh_doc", lambda *a, **k: None),
               (LG, "event_names", lambda: []), (LG, "after_cycle", lambda *a, **k: None),
               (AC, "smtp_send", lambda cfg, raw: sent.append(raw))]
    saved = [(o, n, getattr(o, n)) for o, n, _ in patches]
    for o, n, v in patches:
        setattr(o, n, v)
    try:
        box = Inbox()
        assert RT.file_inbox(box, st, lambda m, msg: logs.append((m, msg)), CFG, False) == 2
    finally:
        for o, n, v in saved:
            setattr(o, n, v)
    assert box.fetched == [b"1", b"2"] and [u for u, _ in box.moved] == [b"1", b"2"]
    assert len(sent) == 2 and b"To: ana@gmail.com" in sent[0] and b"To: rui@gmail.com" in sent[1]
    assert ("<m1@x>", "sent automatically") in logs and ("<m2@x>", "sent automatically") in logs
    assert "INBOX.Drafts" in box.selects   # the first send left the Inbox, and the router came back to it

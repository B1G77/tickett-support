"""Tests for answered-mail tracking and the open-cases digest (no IMAP)."""
import email
import email.policy
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import answered as A
import router as RT

FOLDERS = {"F1": "INBOX.1 Cancelamentos e Reembolsos", "F2": "INBOX.2 Bilhetes n&AOM-o recebidos"}


def state():
    return RT.State(Path(tempfile.mkdtemp()) / "router.db")


def test_a_reply_is_looked_for_in_the_inbox_and_its_conversation_folder():
    st = state()
    st.record("<orig@x>", [], RT.Decision("F1", False, "rule", "high", "refund"), moved=True)
    assert A.targets(["<orig@x>", "<other@x>"], st, FOLDERS) == [
        ("INBOX", "<orig@x>"), ("INBOX.1 Cancelamentos e Reembolsos", "<orig@x>"), ("INBOX", "<other@x>")]


def test_sent_progress_and_digest_day_are_remembered():
    st = state()
    assert A.get_meta(st, "sent_uid") is None
    A.set_meta(st, "sent_uid", "42")
    assert A.get_meta(st, "sent_uid") == "42"


def test_daily_draft_is_due_once_a_day_after_the_hour():
    st = state()
    assert not A.digest_due(st, datetime(2026, 9, 15, 7, 59), hour=8)
    assert A.digest_due(st, datetime(2026, 9, 15, 8, 5), hour=8)
    A.set_meta(st, "digest_day", "2026-09-15")
    assert not A.digest_due(st, datetime(2026, 9, 15, 18, 0), hour=8)
    assert A.digest_due(st, datetime(2026, 9, 16, 8, 1), hour=8)


def test_digest_lists_open_mail_by_folder_oldest_first_with_overdue_marks():
    now = datetime(2026, 9, 15, 9, tzinfo=timezone.utc)
    rows = [("INBOX", now - timedelta(hours=2), "Dúvida"),
            ("1 Cancelamentos e Reembolsos", now - timedelta(days=1), "Re: Reembolso"),
            ("1 Cancelamentos e Reembolsos", now - timedelta(days=3), "Reembolso"),
            ("INBOX", None, "Sem data")]
    lines = A.format_digest(rows, now, overdue_days=2).splitlines()
    assert lines[0] == "Por responder: 4 · atrasadas (mais de 2 dias): 1"
    flagged = [l for l in lines if l.lstrip().startswith("⚠")]
    assert len(flagged) == 1 and flagged[0].endswith("  Reembolso")
    canc = lines.index("1 Cancelamentos e Reembolsos (2)")
    assert lines[canc + 1].endswith("  Reembolso") and lines[canc + 2].endswith("  Re: Reembolso")


def test_draft_is_a_valid_email_to_the_mailbox_itself():
    raw = A.draft_message("Por responder: 0 · atrasadas (mais de 2 dias): 0", "atendimento@lebillet.eu", "2026-09-15")
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert msg["To"] == msg["From"] == "atendimento@lebillet.eu"
    assert "2026-09-15" in msg["Subject"] and "Por responder: 0" in msg.get_content()


def test_a_reply_also_looks_in_the_handoff_folder():
    import router as RT
    st = RT.State(Path(tempfile.mkdtemp()) / "router.db")
    st.record("<m1@x>", [], RT.Decision("F1", False, "claude", "high", "x"), moved=True)
    pairs = A.targets(["<m1@x>"], st, {"F1": "INBOX.1", "P": "INBOX.0 Por Responder"})
    assert ("INBOX.0 Por Responder", "<m1@x>") in pairs and ("INBOX.1", "<m1@x>") in pairs

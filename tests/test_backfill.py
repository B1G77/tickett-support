"""Tests for backfill helpers (no IMAP, no API)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import backfill as B


class FakeBox:
    def __init__(self, quota):
        self._quota = quota

    def quota(self):
        return self._quota


def test_quota_is_parsed_from_getquotaroot_output():
    box = FakeBox([[b'"INBOX" ""'], [b'"" (STORAGE 1024 5120)']])
    assert B.quota_free_kb(box) == 4096
    assert B.quota_free_kb(FakeBox(None)) is None


def test_messages_are_keyed_by_message_id():
    assert B.key_of({"message_id": "<a@x>", "id": "never-read"}) == "<a@x>"


def test_session_labels_become_decisions_and_old_urgency_is_dropped():
    labels = [{"case_id": "a", "folder": "F1", "urgent": True, "code": "1b!"},
              {"case_id": "b", "folder": "F2", "urgent": True, "code": "2a!"},
              {"case_id": "c", "folder": "F5", "urgent": False, "code": "5b"}]
    cases = [{"case_id": "a", "last_date": "2026-09-01T10:00:00"},
             {"case_id": "b", "last_date": "2025-03-01T10:00:00"},
             {"case_id": "c", "last_date": "2026-09-05T10:00:00"}]
    d = B.session_decisions(labels, cases, today="2026-09-11")
    assert d["a"] == {"folder": "F1", "urgent": True, "confidence": "session", "reason": "session label 1b!"}
    assert d["b"]["urgent"] is False and d["c"]["urgent"] is False

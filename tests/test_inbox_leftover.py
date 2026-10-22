"""Tests for the INBOX-leftover filing plan (no IMAP)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import inbox_leftover as L


def test_items_cover_labelled_cases_and_non_customer_mail():
    cases = [{"case_id": "c1", "last_date": "2026-09-10T08:00:00", "message_ids": ["imap:1", "imap:2"]},
             {"case_id": "c2", "last_date": "2026-09-10T08:00:00", "message_ids": ["imap:4"]}]
    msgs = [{"id": "imap:1"}, {"id": "imap:2"}, {"id": "imap:4"}]
    quar = [{"id": "imap:3", "bucket": "phishing", "bucket_reason": "brand impersonation"}]
    labels = [{"case_id": "c1", "folder": "F1", "urgent": True, "code": "1a!"}]
    items, missing = L.work(cases, msgs, quar, labels, "2026-09-11")
    assert missing == 1
    assert [(i[1], i[2], i[3]) for i in items] == [("F1", True, False), ("F1", True, False), ("F8", False, True)]

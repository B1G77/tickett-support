"""Tests for the one-off backlog drafter's selection (no IMAP, no API)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import draft_backlog as B


def rec(mid, date, refs=(), reply_to=""):
    return {"message_id": mid, "date": date, "references": list(refs), "in_reply_to": reply_to}


def test_only_the_newest_open_message_of_each_conversation_is_kept():
    a1 = rec("<a1@x>", "2026-09-20T10:00:00+00:00")
    a2 = rec("<a2@x>", "2026-09-22T10:00:00+00:00", ["<a1@x>"], "<a1@x>")
    b1 = rec("<b1@x>", "2026-09-21T10:00:00+00:00")
    c2 = rec("<c2@x>", "2026-09-23T10:00:00+00:00", ["<c0@x>", "<c1@x>"], "<c1@x>")
    kept = B.newest_per_conversation([a1, b1, a2, c2])
    assert [r["message_id"] for r in kept] == ["<b1@x>", "<a2@x>", "<c2@x>"]


def test_a_reply_without_references_joins_its_parent():
    p = rec("<p@x>", "2026-09-20T10:00:00+00:00")
    q = rec("<q@x>", "2026-09-21T10:00:00+00:00", reply_to="<p@x>")
    assert [r["message_id"] for r in B.newest_per_conversation([p, q])] == ["<q@x>"]


def test_a_message_without_a_date_does_not_break_the_order():
    kept = B.newest_per_conversation([rec("<n@x>", ""), rec("<d@x>", "2026-09-21T10:00:00+00:00")])
    assert [r["message_id"] for r in kept] == ["<n@x>", "<d@x>"]

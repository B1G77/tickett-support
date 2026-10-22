"""The rulebook carries the user's decisions and no personal data."""
import re
from pathlib import Path

BOOK = Path(__file__).resolve().parent.parent / "config" / "rulebook.md"


def test_rulebook_has_every_section_and_decision():
    text = BOOK.read_text(encoding="utf-8")
    for heading in ("## Sources, in this order", "## Hand-off to a person", "## Tone", "## Decisions",
                    "## By topic", "## Never", "## Examples"):
        assert heading in text, heading
    for rule in ("tickett_refund", "no_answer_in_sources", "person_must_act", "service fee is not refunded",
                 "no exchanges", "can be passed to another person", "not sent as PDF", "Never promise a time",
                 "personal contact", "tickett.com/pt-PT/reenviar-bilhete", "Equipa Tickett"):
        assert rule in text, rule


def test_rulebook_holds_no_personal_data():
    text = BOOK.read_text(encoding="utf-8")
    assert set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", text)) <= {"atendimento@lebillet.eu"}
    assert not re.search(r"\+?\d[\d ]{8,}\d", text)

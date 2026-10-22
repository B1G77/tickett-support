"""Tests for the IMAP helpers, against a fake connection (no network)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import imap_util as I


class FakeConn:
    def __init__(self, prefix=""):
        self.capabilities = ("IMAP4REV1", "MOVE", "NAMESPACE")
        self.prefix = prefix
        self.created = []
        self.boxes = ['(\\HasNoChildren) "." INBOX', '(\\HasNoChildren \\Trash) "." "Trash"']

    def namespace(self):
        return "OK", [f'(("{self.prefix}" ".")) NIL NIL'.encode()]

    def list(self):
        return "OK", [b.encode() for b in self.boxes]

    def create(self, name):
        self.created.append(name)
        self.boxes.append(f'(\\HasNoChildren) "." {name}')
        return "OK", [b""]

    def subscribe(self, name):
        return "OK", [b""]


def test_modified_utf7_round_trip():
    assert I.encode_mutf7("8 Não-cliente") == "8 N&AOM-o-cliente"
    assert I.encode_mutf7("4 Alterações de Reserva") == "4 Altera&AOcA9Q-es de Reserva"
    assert I.encode_mutf7("A&B") == "A&-B"
    for s in ("8 Não-cliente", "4 Alterações de Reserva", "A&B", "plain"):
        assert I.decode_mutf7(I.encode_mutf7(s)) == s


def test_quote_and_namespace():
    assert I.quote('a "b" \\c') == '"a \\"b\\" \\\\c"'
    assert I.parse_namespace([b'(("INBOX." ".")) NIL NIL']) == ("INBOX.", ".")
    assert I.parse_namespace([b'(("" "/")) NIL NIL']) == ("", "/")


def test_ensure_folders_creates_only_missing_ones():
    box = I.Mailbox(FakeConn())
    got = box.ensure_folders({"F8": "8 Não-cliente", "F9": "9 A Rever"})
    assert got == {"F8": "8 N&AOM-o-cliente", "F9": "9 A Rever"}
    assert box.c.created == ['"8 N&AOM-o-cliente"', '"9 A Rever"']
    box.ensure_folders({"F8": "8 Não-cliente"})
    assert len(box.c.created) == 2


def test_namespace_prefix_and_trash_detection():
    box = I.Mailbox(FakeConn(prefix="INBOX."))
    assert box.ensure_folders({"F9": "9 A Rever"}) == {"F9": "INBOX.9 A Rever"}
    assert box.trash() == "Trash"


def test_capabilities_are_refreshed_after_login():
    conn = FakeConn()
    conn.capabilities = ("IMAP4REV1", "IDLE")                         # pre-login greeting
    conn.capability = lambda: ("OK", [b"IMAP4rev1 IDLE MOVE UIDPLUS QUOTA NAMESPACE"])
    box = I.Mailbox(conn)
    assert {"MOVE", "UIDPLUS", "QUOTA", "NAMESPACE"} <= box.caps


def test_imap_search_date_format():
    assert I.imap_date("2026-09-10") == "10-Sep-2026"


class SearchConn(FakeConn):
    """SEARCH HEADER matches substrings, so it returns every UID; FETCH gives each real Message-ID."""
    def __init__(self, ids):
        super().__init__()
        self.ids, self.sent = ids, []

    def uid(self, cmd, *args):
        self.sent.append((cmd, *args))
        if cmd == "SEARCH":
            return "OK", [b" ".join(self.ids)]
        hdr = f"Message-ID: {self.ids[args[0]]}\r\n\r\n".encode()
        return "OK", [(args[0] + b" (UID " + args[0] + b" BODY[HEADER.FIELDS (MESSAGE-ID)] {40}", hdr), b")"]


def test_find_keeps_only_exact_message_id_matches():
    box = I.Mailbox(SearchConn({b"1": "<a@x>", b"2": "<b@x>"}))
    assert box.find("<a@x>") == [b"1"]
    assert box.find("@") == []            # an attacker's short ID must not match everything


def test_find_refuses_unsafe_message_ids_without_asking_the_server():
    conn = SearchConn({b"1": "<a@x>"})
    box = I.Mailbox(conn)
    assert box.find("<x\r\nA1 SELECT INBOX\r\n@x>") == []
    assert box.find("<é@x>") == [] and box.find('<a"b@x>') == [] and box.find("") == []
    assert conn.sent == []

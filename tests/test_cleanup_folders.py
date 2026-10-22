"""The folder cleanup must never touch system or router folders."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import cleanup_folders as CF
import imap_util as I


class FakeConn:
    capabilities = ("IMAP4REV1", "NAMESPACE")

    def namespace(self):
        return "OK", [b'(("INBOX." ".")) NIL NIL']

    def list(self):
        boxes = ['(\\HasChildren) "." INBOX', '(\\HasNoChildren \\Sent) "." INBOX.Sent',
                 '(\\HasNoChildren) "." INBOX.Spam', '(\\HasNoChildren \\Trash) "." INBOX.Trash',
                 '(\\HasNoChildren) "." INBOX.Drafts', '(\\HasNoChildren) "." "INBOX.Diogo Nogueira"',
                 '(\\Noselect \\HasChildren) "." INBOX.Dra', '(\\HasNoChildren) "." "INBOX.Dra. Andrea Vermont"',
                 '(\\HasNoChildren) "." "INBOX.9 A Rever"']
        return "OK", [b.encode() for b in boxes]


def test_only_hand_made_folders_deepest_first():
    box = I.Mailbox(FakeConn())
    names = [n for n, _ in CF.targets(box, {"F9": "9 A Rever"})]
    assert names == ["INBOX.Dra. Andrea Vermont", "INBOX.Diogo Nogueira", "INBOX.Dra"]

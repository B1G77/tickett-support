"""Tests for the one-time move of folders 6 and 7 into 9 A Rever (a fake mailbox, no IMAP)."""
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import merge_folders as MF
import router as RT

SHOP, PARTNERS, REVIEW = "INBOX.6 Loja e Encomendas", "INBOX.7 Parceiros e Promotores", "INBOX.9 A Rever"


class Conn:
    def __init__(self, box):
        self.box, self.deleted, self.unsubscribed = box, [], []

    def uid(self, cmd, charset, *crit):
        assert (cmd, crit) == ("SEARCH", ("DELETED",))
        mine = [u for u in self.box.store.get(self.box.selected, []) if u in self.box.marked]
        return "OK", [b" ".join(mine)]

    def unsubscribe(self, q):
        self.unsubscribed.append(q.strip('"'))
        return "OK", [b""]

    def delete(self, q):
        name = q.strip('"')
        assert self.box.selected != name, "never delete the selected folder"
        self.deleted.append(name)
        self.box.store.pop(name)
        return "OK", [b""]


class Box:
    prefix, delim = "INBOX.", "."

    def __init__(self, store, stuck=(), marked=()):
        self.store, self.marked = {k: list(v) for k, v in store.items()}, set(marked)
        self.stuck, self.selected, self.moves, self.created = set(stuck), None, [], []
        self.c = Conn(self)

    def folders(self):
        return {name: "" for name in self.store}

    def encoded(self, names):
        return {k: self.prefix + v for k, v in names.items()}

    def ensure_folders(self, names):
        out = self.encoded(names)
        for enc in out.values():
            if enc not in self.store:
                self.store[enc] = []
                self.created.append(enc)
        return out

    def select(self, enc, readonly=False):
        assert enc in self.store or enc == "INBOX", enc
        self.selected = enc

    def uids(self, since=None):
        return list(self.store.get(self.selected, []))

    def move(self, uid, dest):
        self.moves.append((self.selected, uid, dest))
        if uid not in self.stuck:
            self.store[self.selected].remove(uid)
            self.store[dest].append(uid)

    def _ok(self, res, what):
        if res[0] != "OK":
            raise RuntimeError(what)
        return res[1]

    def logout(self):
        pass


def test_messages_already_copied_but_not_removed_stop_the_run():
    box, out = mailbox(marked={b"2"}), []   # a COPY + \\Deleted from an earlier run, never expunged
    assert MF.apply(box, "9 A Rever", out.append) == 0
    assert box.moves == [] and box.c.deleted == [] and box.store[REVIEW] == [b"9"]
    assert out == ["6 Loja e Encomendas: 1 message(s) already copied but still marked deleted (the server has no "
                   "MOVE or UIDPLUS) - stopped; expunge or remove them by hand, then run again"]


def mailbox(**kw):
    return Box({"INBOX": [], SHOP: [b"1", b"2"], PARTNERS: [b"7"], REVIEW: [b"9"]}, **kw)


def test_the_dry_run_counts_and_changes_nothing():
    box = mailbox()
    assert MF.plan(box) == [("6 Loja e Encomendas", 2), ("7 Parceiros e Promotores", 1)]
    assert box.moves == [] and box.c.deleted == []
    gone = Box({"INBOX": [], REVIEW: []})
    assert MF.plan(gone) == [("6 Loja e Encomendas", None), ("7 Parceiros e Promotores", None)]


def test_apply_moves_everything_into_a_rever_and_deletes_the_empty_folders():
    box, out = mailbox(), []
    assert MF.apply(box, "9 A Rever", out.append) == 3
    assert sorted(box.store[REVIEW]) == [b"1", b"2", b"7", b"9"]
    assert box.c.deleted == [SHOP, PARTNERS] and box.c.unsubscribed == [SHOP, PARTNERS]
    assert SHOP not in box.store and PARTNERS not in box.store
    assert out == ["6 Loja e Encomendas: 2 moved to 9 A Rever, folder removed",
                   "7 Parceiros e Promotores: 1 moved to 9 A Rever, folder removed"]
    assert MF.apply(box, "9 A Rever", out.append) == 0   # a second run finds nothing to do


def test_a_folder_that_is_not_empty_after_the_move_is_kept():
    box, out = mailbox(stuck={b"2"}), []
    assert MF.apply(box, "9 A Rever", out.append) == 2
    assert box.c.deleted == [PARTNERS] and box.store[SHOP] == [b"2"]
    assert out[0] == "6 Loja e Encomendas: 1 message(s) still there after the move - folder kept"


def test_main_is_a_dry_run_unless_apply_is_given():
    boxes = []

    def connect(cfg):
        boxes.append(mailbox())
        return boxes[-1]
    saved = RT.connect, RT.load_config
    RT.connect, RT.load_config = connect, lambda: {"folders": {"F9": "9 A Rever"}}
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert MF.main([]) == 0
        assert boxes[0].moves == [] and "2  6 Loja e Encomendas" in buf.getvalue()
        assert "dry run" in buf.getvalue()
        with contextlib.redirect_stdout(io.StringIO()):
            assert MF.main(["--apply"]) == 0
        assert boxes[1].c.deleted == [SHOP, PARTNERS]
    finally:
        RT.connect, RT.load_config = saved

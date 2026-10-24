#!/usr/bin/env python3
"""One-off: fold the old folders 6 (shop orders) and 7 (partners) into 9 A Rever.

Since 23 Oct 2026 shop orders and partner mail are read by hand in 9 A Rever. This moves
every message of "6 Loja e Encomendas" and "7 Parceiros e Promotores" there - nothing is
deleted - and removes each old folder once it is empty. Run once on the server at deploy.

Usage: merge_folders.py            (dry run: how many messages each old folder holds)
       merge_folders.py --apply    (move them and remove the empty folders)
"""
from __future__ import annotations

import argparse

import router as RT
from imap_util import Mailbox, quote

OLD = ("6 Loja e Encomendas", "7 Parceiros e Promotores")


def plan(box: Mailbox) -> list[tuple[str, int | None]]:
    """(old folder, messages in it), None for a folder that is not there."""
    existing, out = box.folders(), []
    for name, enc in box.encoded(dict(zip(OLD, OLD))).items():
        if enc not in existing:
            out.append((name, None))
            continue
        box.select(enc, readonly=True)
        out.append((name, len(box.uids())))
    return out


def _marked_deleted(box: Mailbox) -> list[bytes]:
    data = box._ok(box.c.uid("SEARCH", None, "DELETED"), "SEARCH")
    return data[0].split() if data and data[0] else []


def apply(box: Mailbox, dest_name: str, say=print) -> int:
    """Move every message of the old folders into `dest_name`; remove each folder that is then empty.
    Returns how many messages were moved."""
    existing = box.folders()
    dest = box.ensure_folders({"F9": dest_name})["F9"]
    moved = 0
    for name, enc in box.encoded(dict(zip(OLD, OLD))).items():
        if enc not in existing:
            continue
        box.select(enc)
        stale = _marked_deleted(box)
        if stale:   # COPY + \Deleted on a server without MOVE or UIDPLUS: copying again would duplicate them
            say(f"{name}: {len(stale)} message(s) already copied but still marked deleted (the server has no MOVE "
                "or UIDPLUS) - stopped; expunge or remove them by hand, then run again")
            return moved
        uids = box.uids()
        for uid in uids:
            box.move(uid, dest)
        box.select(enc, readonly=True)
        left = len(box.uids())
        moved += len(uids) - left
        if left:
            say(f"{name}: {left} message(s) still there after the move - folder kept")
            continue
        box.select(Mailbox.INBOX, readonly=True)   # never delete the selected folder
        box.c.unsubscribe(quote(enc))
        box._ok(box.c.delete(quote(enc)), f"DELETE {name}")
        say(f"{name}: {len(uids)} moved to {dest_name}, folder removed")
    return moved


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="move the messages and remove the empty folders")
    args = ap.parse_args(argv)
    cfg = RT.load_config()
    box = RT.connect(cfg)
    try:
        for name, n in plan(box):
            print(f"{'-' if n is None else n:>5}  {name}" + ("  (not there)" if n is None else ""))
        if not args.apply:
            print(f"dry run: nothing moved. Run with --apply to move these into {cfg['folders']['F9']}.")
            return 0
        print(f"moved {apply(box, cfg['folders']['F9'])} messages into {cfg['folders']['F9']}")
    finally:
        box.logout()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

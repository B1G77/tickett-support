#!/usr/bin/env python3
"""One-off: fold the hand-made folders back into INBOX and remove them.

Keeps INBOX, the system folders (Sent, Drafts, Trash, Junk/Spam, Archive) and
the nine router folders. Every message is MOVED to INBOX - nothing is deleted -
and its original folder is logged in data/router/old_folders.jsonl, so the
old grouping (mostly by event) is not lost and could be recreated. A folder is
only removed once it is empty. The nine router folders are created.

Usage: cleanup_folders.py --plan | --apply
"""
from __future__ import annotations

import argparse
import json
import re
import sys

import router as RT
from imap_util import Mailbox, decode_mutf7, quote

SYSTEM_FLAGS = ("\\Sent", "\\Drafts", "\\Trash", "\\Junk", "\\Archive", "\\All")
SYSTEM_NAMES = {"sent", "sent messages", "drafts", "trash", "deleted messages", "spam", "junk", "archive"}
_MID = re.compile(rb"Message-I[dD]:\s*(<[^>\r\n]+>)")


def targets(box: Mailbox, keep_folders: dict[str, str]) -> list[tuple[str, str]]:
    """Hand-made folders, deepest first so parents are empty when removed."""
    keep = set(box.encoded(keep_folders).values()) | {Mailbox.INBOX}
    out = []
    for name, flags in box.folders().items():
        last = decode_mutf7(name).split(box.delim)[-1].strip().lower()
        if name in keep or name.upper() == "INBOX" or any(f in flags for f in SYSTEM_FLAGS) or last in SYSTEM_NAMES:
            continue
        out.append((name, flags))
    return sorted(out, key=lambda x: (-x[0].count(box.delim), x[0]))


def message_ids(box: Mailbox, enc: str) -> list[str]:
    box.select(enc, readonly=True)
    if not box.uids():
        return []
    typ, data = box.c.uid("FETCH", "1:*", "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
    return [m.group(1).decode(errors="replace") for part in data if isinstance(part, tuple)
            for m in [_MID.search(part[1] or b"")] if m]


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    cfg = RT.load_config()
    box = RT.connect(cfg)
    todo = targets(box, cfg["folders"])
    inbox_ids = set(message_ids(box, Mailbox.INBOX))
    plan = []
    for enc, flags in todo:
        ids = [] if "\\Noselect" in flags else message_ids(box, enc)
        plan.append((enc, flags, ids))
        print(f"{len(ids):>5} msgs  ({sum(i in inbox_ids for i in ids):>3} also in INBOX)  {decode_mutf7(enc)}"
              + ("  [container only]" if "\\Noselect" in flags else ""))
    print(f"total: {sum(len(p[2]) for p in plan)} messages in {len(plan)} hand-made folders -> INBOX")
    if not args.apply:
        box.logout()
        return 0

    log_path = cfg["state_dir"] / "old_folders.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    created = box.ensure_folders(cfg["folders"])
    with log_path.open("a", encoding="utf-8") as log:
        for enc, flags, ids in plan:
            name = decode_mutf7(enc)
            if ids:
                for mid in ids:
                    log.write(json.dumps({"message_id": mid, "old_folder": name}, ensure_ascii=False) + "\n")
                log.flush()
                box.select(enc)
                box._ok(box.c.uid("MOVE", "1:*", quote(Mailbox.INBOX)), f"MOVE {name}")
                box.select(enc, readonly=True)
                if box.uids():
                    print(f"  {name}: not empty after move - left in place", file=sys.stderr)
                    continue
            box.select(Mailbox.INBOX, readonly=True)   # never delete the selected folder
            box.c.unsubscribe(quote(enc))
            res = box.c.delete(quote(enc))
            if res[0] != "OK" and any(b"NONEXISTENT" in x for x in res[1] if isinstance(x, bytes)):
                # An empty container folder disappears with its last subfolder.
                print(f"  {name}: already gone (removed together with its subfolder)")
                continue
            box._ok(res, f"DELETE {name}")
            print(f"  removed {name} ({len(ids)} messages moved to INBOX)")
    print("router folders:", ", ".join(decode_mutf7(v) for v in created.values()))
    box.logout()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
